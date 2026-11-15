"""Retention for per-run artifact directories (screenshots of failed runs)."""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def prune_artifacts(directory: Path, keep: int) -> int:
    """Delete the oldest ``<run_id>`` directories beyond ``keep``.

    A failed run leaves a screenshot (and whatever the parser captured) under
    ``data/artifacts/<run_id>``. Nothing pruned them before, so the directory
    grew without bound and kept page content long after the run was irrelevant.

    Returns the number of directories removed.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return 0
    runs = sorted(
        (path for path in directory.iterdir() if path.is_dir()),
        key=lambda path: (path.stat().st_mtime, path.name),
    )
    excess = runs[: max(len(runs) - keep, 0)]
    removed = 0
    for path in excess:
        try:
            for child in sorted(path.rglob("*"), reverse=True):
                if child.is_dir() and not child.is_symlink():
                    child.rmdir()
                else:
                    child.unlink(missing_ok=True)
            path.rmdir()
        except OSError:
            log.warning("cannot remove artifact directory %s", path, exc_info=True)
            continue
        removed += 1
    return removed
