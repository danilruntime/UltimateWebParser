"""Shared rules for the environment a browser process may inherit."""
from __future__ import annotations

import ctypes
import os
import sys

# A browser is exposed to arbitrary sites: a renderer escape, or ``--no-sandbox``
# after an operator sets ``UWP_NO_SANDBOX=true``, exposes ``/proc/<pid>/environ``.
# ``UWP_BOT_TOKEN`` / ``UWP_SECRET_KEY`` there are a full takeover; the rest is
# what Chromium needs to start on a normal Linux host.
#
# The Playwright Node driver is the one child that still inherits the worker's
# environment: the Python client spawns it before any browser exists. It parses
# protocol traffic and never executes page code, so it is not the leak path the
# browser processes are.
BROWSER_ENV_KEYS = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        "LC_MESSAGES",
        "TZ",
        "TMPDIR",
        "USER",
        "LOGNAME",
        "SHELL",
        "DISPLAY",
        "XAUTHORITY",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_RUNTIME_DIR",
        "FONTCONFIG_PATH",
        "FONTCONFIG_FILE",
        "DBUS_SESSION_BUS_ADDRESS",
    }
)


def browser_env(**overrides: str) -> dict[str, str]:
    """The parent environment reduced to an allowlist, plus ``overrides``."""
    env = {key: value for key, value in os.environ.items() if key in BROWSER_ENV_KEYS}
    env.update(overrides)
    return env


def hide_process_environment() -> bool:
    """Make ``/proc/<pid>/environ`` unreadable to processes of the same uid.

    Measured: the VNC browser runs as the same uid as the worker, so
    ``file:///proc/<worker>/environ`` exposed ``UWP_BOT_TOKEN`` and
    ``UWP_SECRET_KEY``. Linux only checks the target's dumpable flag, so
    clearing it is enough — the process itself keeps full access.

    Returns ``True`` when the flag was cleared. Best effort: a non-Linux host
    or a missing ``prctl`` must not stop the worker from starting.
    """
    if sys.platform != "linux":
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        # PR_SET_DUMPABLE = 4; a zero argument disables core dumps, ptrace and
        # /proc/<pid>/{environ,maps,mem} reads by other processes.
        return libc.prctl(4, 0, 0, 0, 0) == 0
    except (OSError, AttributeError):
        return False
