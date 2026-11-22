"""Orchestration of one parser run: profile, proxy bridge, browser, items, run row."""
import asyncio
import logging
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from uwp.browser import BrowserUnavailable, ProfileInUse, launch
from uwp.config import Settings
from uwp.db.repo.items import ItemsRepo
from uwp.db.repo.runs import RunsRepo
from uwp.domain import ErrorKind, Job, Profile, ProfileKind, Proxy, Run, RunStatus, Site
from uwp.parsers.base import BaseParser, ParseContext, ParseError
from uwp.parsers.registry import ParserRegistry
from uwp.profiles import Identity, ProfileManager, identity_from_profile
from uwp.reports import export_report
from uwp.secretbox import SecretUnreadable
from uwp.timeutil import utcnow

log = logging.getLogger(__name__)

# A healthy page answers well under a second (measured: 0.1 s). The timeout
# exists only so a crashed renderer cannot hold the run for 30 s for a picture
# that will never arrive.
_SCREENSHOT_TIMEOUT_MS = 5_000

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
    # A catalogue key for a sentence *we* wrote (a blocked profile, an unreadable
    # secret). ``delivery`` renders it in the recipient's language. A detail that
    # came from Playwright or a parser stays in ``error`` and is shown raw.
    error_code: str | None = None
    # Values for the code's placeholders (``{"pid": 753}``).
    error_detail: dict = field(default_factory=dict)
    duration_ms: int = 0
    meta: dict = field(default_factory=dict)
    notes: str | None = None
    report_path: Path | None = None
    # ``written`` (file exists), ``skipped`` (export_mode said no) or ``failed``
    # (writing raised). The admin must be able to tell them apart.
    report_status: str = "skipped"
    report_error: str | None = None
    artifacts_dir: Path | None = None
    run_id: int | None = None
    profile_id: int | None = None


def _row_key(row: dict) -> str:
    """The dedup key of a row: the parser's ``external_key`` or its first column."""
    key = row.get("external_key")
    if key in (None, ""):
        key = row.get("app_id")
    if key in (None, ""):
        # A parser that forgets the key is a parser bug, not a bad config.
        raise ValueError("parser row has no external_key")
    return str(key)


def _tracked_view(payload: dict, tracked: list[str]) -> dict:
    """Only the report columns matter when deciding whether a row changed."""
    return {key: payload.get(key) for key in tracked}


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
        outcome = RunOutcome(status=RunStatus.failed, run_id=run_id, artifacts_dir=artifacts_dir)
        profile: Profile | None = None
        session: Any = None
        bridge: Any = None
        try:
            parser = self.registry.get(site.parser_key)
            if parser is None:
                raise _ConfigError(
                    f"unknown parser '{site.parser_key}'",
                    "error.parser.unknown",
                    parser=site.parser_key,
                )
            if cancel is not None and cancel.is_set():
                outcome.status = RunStatus.canceled
                return outcome
            profile = await self._acquire_profile(site, run_id)
            outcome.profile_id = profile.id
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
            new_keys, changed_keys, vanished = await self._save_rows(site, result.rows)
            outcome.new_count = len(new_keys)
            outcome.status = RunStatus.ok
            outcome.duration_ms = int((time.monotonic() - started) * 1000)
            self._export(site, outcome, profile, proxy, new_keys, changed_keys, vanished)
            return outcome
        except ParseError as exc:
            outcome.error_kind = exc.kind
            outcome.error = exc.message
            if exc.partial_rows:
                outcome.rows = exc.partial_rows
                try:
                    # ``complete=False``: these rows are a truncated slice, so
                    # the rows missing from it are not evidence of a sale ending
                    # and must not be flagged absent.
                    new_keys, changed_keys, vanished = await self._save_rows(
                        site, exc.partial_rows, complete=False
                    )
                    outcome.new_count = len(new_keys)
                    outcome.duration_ms = int((time.monotonic() - started) * 1000)
                    self._export(
                        site, outcome, profile, proxy, new_keys, changed_keys, vanished
                    )
                except Exception:  # bad partial rows must not mask the real error
                    log.exception("saving partial rows of run %s failed", run_id)
                outcome.status = RunStatus.partial
            else:
                outcome.status = RunStatus.failed
            return outcome
        except TimeoutError:
            outcome.error_kind = ErrorKind.transient
            outcome.error = f"run timed out after {self.settings.run_timeout}s"
            # The sentence is ours; the seconds are its data.
            outcome.error_code = "error.run.timeout"
            outcome.error_detail = {"seconds": self.settings.run_timeout}
            outcome.status = RunStatus.failed
            return outcome
        except BrowserUnavailable as exc:
            outcome.error_kind = ErrorKind.config_error
            outcome.error = str(exc)
            # ``browser`` says "fingerprint-chromium not found; run `uwp browser
            # install`, then set UWP_BROWSER_PATH …" — correct, but English and
            # written for a terminal.
            outcome.error_code = "error.browser.missing"
            outcome.status = RunStatus.failed
            return outcome
        except ProfileInUse as exc:
            # A blocked profile is an environment problem, not a parser bug:
            # the raw Playwright text here is a 7 KB call log. The sentence is
            # ours, so only the code travels; ``delivery`` picks the language.
            outcome.error_kind = ErrorKind.config_error
            outcome.error = str(exc)
            outcome.error_code = exc.code
            outcome.error_detail = dict(exc.detail)
            outcome.status = RunStatus.failed
            return outcome
        except SecretUnreadable as exc:
            # ``secretbox`` has no ``Settings`` and cannot choose a language.
            outcome.error_kind = ErrorKind.config_error
            outcome.error = str(exc)
            outcome.error_code = exc.code
            outcome.status = RunStatus.failed
            return outcome
        except _ConfigError as exc:
            outcome.error_kind = ErrorKind.config_error
            outcome.error = str(exc)
            outcome.error_code = exc.code
            outcome.error_detail = dict(exc.detail)
            outcome.status = RunStatus.failed
            return outcome
        except Exception as exc:  # any other failure is a parser bug
            log.exception("run %s of site %s failed", run_id, site.id)
            outcome.error_kind = ErrorKind.parser_error
            outcome.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            outcome.status = RunStatus.failed
            return outcome
        finally:
            # A failure is much easier to explain with a picture of the page.
            if outcome.status == RunStatus.failed and session is not None:
                await self._capture_screenshot(session, artifacts_dir)
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
                report_path=str(outcome.report_path) if outcome.report_path else None,
                artifact_dir=str(artifacts_dir) if artifacts_dir.is_dir() else None,
                profile_id=profile.id if profile is not None else None,
            )

    async def _capture_screenshot(self, session: Any, artifacts_dir: Path) -> None:
        """Save what the page looked like when a run failed, for the admin."""
        try:
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            # A crashed renderer answers no CDP call at all: measured, the
            # screenshot then blocks for the full ``UWP_PAGE_TIMEOUT_MS`` (30 s)
            # before raising. The failure is already known; the picture is
            # evidence, not a step the run should wait out.
            await session.page.screenshot(
                path=str(artifacts_dir / "screenshot.png"),
                timeout=_SCREENSHOT_TIMEOUT_MS,
            )
        except Exception:  # a screenshot is nice to have, never required
            # At ``debug`` under the default ``UWP_LOG_LEVEL=INFO`` a failed
            # capture left no trace anywhere: no file, no photo, no log line.
            log.warning(
                "screenshot of run in %s failed; the admin gets text only",
                artifacts_dir,
                exc_info=True,
            )

    def _export(
        self,
        site: Site,
        outcome: RunOutcome,
        profile: Profile | None,
        proxy: Proxy | None,
        new_keys: set[str],
        changed_keys: set[str],
        vanished: int,
    ) -> None:
        """Write the ``.ods`` report of a finished run, if the site wants one."""
        parser = self.registry.get(site.parser_key)
        if parser is None or not outcome.rows:
            return
        # ``settings.report_lang`` is a deployment setting: the worker has no
        # admin in front of it, so the report cannot follow a chat language.
        lang = self.settings.report_lang
        try:
            outcome.report_path = export_report(
                self.settings,
                site=site,
                parser=parser,
                rows=outcome.rows,
                profile=profile,
                proxy=proxy,
                when=utcnow(),
                duration_ms=outcome.duration_ms,
                new_keys=new_keys,
                changed_keys=changed_keys,
                vanished=vanished,
                lang=lang,
            )
            outcome.report_status = "written" if outcome.report_path else "skipped"
        except Exception as exc:  # a broken report must not fail an otherwise good run
            log.exception("writing the report of site %s failed", site.id)
            outcome.report_status = "failed"
            outcome.report_error = f"{type(exc).__name__}: {exc}"

    async def _acquire_profile(self, site: Site, run_id: int) -> Profile:
        if site.profile_mode == ProfileKind.persistent:
            if site.profile_id is None:
                raise _ConfigError(
                    "site has no persistent profile assigned",
                    "error.profile.none_assigned",
                )
            ttl = self.settings.run_timeout + LEASE_MARGIN_SECONDS
            if not await self.profiles.lease(site.profile_id, ttl):
                raise _ConfigError(
                    f"profile busy: {site.profile_id}",
                    "error.profile.locked",
                    id=site.profile_id,
                )
            profile = await self.profiles.repo.get(site.profile_id)
            if profile is None:
                raise _ConfigError(
                    f"profile {site.profile_id} not found",
                    "error.profile.not_found",
                    id=site.profile_id,
                )
            return profile
        return await self.profiles.create_clean(run_id)

    def _make_bridge(self, proxy: Proxy) -> Any:
        factory = self.bridge_factory or _default_bridge_factory
        try:
            return factory(proxy)
        except RuntimeError as exc:
            # The bridge is a package we ship: its sentence is ours to translate.
            raise _ConfigError(
                str(exc), "error.bridge.unavailable", detail=str(exc)
            ) from exc

    def _context(
        self,
        parser: BaseParser,
        site: Site,
        profile: Profile,
        session: Any,
        artifacts_dir: Path,
    ) -> ParseContext:
        def save_artifact(name: str, data: bytes) -> Path:
            # Created here, not up front: an empty ``<run_id>`` directory would
            # otherwise count against the retention of real screenshots.
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            path = artifacts_dir / Path(name).name
            path.write_bytes(data)
            return path

        try:
            parser_settings = parser.settings_model(**site.parser_settings)
        except Exception as exc:  # bad site settings are a config error
            # pydantic's message names the field and the bad value; it is the
            # detail of our sentence, not a sentence of its own.
            raise _ConfigError(
                f"invalid parser settings: {exc}",
                "error.parser.invalid_settings",
                # ``error_detail`` is stored as JSON, so the value is a string.
                detail=str(exc),
            ) from exc
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

    async def _save_rows(
        self, site: Site, rows: list[dict], *, complete: bool = True
    ) -> tuple[set[str], set[str], int]:
        """Store every row; return the new keys, changed keys and vanished count.

        ``external_key`` is the dedup key the parser owns (for Steam it is the
        app id). It stays on the in-memory rows so the report can flag new and
        changed lines, but is popped from the stored payload.

        A row counts as new when it was never stored (``created``) or when the
        previous run did not see it (``returned``): a discount that ends and
        comes back must show up again.

        ``complete=False`` means the row list is a truncated slice (a partial
        run). Presence is then left untouched: rows missing from a captcha'd
        page are not evidence that anything disappeared, and flagging them
        would turn the next full run into a mass of false new rows.
        """
        now = utcnow()
        parser = self.registry.get(site.parser_key)
        # Volatile columns stay in the report and in the stored payload; only
        # the change detection ignores them, or a drifting counter would make
        # ``export_mode=changes`` write a file on every run.
        tracked = (
            [column.key for column in parser.columns if not column.volatile]
            if parser
            else []
        )
        new_keys: set[str] = set()
        changed_keys: set[str] = set()
        seen: set[str] = set()

        for row in rows:
            key = _row_key(row)
            if key in seen:
                log.warning(
                    "parser of site %s returned duplicate key %s", site.slug, key
                )
                continue
            seen.add(key)
            row["external_key"] = key
            payload = {name: value for name, value in row.items() if name != "external_key"}
            previous = await self.items.previous_payload(site.id, key)
            result = await self.items.upsert(site.id, key, payload, now)
            if result.is_new:
                new_keys.add(key)
            elif previous is not None and _tracked_view(previous, tracked) != _tracked_view(
                payload, tracked
            ):
                changed_keys.add(key)

        vanished = await self.items.settle(site.id, seen) if complete else 0
        return new_keys, changed_keys, vanished



class _ConfigError(Exception):
    """A run cannot start because of configuration; mapped to ``config_error``.

    The message is an English developer line for ``runs.error`` and the log. A
    sentence the admin reads is ours, so the raise site also names its catalogue
    key and values; ``delivery`` renders those in the recipient's language, the
    same way ``ProfileInUse`` does (see ``browser.py``).
    """

    def __init__(
        self, message: str, code: str | None = None, **detail: object
    ) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


def _stored_error(outcome: RunOutcome) -> str | None:
    """The ``runs.error`` text: the error kind prefix plus the message."""
    if outcome.error is None:
        return None
    if outcome.error_kind is None:
        return outcome.error
    return f"{outcome.error_kind}: {outcome.error}"


def _identity(profile: Profile) -> Identity:
    """Rebuild the launch identity stored on the profile.

    ``identity_from_profile`` reads the machine fields from the row instead of
    re-deriving them from the seed, so a profile created for one country does not
    launch as another.
    """
    return identity_from_profile(profile)
