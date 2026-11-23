"""The install twins must contain commands that actually run.

Both defects pinned here were measured on a real checkout, in both languages:

* ``docker compose logs -f bot`` answered ``no such service: bot`` — a bare
  ``docker compose`` reads ``compose.yaml``, where the only service is ``uwp``;
* ``docker compose exec uwp-worker ...`` answered ``service "uwp-worker" is not
  running`` — that is the ``container_name``, and ``exec`` wants the service
  name, which is ``worker``.

A third defect is about the key: ``UWP_SECRET_KEY=$(openssl rand -hex 32)`` in a
``.env`` file is not expanded by anything (Compose reads the file as plain
text), so the literal 23-character string becomes the key, passes every length
check and decrypts nothing after a restart.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]
PAIRS = [("docs/install.md", "docs/install.ru.md")]

EXEC = re.compile(r"\bexec\s+(?:-T\s+)?([A-Za-z0-9_.-]+)")
DOTENV_ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Z][A-Z0-9_]*)=\$[({]", re.MULTILINE)


def _services() -> set[str]:
    names: set[str] = set()
    for name in ("compose.yaml", "compose.split.yaml"):
        cfg = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))
        names |= set(cfg["services"])
    return names


def _images() -> set[str]:
    """Images declared by the two base files.

    The hostnet overlay only overrides networking and uses the Compose-specific
    ``!reset`` tag, which a plain ``yaml.safe_load`` cannot parse; it declares no
    image of its own.
    """
    images: set[str] = set()
    for name in ("compose.yaml", "compose.split.yaml"):
        cfg = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))
        for service in cfg["services"].values():
            if "image" in service:
                images.add(service["image"])
    return images


@pytest.mark.parametrize(("english", "russian"), PAIRS)
def test_a_dotenv_line_is_never_a_shell_assignment(english, russian):
    """.env is read by Compose, not by a shell: ``$(...)`` stays literal there."""
    for name in (english, russian):
        text = (ROOT / name).read_text(encoding="utf-8")
        offenders = DOTENV_ASSIGNMENT.findall(text)
        assert offenders == [], f"{name} stores a literal substitution: {offenders}"


@pytest.mark.parametrize(("english", "russian"), PAIRS)
def test_documented_exec_targets_are_compose_services(english, russian):
    """``exec`` takes a service, so a container name fails with "not running"."""
    services = _services()
    for name in (english, russian):
        text = (ROOT / name).read_text(encoding="utf-8")
        targets = set(EXEC.findall(text))
        assert targets <= services, (
            f"{name} runs `exec {sorted(targets - services)}`; "
            f"the services are {sorted(services)}"
        )
        assert targets, f"{name} documents no `docker compose exec` at all"


@pytest.mark.parametrize(("english", "russian"), PAIRS)
def test_a_bare_docker_compose_never_names_a_split_service(english, russian):
    """Without ``-f compose.split.yaml`` the split services do not exist."""
    split_only = _services() - {"uwp"}
    for name in (english, russian):
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
            if "docker compose" not in line or "compose.split.yaml" in line:
                continue
            if line.lstrip().startswith("$compose"):
                continue
            words = set(re.findall(r"[A-Za-z0-9_.-]+", line))
            assert not (words & split_only), f"{name} needs -f: {line.strip()}"


@pytest.mark.parametrize(("english", "russian"), PAIRS)
def test_the_uninstall_note_names_a_real_image(english, russian):
    images = _images()
    for name in (english, russian):
        text = (ROOT / name).read_text(encoding="utf-8")
        removed = set(re.findall(r"docker image rm ([^\s`]+)", text))
        assert removed <= images, (
            f"{name} removes {sorted(removed - images)}; "
            f"the compose files build {sorted(images)}"
        )
