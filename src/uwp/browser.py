"""Launching fingerprint-chromium through Playwright with a profile identity applied."""
import os
import shutil
from pathlib import Path

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from uwp.config import Settings
from uwp.profiles import Identity, browser_flags


class BrowserUnavailable(RuntimeError):
    """No fingerprint-chromium binary could be found."""


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
            viewport={"width": width, "height": height},
            locale=identity.locale,
            timezone_id=identity.timezone,
            ignore_default_args=["--enable-automation"],
        )
    except BaseException:
        await playwright.stop()
        raise
    page = context.pages[0] if context.pages else await context.new_page()
    page.set_default_timeout(settings.page_timeout_ms)
    page.set_default_navigation_timeout(settings.navigation_timeout_ms)
    return BrowserSession(playwright, context, page)
