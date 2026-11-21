"""The VNC login browser is a kiosk; fluxbox is its keyboard filter.

Measured in the image: ``--kiosk`` alone still opens a full 1050x760 window on
Ctrl+N, so the WM has to grab the chord before Chromium sees it. A grab whose
command fluxbox cannot parse is dropped silently, which is the same as no grab
at all — hence the test that every line ends with ``:Exec true``.
"""

from uwp.vnc import _FLUXBOX_KEYS, _GRABBED_CHORDS

# The chords that must never reach Chromium. Pinned literally: a chord that
# silently disappears from the tuple is a hole, not a refactor.
EXPECTED_CHORDS = {
    # Windows and tabs.
    "Control n",
    "Control Shift n",
    "Control t",
    "Control Shift t",
    "Control w",
    "Control Shift w",
    "Control Shift q",
    "Mod1 F4",
    # DevTools and view-source.
    "Control Shift i",
    "Control Shift j",
    "Control Shift c",
    "F12",
    "Control u",
    # File dialogs, printing, downloads, history.
    "Control s",
    "Control p",
    "Control o",
    "Control h",
    "Control j",
    # Bookmarks.
    "Control d",
    "Control Shift o",
    "Control Shift b",
    # Location bar and search.
    "Control l",
    "Control k",
    "Control e",
    "Mod1 d",
    "F6",
    "Control f",
    "F3",
    "Control g",
    # Window chrome and data.
    "F11",
    "F1",
    "Control Shift Delete",
    "Control Shift m",
    # Context menu from the keyboard.
    "Shift F10",
    "Menu",
    # Zoom and home.
    "Control equal",
    "Control minus",
    "Control 0",
    "Mod1 Home",
}

# Reloading and history are how a half-finished login is retried; grabbing
# them would strand the visitor on a broken redirect.
PAGE_CHORDS = {"F5", "Control r", "Mod1 Left", "Mod1 Right"}


def test_fluxbox_grabs_every_escape_chord():
    assert set(_GRABBED_CHORDS) == EXPECTED_CHORDS


def test_fluxbox_keys_are_exec_true_and_nothing_else():
    """fluxbox drops a binding it cannot parse, keeping the key ungrabbeed."""
    lines = [line for line in _FLUXBOX_KEYS.splitlines() if line]
    assert lines
    for line in lines:
        assert line.endswith(" :Exec true"), line


def test_fluxbox_keys_use_a_command_fluxbox_accepts():
    """:Noop / :None / :MacroCmd parse as "Invalid key/modifier" (measured).

    A rejected line means fluxbox never grabs the chord, so the check is on
    the command, not on the presence of the line.
    """
    for rejected in (":Noop", ":None", ":MacroCmd"):
        assert rejected not in _FLUXBOX_KEYS


def test_fluxbox_leaves_the_page_keys_alone():
    grabbed = {line.rsplit(" :", 1)[0] for line in _FLUXBOX_KEYS.splitlines()}
    assert not (grabbed & PAGE_CHORDS)
