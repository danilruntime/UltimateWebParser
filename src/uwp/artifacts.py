"""Retention for per-run artifact directories (screenshots of failed runs)."""
from __future__ import annotations

import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

# A run creates its directory only to write into it, so an empty one is a
# capture that failed. The window exists for the moment between ``mkdir`` and
# the write: deleting a directory a live run is about to fill would turn a
# working screenshot into a silent error.
_EMPTY_GRACE_SECONDS = 600


def _has_files(path: Path) -> bool:
    return any(child.is_file() for child in path.rglob("*"))


def prune_artifacts(
    directory: Path, keep: int, now: float | None = None
) -> int:
    """Delete the oldest ``<run_id>`` directories beyond ``keep``.

    A failed run leaves a screenshot (and whatever the parser captured) under
    ``data/artifacts/<run_id>``. Nothing pruned them before, so the directory
    grew without bound and kept page content long after the run was irrelevant.

    Emptied directories do not count against ``keep``. Every run used to create
    one, even a successful run that captured nothing, so twenty uneventful runs
    evicted the screenshot of the last real failure within the hour.

    Returns the number of directories removed.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return 0
    now = time.time() if now is None else now
    runs = sorted(
        (path for path in directory.iterdir() if path.is_dir()),
        key=lambda path: (path.stat().st_mtime, path.name),
    )
    evidence = [path for path in runs if _has_files(path)]
    excess = set(evidence[: max(len(evidence) - keep, 0)])
    excess.update(
        path
        for path in runs
        if path not in evidence
        and now - path.stat().st_mtime > _EMPTY_GRACE_SECONDS
    )

    removed = 0
    for path in runs:
        if path not in excess:
            continue
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
