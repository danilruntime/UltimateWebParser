"""Documentation links must resolve, but a quoted example is not a link.

CI was red on `main` from 2026-11-22 (commit ``b41656d``; v0.1b3 was clean):
the link step scanned every ``](...)`` in every tracked markdown file, and the
SoundCloud plan quotes a README template
inside a ``` fence. The template's ``](README.ru.md)`` was read relative to
``docs/superpowers/plans/`` and reported broken, so every push after that failed
a check that had nothing to do with the change.

The scan is now `scripts/check_markdown_links.py`, which strips fenced blocks and
inline code first. These tests pin the stripping, because "no links broke" and
"the check stopped looking" look identical from the exit code.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_markdown_links.py"


def _run(tmp: Path, *names: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *names],
        cwd=tmp,
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_link_in_a_fenced_block_is_an_example_not_a_link(tmp_path):
    """The exact shape that reddened `main`: a quoted README template."""
    (tmp_path / "plan.md").write_text(
        "```markdown\n**Русская версия:** [`README.ru.md`](README.ru.md).\n```\n"
    )

    result = _run(tmp_path, "plan.md")

    assert result.returncode == 0, result.stdout


def test_a_real_broken_link_still_fails(tmp_path):
    """Stripping code must not turn the check into a no-op."""
    (tmp_path / "README.md").write_text("see [install](docs/install.md)\n")

    result = _run(tmp_path, "README.md")

    assert result.returncode == 1
    assert "docs/install.md" in result.stdout


def test_a_link_inside_inline_code_is_ignored(tmp_path):
    """`uwp` writes paths as code; one of them must not be mistaken for a link."""
    (tmp_path / "README.md").write_text("run `[x](missing.md)` or nothing\n")

    result = _run(tmp_path, "README.md")

    assert result.returncode == 0, result.stdout


def test_an_unclosed_fence_hides_the_rest_of_the_page(tmp_path):
    """GitHub renders the remainder as code when a fence never closes.

    The scan has to agree with the renderer, not with the author's intent: a
    link after an unclosed fence is text on the page, so reporting it as broken
    would redden CI on a file whose only problem is the missing fence.
    """
    (tmp_path / "plan.md").write_text("```markdown\n[gone](missing.md)\n")

    result = _run(tmp_path, "plan.md")

    assert result.returncode == 0, result.stdout


def test_a_link_title_is_not_part_of_the_path(tmp_path):
    """``](docs/install.md "the install page")`` points at the same file."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "install.md").write_text("hi\n")
    (tmp_path / "README.md").write_text(
        'see [install](docs/install.md "the install page")\n'
    )

    result = _run(tmp_path, "README.md")

    assert result.returncode == 0, result.stdout


def test_a_file_outside_the_working_directory_is_scanned(tmp_path):
    """``check_markdown_links.py ../other/README.md`` used to raise ValueError.

    ``relative_to(root)`` throws for an absolute path outside the root, so the
    script died with a traceback instead of printing what it found.
    """
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "elsewhere.md"
    outside.write_text("see [install](missing.md)\n")

    result = _run(root, str(outside))

    assert result.returncode == 1
    assert "missing.md" in result.stdout


def test_the_check_passes_on_this_repository():
    """The tree that ships must be green, not merely the fixtures above."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout


def test_ci_runs_the_script_and_not_an_inline_copy():
    """Two copies of the scan would drift; the workflow must call this one."""
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")

    assert "python scripts/check_markdown_links.py" in workflow
    assert "rglob(\"*.md\")" not in workflow


def test_the_check_runs_on_an_unpacked_tarball_without_git(tmp_path):
    """A release tarball has no `.git`, and the check is documented as runnable.

    Measured: `git ls-files` inside a `git archive` export exits 128, so
    `check=True` raised `CalledProcessError` and a reader who unpacked the
    release could not run the command the docstring shows.
    """
    import shutil

    tree = tmp_path / "release"
    tree.mkdir()
    (tree / "README.md").write_text(
        "See [the disclaimer](DISCLAIMER.md).\n", encoding="utf-8"
    )
    (tree / "DISCLAIMER.md").write_text("Terms.\n", encoding="utf-8")
    (tree / "scripts").mkdir()
    shutil.copy(SCRIPT, tree / "scripts" / "check_markdown_links.py")

    result = subprocess.run(
        [sys.executable, str(tree / "scripts" / "check_markdown_links.py")],
        cwd=tree,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
