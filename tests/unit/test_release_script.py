"""The release script edits the two files a version lives in, then pushes a tag.

Its arithmetic is small enough to get wrong silently: `0.1b1` -> `0.1b1` (no
bump), `0.1` -> `0.1b2` (a beta behind its own stable release), `0.1b1` ->
`0.1.0` (not the spelling the metadata test accepts). So the transitions are
pinned here, and the script is sourced rather than executed: `compute_next` is
a pure function of (current, kind) and needs no repository.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release.sh"


def _compute(current: str, kind: str) -> subprocess.CompletedProcess[str]:
    """Call ``compute_next`` inside the script without running its main flow."""
    return subprocess.run(
        ["bash", "-c", 'source "$1"; compute_next "$2" "$3"', "_", str(SCRIPT), current, kind],
        capture_output=True,
        check=False,
        text=True,
    )


def test_the_script_exists_and_is_executable():
    assert SCRIPT.is_file(), "scripts/release.sh does not exist"
    assert SCRIPT.stat().st_mode & 0o111, "scripts/release.sh is not executable"


@pytest.mark.parametrize(
    ("current", "kind", "expected"),
    [
        # A beta bumps its own serial: the line has not shipped yet.
        ("0.1b1", "beta", "0.1b2"),
        ("0.1b9", "beta", "0.1b10"),
        ("0.2b1", "beta", "0.2b2"),
        # Promoting a beta keeps the line it belongs to.
        ("0.1b1", "release", "0.1"),
        ("0.2b7", "release", "0.2"),
        # A new line always starts as a beta, from a beta or from a stable.
        ("0.1b1", "minor", "0.2b1"),
        ("0.1", "minor", "0.2b1"),
        # A beta behind its own stable release would be a downgrade, so it
        # moves to the next line instead.
        ("0.1", "beta", "0.2b1"),
    ],
)
def test_the_next_version(current: str, kind: str, expected: str):
    result = _compute(current, kind)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_a_stable_release_is_not_promoted_twice():
    """``0.1`` is already the stable spelling; only `minor` moves past it."""
    result = _compute("0.1", "release")

    assert result.returncode != 0
    assert "уже" in result.stderr.lower() or "already" in result.stderr.lower()


@pytest.mark.parametrize("current", ["0.1 beta", "v0.1", "", "1", "0.1.0b1"])
def test_an_unparsable_version_is_refused(current: str):
    """``v0.1`` is what a tag looks like, not what pyproject may hold."""
    result = _compute(current, "beta")

    assert result.returncode != 0
    assert result.stderr.strip()


def test_the_script_refuses_to_push_a_red_suite():
    """A tag is immutable in practice, so tests run before it exists."""
    text = SCRIPT.read_text(encoding="utf-8")

    assert "pytest" in text
    assert text.index("pytest") < text.index("git tag"), "the tag is created before the suite runs"


def test_the_script_has_an_escape_from_pushing():
    """A version bump is worth inspecting; pushing it is not always wanted."""
    text = SCRIPT.read_text(encoding="utf-8")

    assert "--no-push" in text
    assert "--dry-run" in text


def test_the_script_refuses_a_release_with_no_commits():
    """Measured: `beta` right after a stable release had nothing to describe.

    It wrote a 79-character note — «Новых коммитов с прошлого выпуска нет» —
    which `test_every_release_has_a_notes_file_named_after_its_tag` rejects as a
    stub, so the tag was blocked *after* the bump. The check belongs before the
    first write. A promotion is exempt: it is the same code by definition.
    """
    script = SCRIPT.read_text(encoding="utf-8")

    assert "выпускать нечего" in script
    assert "kind != release" in script


def test_a_failed_run_leaves_the_tree_as_it_found_it():
    """Measured: an aborted run bumped the version, edited both READMEs and left
    a notes file, so the next invocation refused because the tree was dirty.

    The script blocked itself and the recovery was a manual `git checkout`.
    """
    script = SCRIPT.read_text(encoding="utf-8")

    assert "_restore" in script
    assert "trap" in script


def test_the_script_updates_every_place_a_version_appears():
    """Two files hold the version and the READMEs link the notes by filename."""
    text = SCRIPT.read_text(encoding="utf-8")

    assert "pyproject.toml" in text
    assert "src/uwp/__init__.py" in text
    assert "docs/releases" in text
    assert "README.md" in text and "README.ru.md" in text


def test_a_promotion_repeats_the_beta_it_promotes():
    """Measured: `0.1b2 -> 0.1` has no commits of its own.

    With only the range, the page was a heading and 33 characters, and the suite
    refused it as a stub; with one commit in between it was 100 characters and
    still refused. A promotion repeats the beta's sections, because that is what
    the stable release is.
    """
    script = SCRIPT.read_text(encoding="utf-8")

    assert "_inherited_sections" in script
    assert "Стабильная версия" in script
    # The promotion branch has to be reachable from write_notes, not dead code.
    assert 'if [[ $kind == release ]]' in script


def test_a_commit_lands_in_exactly_one_category():
    """Measured: «Прочее» repeated every line already listed under «Служебное».

    `_commits` formatted its output (`- Ci: ...`) before the caller filtered for
    leftovers, so the `^(?!ci)` pattern never matched anything and the note page
    showed each CI commit twice.
    """
    script = SCRIPT.read_text(encoding="utf-8")

    # The leftover filter must see raw subjects, not `- Prefixed: ones`.
    assert "raw" in script
    assert "_format_commits" in script
    # Filtering after formatting is the bug this guards.
    assert "| grep -Ev '^(feat" not in script


def test_the_script_does_not_run_the_suite_with_the_system_interpreter():
    """Measured: `python -m pytest` found no pytest and read as a red suite.

    AGENTS.md asks for the project venv; a missing pytest is a broken script,
    not a failing test, and the two must not share an exit path.
    """
    script = SCRIPT.read_text(encoding="utf-8")

    assert ".venv/bin/python" in script
    assert "RELEASE_PYTHON" in script
    assert "import pytest" in script

