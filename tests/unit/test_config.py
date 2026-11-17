from pathlib import Path

import pytest
from pydantic import ValidationError

from uwp.config import Settings, load_settings
from uwp.i18n import Lang


def test_admin_ids_parsed_from_csv(tmp_path):
    s = load_settings({"UWP_ADMIN_IDS": "111, 222", "UWP_DATA_DIR": str(tmp_path)})
    assert s.admin_ids == [111, 222]


def test_admin_ids_accepts_empty_string(tmp_path):
    s = load_settings({"UWP_ADMIN_IDS": "", "UWP_DATA_DIR": str(tmp_path)})
    assert s.admin_ids == []


def test_directories_created(tmp_path):
    s = load_settings({"UWP_DATA_DIR": str(tmp_path / "d")})
    assert s.db_path == tmp_path / "d" / "uwp.db"
    assert s.profiles_dir.is_dir() and s.exports_dir.is_dir() and s.artifacts_dir.is_dir()
    assert s.profiles_dir == tmp_path / "d" / "profiles"
    assert s.exports_dir == tmp_path / "d" / "exports"
    assert s.artifacts_dir == tmp_path / "d" / "artifacts"


def test_data_directory_is_private(tmp_path):
    """Everything below ``data`` holds profiles and secrets; no world/group bits."""
    s = load_settings({"UWP_DATA_DIR": str(tmp_path / "d")})

    assert s.data_dir.stat().st_mode & 0o077 == 0
    assert s.profiles_dir.stat().st_mode & 0o077 == 0
    assert s.exports_dir.stat().st_mode & 0o077 == 0


def test_defaults():
    s = load_settings({})
    assert s.run_timeout == 300 and s.vnc_port_min == 6000 and s.vnc_port_max == 6100
    assert s.tz == "Europe/Moscow" and s.max_parallel_runs == 1


def test_defaults_do_not_depend_on_process_environment(monkeypatch):
    monkeypatch.setenv("UWP_RUN_TIMEOUT", "999")
    monkeypatch.setenv("UWP_ADMIN_IDS", "5")
    s = load_settings({})
    assert s.run_timeout == 300 and s.admin_ids == []


def test_explicit_env_overrides_defaults(tmp_path):
    s = load_settings(
        {
            "UWP_DATA_DIR": str(tmp_path),
            "UWP_BOT_TOKEN": "123:abc",
            "UWP_RUN_TIMEOUT": "120",
            "UWP_HEADLESS": "false",
            "UWP_NO_SANDBOX": "true",
            "UWP_SECRET_KEY": "k" * 32,
        }
    )
    assert s.bot_token == "123:abc"
    assert s.run_timeout == 120
    assert s.headless is False
    assert s.no_sandbox is True
    assert s.secret_key == "k" * 32


def test_browser_constraints(tmp_path):
    s = load_settings({"UWP_DATA_DIR": str(tmp_path)})
    assert s.browser_version == "150.0.7871.186"
    assert s.browser_sha256 == (
        "48eff860053efd9ad498d51370f0a1f59f983df39c02a72ad2a8682cdead4055"
    )
    assert s.browser_url == (
        "https://github.com/adryfish/fingerprint-chromium/releases/download/"
        "150.0.7871.186/ungoogled-chromium-150.0.7871.186-1-x86_64_linux.tar.xz"
    )
    assert s.browser_path is None


def test_load_settings_returns_settings_instance(tmp_path):
    assert isinstance(load_settings({"UWP_DATA_DIR": str(tmp_path)}), Settings)


def test_data_dir_is_path(tmp_path):
    s = load_settings({"UWP_DATA_DIR": str(tmp_path)})
    assert isinstance(s.data_dir, Path)


def test_load_settings_reads_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        f"UWP_ADMIN_IDS=7,8\nUWP_RUN_TIMEOUT=42\nUWP_DATA_DIR={tmp_path / 'd'}\n",
        encoding="utf-8",
    )
    s = load_settings()
    assert s.admin_ids == [7, 8]
    assert s.run_timeout == 42
    assert s.db_path == tmp_path / "d" / "uwp.db"


def test_two_settings_do_not_share_the_admin_list():
    """A mutable class default would leak ids between tests and processes."""
    first = load_settings({})
    second = load_settings({})

    first.admin_ids.append(999)

    assert second.admin_ids == []


def test_explicit_env_does_not_need_a_json_list():
    settings = load_settings({"UWP_ADMIN_IDS": "111, 222"})

    assert settings.admin_ids == [111, 222]


def test_telegram_proxy_is_optional_and_blank_means_none():
    assert load_settings({}).telegram_proxy is None
    assert load_settings({"UWP_TELEGRAM_PROXY": ""}).telegram_proxy is None
    assert (
        load_settings({"UWP_TELEGRAM_PROXY": " socks5://127.0.0.1:2080 "}).telegram_proxy
        == "socks5://127.0.0.1:2080"
    )


def test_language_defaults(tmp_path):
    s = load_settings({"UWP_DATA_DIR": str(tmp_path)})
    assert s.default_language == "ru"
    assert s.report_language is None
    assert s.drop_pending_updates is True


def test_a_blank_report_language_means_the_default_language(tmp_path):
    """``.env.example`` ships ``UWP_REPORT_LANGUAGE=``: copying it must work."""
    s = load_settings({"UWP_DATA_DIR": str(tmp_path), "UWP_REPORT_LANGUAGE": ""})
    assert s.report_language is None
    assert s.report_lang is Lang.ru


def test_report_lang_falls_back_to_the_default_language(tmp_path):
    s = load_settings({"UWP_DATA_DIR": str(tmp_path)})
    assert s.report_lang is Lang.ru

    assert load_settings(
        {"UWP_DATA_DIR": str(tmp_path), "UWP_DEFAULT_LANGUAGE": "en"}
    ).report_lang is Lang.en

    assert load_settings(
        {"UWP_DATA_DIR": str(tmp_path), "UWP_REPORT_LANGUAGE": "en"}
    ).report_lang is Lang.en

    # An explicit report language wins over the deployment default.
    assert load_settings(
        {
            "UWP_DATA_DIR": str(tmp_path),
            "UWP_DEFAULT_LANGUAGE": "ru",
            "UWP_REPORT_LANGUAGE": "en",
        }
    ).report_lang is Lang.en


def test_drop_pending_updates_can_be_turned_off(tmp_path):
    s = load_settings(
        {"UWP_DATA_DIR": str(tmp_path), "UWP_DROP_PENDING_UPDATES": "false"}
    )
    assert s.drop_pending_updates is False


def test_public_scheme_is_http_or_https(tmp_path):
    """A typo like ``htps`` would otherwise produce a dead VNC link."""
    assert load_settings({"UWP_DATA_DIR": str(tmp_path)}).public_scheme == "http"
    assert (
        load_settings(
            {"UWP_DATA_DIR": str(tmp_path), "UWP_PUBLIC_SCHEME": "HTTPS"}
        ).public_scheme
        == "https"
    )
    with pytest.raises(ValidationError):
        load_settings({"UWP_DATA_DIR": str(tmp_path), "UWP_PUBLIC_SCHEME": "htps"})
