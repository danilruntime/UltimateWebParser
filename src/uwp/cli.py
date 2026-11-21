"""``uwp`` command line: setup, diagnostics and management without Telegram."""
import argparse
import asyncio
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from uwp.config import Settings, load_settings
from uwp.env import hide_process_environment
from uwp.i18n import DEFAULT_LANG, Lang, has_key, t

log = logging.getLogger(__name__)


def _lang(settings: Settings) -> Lang:
    """The deployment language: one operator, one language, no ``--lang`` flag."""
    return Lang(settings.default_language)


def _help_language() -> Lang:
    """The language of ``--help``, resolved without touching the filesystem.

    ``main`` must build the parser before it parses anything, but
    :func:`load_settings` creates ``./data``. ``uwp --help`` worked in a
    read-only directory before and must keep working there, so only the plain
    ``Settings`` is read here; a broken value falls back to the default and the
    real ``load_settings`` still reports it for every actual command.
    """
    try:
        return Lang(Settings().default_language)
    except Exception:  # noqa: BLE001 - help must never be the thing that fails
        return DEFAULT_LANG


# --- helpers ---------------------------------------------------------------


def _db(settings: Settings):
    from uwp.db.database import Database

    return Database(settings.db_path)


async def _with_db(settings: Settings, action: Callable[[object], Awaitable[int]]) -> int:
    db = _db(settings)
    await db.connect()
    try:
        await db.migrate()
        return await action(db)
    finally:
        await db.close()


def _settle(awaitable: Awaitable[int]) -> int:
    return asyncio.run(awaitable)


def _print(text: str = "") -> None:
    print(text)


def _find_site(sites, slug: str):
    return next((site for site in sites if site.slug == slug or str(site.id) == slug), None)


# --- commands --------------------------------------------------------------


def cmd_init(settings: Settings) -> int:
    from uwp.seed import seed, seed_site_name

    lang = _lang(settings)

    async def action(db) -> int:
        created = await seed(db, settings)
        if created:
            _print(t(lang, "cli.init.site_created", name=seed_site_name(settings)))
        else:
            _print(t(lang, "cli.init.skipped"))
        _print(t(lang, "cli.init.db_path", path=settings.db_path))
        return 0

    return _settle(_with_db(settings, action))


def _mask_proxy(url: str) -> str:
    """Hide credentials before a proxy URL reaches a log or a chat."""
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    if parts.password is None:
        return url
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    netloc = f"{parts.username}:***@{host}" if parts.username else f"***@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _loopback_in_container(host: str) -> bool:
    """``127.0.0.1`` inside a container is the container, not the host."""
    return host in {"127.0.0.1", "::1", "localhost"} and Path("/.dockerenv").exists()


def _proxy_url_problem(proxy: str, lang: Lang = DEFAULT_LANG) -> str | None:
    """Validate the URL shape without touching the network."""
    from urllib.parse import urlsplit

    parts = urlsplit(proxy)
    scheme = (parts.scheme or "").lower()
    if scheme not in {"http", "https", "socks5", "socks4"}:
        return t(lang, "cli.proxy.bad_scheme", scheme=scheme or "—")
    if not parts.hostname or parts.port is None:
        return t(lang, "cli.proxy.no_host_port")
    return None


def _probe_telegram_proxy(
    proxy: str,
    timeout: float = 10.0,
    lang: Lang = DEFAULT_LANG,
) -> str | None:
    """Return ``None`` when Bot API answers through the proxy, else the reason."""
    import asyncio
    from urllib.parse import urlsplit

    if (problem := _proxy_url_problem(proxy, lang)) is not None:
        return problem
    parts = urlsplit(proxy)
    scheme = (parts.scheme or "").lower()
    host = parts.hostname or ""
    port = parts.port or 0

    async def probe() -> str | None:
        import aiohttp

        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=min(timeout, 5)
            )
        except (OSError, TimeoutError) as exc:
            return t(lang, "cli.proxy.unreachable", host=host, port=port, error=exc)
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

        if scheme.startswith("socks"):
            try:
                from aiohttp_socks import ProxyConnector
            except ImportError:
                return t(lang, "cli.proxy.socks_missing")
            # ``rdns=True`` matters: resolving locally picks the AAAA record
            # first, and an unreachable IPv6 route turns a 0.5 s call into a
            # 60 s timeout. aiogram uses the same setting.
            connector = ProxyConnector.from_url(proxy, rdns=True)
            session_args: dict = {"connector": connector}
        else:
            session_args = {"proxy": proxy}
        try:
            async with (
                aiohttp.ClientSession(**session_args) as session,
                session.get(
                    "https://api.telegram.org/",
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as response,
            ):
                if response.status >= 500:
                    return t(lang, "cli.proxy.http_status", status=response.status)
                return None
        except Exception as exc:  # noqa: BLE001 - any failure is a failed check
            return t(
                lang,
                "cli.proxy.request_failed",
                error=f"{type(exc).__name__}: {exc}",
            )

    async def guarded() -> str | None:
        # A hard wall around the whole probe: the SOCKS handshake inside
        # ``start_tls`` can outlive aiohttp's own timeout, and a healthcheck on
        # a flaky upstream proxy must not stretch to a minute.
        try:
            return await asyncio.wait_for(probe(), timeout=timeout + 2)
        except TimeoutError:
            return t(lang, "cli.proxy.timeout", seconds=timeout)

    reason = asyncio.run(guarded())
    if reason and _loopback_in_container(host):
        reason += t(lang, "cli.proxy.loopback_hint")
    return reason


def _check_telegram_proxy(
    proxy: str, online: bool = True, lang: Lang = DEFAULT_LANG
) -> tuple[str, bool, str]:
    """``doctor`` row for UWP_TELEGRAM_PROXY.

    ``online=False`` (the container healthcheck) only validates the URL: the
    probe crosses a third-party proxy, and its slowness must not mark a working
    container unhealthy.
    """
    label = t(lang, "cli.doctor.proxy_label", url=_mask_proxy(proxy))
    if not online:
        problem = _proxy_url_problem(proxy, lang)
        return (label, problem is None, problem or "")
    reason = _probe_telegram_proxy(proxy, lang=lang)
    return (label, reason is None, reason or "")


def _check_playwright_driver(lang: Lang = DEFAULT_LANG) -> tuple[bool, str]:
    """Run the driver's bundled ``node --version``; returns ``(ok, detail)``.

    Playwright talks to its own Node build, not the system one. A missing shared
    library (``libatomic.so.1`` in a slim Debian image) makes every run fail
    with "Connection closed while reading from the driver" while the browser
    check still passes — hence a real execution rather than a file-exists test.

    Split out of :func:`cmd_doctor` so tests can stub it: whether a test
    machine happens to have ``libatomic1`` must not decide whether a test about
    bot tokens passes.
    """
    try:
        from playwright._impl._driver import compute_driver_executable

        driver = compute_driver_executable()
        node = str(driver[0] if isinstance(driver, tuple) else driver)
    except Exception as exc:  # noqa: BLE001 - an import failure is a failed check
        return False, t(lang, "cli.doctor.driver_missing", error=exc)

    try:
        completed = subprocess.run(
            [node, "--version"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, t(lang, "cli.doctor.driver_broken", error=exc)

    output = (completed.stderr or completed.stdout).strip()
    return completed.returncode == 0, output.splitlines()[0] if output else ""


def cmd_doctor(
    settings: Settings, quiet: bool = False, offline: bool = False, worker: bool = False
) -> int:
    from uwp.browser import BrowserUnavailable, resolve_browser

    lang = _lang(settings)
    checks: list[tuple[str, bool, bool]] = []  # (text, ok, is_warning)

    def check(name: str, ok: bool, detail: str = "", warning: bool = False) -> None:
        # A detail only explains a problem, so a passing check stays terse.
        suffix = "" if ok else (f": {detail}" if detail else "")
        mark = "✅" if ok else ("⚠️" if warning else "❌")
        checks.append((f"{mark} {name}{suffix}", ok, warning))

    for directory in (settings.data_dir, settings.profiles_dir, settings.exports_dir):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            writable = os.access(directory, os.W_OK)
        except OSError:
            writable = False
        check(t(lang, "cli.doctor.dir", path=directory), writable)

    browser_label = t(lang, "cli.doctor.browser")
    try:
        executable = resolve_browser(settings)
    except BrowserUnavailable as exc:
        check(browser_label, False, str(exc))
    else:
        try:
            completed = subprocess.run(
                [str(executable), "--version"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            detail = t(lang, "cli.doctor.browser_no_version", error=exc)
            check(browser_label, False, detail)
        else:
            if completed.returncode == 0:
                check(browser_label, True, completed.stdout.strip() or str(executable))
            else:
                check(
                    browser_label,
                    False,
                    t(lang, "cli.doctor.browser_returncode", code=completed.returncode),
                )

    for tool in ("Xvfb", "x11vnc", "websockify"):
        found = shutil.which(tool)
        check(
            tool,
            found is not None,
            found or t(lang, "cli.doctor.tool_missing"),
            warning=True,
        )

    # Playwright talks to its own bundled Node build, not the system one.  A
    # missing shared library (``libatomic.so.1`` in a slim Debian image) makes
    # every run fail with "Connection closed while reading from the driver"
    # while the browser check above still passes.
    ok, detail = _check_playwright_driver(lang)
    check(t(lang, "cli.doctor.playwright"), ok, detail)

    # The split worker never polls Telegram, so a deployment that keeps the
    # token out of its environment is correct, not broken.
    if not worker:
        check(
            "UWP_BOT_TOKEN",
            bool(settings.bot_token),
            t(lang, "cli.doctor.token_missing"),
        )
    check(
        "UWP_ADMIN_IDS",
        bool(settings.admin_ids),
        t(lang, "cli.doctor.admins_missing"),
    )
    if settings.telegram_proxy:
        check(
            *_check_telegram_proxy(
                settings.telegram_proxy, online=not offline, lang=lang
            )
        )
    else:
        check(
            t(lang, "cli.doctor.proxy_none"),
            True,
            t(lang, "cli.doctor.proxy_missing"),
        )
    if not settings.secret_key:
        check(
            "UWP_SECRET_KEY",
            False,
            t(lang, "cli.doctor.secret_missing"),
            warning=True,
        )
    elif len(settings.secret_key) < 16:
        # PBKDF2 makes guessing expensive, but a 4-character secret is still
        # guessable; a leaked DB would give the attacker an offline oracle.
        check(
            "UWP_SECRET_KEY",
            False,
            t(lang, "cli.doctor.secret_short", length=len(settings.secret_key)),
            warning=True,
        )
    else:
        check("UWP_SECRET_KEY", True)

    try:
        async def action(db) -> int:
            return 0

        _settle(_with_db(settings, action))
        check(t(lang, "cli.doctor.db"), True, str(settings.db_path))
    except Exception as exc:  # noqa: BLE001 - any migration failure fails the check
        check(t(lang, "cli.doctor.db"), False, str(exc))

    failed = [text for text, ok, warning in checks if not ok and not warning]
    if not quiet:
        for text, _ok, _warning in checks:
            _print(text)
        if not failed:
            _print(t(lang, "cli.doctor.all_ok"))
    return 1 if failed else 0


def browser_url_for(version: str) -> str:
    """The release URL of a fingerprint-chromium version."""
    return (
        "https://github.com/adryfish/fingerprint-chromium/releases/download/"
        f"{version}/ungoogled-chromium-{version}-1-x86_64_linux.tar.xz"
    )


def cmd_browser_install(
    settings: Settings, version: str, url: str, dest: Path | None, sha256: str
) -> int:
    lang = _lang(settings)
    target = dest or settings.data_dir / "browser"
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".tar.xz", delete=False) as handle:
        archive = Path(handle.name)
    try:
        _print(t(lang, "cli.browser.downloading", url=url))
        urllib.request.urlretrieve(url, archive)
        digest = _sha256(archive)
        if digest != sha256:
            _print(t(lang, "cli.browser.sha_mismatch", digest=digest))
            return 1
        _print(t(lang, "cli.browser.sha_ok"))
        _safe_extract(archive, target, lang)
    finally:
        archive.unlink(missing_ok=True)
    executable = target / "chrome"
    _print(t(lang, "cli.browser.installed", path=executable))
    _print(t(lang, "cli.browser.export_hint", path=executable))
    return 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_extract(archive: Path, target: Path, lang: Lang = DEFAULT_LANG) -> None:
    """Extract a tar archive, refusing absolute paths and ``..`` escapes."""
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError(t(lang, "cli.browser.unsafe_path", path=member.name))
        tar.extractall(target, filter="data")


def cmd_parsers_list(settings: Settings) -> int:
    from uwp.parsers.registry import ParserRegistry

    lang = _lang(settings)
    registry = ParserRegistry.discover()
    for parser in registry.all():
        login = t(lang, "cli.parsers.login_required") if parser.requires_login else ""
        _print(f"{parser.key:<20} {parser.title_for(lang)}{login}")
    for name, reason in registry.broken().items():
        _print(f"⚠️ {name}: {reason}")
    return 0


def cmd_site_list(settings: Settings) -> int:
    from uwp.db.repo.sites import SitesRepo
    from uwp.timeutil import format_local

    lang = _lang(settings)

    async def action(db) -> int:
        sites = await SitesRepo(db).list()
        if not sites:
            _print(t(lang, "cli.site.empty"))
            return 0
        _print(
            t(
                lang,
                "cli.site.list_header",
                slug=t(lang, "cli.site.col_slug"),
                state=t(lang, "cli.site.col_state"),
                interval=t(lang, "cli.site.col_interval"),
                next=t(lang, "cli.site.col_next"),
                parser=t(lang, "cli.site.col_parser"),
            )
        )
        for site in sites:
            state = (
                t(lang, "cli.site.state_on")
                if site.enabled
                else t(lang, "cli.site.state_off")
            )
            interval = f"{site.interval_min_min}–{site.interval_max_min}"
            next_run = (
                format_local(site.next_run_at, settings.tz, lang)
                if site.next_run_at
                else "—"
            )
            _print(
                f"{site.slug:<20} {state:<10} {interval:<12} {next_run:<18} {site.parser_key}"
            )
        return 0

    return _settle(_with_db(settings, action))


def cmd_site_add(settings: Settings, parser_key: str, name: str | None) -> int:
    from uwp.db.repo.jobs import JobsRepo
    from uwp.db.repo.sites import SitesRepo
    from uwp.parsers.registry import ParserRegistry
    from uwp.scheduler import Scheduler
    from uwp.services.sites import SiteService

    lang = _lang(settings)

    async def action(db) -> int:
        sites, jobs = SitesRepo(db), JobsRepo(db)
        svc = SiteService(
            sites, jobs, Scheduler(sites, jobs), ParserRegistry.discover(), lang=lang
        )
        try:
            site = await svc.create(parser_key, name=name)
        except ValueError as exc:
            _print(f"❌ {exc}")
            return 1
        _print(t(lang, "cli.site.created", slug=site.slug, name=site.name))
        return 0

    return _settle(_with_db(settings, action))


def cmd_site_toggle(settings: Settings, slug: str, enabled: bool) -> int:
    from uwp.db.repo.jobs import JobsRepo
    from uwp.db.repo.sites import SitesRepo
    from uwp.scheduler import Scheduler
    from uwp.timeutil import utcnow

    lang = _lang(settings)

    async def action(db) -> int:
        sites, jobs = SitesRepo(db), JobsRepo(db)
        site = _find_site(await sites.list(), slug)
        if site is None:
            _print(t(lang, "cli.site.not_found", slug=slug))
            return 1
        svc_scheduler = Scheduler(sites, jobs)
        await sites.update(site.id, enabled=enabled)
        if enabled:
            await svc_scheduler.on_enabled(site.id, utcnow())
        else:
            await svc_scheduler.on_disabled(site.id)
        state = t(lang, "cli.site.state_on" if enabled else "cli.site.state_off")
        _print(t(lang, "cli.site.toggled", name=site.name, state=state))
        return 0

    return _settle(_with_db(settings, action))


def cmd_site_set(settings: Settings, slug: str, interval: str | None, name: str | None) -> int:
    from uwp.bot.handlers.settings import parse_interval
    from uwp.db.repo.sites import SitesRepo
    from uwp.services.sites import clean_site_name

    lang = _lang(settings)

    fields: dict[str, object] = {}
    if interval is not None:
        try:
            low, high = parse_interval(interval, lang)
        except ValueError as exc:
            _print(t(lang, "cli.site.bad_interval", error=exc))
            return 1
        fields["interval_min_min"] = low
        fields["interval_max_min"] = high
    if name is not None:
        try:
            fields["name"] = clean_site_name(name, lang)
        except ValueError as exc:
            # The bot refuses the same names; the CLI must not be the back door
            # that stores a button label Telegram would reject at render time.
            _print(f"❌ {exc}")
            return 1
    if not fields:
        _print(t(lang, "cli.site.nothing_to_change"))
        return 1

    async def action(db) -> int:
        repo = SitesRepo(db)
        site = _find_site(await repo.list(), slug)
        if site is None:
            _print(t(lang, "cli.site.not_found", slug=slug))
            return 1
        await repo.update(site.id, **fields)
        _print(
            t(
                lang,
                "cli.site.updated",
                name=fields.get("name", site.name),
            )
        )
        return 0

    return _settle(_with_db(settings, action))


def cmd_site_run(settings: Settings, slug: str) -> int:
    from uwp.db.repo.jobs import JobsRepo
    from uwp.db.repo.sites import SitesRepo

    lang = _lang(settings)

    async def action(db) -> int:
        sites, jobs = SitesRepo(db), JobsRepo(db)
        site = _find_site(await sites.list(), slug)
        if site is None:
            _print(t(lang, "cli.site.not_found", slug=slug))
            return 1
        job_id = await jobs.create(site.id, "manual", priority=10)
        if job_id is None:
            _print(t(lang, "cli.site.already_running"))
            return 1
        _print(t(lang, "cli.site.queued", job=job_id))
        return 0

    return _settle(_with_db(settings, action))


def cmd_profile_list(settings: Settings) -> int:
    from uwp.db.repo.profiles import ProfilesRepo

    lang = _lang(settings)

    async def action(db) -> int:
        profiles = [p for p in await ProfilesRepo(db).list() if p.kind == "persistent"]
        if not profiles:
            _print(t(lang, "cli.profile.empty"))
            return 0
        for profile in profiles:
            # Render first, pad second: the label is longer than the enum and
            # `:<8` on `profile.status` would misalign the Russian column.
            key = f"cli.profile.status.{profile.status}"
            status = t(lang, key) if has_key(lang, key) else str(profile.status)
            _print(
                f"{profile.id:<4} {profile.name:<20} {status:<20} "
                f"{profile.user_data_dir}"
            )
        return 0

    return _settle(_with_db(settings, action))


def cmd_profile_create(settings: Settings, name: str) -> int:
    from uwp.db.repo.profiles import ProfilesRepo
    from uwp.profiles import ProfileManager
    from uwp.services.profiles import ProfilesService

    lang = _lang(settings)

    async def action(db) -> int:
        repo = ProfilesRepo(db)
        service = ProfilesService(ProfileManager(settings, repo), repo, settings)
        # Through the service, not the manager: the manager only refuses a
        # duplicate, while the service also refuses a blank name and renders
        # both in the CLI's language. ``uwp profile create "   "`` used to
        # create a profile literally named by spaces.
        try:
            profile = await service.create(name, lang=lang)
        except ValueError as exc:
            _print(f"❌ {exc}")
            return 1
        _print(t(lang, "cli.profile.created", id=profile.id, name=profile.name))
        return 0

    return _settle(_with_db(settings, action))


def cmd_profile_delete(settings: Settings, profile_id: int) -> int:
    from uwp.db.repo.profiles import ProfilesRepo
    from uwp.profiles import ProfileManager
    from uwp.services.profiles import ProfilesService

    lang = _lang(settings)

    async def action(db) -> int:
        repo = ProfilesRepo(db)
        service = ProfilesService(ProfileManager(settings, repo), repo, settings)
        try:
            await service.delete(profile_id, lang=lang)
        except ValueError as exc:
            _print(f"❌ {exc}")
            return 1
        _print(t(lang, "cli.profile.deleted", id=profile_id))
        return 0

    return _settle(_with_db(settings, action))


def cmd_profile_login(
    settings: Settings, profile_id: int, site_slug: str | None = None
) -> int:
    from uwp.db.repo.profiles import ProfilesRepo
    from uwp.db.repo.proxies import ProxiesRepo
    from uwp.db.repo.sites import SitesRepo
    from uwp.db.repo.vnc import VncRepo
    from uwp.parsers.registry import ParserRegistry
    from uwp.services.proxies import ProxyService
    from uwp.services.vnc import VncService
    from uwp.timeutil import utcnow
    from uwp.vnc import VncManager

    lang = _lang(settings)

    async def action(db) -> int:
        from uwp.domain import VncStatus

        repo = ProfilesRepo(db)
        profile = await repo.get(profile_id)
        if profile is None:
            _print(t(lang, "cli.profile.not_found", id=profile_id))
            return 1
        site = None
        if site_slug is not None:
            site = _find_site(await SitesRepo(db).list(), site_slug)
            if site is None:
                _print(t(lang, "cli.site.not_found", slug=site_slug))
                return 1
        sessions = VncRepo(db)
        manager = VncManager(
            settings, repo, sessions, proxies=ProxyService(ProxiesRepo(db), settings)
        )
        svc = VncService(
            manager,
            repo,
            SitesRepo(db),
            settings,
            sessions,
            parser_auth_urls={
                parser.key: parser.login_url
                for parser in ParserRegistry.discover().all()
            },
        )
        try:
            info = await svc.request(
                profile_id, site_id=site.id if site is not None else None
            )
        except ValueError as exc:
            _print(f"❌ {exc}")
            return 1
        _print(t(lang, "cli.profile.vnc_link", url=info.url))
        _print(t(lang, "cli.profile.vnc_password", password=info.password))
        _print(t(lang, "cli.profile.vnc_hint"))
        try:
            while True:
                await asyncio.sleep(2)
                # Serve the same tick the worker does: that honours
                # ``uwp vnc stop`` typed in another terminal, the hard TTL and
                # the idle timeout, and reaps the stack once the row is gone.
                await manager.tick(utcnow())
                session = await sessions.get(info.session_id)
                if session is None or session.status in (
                    VncStatus.stopped,
                    VncStatus.failed,
                ):
                    _print(t(lang, "cli.profile.session_finished"))
                    break
        except (KeyboardInterrupt, asyncio.CancelledError):
            await svc.stop(info.session_id)
            _print(t(lang, "cli.profile.session_finished"))
        return 0

    return _settle(_with_db(settings, action))


def cmd_proxy_list(settings: Settings) -> int:
    from uwp.db.repo.proxies import ProxiesRepo

    lang = _lang(settings)

    async def action(db) -> int:
        proxies = await ProxiesRepo(db).list()
        if not proxies:
            _print(t(lang, "cli.proxy.empty"))
            return 0
        for proxy in proxies:
            _print(f"{proxy.id:<4} {proxy.name:<20} {proxy.scheme}://{proxy.host}:{proxy.port}")
        return 0

    return _settle(_with_db(settings, action))


def cmd_proxy_add(settings: Settings, name: str, url: str) -> int:
    from uwp.db.repo.proxies import ProxiesRepo
    from uwp.services.proxies import ProxyService

    lang = _lang(settings)

    async def action(db) -> int:
        try:
            proxy = await ProxyService(ProxiesRepo(db), settings).add(name, url)
        except ValueError as exc:
            _print(f"❌ {exc}")
            return 1
        _print(t(lang, "cli.proxy.added", id=proxy.id, name=proxy.name))
        return 0

    return _settle(_with_db(settings, action))


def cmd_proxy_test(settings: Settings, proxy_id: int) -> int:
    from uwp.db.repo.proxies import ProxiesRepo
    from uwp.services.proxies import ProxyService

    lang = _lang(settings)

    async def action(db) -> int:
        check = await ProxyService(ProxiesRepo(db), settings).test(proxy_id)
        key = "cli.proxy.check_ok" if check.ok else "cli.proxy.check_failed"
        _print(t(lang, key, text=check.text(lang)))
        return 0 if check.ok else 1

    return _settle(_with_db(settings, action))


def cmd_vnc_list(settings: Settings) -> int:
    from uwp.db.repo.vnc import VncRepo

    lang = _lang(settings)

    async def action(db) -> int:
        sessions = await VncRepo(db).active()
        if not sessions:
            _print(t(lang, "cli.vnc.empty"))
            return 0
        for session in sessions:
            _print(
                t(
                    lang,
                    "cli.vnc.line",
                    id=session.id,
                    profile=session.profile_id,
                    port=session.port,
                )
            )
        return 0

    return _settle(_with_db(settings, action))


def cmd_vnc_stop(settings: Settings, session_id: int) -> int:
    from uwp.db.repo.profiles import ProfilesRepo
    from uwp.db.repo.vnc import VncRepo
    from uwp.vnc import VncManager

    lang = _lang(settings)

    async def action(db) -> int:
        sessions = VncRepo(db)
        session = await sessions.get(session_id)
        if session is None:
            _print(t(lang, "cli.vnc.not_found", id=session_id))
            return 1
        if session.status.value in ("stopped", "failed"):
            _print(t(lang, "cli.vnc.already_stopped", id=session_id))
            return 0
        # This process does not own the browser stack, so it can only ask:
        # the worker's tick kills the processes and frees the profile.
        await VncManager(settings, ProfilesRepo(db), sessions).request_stop(session_id)
        _print(t(lang, "cli.vnc.stop_queued", id=session_id))
        return 0

    return _settle(_with_db(settings, action))


def cmd_export(settings: Settings, target: str, output: Path | None) -> int:
    from uwp.db.repo.runs import RunsRepo
    from uwp.db.repo.sites import SitesRepo

    lang = _lang(settings)

    async def action(db) -> int:
        runs, sites = RunsRepo(db), SitesRepo(db)
        run = None
        if target.isdigit():
            run = await runs.get(int(target))
        else:
            site = _find_site(await sites.list(), target)
            if site is not None:
                recent = await runs.list_for_site(site.id, limit=1)
                run = recent[0] if recent else None
        if run is None:
            _print(t(lang, "cli.export.not_found", target=target))
            return 1
        if not run.report_path or not Path(run.report_path).is_file():
            _print(t(lang, "cli.export.no_report"))
            return 1
        destination = output or Path(run.report_path).with_name(Path(run.report_path).name)
        if destination.resolve() != Path(run.report_path).resolve():
            shutil.copyfile(run.report_path, destination)
        _print(t(lang, "cli.export.written", path=destination))
        return 0

    return _settle(_with_db(settings, action))


def cmd_run(settings: Settings, once: bool) -> int:
    from uwp import app

    lang = _lang(settings)

    async def action() -> int:
        if once:
            await app.run_once(settings)
            _print(t(lang, "cli.run.once_done"))
        else:
            await app.run_combined(settings)
        return 0

    return _settle(action())


def cmd_bot(settings: Settings) -> int:
    from uwp import app

    return _settle(app.run_bot_only(settings))


def cmd_worker(settings: Settings) -> int:
    from uwp import app

    return _settle(app.run_worker_only(settings))


def cmd_db_upgrade(settings: Settings) -> int:
    lang = _lang(settings)

    async def action(db) -> int:
        _print(t(lang, "cli.db.upgraded", path=settings.db_path))
        return 0

    return _settle(_with_db(settings, action))


# --- argparse --------------------------------------------------------------


def build_parser(lang: Lang = DEFAULT_LANG) -> argparse.ArgumentParser:
    def tr(key: str) -> str:
        return t(lang, key)

    parser = argparse.ArgumentParser(prog="uwp", description="UltimateWebParser")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help=tr("cli.help.init"))

    doctor = sub.add_parser("doctor", help=tr("cli.help.doctor"))
    doctor.add_argument(
        "--quiet", action="store_true", help=tr("cli.help.doctor_quiet")
    )
    doctor.add_argument(
        "--offline",
        action="store_true",
        help=tr("cli.help.doctor_offline"),
    )
    doctor.add_argument(
        "--worker",
        action="store_true",
        help=tr("cli.help.doctor_worker"),
    )

    browser = sub.add_parser("browser", help=tr("cli.help.browser"))
    browser_sub = browser.add_subparsers(dest="browser_command", required=True)
    install = browser_sub.add_parser("install", help=tr("cli.help.browser_install"))
    install.add_argument(
        "--version", default=None, help=tr("cli.help.browser_version")
    )
    install.add_argument("--url", default=None, help=tr("cli.help.browser_url"))
    install.add_argument(
        "--dest", type=Path, default=None, help=tr("cli.help.browser_dest")
    )

    parsers = sub.add_parser("parsers", help=tr("cli.help.parsers"))
    parsers_sub = parsers.add_subparsers(dest="parsers_command", required=True)
    parsers_sub.add_parser("list", help=tr("cli.help.parsers_list"))

    site = sub.add_parser("site", help=tr("cli.help.site"))
    site_sub = site.add_subparsers(dest="site_command", required=True)
    site_sub.add_parser("list", help=tr("cli.help.site_list"))
    add = site_sub.add_parser("add", help=tr("cli.help.site_add"))
    add.add_argument("parser_key")
    add.add_argument("--name", default=None)
    enable = site_sub.add_parser("enable", help=tr("cli.help.site_enable"))
    enable.add_argument("slug")
    disable = site_sub.add_parser("disable", help=tr("cli.help.site_disable"))
    disable.add_argument("slug")
    run = site_sub.add_parser("run", help=tr("cli.help.site_run"))
    run.add_argument("slug")
    setter = site_sub.add_parser("set", help=tr("cli.help.site_set"))
    setter.add_argument("slug")
    setter.add_argument("--interval", default=None)
    setter.add_argument("--name", default=None)

    profile = sub.add_parser("profile", help=tr("cli.help.profile"))
    profile_sub = profile.add_subparsers(dest="profile_command", required=True)
    profile_sub.add_parser("list", help=tr("cli.help.profile_list"))
    create = profile_sub.add_parser("create", help=tr("cli.help.profile_create"))
    create.add_argument("name")
    gain = profile_sub.add_parser("login", help=tr("cli.help.profile_login"))
    gain.add_argument("profile_id", type=int)
    gain.add_argument(
        "--site", default=None, help=tr("cli.help.profile_login_site")
    )
    drop = profile_sub.add_parser("delete", help=tr("cli.help.profile_delete"))
    drop.add_argument("profile_id", type=int)

    proxy = sub.add_parser("proxy", help=tr("cli.help.proxy"))
    proxy_sub = proxy.add_subparsers(dest="proxy_command", required=True)
    proxy_sub.add_parser("list", help=tr("cli.help.proxy_list"))
    proxy_add = proxy_sub.add_parser("add", help=tr("cli.help.proxy_add"))
    proxy_add.add_argument("name")
    proxy_add.add_argument("url")
    proxy_test = proxy_sub.add_parser("test", help=tr("cli.help.proxy_test"))
    proxy_test.add_argument("proxy_id", type=int)

    vnc = sub.add_parser("vnc", help=tr("cli.help.vnc"))
    vnc_sub = vnc.add_subparsers(dest="vnc_command", required=True)
    vnc_sub.add_parser("list", help=tr("cli.help.vnc_list"))
    vnc_stop = vnc_sub.add_parser("stop", help=tr("cli.help.vnc_stop"))
    vnc_stop.add_argument("session_id", type=int)

    export = sub.add_parser("export", help=tr("cli.help.export"))
    export.add_argument("target", help=tr("cli.help.export_target"))
    export.add_argument("-o", "--output", type=Path, default=None)

    run_cmd = sub.add_parser("run", help=tr("cli.help.run"))
    run_cmd.add_argument("--once", action="store_true", help=tr("cli.help.run_once"))

    sub.add_parser("bot", help=tr("cli.help.bot"))
    sub.add_parser("worker", help=tr("cli.help.worker"))

    db = sub.add_parser("db", help=tr("cli.help.db"))
    db_sub = db.add_subparsers(dest="db_command", required=True)
    db_sub.add_parser("upgrade", help=tr("cli.help.db_upgrade"))

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; returns a process exit code."""
    # Profiles, the database and its WAL/SHM sidecars, reports and VNC logs are
    # all written by this process. The default 022 umask would make them
    # world-readable on the host (``./data`` is a bind mount).
    os.umask(0o077)
    # This process holds UWP_BOT_TOKEN and UWP_SECRET_KEY in its environment.
    # The VNC browser runs as the same uid, so ``file:///proc/<pid>/environ``
    # would hand both to any page that escapes Chromium's sandbox.
    hide_process_environment()
    logging.basicConfig(
        level=os.environ.get("UWP_LOG_LEVEL", "WARNING"),
        format="%(levelname)s %(name)s: %(message)s",
    )
    # ``argparse`` builds the help text before any command runs, so the
    # language must be known first. ``_help_language`` reads the environment
    # only; ``load_settings`` below creates ``./data`` and must not run for
    # ``uwp --help``.
    args = build_parser(_help_language()).parse_args(argv)
    settings = load_settings()

    if args.command == "init":
        return cmd_init(settings)
    if args.command == "doctor":
        return cmd_doctor(
            settings, quiet=args.quiet, offline=args.offline, worker=args.worker
        )
    if args.command == "browser":
        return cmd_browser_install(
            settings,
            version=args.version or settings.browser_version,
            # ``--version`` alone must change where the archive comes from, or
            # the SHA check would reject a download of a different release.
            url=args.url
            or (
                browser_url_for(args.version)
                if args.version
                else settings.browser_url
            ),
            dest=args.dest,
            sha256=settings.browser_sha256,
        )
    if args.command == "parsers":
        return cmd_parsers_list(settings)
    if args.command == "site":
        return _dispatch_site(settings, args)
    if args.command == "profile":
        return _dispatch_profile(settings, args)
    if args.command == "proxy":
        return _dispatch_proxy(settings, args)
    if args.command == "vnc":
        return _dispatch_vnc(settings, args)
    if args.command == "export":
        return cmd_export(settings, args.target, args.output)
    if args.command == "run":
        return cmd_run(settings, once=args.once)
    if args.command == "bot":
        return cmd_bot(settings)
    if args.command == "worker":
        return cmd_worker(settings)
    if args.command == "db":
        return cmd_db_upgrade(settings)
    return 1


def _dispatch_site(settings: Settings, args) -> int:
    if args.site_command == "list":
        return cmd_site_list(settings)
    if args.site_command == "add":
        return cmd_site_add(settings, args.parser_key, args.name)
    if args.site_command == "enable":
        return cmd_site_toggle(settings, args.slug, True)
    if args.site_command == "disable":
        return cmd_site_toggle(settings, args.slug, False)
    if args.site_command == "run":
        return cmd_site_run(settings, args.slug)
    if args.site_command == "set":
        return cmd_site_set(settings, args.slug, args.interval, args.name)
    return 1


def _dispatch_profile(settings: Settings, args) -> int:
    if args.profile_command == "list":
        return cmd_profile_list(settings)
    if args.profile_command == "create":
        return cmd_profile_create(settings, args.name)
    if args.profile_command == "login":
        return cmd_profile_login(settings, args.profile_id, args.site)
    if args.profile_command == "delete":
        return cmd_profile_delete(settings, args.profile_id)
    return 1


def _dispatch_proxy(settings: Settings, args) -> int:
    if args.proxy_command == "list":
        return cmd_proxy_list(settings)
    if args.proxy_command == "add":
        return cmd_proxy_add(settings, args.name, args.url)
    if args.proxy_command == "test":
        return cmd_proxy_test(settings, args.proxy_id)
    return 1


def _dispatch_vnc(settings: Settings, args) -> int:
    if args.vnc_command == "list":
        return cmd_vnc_list(settings)
    if args.vnc_command == "stop":
        return cmd_vnc_stop(settings, args.session_id)
    return 1


if __name__ == "__main__":
    sys.exit(main())
