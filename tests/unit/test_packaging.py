"""Packaging metadata is code: a dependency in the wrong table fails silently.

Measured on 2026-11-16: adding ``[project.urls]`` immediately above the existing
``dependencies`` list left the list *inside* the URLs table. TOML accepted it,
``ruff`` accepted it, ``pytest`` accepted it — because the tests run from an
already-installed venv — and ``pip install ultimate-web-parser`` would have
installed the package with **zero** runtime dependencies. The first symptom
would have been an ``ImportError`` on a clean machine.

Hence a test that reads ``pyproject.toml`` the way a build backend does, rather
than a test that imports the modules into a venv where they already exist.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _project() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]


def test_the_runtime_dependencies_are_a_list_and_not_empty():
    """``[project.urls]`` swallows a following ``dependencies`` block silently."""
    project = _project()

    assert "dependencies" in project, "the [project] table lost its dependencies"
    dependencies = project["dependencies"]

    assert isinstance(dependencies, list)
    assert dependencies, "an empty dependency list means a clean install cannot start"
    for dependency in dependencies:
        assert isinstance(dependency, str), f"{dependency!r} is not a requirement string"


def test_the_url_table_holds_urls_and_nothing_else():
    """A table that swallowed a dependency list is the bug this file exists for."""
    urls = _project()["urls"]

    assert set(urls) == {"Homepage", "Repository", "Issues", "Changelog"}
    for name, value in urls.items():
        assert isinstance(value, str), f"{name} is not a URL"
        assert value.startswith("https://"), f"{name} is not an https URL: {value}"
        assert " " not in value, f"{name} contains a space: {value}"


def test_every_runtime_import_is_declared_as_a_dependency():
    """An undeclared import works locally and fails on the reader's machine.

    ``import importlib.metadata`` would report what this venv happens to have,
    which is exactly the false reassurance above; the mapping is explicit so a
    new third-party import has to be added here on purpose.
    """
    distribution = {
        "playwright": "playwright",
        "aiogram": "aiogram",
        "aiohttp_socks": "aiohttp-socks",
        "aiosqlite": "aiosqlite",
        "odf": "odfpy",
        "pydantic": "pydantic",
        "pydantic_settings": "pydantic-settings",
        "bs4": "beautifulsoup4",
        "cryptography": "cryptography",
        "tzdata": "tzdata",
    }
    declared = {
        re.split(r"[<>=!\[ ]", requirement)[0].lower()
        for requirement in _project()["dependencies"]
    }

    imported = set()
    for source in (ROOT / "src" / "uwp").rglob("*.py"):
        for line in source.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*(?:from|import)\s+([A-Za-z_][\w]*)", line)
            if match:
                imported.add(match.group(1))

    third_party = imported & set(distribution)
    missing = {distribution[name] for name in third_party} - declared

    assert not missing, f"imported but undeclared: {sorted(missing)}"
    assert third_party, "the import scanner found no third-party imports at all"


def test_the_metadata_points_at_the_public_repository():
    """A published package whose Homepage is a placeholder is a dead link."""
    project = _project()
    homepage = project["urls"]["Homepage"]

    assert project["readme"] == "README.md"
    assert (ROOT / project["readme"]).is_file()
    assert (ROOT / project["license"]["file"]).is_file()
    assert project["authors"] == [{"name": "danilruntime"}]
    assert homepage == "https://github.com/danilruntime/UltimateWebParser"


def test_the_declared_version_is_a_valid_prerelease_and_matches_the_package():
    """The version lives in two files; a release that edits one is a bug.

    ``0.1 beta`` is what a human writes and what PEP 440 rejects — pip refuses
    to build it. ``0.1b1`` is the canonical spelling of the same thing, and the
    metadata sheet of every report quotes ``uwp.__version__``, so a drift
    between the two files reaches the delivered ``.ods``.

    This used to assert that the version *is* a prerelease on the 0.1 line: a
    guard for the first release, which would have blocked the first stable one
    outright. Canonical form is what actually mattered — ``v0.1`` parses to
    ``0.1``, so pip would see a different version than the file states.
    """
    from packaging.version import Version

    from uwp import __version__

    declared = _project()["version"]

    assert declared == __version__, f"pyproject says {declared}, the package says {__version__}"

    version = Version(declared)
    assert str(version) == declared, f"{declared} is not the canonical spelling of {version}"
