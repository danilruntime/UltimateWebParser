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


def test_a_manual_run_can_repair_an_existing_release():
    """Measured: GitHub refused with "Workflow does not exist or does not have a
    workflow_dispatch trigger in this tag".

    Two causes, both fixed here. The trigger must exist at all, and the notes
    must come from the commit that started the run — reading them from the
    checked-out tag would republish the stale text a repair is meant to replace.
    """
    triggers = _workflow().get("on") or _workflow().get(True)

    assert "workflow_dispatch" in triggers
    assert triggers["workflow_dispatch"]["inputs"]["tag"]["required"] is True

    runs = "\n".join(step["run"] for step in _steps() if "run" in step)
    assert "gh release edit" in runs, "an existing release would fail instead of updating"

    # The checkout has to follow the requested tag, or a manual run tests `main`
    # while claiming to describe the tag.
    checkout = next(step for step in _steps() if step.get("uses", "").startswith("actions/checkout"))
    assert "ref" in checkout["with"]


def test_the_workflow_attaches_no_archives():
    """A release page offering a wheel promises an install path that does not exist.

    Measured on the v0.1b1 wheel: its only top-level entries are `uwp/` and
    `ultimate_web_parser-0.1b1.dist-info/`. No `compose.yaml`, no `Dockerfile`,
    no `docker/seccomp-uwp.json`, no `docker/entrypoint.sh`, no `.env.example`.
    Chromium runs sandboxed only through that seccomp profile, so a reader who
    installs the wheel gets a CLI and no way to start the service — and nothing
    in the package that says so.

    The sdist added nothing either: 154 files against 153 in the tag's own
    working tree, which GitHub already serves as the source archive.
    """
    runs = "\n".join(step["run"] for step in _steps() if "run" in step)

    assert "gh release upload" not in runs, "archives are attached to the release"

    creates = [step["run"] for step in _steps() if "gh release create" in step.get("run", "")]
    assert creates, "the workflow no longer creates a release"
    for run in creates:
        assert "$RELEASE_TAG" in run
        for line in run.splitlines():
            if "gh release create" in line:
                assert "dist/" not in line, f"`gh release create` is handed archives: {line.strip()}"


def test_an_existing_release_loses_the_archives_it_already_has():
    """`v0.1b1` was published with two assets before the decision to drop them.

    Re-running the workflow has to remove them, otherwise the page keeps
    advertising a wheel the project no longer ships — and a repair run is the
    only mechanism that can touch an already-published page.
    """
    runs = "\n".join(step["run"] for step in _steps() if "run" in step)

    assert "gh release delete-asset" in runs
    assert "--yes" in runs, "the step runs without a TTY and would wait for input"


def test_the_workflow_still_builds_the_archives_it_does_not_ship():
    """Building is a check, not a deliverable — and it has already paid for itself.

    Measured: putting `[project.urls]` above `dependencies` left the list inside
    the URL table. Valid TOML, clean ruff, green tests, and a wheel whose
    `Requires-Dist` held zero runtime requirements.
    """
    runs = "\n".join(step["run"] for step in _steps() if "run" in step)

    assert "python -m build" in runs
    assert "wheel" in runs or "--sdist" in runs


def test_the_notes_are_read_from_the_triggering_commit():
    """A repair run starts on `main`, where the notes were fixed.

    `GITHUB_SHA` is the tag on a tag push and the branch on a dispatch, so both
    cases read the right file; the checked-out tree is the tag, which still
    holds the old text.
    """
    runs = "\n".join(step["run"] for step in _steps() if "run" in step)

    assert 'git show "$GITHUB_SHA:docs/releases/$RELEASE_TAG.md"' in runs
    assert '--notes-file "$RUNNER_TEMP/notes.md"' in runs


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
