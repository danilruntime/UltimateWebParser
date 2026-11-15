"""Launching fingerprint-chromium through Playwright with a profile identity applied."""
import os
import shutil
import socket
from pathlib import Path

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from uwp.config import Settings
from uwp.env import browser_env
from uwp.profiles import Identity, browser_flags

# Chromium's own single-instance files inside a profile directory. It refuses to
# start while they exist, whatever the state of the process that wrote them.
SINGLETON_FILES = ("SingletonLock", "SingletonCookie", "SingletonSocket")


class BrowserUnavailable(RuntimeError):
    """No fingerprint-chromium binary could be found."""


class ProfileInUse(RuntimeError):
    """Another live browser on this host holds the profile directory.

    Not a parser bug: the profile is blocked by a leftover VNC login browser or
    by a run that is still shutting down.
    """


def _lock_owner(link: Path) -> tuple[str, int] | None:
    """Parse ``SingletonLock -> <hostname>-<pid>``; ``None`` if unreadable."""
    try:
        target = os.readlink(link)
    except OSError:
        return None
    host, _, pid = target.rpartition("-")
    if not host or not pid.isdigit():
        return None
    return host, int(pid)


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # exists, but owned by someone else
        return True
    return True


def clear_stale_singleton_lock(user_data_dir: Path) -> bool:
    """Drop Chromium's single-instance files when their owner is gone.

    Measured in the container: a leaked headful login browser kept
    ``SingletonLock`` pointing at its own live pid, and after it was killed the
    leftovers still broke every launch with "Failed to create a ProcessSingleton
    for your profile directory" / "SingletonLock: File exists".

    Only an orphaned lock is removed. A lock from a *live* process on this host
    is the browser actually using the profile, and a lock from another hostname
    cannot be checked at all, so both are left for Chromium to judge.
    """
    link = user_data_dir / "SingletonLock"
    if not link.is_symlink():
        return False
    owner = _lock_owner(link)
    if owner is None:
        return False
    host, pid = owner
    if host != socket.gethostname() or _is_alive(pid):
        return False
    for name in SINGLETON_FILES:
        (user_data_dir / name).unlink(missing_ok=True)
    return True


def live_lock_pid(user_data_dir: Path) -> int | None:
    """Pid of the browser holding this profile, if it is still running here."""
    owner = _lock_owner(user_data_dir / "SingletonLock")
    if owner is None:
        return None
    host, pid = owner
    if host != socket.gethostname() or not _is_alive(pid):
        return None
    return pid


def prepare_profile(user_data_dir: Path) -> None:
    """Make a profile directory launchable, or say plainly why it is not.

    An orphaned lock is removed. A lock held by a browser that is still alive
    is not ours to break, and Playwright's own error for it is a long call log
    that reads like a parser bug.
    """
    clear_stale_singleton_lock(user_data_dir)
    pid = live_lock_pid(user_data_dir)
    if pid is not None:
        raise ProfileInUse(
            f"профиль уже открыт другим браузером (pid {pid}); "
            "закройте сессию входа или дождитесь её завершения"
        )



def resolve_browser(settings: Settings) -> Path:
    """Return the configured browser if executable, else ``fingerprint-chromium`` from ``PATH``."""
    if settings.browser_path is not None:
        path = Path(settings.browser_path)
        if path.is_file() and os.access(path, os.X_OK):
            return path
    found = shutil.which("fingerprint-chromium")
    if found:
        return Path(found)
    raise BrowserUnavailable("fingerprint-chromium not found; run `uwp browser install`")


class BrowserSession:
    """An open persistent browser context plus the Playwright driver behind it."""

    def __init__(self, playwright: Playwright, context: BrowserContext, page: Page) -> None:
        self._pw = playwright
        self.context = context
        self.page = page
        self._closed = False

    async def close(self) -> None:
        """Close the context and stop the driver; safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        try:
            await self.context.close()
        finally:
            await self._pw.stop()


async def launch(
    settings: Settings,
    identity: Identity,
    user_data_dir: Path,
    proxy_url: str | None,
    headless: bool | None = None,
) -> BrowserSession:
    """Start fingerprint-chromium with ``identity`` applied and return the open session."""
    if headless is None:
        headless = settings.headless
    executable = resolve_browser(settings)
    prepare_profile(user_data_dir)
    width, height = identity.viewport
    # Playwright passes ``--user-data-dir`` itself and rejects it in ``args``.
    args = [flag for flag in browser_flags(identity, user_data_dir, proxy_url, headless, settings.no_sandbox)
            if not flag.startswith("--user-data-dir=")]
    playwright = await async_playwright().start()
    try:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            executable_path=str(executable),
            args=args,
            headless=headless,
            # The page can read this environment. Without an explicit allowlist
            # the browser inherits UWP_BOT_TOKEN and UWP_SECRET_KEY, which turns
            # any renderer exploit into full control of the bot.
            env=browser_env(),
            viewport={"width": width, "height": height},
            locale=identity.locale,
            timezone_id=identity.timezone,
            ignore_default_args=["--enable-automation"],
            # Playwright appends ``--no-sandbox`` itself unless this is True, so
            # leaving it unset silently disables the renderer sandbox even when
            # ``browser_flags`` never asked for it. An operator can still force
            # the old behaviour with ``UWP_NO_SANDBOX=true`` (that path also
            # adds the flag through ``browser_flags``).
            chromium_sandbox=not settings.no_sandbox,
        )
    except BaseException:
        await playwright.stop()
        raise
    page = context.pages[0] if context.pages else await context.new_page()
    page.set_default_timeout(settings.page_timeout_ms)
    page.set_default_navigation_timeout(settings.navigation_timeout_ms)
    return BrowserSession(playwright, context, page)
