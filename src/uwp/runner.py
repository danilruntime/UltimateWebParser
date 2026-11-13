"""Orchestration of one parser run: profile, proxy bridge, browser, items, run row."""
import asyncio
import logging
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from uwp.browser import BrowserUnavailable, launch
from uwp.config import Settings
from uwp.db.repo.items import ItemsRepo
from uwp.db.repo.runs import RunsRepo
from uwp.domain import ErrorKind, Job, Profile, ProfileKind, Proxy, Run, RunStatus, Site
from uwp.parsers.base import BaseParser, ParseContext, ParseError
from uwp.parsers.registry import ParserRegistry
from uwp.profiles import Identity, ProfileManager, identity_from_seed
from uwp.timeutil import utcnow

log = logging.getLogger(__name__)

# Lease length for a persistent profile: the run timeout plus a safety margin.
LEASE_MARGIN_SECONDS = 60


@dataclass
class RunOutcome:
    """What one run produced, for the worker to retry, notify and export."""

    status: RunStatus
    rows: list[dict] = field(default_factory=list)
    new_count: int = 0
    error_kind: ErrorKind | None = None
    error: str | None = None
    duration_ms: int = 0
    meta: dict = field(default_factory=dict)
    notes: str | None = None


def _default_bridge_factory(proxy: Proxy) -> Any:
    """Import the proxy bridge lazily so the runner works without it."""
    try:
        from uwp.proxybridge import ProxyBridge
    except ImportError as exc:
        raise RuntimeError(f"proxy bridge unavailable: {exc}") from exc
    return ProxyBridge(proxy)


class Runner:
    """Runs one job end to end and records the result in the ``runs`` table."""

    def __init__(
        self,
        settings: Settings,
        profiles: ProfileManager,
        registry: ParserRegistry,
        runs: RunsRepo,
        items: ItemsRepo,
        launcher: Callable[..., Any] = launch,
        bridge_factory: Callable[[Proxy], Any] | None = None,
    ) -> None:
        self.settings = settings
        self.profiles = profiles
        self.registry = registry
        self.runs = runs
        self.items = items
        self.launcher = launcher
        self.bridge_factory = bridge_factory

    async def run(
        self,
        site: Site,
        job: Job,
        proxy: Proxy | None,
        cancel: asyncio.Event | None = None,
    ) -> RunOutcome:
        """Execute the site's parser once and return the outcome."""
        started = time.monotonic()
        run_id = await self.runs.create(
            Run(job_id=job.id, site_id=site.id, status=RunStatus.running, proxy_id=proxy.id if proxy else None)
        )
        artifacts_dir = self.settings.artifacts_dir / str(run_id)
        outcome = RunOutcome(status=RunStatus.failed)
        profile: Profile | None = None
        session: Any = None
        bridge: Any = None
        try:
            parser = self.registry.get(site.parser_key)
            if parser is None:
                raise _ConfigError(f"unknown parser '{site.parser_key}'")
            if cancel is not None and cancel.is_set():
                outcome.status = RunStatus.canceled
                return outcome
            profile = await self._acquire_profile(site, run_id)
            if proxy is not None:
                bridge = self._make_bridge(proxy)
                proxy_url = await bridge.start()
            else:
                proxy_url = None
            session = await self.launcher(
                self.settings,
                _identity(profile),
                Path(profile.user_data_dir),
                proxy_url,
            )
            ctx = self._context(parser, site, profile, session, artifacts_dir)
            result = await asyncio.wait_for(parser.run(ctx), self.settings.run_timeout)
            if cancel is not None and cancel.is_set():
                outcome.status = RunStatus.canceled
                return outcome
            outcome.rows = result.rows
            outcome.meta = result.meta
            outcome.notes = result.notes
            outcome.new_count = await self._save_rows(site, result.rows)
            outcome.status = RunStatus.ok
            return outcome
        except ParseError as exc:
            outcome.error_kind = exc.kind
            outcome.error = exc.message
            if exc.partial_rows:
                outcome.rows = exc.partial_rows
                outcome.new_count = await self._save_rows(site, exc.partial_rows)
                outcome.status = RunStatus.partial
            else:
                outcome.status = RunStatus.failed
            return outcome
        except TimeoutError:
            outcome.error_kind = ErrorKind.transient
            outcome.error = f"run timed out after {self.settings.run_timeout}s"
            outcome.status = RunStatus.failed
            return outcome
        except BrowserUnavailable as exc:
            outcome.error_kind = ErrorKind.config_error
            outcome.error = str(exc)
            outcome.status = RunStatus.failed
            return outcome
        except _ConfigError as exc:
            outcome.error_kind = ErrorKind.config_error
            outcome.error = str(exc)
            outcome.status = RunStatus.failed
            return outcome
        except Exception as exc:  # any other failure is a parser bug
            log.exception("run %s of site %s failed", run_id, site.id)
            outcome.error_kind = ErrorKind.parser_error
            outcome.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            outcome.status = RunStatus.failed
            return outcome
        finally:
            if session is not None:
                try:
                    await session.close()
                except Exception:  # cleanup must not mask the outcome
                    log.exception("closing browser of run %s failed", run_id)
            if bridge is not None:
                try:
                    await bridge.stop()
                except Exception:
                    log.exception("stopping proxy bridge of run %s failed", run_id)
            if profile is not None:
                try:
                    await self.profiles.release(profile.id)
                except Exception:
                    log.exception("releasing profile %s failed", profile.id)
            outcome.duration_ms = int((time.monotonic() - started) * 1000)
            await self.runs.update(
                run_id,
                status=outcome.status,
                finished_at=utcnow(),
                duration_ms=outcome.duration_ms,
                error=_stored_error(outcome),
                rows_count=len(outcome.rows),
                new_count=outcome.new_count,
                artifact_dir=str(artifacts_dir) if artifacts_dir.is_dir() else None,
                profile_id=profile.id if profile is not None else None,
            )

    async def _acquire_profile(self, site: Site, run_id: int) -> Profile:
        if site.profile_mode == ProfileKind.persistent:
            if site.profile_id is None:
                raise _ConfigError("site has no persistent profile assigned")
            ttl = self.settings.run_timeout + LEASE_MARGIN_SECONDS
            if not await self.profiles.lease(site.profile_id, ttl):
                raise _ConfigError(f"profile busy: {site.profile_id}")
            profile = await self.profiles.repo.get(site.profile_id)
            if profile is None:
                raise _ConfigError(f"profile {site.profile_id} not found")
            return profile
        return await self.profiles.create_clean(run_id)

    def _make_bridge(self, proxy: Proxy) -> Any:
        factory = self.bridge_factory or _default_bridge_factory
        try:
            return factory(proxy)
        except RuntimeError as exc:
            raise _ConfigError(str(exc)) from exc

    def _context(
        self,
        parser: BaseParser,
        site: Site,
        profile: Profile,
        session: Any,
        artifacts_dir: Path,
    ) -> ParseContext:
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        def save_artifact(name: str, data: bytes) -> Path:
            path = artifacts_dir / Path(name).name
            path.write_bytes(data)
            return path

        try:
            parser_settings = parser.settings_model(**site.parser_settings)
        except Exception as exc:  # bad site settings are a config error
            raise _ConfigError(f"invalid parser settings: {exc}") from exc
        return ParseContext(
            page=session.page,
            context=session.context,
            settings=parser_settings,
            log=logging.getLogger(f"uwp.parsers.{parser.key}"),
            artifacts_dir=artifacts_dir,
            site=site,
            profile=profile,
            known_keys=lambda: self.items.keys(site.id),
            save_artifact=save_artifact,
        )

    async def _save_rows(self, site: Site, rows: list[dict]) -> int:
        now = utcnow()
        new_count = 0
        for row in rows:
            payload = dict(row)
            key = str(payload.pop("external_key"))
            if await self.items.upsert(site.id, key, payload, now):
                new_count += 1
        return new_count


class _ConfigError(Exception):
    """A run cannot start because of configuration; mapped to ``config_error``."""


def _stored_error(outcome: RunOutcome) -> str | None:
    """The ``runs.error`` text: the error kind prefix plus the message."""
    if outcome.error is None:
        return None
    if outcome.error_kind is None:
        return outcome.error
    return f"{outcome.error_kind}: {outcome.error}"


def _identity(profile: Profile) -> Identity:
    """Rebuild the launch identity stored on the profile.

    The profile keeps only the seed and the machine fields; the remaining
    fields are derived from the seed so a persistent profile keeps the same
    identity across runs.
    """
    return identity_from_seed(profile.seed)
