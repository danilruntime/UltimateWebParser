"""Tests for the Docker assets: compose wiring and pinned browser build."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]


def test_combined_compose_has_ports_and_volume():
    cfg = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    svc = cfg["services"]["uwp"]

    assert "127.0.0.1:6000-6100:6000-6100" in svc["ports"]
    # ``:z`` keeps the volume usable when SELinux is enforcing.
    assert "./data:/data:z" in svc["volumes"]
    assert svc["shm_size"] == "1gb"
    assert svc["restart"] == "unless-stopped"


def test_split_compose_worker_owns_vnc_ports():
    cfg = yaml.safe_load((ROOT / "compose.split.yaml").read_text(encoding="utf-8"))

    assert "127.0.0.1:6000-6100:6000-6100" in cfg["services"]["worker"]["ports"]
    assert "ports" not in cfg["services"]["bot"]
    assert "./data:/data:z" in cfg["services"]["worker"]["volumes"]
    assert cfg["services"]["bot"]["command"] == ["bot"]
    assert cfg["services"]["worker"]["command"] == ["worker"]


def test_split_worker_does_not_receive_the_bot_token():
    """Only the bot polls Telegram; the worker has no use for the token.

    Both services read the same ``.env``, so the worker has to blank the token
    explicitly. Leaving it in place puts a live secret into a second process
    that also runs a browser.
    """
    cfg = yaml.safe_load((ROOT / "compose.split.yaml").read_text(encoding="utf-8"))

    assert cfg["services"]["worker"]["environment"]["UWP_BOT_TOKEN"] == ""
    # The bot keeps whatever ``.env`` provides.
    assert "UWP_BOT_TOKEN" not in cfg["services"]["bot"].get("environment", {})


def test_the_split_worker_healthcheck_knows_it_has_no_token():
    """``doctor`` fails without the token unless it is told this is a worker."""
    cfg = yaml.safe_load((ROOT / "compose.split.yaml").read_text(encoding="utf-8"))

    worker_check = cfg["services"]["worker"]["healthcheck"]["test"]
    assert "--worker" in worker_check
    assert "--worker" not in cfg["services"]["bot"]["healthcheck"]["test"]


def test_dockerfile_pins_browser_sha():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "70d239830332e5820aa34dfcb284161cac0429eee25da642830afe04bda717f4" in text
    assert "148.0.7778.215" in text
    assert "sha256sum -c" in text


def test_dockerfile_installs_the_playwright_driver_libraries():
    """Without libatomic1 the bundled driver node cannot start at all.

    The symptom is a run failing with "Connection closed while reading from
    the driver" while every other check (including `doctor`) stays green, so
    pin the library here instead of rediscovering it from a traceback.
    """
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "libatomic1" in text


def test_dockerfile_uses_tini_and_unprivileged_user():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert 'ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]' in text
    assert 'CMD ["run"]' in text
    # The entrypoint starts as root to fix a fresh volume's ownership, then
    # drops to the unprivileged user before running the CLI.
    assert "USER uwp" not in text
    assert "UWP_BROWSER_PATH=/opt/uwp/browser/chrome" in text
    # The sandbox is on by default; ``UWP_NO_SANDBOX`` must appear only in the
    # comment that documents the escape hatch.
    assert "\n    UWP_NO_SANDBOX=true" not in text


def test_the_image_ships_the_seccomp_profile():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "docker/seccomp-uwp.json" in text
    assert (ROOT / "docker" / "seccomp-uwp.json").is_file()


def test_seccomp_profile_keeps_the_three_relaxations():
    """Docker's stock profile blocks Chromium's sandbox in three places.

    Regenerating the profile from upstream silently drops them, and the only
    symptom is ``No usable sandbox`` at runtime, so pin the three edits here.
    """
    profile = json.loads((ROOT / "docker" / "seccomp-uwp.json").read_text(encoding="utf-8"))
    rules = {tuple(sorted(rule.get("names") or [])): rule for rule in profile["syscalls"]}

    # The one added rule: unshare, but only CLONE_NEWNS (0x20000).
    unshare = rules[("unshare",)]
    assert unshare["action"] == "SCMP_ACT_ALLOW"
    assert unshare["args"] == [{"index": 0, "value": 131072, "op": "SCMP_CMP_MASKED_EQ"}]

    # ``clone`` must accept CLONE_NEWNS alone, not only the stock all-flags mask.
    clones = [r for r in profile["syscalls"] if (r.get("names") or []) == ["clone"]]
    x86 = next(r for r in clones if "s390" not in json.dumps(r.get("includes", {})))
    assert x86["args"][0]["value"] == 131072

    # ``clone3`` must be allowed, not ENOSYS.
    clone3 = [r for r in profile["syscalls"] if (r.get("names") or []) == ["clone3"]]
    assert [r["action"] for r in clone3] == ["SCMP_ACT_ALLOW"]


def test_compose_mounts_the_seccomp_profile():
    """Only the services that launch a browser need it."""
    for name in ("compose.yaml", "compose.split.yaml"):
        cfg = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))
        browser_services = {
            svc_name: svc
            for svc_name, svc in cfg["services"].items()
            if svc.get("command") in (None, ["run"], ["worker"])
        }
        assert browser_services, name
        for svc in browser_services.values():
            assert "seccomp=./docker/seccomp-uwp.json" in svc["security_opt"], name


def test_entrypoint_execs_the_cli():
    text = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")

    assert 'exec uwp "$@"' in text
    assert "set -e" in text
    assert "setpriv" in text
    assert "chown -R" in text
    # An existing but unwritable volume must fail with a hint, not with
    # sqlite's "unable to open database file" traceback.
    assert "is not writable by uid" in text


def test_entrypoint_chowns_every_directory_it_creates_as_root():
    """Measured: a `.cache` created by root and left out of the chown walk killed
    every run with ``TargetClosedError``.

    The entrypoint runs as root when ``./data`` is a fresh bind mount, so
    ``prepare_data_dir`` creates ``.cache`` and ``.config`` as root. Chromium
    then runs as uid 1000 with ``XDG_CACHE_HOME`` pointing there, cannot create
    its cache, and Playwright reports only "browser has been closed". The
    ownership check must cover the same list it creates — a directory that is
    created but not repaired is unreadable by the process that needs it.
    """
    text = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    created = _entrypoint_paths(text, "prepare_data_dir", "for path in")
    checked = _entrypoint_paths(text, "needs_chown", "for path in")

    assert created
    assert created <= checked, f"created but never chowned: {created - checked}"


def test_entrypoint_does_not_create_a_downloads_directory():
    """The login browser writes nothing, so there is no folder to prepare.

    Measured: a page-initiated click saved a file with no admin gesture, and
    ``download.restrictions`` did not stop it. The directory and the
    preference that filled it were removed together; leaving the folder would
    suggest downloads are supported.
    """
    text = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    created = _entrypoint_paths(text, "prepare_data_dir", "for path in")

    assert "/downloads" not in created


def _entrypoint_paths(text: str, marker: str, loop: str) -> set[str]:
    """The ``$DATA_DIR/...`` list of the ``for`` loop that follows ``marker``."""
    start = text.index(marker)
    loop_at = text.index(loop, start)
    # ``do`` as a shell keyword ends the list; a plain ``index("do")`` would
    # stop inside a directory name such as ``downloads``.
    end = text.index("; do", loop_at)
    return set(re.findall(r'"\$DATA_DIR(/[^"]*)?"', text[loop_at:end]))


def test_env_example_lists_the_required_variables():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")

    for name in (
        "UWP_BOT_TOKEN",
        "UWP_ADMIN_IDS",
        "UWP_SECRET_KEY",
        "UWP_DATA_DIR",
        "UWP_TZ",
        "UWP_VNC_PORT_MIN",
        "UWP_NO_SANDBOX",
    ):
        assert f"{name}=" in text


def test_env_example_documents_every_setting():
    """The README says «полный список» — a new setting must not silently skip it.

    A commented default (``# UWP_X=...``) counts as documented: ``UWP_NO_SANDBOX``
    and the VNC listen host are deliberately left at the image default.
    """
    from uwp.config import Settings

    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    documented = set(re.findall(r"^#?\s*(UWP_[A-Z0-9_]+)=", text, re.MULTILINE))

    expected = {f"UWP_{name.upper()}" for name in Settings.model_fields}
    assert expected - documented == set()


def test_env_example_documents_the_telegram_proxy():
    """A blocked api.telegram.org is the most likely first-run failure."""
    text = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "UWP_TELEGRAM_PROXY=" in text
    assert "socks5://" in text


def test_the_hostnet_overlay_keeps_novnc_on_loopback():
    """With ``network_mode: host`` no port publishing stands in front of it.

    websockify binds ``0.0.0.0`` in a bridged container on purpose (Docker's
    proxy dials the container address, not its loopback); that default would
    expose noVNC on every host interface here, so the overlay overrides it.
    """
    text = (ROOT / "compose.hostnet.yaml").read_text(encoding="utf-8")

    assert "UWP_VNC_LISTEN_HOST: 127.0.0.1" in text


def test_env_example_documents_the_private_bind_and_public_scheme():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "UWP_PUBLIC_SCHEME=http" in text
    assert "UWP_ARTIFACTS_RETENTION=20" in text
    assert "UWP_VNC_HISTORY_DAYS=7" in text


def test_hostnet_overlays_exist_for_a_loopback_proxy():
    """127.0.0.1 inside a bridged container is the container, not the host."""
    for name in ("compose.hostnet.yaml", "compose.split.hostnet.yaml"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "network_mode: host" in text
        # Compose merges sequences, so the published ports must be reset.
        assert "!reset []" in text


def test_only_the_bot_process_needs_the_proxy_in_split_mode():
    """The worker never talks to Telegram, so it keeps bridged networking."""
    text = (ROOT / "compose.split.hostnet.yaml").read_text(encoding="utf-8")

    # ``!reset []`` is a Compose tag, so parse by hand rather than with yaml.
    services = text.split("services:", 1)[1]
    assert "bot:" in services
    assert "worker:" not in services


def test_compose_does_not_nest_tini_twice():
    """The image ENTRYPOINT already runs tini; `init: true` adds a second one.

    The old combination printed a TINI_SUBREAPER warning on every start, before
    any application line.
    """
    for name in ("compose.yaml", "compose.split.yaml"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "init: true" not in text


def test_no_vnc_port_is_published_on_all_interfaces():
    """noVNC speaks plain HTTP and authenticates with an 8-character password.

    Publishing 6000-6100 on ``0.0.0.0`` put an unencrypted desktop with a weak
    secret on the internet-facing address of the host, so the default bind is
    loopback; a remote admin is expected to tunnel or front it with TLS.
    """
    for name in ("compose.yaml", "compose.split.yaml"):
        cfg = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))
        published = [
            port
            for svc in cfg["services"].values()
            for port in svc.get("ports", [])
        ]
        vnc = [port for port in published if "6000-6100" in str(port)]
        assert vnc, name
        for port in vnc:
            assert str(port).startswith("127.0.0.1:"), f"{name}: {port}"


def test_the_image_healthcheck_does_not_probe_the_telegram_proxy():
    """Compose passes ``--offline``; the image's own HEALTHCHECK must agree.

    The online probe crosses a third-party proxy and was measured at ~12 s;
    with ``--interval=60s`` one slow probe marks a healthy container unhealthy,
    and ``docs/README.md`` already promises the offline form.
    """
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    check = next(line for line in text.splitlines() if "doctor" in line and "CMD" in line)

    assert "--offline" in check
    assert "--quiet" in check
