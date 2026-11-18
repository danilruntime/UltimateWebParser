"""The release script edits the two files a version lives in, then pushes a tag.

Its arithmetic is small enough to get wrong silently: `0.1b1` -> `0.1b1` (no
bump), `0.1` -> `0.1b2` (a beta behind its own stable release), `0.1b1` ->
`0.1.0` (not the spelling the metadata test accepts). So the transitions are
pinned here, and the script is sourced rather than executed: `compute_next` is
a pure function of (current, kind) and needs no repository.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
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

    It wrote a 79-character note — «No commits since the previous release.» —
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
    assert "Stable version" in script
    # The promotion branch has to be reachable from write_notes, not dead code.
    assert 'if [[ $kind == release ]]' in script


def test_a_commit_lands_in_exactly_one_category():
    """Measured: «Other» repeated every line already listed under «Chores».

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



def test_the_generated_notes_are_english():
    """Measured on v0.1b2: «## Новое» sat above «A shell script that bumps…».

    The page is published on GitHub next to an English README, and its body is
    the commit subjects, which the project writes in English. The headings were
    the only Russian left, so the release read as two languages at once. Written
    by sourcing `write_notes` in a throwaway repository, because the output is
    the contract — the titles live inside the function, not on a line a grep can
    pin. The promotion branch is exercised too: it repeats the beta's sections
    verbatim, so one Russian heading there would survive every future release.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "t@example.com"], cwd=root, check=True
        )
        subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
        (root / "docs" / "releases").mkdir(parents=True)
        (root / "docs" / "releases" / "v0.1b1.md").write_text("# v0.1b1\n")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
        subprocess.run(["git", "tag", "-a", "v0.1b1", "-m", "v0.1b1"], cwd=root, check=True)
        for subject in ("feat(bot): a switch", "fix(vnc): a port", "docs: a page"):
            subprocess.run(
                ["git", "commit", "-q", "--allow-empty", "-m", subject],
                cwd=root,
                check=True,
            )

        beta = _write_notes(root, "0.1b2", "v0.1b1", "", "beta")

        # The promotion reads the beta's file back, so the beta has to be in the
        # tree at the tag `git describe` would name.
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "chore: release 0.1b2"], cwd=root, check=True)
        subprocess.run(["git", "tag", "-a", "v0.1b2", "-m", "v0.1b2"], cwd=root, check=True)
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", "test: a check"],
            cwd=root,
            check=True,
        )

        stable = _write_notes(root, "0.1", "v0.1b2", "0.1b2", "release")

    assert not re.search(r"[А-Яа-яЁё]", beta), (
        "the beta notes fall back to Russian somewhere:\n" + beta
    )
    headings = [line for line in beta.splitlines() if line.startswith("## ")]
    assert headings == ["## New", "## Fixed", "## Documentation"], headings

    assert not re.search(r"[А-Яа-яЁё]", stable), (
        "the promotion notes fall back to Russian somewhere:\n" + stable
    )
    headings = [line for line in stable.splitlines() if line.startswith("## ")]
    assert headings[-1] == "## Since the beta", headings
    assert "## New" in headings, "the promotion dropped the beta's sections"


def test_promoting_a_beta_written_in_russian_drops_its_version_line():
    """A beta cut before the page became English says «Версия … Тег …».

    `_inherited_sections` strips that line from the beta it repeats. Matching
    only the English spelling left the old one in place, so the stable page
    carried a stray «Версия `0.1b2`» three lines under its own English header.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "t@example.com"], cwd=root, check=True
        )
        subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
        (root / "docs" / "releases").mkdir(parents=True)
        (root / "docs" / "releases" / "v0.1b2.md").write_text(
            "# v0.1b2\n\nВерсия `0.1b2`. Тег `v0.1b2`.\n\n## Новое\n\n- A switch\n"
        )
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)

        stable = _write_notes(root, "0.1", "v0.1b2", "0.1b2", "release")

    assert "Версия `0.1b2`" not in stable, stable
    assert stable.count("Version `0.1`") == 1, stable


def _write_notes(root: Path, version: str, previous: str, current: str, kind: str) -> str:
    """Run `write_notes` in `root` and return the file it wrote."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; write_notes "$2" "$3" "$4" "$5"',
            "_",
            str(SCRIPT),
            version,
            previous,
            current,
            kind,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    return (root / "docs" / "releases" / f"v{version}.md").read_text(encoding="utf-8")


def test_a_successful_run_exits_zero():
    """Measured: every successful release exited 1.

    ``rollback`` and ``next`` were ``local`` to ``main``, but the ``trap`` runs
    at EXIT — after ``main`` returned and its locals were gone. Under
    ``set -u`` the trap itself died with "rollback: unbound variable", which
    turned a created tag into a failed command: `./scripts/release.sh beta &&
    git push` never pushed, and CI read the release as broken. The script is run
    here in a throwaway repository, because exit status is the whole point.
    """
    _made: list[Path] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "t@example.com"], cwd=root, check=True
        )
        subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
        (root / "pyproject.toml").write_text('version = "0.1b1"\n')
        (root / "src" / "uwp").mkdir(parents=True)
        (root / "src" / "uwp" / "__init__.py").write_text('__version__ = "0.1b1"\n')
        (root / "README.md").write_text("see docs/releases/v0.1b1.md\n")
        (root / "README.ru.md").write_text("см. docs/releases/v0.1b1.md\n")
        (root / "docs" / "releases").mkdir(parents=True)
        (root / "docs" / "releases" / "v0.1b1.md").write_text("# v0.1b1\n")
        (root / "scripts").mkdir()
        (root / "scripts" / "release.sh").write_text(SCRIPT.read_text())
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
        subprocess.run(["git", "tag", "-a", "v0.1b1", "-m", "v0.1b1"], cwd=root, check=True)
        # The origin check compares HEAD with origin/main; point both at the
        # same commit without a network.
        subprocess.run(["git", "tag", "origin-main"], cwd=root, check=True)
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
            cwd=root,
            check=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", str(root)], cwd=root, check=True
        )
        # One commit after the tag, so "nothing to release" does not fire.
        (root / "CHANGELOG.md").write_text("x\n")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "feat: x"], cwd=root, check=True)

        # Outside the repository: the script refuses a dirty tree, and an
        # untracked helper inside it would be exactly that.
        fake = Path(tmp).parent / f"{Path(tmp).name}-fakepy"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)
        _made.append(fake)

        result = subprocess.run(
            ["bash", str(root / "scripts" / "release.sh"), "beta", "--no-push"],
            cwd=root,
            capture_output=True,
            text=True,
            env={**os.environ, "RELEASE_PYTHON": str(fake)},
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert "unbound" not in result.stderr

    for path in _made:
        path.unlink(missing_ok=True)
