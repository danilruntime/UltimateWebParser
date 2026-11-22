"""Documentation links must resolve, but a quoted example is not a link.

CI was red on `main` from v0.1b3: the link step scanned every ``](...)`` in
every tracked markdown file, and the SoundCloud plan quotes a README template
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
