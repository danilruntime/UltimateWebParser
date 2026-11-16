"""A release workflow that cannot publish is only discovered on the tag.

`v0.1b1` created the page by hand the first time. From now on a tag triggers
`.github/workflows/release.yml`, which reads `docs/releases/<tag>.md` and refuses
to publish without it. Both facts are cheap to check offline: a typo in the
path would otherwise surface as a failed run on a tag that is already pushed,
where it cannot be fixed without deleting the tag.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return _workflow()["jobs"]["release"]["steps"]


def test_the_workflow_runs_on_a_version_tag():
    triggers = _workflow().get("on") or _workflow().get(True)

    assert triggers["push"]["tags"] == ["v*"]


def test_the_workflow_may_write_the_release():
    """Creating a release needs `contents: write`; the default is read-only."""
    assert _workflow()["permissions"]["contents"] == "write"


def test_the_workflow_reads_notes_from_the_path_this_test_checks():
    """The notes file is found by tag name, so the two must agree on the shape."""
    runs = "\n".join(step["run"] for step in _steps() if "run" in step)

    assert "docs/releases" in runs
    assert "gh release create" in runs


def test_every_release_has_a_notes_file_named_after_its_tag():
    """A pushed tag is immutable in practice; a missing file blocks the release."""
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    notes = ROOT / "docs" / "releases" / f"v{version}.md"

    assert notes.is_file(), f"{notes.relative_to(ROOT)} is missing for version {version}"
    assert len(notes.read_text(encoding="utf-8").strip()) > 200, "release notes are a stub"


def test_the_release_notes_describe_the_current_version():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    text = (ROOT / "docs" / "releases" / f"v{version}.md").read_text(encoding="utf-8")

    assert version in text.splitlines()[0], "the heading does not name the version"


def test_a_prerelease_is_published_as_a_prerelease():
    """GitHub badges the newest non-prerelease as "Latest".

    A beta wearing that badge reads as a stable recommendation, so the flag has
    to follow the version. Measured on v0.1b1: published without the flag, the
    release page said "Latest" for a 0.x beta.
    """
    runs = "\n".join(step["run"] for step in _steps() if "run" in step)

    assert "is_prerelease" in runs
    assert "--prerelease" in runs


def test_the_notes_do_not_pin_the_test_count():
    """A release page describes a commit, so a count in it cannot stay true.

    Measured twice in one sitting: the notes said 626, then 631 after two tests
    were added. The badge already reports the live number.
    """
    for notes in (ROOT / "docs" / "releases").glob("*.md"):
        text = notes.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "тест" not in line.lower() and "test" not in line.lower():
                continue
            assert not re.search(r"\b\d{3,}\s+(тест\w*|tests?)\b", line, re.IGNORECASE), (
                f"{notes.name} pins a test count: {line.strip()}"
            )
