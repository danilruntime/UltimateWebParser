#!/usr/bin/env python3
"""Fail on a broken local markdown link, ignoring fenced and inline code.

The CI check this replaces scanned every ``](...)`` in every tracked ``.md`` and
went red on ``main`` from 2026-11-22 on (commit ``b41656d``, not at v0.1b3): a
design plan quotes a README template inside a ``` fence, and the regex read the
template's ``](README.ru.md)`` as a link relative to
``docs/superpowers/plans/``. Measured: the old snippet finds 0 broken links on
``v0.1b3`` and 6 on ``b41656d``. A quoted example is not a link, so the scan has
to skip code before it decides. Markdown links are what a reader clicks; that is
what this checks.

Run from the repository root:

    python scripts/check_markdown_links.py            # every tracked .md
    python scripts/check_markdown_links.py FILE ...   # just these
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_LINK = re.compile(r"\]\(([^)#:]+?)(?:\s+\"[^\"]*\")?\)")
_FENCE = re.compile(r"^\s*(````+|````*|~~~)")
_INLINE_CODE = re.compile(r"`[^`\n]*`")

# Directories that are generated, vendored or scratch; none of them is published.
_SKIP = {".git", ".venv", "node_modules", ".superpowers", ".worktrees", "__pycache__"}


def strip_code(text: str) -> str:
    """Remove fenced blocks and inline code spans, keeping line count stable."""
    out: list[str] = []
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        match = _FENCE.match(line)
        if fence is None:
            if match:
                fence = match.group(1)[0] * 3
                out.append("\n" if line.endswith("\n") else "")
                continue
            out.append(_INLINE_CODE.sub("", line))
        else:
            if match and match.group(1).startswith(fence):
                fence = None
            out.append("\n" if line.endswith("\n") else "")
    return "".join(out)


def _display(path: Path, root: Path) -> str:
    """The path as the reader would type it.

    ``relative_to`` raises for a file outside the working directory, which is
    what ``check_markdown_links.py ../other/README.md`` passes: the script used
    to die with ``ValueError`` before printing what it found.
    """
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def broken_links(path: Path, root: Path) -> list[str]:
    """``path -> target`` for every local link that does not resolve."""
    broken = []
    for target in _LINK.findall(strip_code(path.read_text(encoding="utf-8"))):
        if target.startswith(("http", "mailto")):
            continue
        if not (path.parent / target).exists():
            broken.append(f"{_display(path, root)} -> {target}")
    return broken


def tracked_markdown(root: Path) -> list[Path]:
    """Every markdown file git knows about, or every file if this is not a checkout.

    A source tarball has no `.git`, and CI is not the only caller: a reader who
    unpacks the release should be able to run the same check. The fallback walks
    the tree and skips the same generated directories the tracked path ignores.
    """
    try:
        names = subprocess.run(
            ["git", "ls-files", "*.md"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        names = [str(path.relative_to(root)) for path in sorted(root.rglob("*.md"))]
    return [root / name for name in names]


def _skipped(path: Path, root: Path) -> bool:
    """True for generated or vendored directories — and for anything outside."""
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        # An explicit argument outside the tree is not ours to skip: the caller
        # asked for that file.
        return False
    return bool(_SKIP & set(parts))


def main(argv: list[str]) -> int:
    root = Path.cwd()
    paths = [Path(arg).resolve() for arg in argv] or tracked_markdown(root)
    broken = [
        line
        for path in paths
        if not _skipped(path, root)
        for line in broken_links(path, root)
    ]
    if broken:
        print("Broken local links:")
        print("\n".join(broken))
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
