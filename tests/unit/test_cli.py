"""Tests for the command line interface."""
import pytest

from uwp.cli import main


def test_parsers_list(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))

    assert main(["parsers", "list"]) == 0
    assert "steam_deals" in capsys.readouterr().out


def test_doctor_fails_without_bot_token(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("UWP_BOT_TOKEN", raising=False)
    monkeypatch.delenv("UWP_ADMIN_IDS", raising=False)

    assert main(["doctor"]) == 1

    out = capsys.readouterr().out
    assert "UWP_BOT_TOKEN" in out
    assert "UWP_ADMIN_IDS" in out


def test_doctor_quiet_prints_nothing_on_failure(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("UWP_BOT_TOKEN", raising=False)

    assert main(["doctor", "--quiet"]) == 1
    assert capsys.readouterr().out == ""


def test_site_add_and_list(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))

    assert main(["init"]) == 0
    assert main(["site", "add", "steam_deals", "--name", "Steam US"]) == 0
    assert main(["site", "list"]) == 0

    assert "Steam US" in capsys.readouterr().out


def test_init_is_idempotent(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))

    assert main(["init"]) == 0
    assert main(["init"]) == 0
    assert main(["site", "list"]) == 0

    # The seeded site is not duplicated by a second init.
    assert capsys.readouterr().out.count("Steam — скидки") == 1


def test_site_enable_disable(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    main(["init"])

    assert main(["site", "enable", "steam_skidki"]) == 0
    assert "включён" in capsys.readouterr().out
    assert main(["site", "disable", "steam_skidki"]) == 0
    assert "выключен" in capsys.readouterr().out


def test_unknown_site_is_an_error(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    main(["init"])

    assert main(["site", "enable", "nope"]) == 1
    assert "не найден" in capsys.readouterr().out


def test_set_interval_and_show_it(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    main(["init"])

    assert main(["site", "set", "steam_skidki", "--interval", "30-40"]) == 0
    assert main(["site", "list"]) == 0
    assert "30–40" in capsys.readouterr().out


def test_bad_interval_is_rejected(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    main(["init"])

    assert main(["site", "set", "steam_skidki", "--interval", "90-10"]) == 1
    assert "Интервал" in capsys.readouterr().out


def test_export_requires_a_known_site(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    main(["init"])

    assert main(["export", "nope"]) == 1
    assert "не найден" in capsys.readouterr().out


def test_db_upgrade_creates_the_schema(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))

    assert main(["db", "upgrade"]) == 0
    assert (tmp_path / "uwp.db").is_file()


def test_parser_install_help_is_available(capsys):
    with pytest.raises(SystemExit):
        main(["browser", "install", "--help"])
    assert "sha256" in capsys.readouterr().out.lower()
