"""Tests for the environment rules given to browser and helper processes."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from uwp.env import BROWSER_ENV_KEYS, browser_env


def test_browser_env_drops_the_bot_secrets(monkeypatch):
    monkeypatch.setenv("UWP_BOT_TOKEN", "1:secret")
    monkeypatch.setenv("UWP_SECRET_KEY", "k" * 32)
    monkeypatch.setenv("PATH", "/usr/bin")

    env = browser_env()

    assert "UWP_BOT_TOKEN" not in env
    assert "UWP_SECRET_KEY" not in env
    assert env["PATH"] == "/usr/bin"


def test_browser_env_keeps_the_allowlist_and_applies_overrides(monkeypatch):
    monkeypatch.setenv("HOME", "/home/uwp")

    env = browser_env(DISPLAY=":101")

    assert env["HOME"] == "/home/uwp"
    assert env["DISPLAY"] == ":101"
    assert set(env) <= BROWSER_ENV_KEYS | {"DISPLAY"}


@pytest.mark.skipif(sys.platform != "linux", reason="PR_SET_DUMPABLE is Linux-only")
def test_a_hidden_process_environment_is_unreadable_by_the_same_uid():
    """Measured: ``/proc/<worker>/environ`` exposed the token and secret key.

    The VNC browser runs as the same uid, so a sandbox escape could read that
    file. ``PR_SET_DUMPABLE=0`` denies it while ``os.environ`` keeps working.
    """
    script = textwrap.dedent(
        """
        import os
        import subprocess
        import sys

        from uwp.env import hide_process_environment

        assert hide_process_environment() is True
        pid = os.getpid()
        reader = (
            "import sys\\n"
            "try:\\n"
            f"    open('/proc/{pid}/environ', 'rb').read()\\n"
            "    print('READABLE')\\n"
            "except PermissionError:\\n"
            "    print('DENIED')\\n"
        )
        print(subprocess.run(
            [sys.executable, "-c", reader], capture_output=True, text=True
        ).stdout.strip())
        print("PATH" if os.environ.get("PATH") else "NO-PATH")
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split() == ["DENIED", "PATH"]
