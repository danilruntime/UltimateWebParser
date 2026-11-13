"""Integration test: fingerprint-chromium launches through Playwright with the identity applied."""
import os
import random

import pytest

from uwp.browser import launch
from uwp.config import load_settings
from uwp.profiles import random_identity

pytestmark = pytest.mark.integration
BROWSER = os.environ.get("UWP_TEST_BROWSER")


@pytest.mark.skipif(not BROWSER, reason="UWP_TEST_BROWSER not set")
async def test_fingerprint_platform_applied(tmp_path):
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path), "UWP_BROWSER_PATH": BROWSER})
    ident = random_identity(random.Random(3))
    session = await launch(settings, ident, tmp_path / "udd", proxy_url=None, headless=True)
    try:
        await session.page.goto("data:text/html,<div id=o></div>")
        platform = await session.page.evaluate("navigator.platform")
        webdriver = await session.page.evaluate("navigator.webdriver")
        assert platform == {"windows": "Win32", "macos": "MacIntel", "linux": "Linux x86_64"}[ident.platform]
        assert webdriver is False
    finally:
        await session.close()
