"""Launching fingerprint-chromium through Playwright with a profile identity applied."""
import json
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

    The exception carries a ``code`` and its detail (``pid``/``host``) instead of
    a finished Russian sentence: ``runner`` copies it into ``outcome.error`` and
    ``delivery`` prints that into a chat, where the language is the recipient's,
    not this layer's. ``str()`` stays an English developer line for the log.
    """

    def __init__(self, code: str, **detail: object) -> None:
        self.code = code
        self.detail = detail
        if code == "error.profile.in_use_same_host":
            super().__init__(
                f"profile is held by a live browser (pid {detail['pid']})"
            )
        else:
            super().__init__(
                f"profile is held by a live browser on host {detail['host']}"
            )


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


def _owns_profile(pid: int, user_data_dir: Path) -> bool:
    """Whether ``pid`` is a browser whose command line names this profile.

    A pid from a lock can outlive its process: container pid namespaces start
    from 1 again, so an unrelated process may sit on the number a previous
    browser used. Treating it as the owner blocks the profile forever. When the
    command line cannot be read (another user), assume the browser is real.
    """
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return True
    if not raw:
        # A zombie or a process between fork and exec: too early to judge.
        return True
    wanted = os.path.realpath(user_data_dir)
    for arg in raw.split(b"\0"):
        if not arg.startswith(b"--user-data-dir="):
            continue
        value = arg.split(b"=", 1)[1].decode(errors="replace")
        if os.path.realpath(value) == wanted:
            return True
    return False


def _socket_is_live(user_data_dir: Path) -> bool:
    """Whether the browser behind a foreign-host lock still listens here.

    Chromium puts ``SingletonSocket`` in a temp directory of its *own* mount
    namespace, so a recreated container leaves a lock naming a path that cannot
    exist any more. Connecting is the same liveness probe Chromium uses for a
    lock from the same host; a successful connect means the browser is real.
    """
    link = user_data_dir / "SingletonSocket"
    try:
        target = os.readlink(link)
    except OSError:
        return False
    if not os.path.isabs(target):
        target = str(link.parent / target)
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(0.2)
        probe.connect(target)
    except OSError:
        return False
    finally:
        probe.close()
    return True


def clear_stale_singleton_lock(user_data_dir: Path) -> bool:
    """Drop Chromium's single-instance files when their owner is gone.

    Measured in the container: a leaked headful login browser kept
    ``SingletonLock`` pointing at its own live pid, and after it was killed the
    leftovers still broke every launch with "Failed to create a ProcessSingleton
    for your profile directory" / "SingletonLock: File exists".

    A lock from the same hostname is kept while its pid is alive. A lock from a
    *different* hostname is normally another machine sharing the profile, which
    this deployment does not support (SQLite lives in the same directory). It is
    usually a recreated container: Docker gives every new container a new
    hostname, so the lock names a process that cannot be checked locally.
    Chromium then refuses the profile forever with "appears to be in use by
    another Chromium process on another computer". Such a lock is removed unless
    its socket still accepts connections, which proves the browser is alive.
    """
    link = user_data_dir / "SingletonLock"
    if not link.is_symlink():
        return False
    owner = _lock_owner(link)
    if owner is None:
        return False
    host, pid = owner
    if host == socket.gethostname():
        if _is_alive(pid) and _owns_profile(pid, user_data_dir):
            return False
    elif _socket_is_live(user_data_dir):
        return False
    for name in SINGLETON_FILES:
        (user_data_dir / name).unlink(missing_ok=True)
    return True


def seed_download_preferences(user_data_dir: Path, downloads_dir: Path) -> None:
    """Make Chromium save a download instead of silently cancelling it.

    Measured in the container: a download clicked in the VNC browser left a
    history entry with ``state=2``, ``interrupt_reason=40``
    (``DOWNLOAD_INTERRUPT_REASON_USER_CANCELED``), ``received_bytes=0`` and an
    empty ``target_path``. A fresh profile has no ``download`` key at all, so
    Chromium falls back to the "Save as" dialog, and kiosk mode hides every
    dialog — the click is dropped.

    Both keys are load-bearing and were measured separately: with only
    ``default_directory`` the dialog still appears (and cannot be answered),
    with only ``prompt_for_download: false`` Chromium picks an XDG folder that
    does not exist in the image. ``--disable-prompt-on-download`` is not a
    substitute: it does not create the directory preference.

    The existing file is merged, not replaced: it carries the profile's
    session and fingerprint settings, and a whole-file rewrite would sign the
    admin out.
    """
    prefs_path = user_data_dir / "Default" / "Preferences"
    prefs: dict = {}
    try:
        loaded = json.loads(prefs_path.read_text("utf-8"))
    except (OSError, ValueError):
        # A missing file is the normal first start; a truncated one is
        # Chromium's own crash damage and is not a reason to refuse a login.
        loaded = None
    if isinstance(loaded, dict):
        prefs = loaded
    download = prefs.get("download")
    if not isinstance(download, dict):
        download = {}
    download["default_directory"] = str(downloads_dir)
    download["prompt_for_download"] = False
    prefs["download"] = download
    downloads_dir.mkdir(parents=True, exist_ok=True)
    prefs_path.parent.mkdir(parents=True, exist_ok=True)
    prefs_path.write_text(json.dumps(prefs), "utf-8")


def prepare_profile(user_data_dir: Path) -> None:
    """Make a profile directory launchable, or say plainly why it is not.

    An orphaned lock is removed. A lock held by a browser that is still alive
    is not ours to break, and Playwright's own error for it is a long call log
    that reads like a parser bug.
    """
    clear_stale_singleton_lock(user_data_dir)
    owner = _lock_owner(user_data_dir / "SingletonLock")
    if owner is None:
        return
    host, pid = owner
    if host == socket.gethostname():
        raise ProfileInUse("error.profile.in_use_same_host", pid=pid)
    raise ProfileInUse("error.profile.in_use_foreign_host", host=host)



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
