"""Unit tests for browser resolution and the Playwright launch wrapper."""
import os
import random
import socket
import subprocess

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


async def test_launch_does_not_hand_app_secrets_to_the_browser(tmp_path, monkeypatch):
    """A compromised page reads its own process environment.

    ``UWP_BOT_TOKEN`` in ``/proc/<pid>/environ`` lets the page take over the
    bot, and Playwright passes the parent process environment unchanged unless
    ``env=`` is given.
    """
    import uwp.browser as browser_mod

    monkeypatch.setenv("UWP_BOT_TOKEN", "123:secret")
    monkeypatch.setenv("UWP_SECRET_KEY", "k" * 32)
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

    env = chromium.kwargs["env"]
    assert "UWP_BOT_TOKEN" not in env
    assert "UWP_SECRET_KEY" not in env
    # The browser still needs its profile paths and a usable PATH.
    assert env["HOME"] == os.environ["HOME"]
    assert "PATH" in env


async def test_launch_refuses_a_profile_a_live_browser_holds(tmp_path, monkeypatch):
    """A readable reason instead of a 7 KB Playwright call log."""
    import uwp.browser as browser_mod
    from uwp.browser import ProfileInUse

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
    profile = tmp_path / "prof"
    profile.mkdir()
    _lock_on(profile, os.getpid())

    with pytest.raises(ProfileInUse, match=str(os.getpid())):
        await browser_mod.launch(settings, identity, profile, None)

    assert chromium.kwargs == {}, "no browser may be started on a held profile"


class _FakeStarter:
    def __init__(self, pw: _FakePlaywright) -> None:
        self._pw = pw

    async def start(self) -> _FakePlaywright:
        return self._pw


def _dead_pid() -> int:
    """A pid that has already been reaped, so ``kill(pid, 0)`` fails."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _lock_on(path, pid: int) -> None:
    """Write the symlink Chromium uses: ``SingletonLock -> <host>-<pid>``."""
    (path / "SingletonLock").symlink_to(f"{socket.gethostname()}-{pid}")
    (path / "SingletonCookie").symlink_to("12345678901234567890")
    (path / "SingletonSocket").symlink_to(f"/tmp/.org.chromium.Chromium.{pid}")


def test_a_lock_of_a_dead_browser_is_removed(tmp_path):
    """Measured: a leaked headful browser held this lock for two hours.

    Chromium then refused to start at all: "Failed to create a ProcessSingleton
    for your profile directory" / "SingletonLock: File exists". Every parser run
    on the profile failed with that message.
    """
    from uwp.browser import clear_stale_singleton_lock

    profile = tmp_path / "prof"
    profile.mkdir()
    _lock_on(profile, _dead_pid())

    assert clear_stale_singleton_lock(profile) is True
    assert list(profile.iterdir()) == []


def test_a_lock_of_a_live_browser_is_left_alone(tmp_path):
    """Never steal a profile from a browser that is still running."""
    from uwp.browser import clear_stale_singleton_lock

    profile = tmp_path / "prof"
    profile.mkdir()
    _lock_on(profile, os.getpid())

    assert clear_stale_singleton_lock(profile) is False
    assert (profile / "SingletonLock").is_symlink()


def test_a_lock_from_another_host_is_left_alone(tmp_path):
    """The pid of another machine says nothing about ours."""
    from uwp.browser import clear_stale_singleton_lock

    profile = tmp_path / "prof"
    profile.mkdir()
    (profile / "SingletonLock").symlink_to("some-other-host-4242")

    assert clear_stale_singleton_lock(profile) is False
    assert (profile / "SingletonLock").is_symlink()


def test_a_profile_without_a_lock_is_untouched(tmp_path):
    from uwp.browser import clear_stale_singleton_lock

    profile = tmp_path / "prof"
    profile.mkdir()
    (profile / "Default").mkdir()

    assert clear_stale_singleton_lock(profile) is False
    assert (profile / "Default").is_dir()


async def test_launch_clears_a_stale_lock_of_a_dead_browser(tmp_path, monkeypatch):
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
    profile = tmp_path / "prof"
    profile.mkdir()
    _lock_on(profile, _dead_pid())

    session = await browser_mod.launch(settings, identity, profile, None)
    await session.close()

    assert not (profile / "SingletonLock").is_symlink()

