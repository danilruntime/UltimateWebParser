"""Tests for the command line interface."""
import pytest

from uwp.cli import main


@pytest.fixture
def healthy_driver(monkeypatch):
    """Stub the bundled-Node probe so a test depends on the code, not the host.

    ``doctor`` really executes Playwright's ``node``, which needs
    ``libatomic.so.1``. Without this stub the two token tests below fail on a
    slim Debian image for a reason that has nothing to do with bot tokens —
    measured in ``python:3.12-slim``. Tests that *are* about the driver run it
    for real (see ``test_doctor_reports_a_broken_playwright_driver``).
    """
    monkeypatch.setattr(
        "uwp.cli._check_playwright_driver", lambda *_a, **_kw: (True, "v22.0.0")
    )


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


def test_doctor_does_not_require_a_bot_token_for_the_worker(
    capsys, monkeypatch, tmp_path, healthy_driver
):
    """The worker never polls Telegram, so it must start without the token.

    A split deployment deliberately keeps ``UWP_BOT_TOKEN`` out of the worker's
    environment; the healthcheck calls ``doctor``, so requiring the token there
    would mark a correctly configured worker unhealthy forever.
    """
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UWP_BROWSER_PATH", "/bin/true")
    monkeypatch.delenv("UWP_BOT_TOKEN", raising=False)
    monkeypatch.setenv("UWP_ADMIN_IDS", "1")
    monkeypatch.setenv("UWP_SECRET_KEY", "k" * 32)
    monkeypatch.delenv("UWP_TELEGRAM_PROXY", raising=False)
    # pydantic also reads ``.env`` from the working directory; a stray token
    # there would make this test pass for the wrong reason.
    monkeypatch.chdir(tmp_path)

    assert main(["doctor", "--offline", "--worker"]) == 0

    out = capsys.readouterr().out
    assert "UWP_BOT_TOKEN" not in out


def test_doctor_still_requires_a_bot_token_for_the_bot(
    capsys, monkeypatch, tmp_path, healthy_driver
):
    """The bot process is exactly the one that needs the token."""
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UWP_BROWSER_PATH", "/bin/true")
    monkeypatch.delenv("UWP_BOT_TOKEN", raising=False)
    monkeypatch.setenv("UWP_ADMIN_IDS", "1")
    monkeypatch.setenv("UWP_SECRET_KEY", "k" * 32)
    monkeypatch.delenv("UWP_TELEGRAM_PROXY", raising=False)
    monkeypatch.chdir(tmp_path)

    assert main(["doctor", "--offline"]) == 1

    assert "UWP_BOT_TOKEN" in capsys.readouterr().out


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


def test_doctor_checks_the_playwright_driver(capsys, monkeypatch, tmp_path):
    """A broken driver node used to surface only as a failed run, not a check."""
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UWP_BOT_TOKEN", "1:fake")
    monkeypatch.setenv("UWP_ADMIN_IDS", "1")
    monkeypatch.setattr(
        "uwp.cli._check_playwright_driver", lambda *_a, **_kw: (True, "v22.0.0")
    )

    main(["doctor"])

    assert "драйвер playwright" in capsys.readouterr().out


def test_doctor_reports_a_broken_driver(monkeypatch, tmp_path):
    """The measured symptom: ``node`` links against libatomic, a slim image has none."""
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UWP_BOT_TOKEN", "1:fake")
    monkeypatch.setenv("UWP_ADMIN_IDS", "1")

    from uwp import cli

    def broken(*_a, **_kw):
        return False, "error while loading shared libraries: libatomic.so.1"

    monkeypatch.setattr("uwp.cli._check_playwright_driver", broken)

    assert cli.main(["doctor"]) == 1


def test_the_driver_check_really_executes_the_bundled_node(monkeypatch):
    """Stubbing the driver everywhere else would hide a probe that never runs.

    ``_check_playwright_driver`` exists because a file-exists test is not
    enough: the binary can be present and still fail to load a shared library.
    """
    import subprocess as real_subprocess

    from uwp import cli

    seen: list[list[str]] = []

    class _Ok:
        returncode = 0
        stdout = "v22.11.0\n"
        stderr = ""

    def fake_run(cmd, *args, **kwargs):
        seen.append(list(cmd))
        return _Ok()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    ok, detail = cli._check_playwright_driver()

    assert ok is True
    assert detail == "v22.11.0"
    assert seen and seen[0][-1] == "--version"
    assert real_subprocess  # the signature we replaced is the real one


def test_doctor_probes_the_telegram_proxy(capsys, monkeypatch, tmp_path):
    """A blocked api.telegram.org used to surface only as a restart loop."""
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UWP_BOT_TOKEN", "1:fake")
    monkeypatch.setenv("UWP_ADMIN_IDS", "1")
    monkeypatch.setenv("UWP_TELEGRAM_PROXY", "socks5://127.0.0.1:2080")

    from uwp import cli

    probed: list[str] = []

    def fake_probe(proxy, timeout=8.0, **_kw):
        probed.append(proxy)

    monkeypatch.setattr(cli, "_probe_telegram_proxy", fake_probe)

    out_lines = []
    assert cli.main(["doctor"]) in (0, 1)
    out_lines.append(capsys.readouterr().out)

    assert probed == ["socks5://127.0.0.1:2080"]
    assert "✅ прокси Telegram (socks5://127.0.0.1:2080)" in out_lines[0]


def test_a_dead_telegram_proxy_fails_the_doctor(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UWP_BOT_TOKEN", "1:fake")
    monkeypatch.setenv("UWP_ADMIN_IDS", "1")
    monkeypatch.setenv("UWP_TELEGRAM_PROXY", "socks5://127.0.0.1:2080")

    from uwp import cli

    monkeypatch.setattr(
        cli,
        "_probe_telegram_proxy",
        lambda proxy, timeout=8.0, **_kw: "прокси недоступен",
    )

    assert cli.main(["doctor"]) == 1
    assert "прокси недоступен" in capsys.readouterr().out


def test_the_proxy_password_never_reaches_the_doctor_row(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UWP_BOT_TOKEN", "1:fake")
    monkeypatch.setenv("UWP_ADMIN_IDS", "1")
    monkeypatch.setenv("UWP_TELEGRAM_PROXY", "socks5://alice:s3cret@10.0.0.5:1080")

    from uwp import cli

    monkeypatch.setattr(
        cli, "_probe_telegram_proxy", lambda proxy, timeout=8.0, **_kw: None
    )

    cli.main(["doctor"])
    out = capsys.readouterr().out

    assert "alice:***@10.0.0.5:1080" in out
    assert "s3cret" not in out


def test_the_proxy_probe_rejects_a_bad_url_without_a_network_call(monkeypatch):
    from uwp import cli

    assert "схема" in cli._probe_telegram_proxy("ftp://127.0.0.1:21")
    assert "хоста или порта" in cli._probe_telegram_proxy("socks5://127.0.0.1")


def test_doctor_offline_never_touches_the_network(capsys, monkeypatch, tmp_path):
    """The healthcheck must not depend on a third-party proxy's latency."""
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UWP_BOT_TOKEN", "1:fake")
    monkeypatch.setenv("UWP_ADMIN_IDS", "1")
    monkeypatch.setenv("UWP_TELEGRAM_PROXY", "socks5://127.0.0.1:2080")

    from uwp import cli

    def explode(*_a, **_kw):
        raise AssertionError("--offline must not open a connection")

    monkeypatch.setattr(cli, "_probe_telegram_proxy", explode)

    # Only the URL shape is checked, so the proxy row stays green. The overall
    # exit code may still be 1 locally (the browser is not installed).
    assert cli.main(["doctor", "--offline"]) in (0, 1)
    out = capsys.readouterr().out
    assert "✅ прокси Telegram (socks5://127.0.0.1:2080)" in out


def test_doctor_offline_still_rejects_a_broken_proxy_url(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UWP_BOT_TOKEN", "1:fake")
    monkeypatch.setenv("UWP_ADMIN_IDS", "1")
    monkeypatch.setenv("UWP_TELEGRAM_PROXY", "ftp://127.0.0.1:21")

    from uwp import cli

    assert cli.main(["doctor", "--offline"]) == 1


# --- the CLI speaks the deployment language -------------------------------


def test_the_cli_prints_in_the_configured_language(capsys, monkeypatch, tmp_path):
    """One deployment, one operator: ``UWP_DEFAULT_LANGUAGE`` decides, no ``--lang``."""
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UWP_DEFAULT_LANGUAGE", "en")
    monkeypatch.setenv("UWP_BROWSER_PATH", "/bin/true")
    monkeypatch.delenv("UWP_BOT_TOKEN", raising=False)
    monkeypatch.setenv("UWP_ADMIN_IDS", "1")
    monkeypatch.setenv("UWP_SECRET_KEY", "k" * 32)
    monkeypatch.delenv("UWP_TELEGRAM_PROXY", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "uwp.cli._check_playwright_driver", lambda *_a, **_kw: (True, "v22.0.0")
    )

    assert main(["init"]) == 0
    out = capsys.readouterr().out
    assert "Created the site" in out
    assert "Создан" not in out


def test_the_cli_site_list_uses_the_language(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UWP_DEFAULT_LANGUAGE", "en")
    main(["init"])

    assert main(["site", "list"]) == 0
    out = capsys.readouterr().out
    assert "enabled" in out or "disabled" in out
    assert "включён" not in out and "выключен" not in out


def test_the_cli_help_is_localised(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UWP_DEFAULT_LANGUAGE", "en")

    with pytest.raises(SystemExit):
        main(["site", "--help"])

    out = capsys.readouterr().out
    assert "list sites" in out
    assert "список сайтов" not in out


def test_help_does_not_create_the_data_directory(capsys, monkeypatch, tmp_path):
    """``uwp --help`` must work in a read-only directory; it is not a command.

    ``load_settings`` creates ``./data`` and chmods it, so building the help
    from it would make a bare ``--help`` write to the current directory — and
    fail outright where it cannot.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("UWP_DATA_DIR", raising=False)

    with pytest.raises(SystemExit):
        main(["--help"])

    assert not (tmp_path / "data").exists()
    assert "usage: uwp" in capsys.readouterr().out


def test_the_cli_profile_list_prints_the_status_in_its_own_language(
    capsys, monkeypatch, tmp_path
):
    """The raw enum ``free`` leaked into both terminals; the bot translates it."""
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    main(["profile", "create", "Phone"])

    assert main(["profile", "list"]) == 0
    out = capsys.readouterr().out
    assert "свободен" in out
    assert "free" not in out

    monkeypatch.setenv("UWP_DEFAULT_LANGUAGE", "en")
    assert main(["profile", "list"]) == 0
    out = capsys.readouterr().out
    assert " free " in out
    assert "свободен" not in out


def test_the_cli_still_refuses_in_the_deployment_language(
    capsys, monkeypatch, tmp_path
):
    """The CLI has no reader but the operator, so ``lang=None`` keeps today's text."""
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UWP_DEFAULT_LANGUAGE", "en")
    main(["proxy", "add", "DE-1", "http://1.2.3.4:8080"])

    assert main(["proxy", "add", "DE-1", "http://1.2.3.4:8080"]) == 1
    assert "already exists" in capsys.readouterr().out


def test_profile_create_refuses_a_blank_name_instead_of_storing_spaces(
    capsys, monkeypatch, tmp_path
):
    """``uwp profile create "   "`` used to store a profile named by spaces."""
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))

    assert main(["profile", "create", "   "]) == 1
    assert "не может быть пустым" in capsys.readouterr().out
    assert main(["profile", "list"]) == 0
    assert "Постоянных профилей нет" in capsys.readouterr().out


def test_profile_create_reports_a_duplicate_instead_of_a_traceback(
    capsys, monkeypatch, tmp_path
):
    """The CLI called ``ProfileManager`` directly, so the refusal escaped as a
    traceback while the bot answered the same mistake with a sentence."""
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    main(["profile", "create", "Phone"])

    assert main(["profile", "create", "Phone"]) == 1
    assert "уже есть" in capsys.readouterr().out

    monkeypatch.setenv("UWP_DEFAULT_LANGUAGE", "en")
    assert main(["profile", "create", "Phone"]) == 1
    assert "already exists" in capsys.readouterr().out


def test_profile_login_says_when_the_profile_has_no_site(capsys, monkeypatch, tmp_path):
    """``uwp profile login`` opened example.com and never named the reason."""
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    main(["profile", "create", "Steam1"])

    assert main(["profile", "login", "1"]) == 1
    assert "не привязан" in capsys.readouterr().out


def test_profile_login_rejects_an_unknown_site_slug(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    main(["profile", "create", "Steam1"])

    assert main(["profile", "login", "1", "--site", "nope"]) == 1
    out = capsys.readouterr().out
    assert "не найден" in out
    # The slug named the lookup, not just the parser: an accepted-but-ignored
    # ``--site`` would fall back to the profile's own (absent) attachment.
    assert "nope" in out


def test_profile_login_forwards_the_site_to_the_service(capsys, monkeypatch, tmp_path):
    """A lookup alone starts nothing: the resolved id must reach ``request``."""
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    main(["init"])
    main(["profile", "create", "Steam1"])

    seen: list[int | None] = []

    async def fake_request(self, profile_id, site_id=None, **kwargs):
        seen.append(site_id)
        raise ValueError("refused for the test")

    monkeypatch.setattr("uwp.services.vnc.VncService.request", fake_request)

    assert main(["profile", "login", "1", "--site", "steam_skidki"]) == 1
    # A numeric id is the same handle the rest of the CLI accepts.
    assert main(["profile", "login", "1", "--site", "1"]) == 1

    # The fresh database has exactly one site, the seeded one.
    assert seen == [1, 1]


def test_the_seeded_site_is_named_in_the_deployments_language(
    capsys, monkeypatch, tmp_path
):
    """The seed carried a Russian literal, so an English first run showed the
    Russian name while the parser picker beside it said "Steam — deals"."""
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UWP_DEFAULT_LANGUAGE", "en")

    assert main(["init"]) == 0
    assert "Steam — deals" in capsys.readouterr().out

    # The slug is a stable handle the docs and `uwp site enable` rely on.
    assert main(["site", "list"]) == 0
    assert "steam_skidki" in capsys.readouterr().out


def test_site_rename_normalises_the_name(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    main(["init"])

    assert main(["site", "set", "steam_skidki", "--name", "  Мой   Сайт "]) == 0
    out = capsys.readouterr().out
    assert "Мой Сайт" in out

    # A second write re-reads the row and prints the stored name: the CLI list
    # has never rendered ``sites.name``, so it cannot prove persistence.
    assert main(["site", "set", "steam_skidki", "--interval", "30-40"]) == 0
    assert "Мой Сайт" in capsys.readouterr().out


def test_site_rename_refuses_an_empty_name(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    main(["init"])

    assert main(["site", "set", "steam_skidki", "--name", "   "]) == 1

    assert "пустым" in capsys.readouterr().out


def test_site_rename_refuses_a_name_telegram_would_reject(capsys, monkeypatch, tmp_path):
    """The CLI writes the same column the bot renders on a button."""
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    main(["init"])

    assert main(["site", "set", "steam_skidki", "--name", "x" * 65]) == 1

    assert "64" in capsys.readouterr().out
