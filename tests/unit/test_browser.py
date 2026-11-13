"""Unit tests for browser resolution and the Playwright launch wrapper."""
import pytest

from uwp.browser import BrowserUnavailable, resolve_browser
from uwp.config import load_settings


def test_missing_browser_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(BrowserUnavailable):
        resolve_browser(load_settings({"UWP_DATA_DIR": str(tmp_path)}))


def test_explicit_browser_path_wins(tmp_path):
    exe = tmp_path / "chrome"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    assert resolve_browser(load_settings({"UWP_DATA_DIR": str(tmp_path), "UWP_BROWSER_PATH": str(exe)})) == exe


def test_non_executable_browser_path_falls_back_to_path(tmp_path, monkeypatch):
    exe = tmp_path / "chrome"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o644)
    found = tmp_path / "fingerprint-chromium"
    found.write_text("#!/bin/sh\n")
    found.chmod(0o755)
    monkeypatch.setattr("shutil.which", lambda name: str(found) if name == "fingerprint-chromium" else None)
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path), "UWP_BROWSER_PATH": str(exe)})
    assert resolve_browser(settings) == found


def test_browser_found_on_path(tmp_path, monkeypatch):
    found = tmp_path / "fingerprint-chromium"
    found.write_text("#!/bin/sh\n")
    found.chmod(0o755)
    monkeypatch.setattr("shutil.which", lambda name: str(found) if name == "fingerprint-chromium" else None)
    assert resolve_browser(load_settings({"UWP_DATA_DIR": str(tmp_path)})) == found
