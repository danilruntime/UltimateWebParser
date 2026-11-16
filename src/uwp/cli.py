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
from uwp.seed import SEED_SITE_NAME

log = logging.getLogger(__name__)


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
    from uwp.seed import seed

    async def action(db) -> int:
        created = await seed(db, settings)
        if created:
            _print(f"Создан сайт «{SEED_SITE_NAME}» (выключен).")
        else:
            _print("Сайты уже есть, сид пропущен.")
        _print(f"База данных: {settings.db_path}")
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


def _proxy_url_problem(proxy: str) -> str | None:
    """Validate the URL shape without touching the network."""
    from urllib.parse import urlsplit

    parts = urlsplit(proxy)
    scheme = (parts.scheme or "").lower()
    if scheme not in {"http", "https", "socks5", "socks4"}:
        return f"неподдерживаемая схема «{scheme or '—'}» (нужно http, https или socks5)"
    if not parts.hostname or parts.port is None:
        return "в адресе прокси нет хоста или порта"
    return None


def _probe_telegram_proxy(proxy: str, timeout: float = 10.0) -> str | None:
    """Return ``None`` when Bot API answers through the proxy, else the reason."""
    import asyncio
    from urllib.parse import urlsplit

    if (problem := _proxy_url_problem(proxy)) is not None:
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
            return f"прокси {host}:{port} недоступен: {exc}"
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

        if scheme.startswith("socks"):
            try:
                from aiohttp_socks import ProxyConnector
            except ImportError:
                return "для socks-прокси нужен пакет aiohttp-socks"
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
                    return f"api.telegram.org вернул {response.status}"
                return None
        except Exception as exc:  # noqa: BLE001 - any failure is a failed check
            return f"запрос через прокси не прошёл: {type(exc).__name__}: {exc}"

    async def guarded() -> str | None:
        # A hard wall around the whole probe: the SOCKS handshake inside
        # ``start_tls`` can outlive aiohttp's own timeout, and a healthcheck on
        # a flaky upstream proxy must not stretch to a minute.
        try:
            return await asyncio.wait_for(probe(), timeout=timeout + 2)
        except TimeoutError:
            return f"прокси не ответил за {timeout:.0f} с"

    reason = asyncio.run(guarded())
    if reason and _loopback_in_container(host):
        reason += (
            "; внутри контейнера 127.0.0.1 — это сам контейнер, "
            "запустите его с network_mode: host или укажите адрес хоста"
        )
    return reason


def _check_telegram_proxy(proxy: str, online: bool = True) -> tuple[str, bool, str]:
    """``doctor`` row for UWP_TELEGRAM_PROXY.

    ``online=False`` (the container healthcheck) only validates the URL: the
    probe crosses a third-party proxy, and its slowness must not mark a working
    container unhealthy.
    """
    label = f"прокси Telegram ({_mask_proxy(proxy)})"
    if not online:
        problem = _proxy_url_problem(proxy)
        return (label, problem is None, problem or "")
    reason = _probe_telegram_proxy(proxy)
    return (label, reason is None, reason or "")


def _check_playwright_driver() -> tuple[bool, str]:
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
        return False, f"не найден: {exc}"

    try:
        completed = subprocess.run(
            [node, "--version"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"не запускается: {exc}"

    output = (completed.stderr or completed.stdout).strip()
    return completed.returncode == 0, output.splitlines()[0] if output else ""


def cmd_doctor(
    settings: Settings, quiet: bool = False, offline: bool = False, worker: bool = False
) -> int:
    from uwp.browser import BrowserUnavailable, resolve_browser

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
        check(f"каталог {directory}", writable)

    try:
        executable = resolve_browser(settings)
    except BrowserUnavailable as exc:
        check("браузер fingerprint-chromium", False, str(exc))
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
            check("браузер fingerprint-chromium", False, f"не запускается: {exc}")
        else:
            if completed.returncode == 0:
                check(
                    "браузер fingerprint-chromium",
                    True,
                    completed.stdout.strip() or str(executable),
                )
            else:
                check(
                    "браузер fingerprint-chromium",
                    False,
                    f"--version вернул {completed.returncode}",
                )

    for tool in ("Xvfb", "x11vnc", "websockify"):
        found = shutil.which(tool)
        check(tool, found is not None, found or "не найден (нужен только для VNC)", warning=True)

    # Playwright talks to its own bundled Node build, not the system one.  A
    # missing shared library (``libatomic.so.1`` in a slim Debian image) makes
    # every run fail with "Connection closed while reading from the driver"
    # while the browser check above still passes.
    ok, detail = _check_playwright_driver()
    check("драйвер playwright", ok, detail)

    # The split worker never polls Telegram, so a deployment that keeps the
    # token out of its environment is correct, not broken.
    if not worker:
        check("UWP_BOT_TOKEN", bool(settings.bot_token), "не задан")
    check("UWP_ADMIN_IDS", bool(settings.admin_ids), "пусто")
    if settings.telegram_proxy:
        check(*_check_telegram_proxy(settings.telegram_proxy, online=not offline))
    else:
        check(
            "прокси Telegram",
            True,
            "не задан (UWP_TELEGRAM_PROXY): нужен там, где api.telegram.org недоступен",
        )
    if not settings.secret_key:
        check(
            "UWP_SECRET_KEY",
            False,
            "не задан: пароли прокси хранятся открыто",
            warning=True,
        )
    elif len(settings.secret_key) < 16:
        # PBKDF2 makes guessing expensive, but a 4-character secret is still
        # guessable; a leaked DB would give the attacker an offline oracle.
        check(
            "UWP_SECRET_KEY",
            False,
            f"всего {len(settings.secret_key)} символов: нужен длинный "
            "случайный ключ (32+ символа)",
            warning=True,
        )
    else:
        check("UWP_SECRET_KEY", True)

    try:
        async def action(db) -> int:
            return 0

        _settle(_with_db(settings, action))
        check("база данных", True, str(settings.db_path))
    except Exception as exc:  # noqa: BLE001 - any migration failure fails the check
        check("база данных", False, str(exc))

    failed = [text for text, ok, warning in checks if not ok and not warning]
    if not quiet:
        for text, _ok, _warning in checks:
            _print(text)
        if not failed:
            _print("Всё в порядке.")
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
    target = dest or settings.data_dir / "browser"
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".tar.xz", delete=False) as handle:
        archive = Path(handle.name)
    try:
        _print(f"Скачиваю {url}")
        urllib.request.urlretrieve(url, archive)
        digest = _sha256(archive)
        if digest != sha256:
            _print(f"❌ sha256 не совпал: {digest}")
            return 1
        _print("sha256 совпал, распаковываю.")
        _safe_extract(archive, target)
    finally:
        archive.unlink(missing_ok=True)
    executable = target / "chrome"
    _print(f"Браузер установлен: {executable}")
    _print("Добавьте в окружение UWP_BROWSER_PATH=" + str(executable))
    return 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_extract(archive: Path, target: Path) -> None:
    """Extract a tar archive, refusing absolute paths and ``..`` escapes."""
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError(f"небезопасный путь в архиве: {member.name}")
        tar.extractall(target, filter="data")


def cmd_parsers_list() -> int:
    from uwp.parsers.registry import ParserRegistry

    registry = ParserRegistry.discover()
    for parser in registry.all():
        login = " (нужен вход)" if parser.requires_login else ""
        _print(f"{parser.key:<20} {parser.title}{login}")
    for name, reason in registry.broken().items():
        _print(f"⚠️ {name}: {reason}")
    return 0


def cmd_site_list(settings: Settings) -> int:
    from uwp.db.repo.sites import SitesRepo
    from uwp.timeutil import format_local

    async def action(db) -> int:
        sites = await SitesRepo(db).list()
        if not sites:
            _print("Сайтов нет. Запустите `uwp init`.")
            return 0
        _print(f"{'slug':<20} {'состояние':<10} {'интервал':<12} {'следующий':<18} парсер")
        for site in sites:
            state = "включён" if site.enabled else "выключен"
            interval = f"{site.interval_min_min}–{site.interval_max_min}"
            next_run = (
                format_local(site.next_run_at, settings.tz) if site.next_run_at else "—"
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

    async def action(db) -> int:
        sites, jobs = SitesRepo(db), JobsRepo(db)
        svc = SiteService(sites, jobs, Scheduler(sites, jobs), ParserRegistry.discover())
        try:
            site = await svc.create(parser_key, name=name)
        except ValueError as exc:
            _print(f"❌ {exc}")
            return 1
        _print(f"Создан сайт {site.slug} ({site.name}), пока выключен.")
        return 0

    return _settle(_with_db(settings, action))


def cmd_site_toggle(settings: Settings, slug: str, enabled: bool) -> int:
    from uwp.db.repo.jobs import JobsRepo
    from uwp.db.repo.sites import SitesRepo
    from uwp.scheduler import Scheduler
    from uwp.timeutil import utcnow

    async def action(db) -> int:
        sites, jobs = SitesRepo(db), JobsRepo(db)
        site = _find_site(await sites.list(), slug)
        if site is None:
            _print(f"❌ Сайт «{slug}» не найден.")
            return 1
        svc_scheduler = Scheduler(sites, jobs)
        await sites.update(site.id, enabled=enabled)
        if enabled:
            await svc_scheduler.on_enabled(site.id, utcnow())
        else:
            await svc_scheduler.on_disabled(site.id)
        _print(f"Сайт «{site.name}» {'включён' if enabled else 'выключен'}.")
        return 0

    return _settle(_with_db(settings, action))


def cmd_site_set(settings: Settings, slug: str, interval: str | None, name: str | None) -> int:
    from uwp.bot.handlers.settings import parse_interval
    from uwp.db.repo.sites import SitesRepo

    fields: dict[str, object] = {}
    if interval is not None:
        try:
            low, high = parse_interval(interval)
        except ValueError as exc:
            _print(f"❌ Интервал: {exc}")
            return 1
        fields["interval_min_min"] = low
        fields["interval_max_min"] = high
    if name is not None:
        fields["name"] = name
    if not fields:
        _print("Нечего менять: укажите --interval и/или --name.")
        return 1

    async def action(db) -> int:
        repo = SitesRepo(db)
        site = _find_site(await repo.list(), slug)
        if site is None:
            _print(f"❌ Сайт «{slug}» не найден.")
            return 1
        await repo.update(site.id, **fields)
        _print(f"Сайт «{site.name}» обновлён.")
        return 0

    return _settle(_with_db(settings, action))


def cmd_site_run(settings: Settings, slug: str) -> int:
    from uwp.db.repo.jobs import JobsRepo
    from uwp.db.repo.sites import SitesRepo

    async def action(db) -> int:
        sites, jobs = SitesRepo(db), JobsRepo(db)
        site = _find_site(await sites.list(), slug)
        if site is None:
            _print(f"❌ Сайт «{slug}» не найден.")
            return 1
        job_id = await jobs.create(site.id, "manual", priority=10)
        if job_id is None:
            _print("Прогон уже идёт или стоит в очереди.")
            return 1
        _print(f"Прогон поставлен в очередь (job {job_id}).")
        return 0

    return _settle(_with_db(settings, action))


def cmd_profile_list(settings: Settings) -> int:
    from uwp.db.repo.profiles import ProfilesRepo

    async def action(db) -> int:
        profiles = [p for p in await ProfilesRepo(db).list() if p.kind == "persistent"]
        if not profiles:
            _print("Постоянных профилей нет.")
            return 0
        for profile in profiles:
            _print(f"{profile.id:<4} {profile.name:<20} {profile.status:<8} {profile.user_data_dir}")
        return 0

    return _settle(_with_db(settings, action))


def cmd_profile_create(settings: Settings, name: str) -> int:
    from uwp.db.repo.profiles import ProfilesRepo
    from uwp.profiles import ProfileManager

    async def action(db) -> int:
        repo = ProfilesRepo(db)
        profile = await ProfileManager(settings, repo).create_persistent(name)
        _print(f"Создан профиль {profile.id} «{profile.name}».")
        return 0

    return _settle(_with_db(settings, action))


def cmd_profile_delete(settings: Settings, profile_id: int) -> int:
    from uwp.db.repo.profiles import ProfilesRepo
    from uwp.profiles import ProfileManager

    async def action(db) -> int:
        repo = ProfilesRepo(db)
        try:
            await ProfileManager(settings, repo).delete(profile_id)
        except ValueError as exc:
            _print(f"❌ {exc}")
            return 1
        _print(f"Профиль {profile_id} удалён.")
        return 0

    return _settle(_with_db(settings, action))


def cmd_profile_login(settings: Settings, profile_id: int) -> int:
    from uwp.db.repo.profiles import ProfilesRepo
    from uwp.db.repo.proxies import ProxiesRepo
    from uwp.db.repo.sites import SitesRepo
    from uwp.db.repo.vnc import VncRepo
    from uwp.services.proxies import ProxyService
    from uwp.services.vnc import VncService
    from uwp.timeutil import utcnow
    from uwp.vnc import VncManager

    async def action(db) -> int:
        from uwp.domain import VncStatus

        repo = ProfilesRepo(db)
        profile = await repo.get(profile_id)
        if profile is None:
            _print(f"❌ Профиль {profile_id} не найден.")
            return 1
        sessions = VncRepo(db)
        manager = VncManager(
            settings, repo, sessions, proxies=ProxyService(ProxiesRepo(db), settings)
        )
        svc = VncService(manager, repo, SitesRepo(db), settings, sessions)
        info = await svc.request(profile_id)
        _print(f"Ссылка: {info.url}")
        _print(f"Пароль: {info.password}")
        _print("Нажмите Ctrl+C, чтобы завершить сессию.")
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
                    _print("Сессия завершена.")
                    break
        except (KeyboardInterrupt, asyncio.CancelledError):
            await svc.stop(info.session_id)
            _print("Сессия завершена.")
        return 0

    return _settle(_with_db(settings, action))


def cmd_proxy_list(settings: Settings) -> int:
    from uwp.db.repo.proxies import ProxiesRepo

    async def action(db) -> int:
        proxies = await ProxiesRepo(db).list()
        if not proxies:
            _print("Прокси нет.")
            return 0
        for proxy in proxies:
            _print(f"{proxy.id:<4} {proxy.name:<20} {proxy.scheme}://{proxy.host}:{proxy.port}")
        return 0

    return _settle(_with_db(settings, action))


def cmd_proxy_add(settings: Settings, name: str, url: str) -> int:
    from uwp.db.repo.proxies import ProxiesRepo
    from uwp.services.proxies import ProxyService

    async def action(db) -> int:
        try:
            proxy = await ProxyService(ProxiesRepo(db), settings).add(name, url)
        except ValueError as exc:
            _print(f"❌ {exc}")
            return 1
        _print(f"Добавлен прокси {proxy.id} «{proxy.name}».")
        return 0

    return _settle(_with_db(settings, action))


def cmd_proxy_test(settings: Settings, proxy_id: int) -> int:
    from uwp.db.repo.proxies import ProxiesRepo
    from uwp.services.proxies import ProxyService

    async def action(db) -> int:
        ok, message = await ProxyService(ProxiesRepo(db), settings).test(proxy_id)
        _print(("✅ " if ok else "❌ ") + message)
        return 0 if ok else 1

    return _settle(_with_db(settings, action))


def cmd_vnc_list(settings: Settings) -> int:
    from uwp.db.repo.vnc import VncRepo

    async def action(db) -> int:
        sessions = await VncRepo(db).active()
        if not sessions:
            _print("Активных сессий нет.")
            return 0
        for session in sessions:
            _print(f"{session.id:<4} профиль {session.profile_id} порт {session.port}")
        return 0

    return _settle(_with_db(settings, action))


def cmd_vnc_stop(settings: Settings, session_id: int) -> int:
    from uwp.db.repo.profiles import ProfilesRepo
    from uwp.db.repo.vnc import VncRepo
    from uwp.vnc import VncManager

    async def action(db) -> int:
        sessions = VncRepo(db)
        session = await sessions.get(session_id)
        if session is None:
            _print(f"❌ Сессия {session_id} не найдена.")
            return 1
        if session.status.value in ("stopped", "failed"):
            _print(f"Сессия {session_id} уже завершена.")
            return 0
        # This process does not own the browser stack, so it can only ask:
        # the worker's tick kills the processes and frees the profile.
        await VncManager(settings, ProfilesRepo(db), sessions).request_stop(session_id)
        _print(f"Сессия {session_id} помечена на остановку; процесс остановит воркер.")
        return 0

    return _settle(_with_db(settings, action))


def cmd_export(settings: Settings, target: str, output: Path | None) -> int:
    from uwp.db.repo.runs import RunsRepo
    from uwp.db.repo.sites import SitesRepo

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
            _print(f"❌ Прогон или сайт «{target}» не найден.")
            return 1
        if not run.report_path or not Path(run.report_path).is_file():
            _print("❌ У прогона нет файла отчёта.")
            return 1
        destination = output or Path(run.report_path).with_name(Path(run.report_path).name)
        if destination.resolve() != Path(run.report_path).resolve():
            shutil.copyfile(run.report_path, destination)
        _print(f"Отчёт: {destination}")
        return 0

    return _settle(_with_db(settings, action))


def cmd_run(settings: Settings, once: bool) -> int:
    from uwp import app

    async def action() -> int:
        if once:
            await app.run_once(settings)
            _print("Один цикл воркера выполнен.")
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
    async def action(db) -> int:
        _print(f"Схема обновлена: {settings.db_path}")
        return 0

    return _settle(_with_db(settings, action))


# --- argparse --------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="uwp", description="UltimateWebParser")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="создать базу, админов и тестовый сайт")

    doctor = sub.add_parser("doctor", help="проверить окружение")
    doctor.add_argument("--quiet", action="store_true", help="молчать при успехе")
    doctor.add_argument(
        "--offline",
        action="store_true",
        help="не ходить в сеть (так его зовёт healthcheck контейнера)",
    )
    doctor.add_argument(
        "--worker",
        action="store_true",
        help="процесс без Telegram: UWP_BOT_TOKEN не требуется",
    )

    browser = sub.add_parser("browser", help="управление браузером")
    browser_sub = browser.add_subparsers(dest="browser_command", required=True)
    install = browser_sub.add_parser("install", help="скачать fingerprint-chromium")
    install.add_argument("--version", default=None, help="версия (по умолчанию из настроек)")
    install.add_argument("--url", default=None, help="URL архива; проверяется sha256")
    install.add_argument("--dest", type=Path, default=None, help="каталог установки")

    parsers = sub.add_parser("parsers", help="парсеры")
    parsers_sub = parsers.add_subparsers(dest="parsers_command", required=True)
    parsers_sub.add_parser("list", help="список парсеров")

    site = sub.add_parser("site", help="сайты")
    site_sub = site.add_subparsers(dest="site_command", required=True)
    site_sub.add_parser("list", help="список сайтов")
    add = site_sub.add_parser("add", help="добавить сайт")
    add.add_argument("parser_key")
    add.add_argument("--name", default=None)
    enable = site_sub.add_parser("enable", help="включить сайт")
    enable.add_argument("slug")
    disable = site_sub.add_parser("disable", help="выключить сайт")
    disable.add_argument("slug")
    run = site_sub.add_parser("run", help="запустить сейчас")
    run.add_argument("slug")
    setter = site_sub.add_parser("set", help="изменить настройки")
    setter.add_argument("slug")
    setter.add_argument("--interval", default=None)
    setter.add_argument("--name", default=None)

    profile = sub.add_parser("profile", help="профили")
    profile_sub = profile.add_subparsers(dest="profile_command", required=True)
    profile_sub.add_parser("list", help="список профилей")
    create = profile_sub.add_parser("create", help="создать профиль")
    create.add_argument("name")
    gain = profile_sub.add_parser("login", help="войти через VNC")
    gain.add_argument("profile_id", type=int)
    drop = profile_sub.add_parser("delete", help="удалить профиль")
    drop.add_argument("profile_id", type=int)

    proxy = sub.add_parser("proxy", help="прокси")
    proxy_sub = proxy.add_subparsers(dest="proxy_command", required=True)
    proxy_sub.add_parser("list", help="список прокси")
    proxy_add = proxy_sub.add_parser("add", help="добавить прокси")
    proxy_add.add_argument("name")
    proxy_add.add_argument("url")
    proxy_test = proxy_sub.add_parser("test", help="проверить прокси")
    proxy_test.add_argument("proxy_id", type=int)

    vnc = sub.add_parser("vnc", help="сессии входа")
    vnc_sub = vnc.add_subparsers(dest="vnc_command", required=True)
    vnc_sub.add_parser("list", help="активные сессии")
    vnc_stop = vnc_sub.add_parser("stop", help="остановить сессию")
    vnc_stop.add_argument("session_id", type=int)

    export = sub.add_parser("export", help="сохранить отчёт прогона")
    export.add_argument("target", help="id прогона или slug сайта")
    export.add_argument("-o", "--output", type=Path, default=None)

    run_cmd = sub.add_parser("run", help="запустить бота и воркер вместе")
    run_cmd.add_argument("--once", action="store_true", help="один цикл воркера")

    sub.add_parser("bot", help="только бот")
    sub.add_parser("worker", help="только воркер")

    db = sub.add_parser("db", help="база данных")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    db_sub.add_parser("upgrade", help="применить миграции")

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
    args = build_parser().parse_args(argv)
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
        return cmd_parsers_list()
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
        return cmd_profile_login(settings, args.profile_id)
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
