"""Unit tests for browser resolution and the Playwright launch wrapper."""
import random

import pytest

from uwp.browser import BrowserUnavailable, resolve_browser
from uwp.config import load_settings
from uwp.profiles import random_identity


def test_missing_browser_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(BrowserUnavailable):
        resolve_browser(load_settings({"UWP_DATA_DIR": str(tmp_path)}))


def test_explicit_browser_path_wins(tmp_path):
    exe = tmp_path / "chrome"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    assert resolve_browser(load_settings({"UWP_DATA_DIR": str(tmp_path), "UWP_BROWSER_PATH": str(exe)})) == exe


def test_non_executable_browser_path_falls_back_to_path(tmp_path, monkeypatch):
    exe = tmp_path / "chrome"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o644)
    found = tmp_path / "fingerprint-chromium"
    found.write_text("#!/bin/sh\n")
    found.chmod(0o755)
    monkeypatch.setattr("shutil.which", lambda name: str(found) if name == "fingerprint-chromium" else None)
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path), "UWP_BROWSER_PATH": str(exe)})
    assert resolve_browser(settings) == found


def test_browser_found_on_path(tmp_path, monkeypatch):
    found = tmp_path / "fingerprint-chromium"
    found.write_text("#!/bin/sh\n")
    found.chmod(0o755)
    monkeypatch.setattr("shutil.which", lambda name: str(found) if name == "fingerprint-chromium" else None)
    assert resolve_browser(load_settings({"UWP_DATA_DIR": str(tmp_path)})) == found


class _FakeChromium:
    """Records the kwargs Playwright would receive."""

    def __init__(self) -> None:
        self.kwargs: dict = {}

    async def launch_persistent_context(self, **kwargs):
        self.kwargs = kwargs
        return _FakeContext()


class _FakeContext:
    def __init__(self) -> None:
        self.pages: list = []

    async def new_page(self):
        return _FakePage()

    async def close(self) -> None:
        return None


class _FakePage:
    def set_default_timeout(self, value: int) -> None:
        return None

    def set_default_navigation_timeout(self, value: int) -> None:
        return None


class _FakePlaywright:
    def __init__(self, chromium: _FakeChromium) -> None:
        self.chromium = chromium

    async def stop(self) -> None:
        return None


async def test_launch_enables_the_chromium_sandbox(tmp_path, monkeypatch):
    """Playwright injects ``--no-sandbox`` unless ``chromium_sandbox=True``.

    Measured in the image: without it the renderer runs in the container's own
    user namespace and can read the app's environment.
    """
    import uwp.browser as browser_mod

    chromium = _FakeChromium()
    monkeypatch.setattr(
        browser_mod, "async_playwright", lambda: _FakeStarter(_FakePlaywright(chromium))
    )
    exe = tmp_path / "chrome"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    settings = load_settings(
        {"UWP_DATA_DIR": str(tmp_path), "UWP_BROWSER_PATH": str(exe)}
    )
    identity = random_identity(random.Random(7))

    session = await browser_mod.launch(settings, identity, tmp_path / "prof", None)
    await session.close()

    assert chromium.kwargs["chromium_sandbox"] is True


class _FakeStarter:
    def __init__(self, pw: _FakePlaywright) -> None:
        self._pw = pw

    async def start(self) -> _FakePlaywright:
        return self._pw

