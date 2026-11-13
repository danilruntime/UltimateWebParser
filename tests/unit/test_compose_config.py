"""Tests for the Docker assets: compose wiring and pinned browser build."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]


def test_combined_compose_has_ports_and_volume():
    cfg = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    svc = cfg["services"]["uwp"]

    assert "6000-6100:6000-6100" in svc["ports"]
    # ``:z`` keeps the volume usable when SELinux is enforcing.
    assert "./data:/data:z" in svc["volumes"]
    assert svc["shm_size"] == "1gb"
    assert svc["restart"] == "unless-stopped"


def test_split_compose_worker_owns_vnc_ports():
    cfg = yaml.safe_load((ROOT / "compose.split.yaml").read_text(encoding="utf-8"))

    assert "6000-6100:6000-6100" in cfg["services"]["worker"]["ports"]
    assert "ports" not in cfg["services"]["bot"]
    assert "./data:/data:z" in cfg["services"]["worker"]["volumes"]
    assert cfg["services"]["bot"]["command"] == ["bot"]
    assert cfg["services"]["worker"]["command"] == ["worker"]


def test_dockerfile_pins_browser_sha():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "48eff860053efd9ad498d51370f0a1f59f983df39c02a72ad2a8682cdead4055" in text
    assert "150.0.7871.186" in text
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
    assert "UWP_NO_SANDBOX=true" in text


def test_entrypoint_execs_the_cli():
    text = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")

    assert 'exec uwp "$@"' in text
    assert "set -e" in text
    assert "setpriv" in text
    assert "chown -R" in text
    # An existing but unwritable volume must fail with a hint, not with
    # sqlite's "unable to open database file" traceback.
    assert "is not writable by uid" in text


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
