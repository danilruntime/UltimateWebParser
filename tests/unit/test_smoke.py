import tomllib
from pathlib import Path

import uwp

ROOT = Path(__file__).resolve().parents[2]


def test_package_has_version():
    """The package is importable and carries a version string.

    Pinning the literal here would make every release edit three files; the
    exact value is checked against ``pyproject.toml`` in ``test_packaging.py``.
    """
    assert isinstance(uwp.__version__, str)
    assert uwp.__version__.count(".") >= 1


def test_the_package_version_comes_from_the_metadata():
    """One source of truth: pyproject.toml, read the way pip reads it."""
    with (ROOT / "pyproject.toml").open("rb") as handle:
        declared = tomllib.load(handle)["project"]["version"]

    assert uwp.__version__ == declared
