"""Shared rules for the environment a browser process may inherit."""
from __future__ import annotations

import os

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
