"""Unit tests for browser resolution and the Playwright launch wrapper."""
import os
import random
import socket
import subprocess
import sys
from pathlib import Path

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


async def test_launch_uses_the_configured_browser_major_not_the_profile_row(
    tmp_path, monkeypatch
):
    """A rollback to another build must move the client hints with it.

    Measured with the pinned 148 binary: the User-Agent follows the binary, but
    ``--fingerprint-brand-version`` comes from the profile row, so overriding
    ``UWP_BROWSER_VERSION`` to 150 left the UA saying 148 while ``sec-ch-ua``
    said 150 — the split this branch set out to remove.
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
        {
            "UWP_DATA_DIR": str(tmp_path),
            "UWP_BROWSER_PATH": str(exe),
            "UWP_BROWSER_VERSION": "150.0.7871.186",
        }
    )
    identity = random_identity(random.Random(7))
    assert identity.brand_version != "150"

    session = await browser_mod.launch(settings, identity, tmp_path / "prof", None)
    await session.close()

    flags = chromium.kwargs["args"]
    assert "--fingerprint-brand-version=150" in flags


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
    # A real process that names this profile, so it *is* the browser owner.
    holder = _process_on(profile)
    _lock_on(profile, holder.pid)

    try:
        with pytest.raises(ProfileInUse, match=str(holder.pid)):
            await browser_mod.launch(settings, identity, profile, None)
    finally:
        holder.kill()
        holder.wait()

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
    # The pytest process is alive but does not use the profile; pretend it is
    # the browser the lock names, which is the case this rule protects.
    import uwp.browser as browser_mod

    original = browser_mod._owns_profile
    browser_mod._owns_profile = lambda pid, path: pid == os.getpid()
    try:
        assert clear_stale_singleton_lock(profile) is False
    finally:
        browser_mod._owns_profile = original
    assert (profile / "SingletonLock").is_symlink()


def test_owns_profile_reads_the_user_data_dir_from_the_command_line(tmp_path):
    """The only evidence that a pid is *the* browser, not a reused number."""
    from uwp.browser import _owns_profile

    profile = tmp_path / "prof"
    profile.mkdir()
    holder = _process_on(profile)
    try:
        assert _owns_profile(holder.pid, profile) is True
        assert _owns_profile(os.getpid(), profile) is False
    finally:
        holder.kill()
        holder.wait()


def _process_on(profile: Path) -> subprocess.Popen:
    """A live process whose command line names ``profile`` as its data dir."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", f"--user-data-dir={profile}"]
    )


def test_a_previous_containers_lock_is_removed(tmp_path):
    """Measured on ForSteam: the lock named ``fedora-147``.

    A combined container wrote it while a login browser was open; the split
    worker that replaced it has hostname ``7417efec8aad`` and no pid 147, so
    the lock looked like "another computer" forever and Chromium refused every
    launch with "The profile appears to be in use by another Chromium process
    (147) on another computer (fedora)".

    Chromium stores the path of its own mount namespace in ``SingletonSocket``.
    That temp directory dies with the container, and a socket that cannot be
    connected to proves the owner cannot be running any more.
    """
    from uwp.browser import clear_stale_singleton_lock

    profile = tmp_path / "prof"
    profile.mkdir()
    (profile / "SingletonLock").symlink_to("some-other-host-4242")
    (profile / "SingletonCookie").symlink_to("12345678901234567890")
    (profile / "SingletonSocket").symlink_to(
        str(tmp_path / "gone-namespace" / "SingletonSocket")
    )

    assert clear_stale_singleton_lock(profile) is True
    assert list(profile.iterdir()) == []


def test_a_lock_of_a_live_browser_on_another_host_is_left_alone(tmp_path):
    """A listening socket means the browser really is using the profile."""
    from uwp.browser import clear_stale_singleton_lock

    profile = tmp_path / "prof"
    profile.mkdir()
    socket_path = tmp_path / "SingletonSocket"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(1)
    try:
        (profile / "SingletonLock").symlink_to("some-other-host-4242")
        (profile / "SingletonCookie").symlink_to("12345678901234567890")
        (profile / "SingletonSocket").symlink_to(str(socket_path))

        assert clear_stale_singleton_lock(profile) is False
        assert (profile / "SingletonLock").is_symlink()
    finally:
        listener.close()


def test_a_lock_without_singleton_socket_is_removed(tmp_path):
    """Chromium writes all three files; a lone lock is a leftover."""
    from uwp.browser import clear_stale_singleton_lock

    profile = tmp_path / "prof"
    profile.mkdir()
    (profile / "SingletonLock").symlink_to("some-other-host-4242")

    assert clear_stale_singleton_lock(profile) is True
    assert list(profile.iterdir()) == []


def test_prepare_profile_clears_a_previous_containers_lock(tmp_path):
    """The login stack must not fail on a lock its own namespace cannot hold."""
    from uwp.browser import prepare_profile

    profile = tmp_path / "prof"
    profile.mkdir()
    (profile / "SingletonLock").symlink_to("some-other-host-4242")
    (profile / "SingletonSocket").symlink_to(
        str(tmp_path / "gone-namespace" / "SingletonSocket")
    )

    prepare_profile(profile)

    assert not (profile / "SingletonLock").is_symlink()


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



def test_seed_download_preferences_points_chromium_at_a_real_directory(tmp_path):
    """A fresh profile cannot save a download without an explicit directory.

    Measured in the container: clicking a download link left the entry at
    ``state=2`` / ``interrupt_reason=40`` (``USER_CANCELED``) with an empty
    ``target_path``, because the "Save as" dialog never opens in kiosk mode.
    Seeding both keys makes a 20 MB file land complete.
    """
    import json

    from uwp.browser import seed_download_preferences

    profile = tmp_path / "prof"
    profile.mkdir()
    downloads = tmp_path / "downloads"

    seed_download_preferences(profile, downloads)

    prefs = json.loads((profile / "Default" / "Preferences").read_text("utf-8"))
    assert prefs["download"]["default_directory"] == str(downloads)
    assert prefs["download"]["prompt_for_download"] is False
    assert downloads.is_dir()


def test_seed_download_preferences_keeps_the_rest_of_the_profile(tmp_path):
    """The file already holds session and fingerprint settings of a real profile."""
    import json

    from uwp.browser import seed_download_preferences

    profile = tmp_path / "prof"
    (profile / "Default").mkdir(parents=True)
    (profile / "Default" / "Preferences").write_text(
        json.dumps({"profile": {"name": "Steam1"}, "download": {"prompt_for_download": True}}),
        "utf-8",
    )

    seed_download_preferences(profile, tmp_path / "downloads")

    prefs = json.loads((profile / "Default" / "Preferences").read_text("utf-8"))
    assert prefs["profile"] == {"name": "Steam1"}
    assert prefs["download"]["prompt_for_download"] is False


def test_seed_download_preferences_survives_a_corrupt_file(tmp_path):
    """A truncated Preferences file must not stop the login browser from starting."""
    import json

    from uwp.browser import seed_download_preferences

    profile = tmp_path / "prof"
    (profile / "Default").mkdir(parents=True)
    (profile / "Default" / "Preferences").write_text("{not json", "utf-8")

    seed_download_preferences(profile, tmp_path / "downloads")

    prefs = json.loads((profile / "Default" / "Preferences").read_text("utf-8"))
    assert prefs["download"]["prompt_for_download"] is False
