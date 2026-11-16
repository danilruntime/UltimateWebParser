"""Third-party attribution must stay complete, not just exist.

``THIRD_PARTY_NOTICES.md`` carries a legal obligation: Apache-2.0 §4(a) wants the
license text to reach whoever receives ``docker/seccomp-uwp.json``, and that file
is a derivative of Docker's stock profile. A new vendored file that skips the
document, or an edited notice that drops the Apache text, breaks the obligation
silently — both are cheap to detect.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NOTICES = ROOT / "THIRD_PARTY_NOTICES.md"
APACHE = ROOT / "docker" / "seccomp-uwp.json"

# Files in ``docker/`` written by this project. Everything else there is either
# vendored or generated from a vendored source and must be attributed.
OURS = {"entrypoint.sh"}


def test_the_notices_are_linked_from_the_readme():
    """A notices file nobody links to is not "provided" to the recipient."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "THIRD_PARTY_NOTICES.md" in readme


def test_every_vendored_docker_file_is_attributed():
    text = NOTICES.read_text(encoding="utf-8")

    missing = [
        name
        for name in sorted(p.name for p in (ROOT / "docker").iterdir() if p.is_file())
        if name not in OURS and name not in text
    ]
    assert missing == []


def test_the_image_ships_the_notices_with_the_apache_file():
    """§4(a) binds whoever receives the work — that is the image, not the clone.

    ``seccomp-uwp.json`` is baked into ``/opt/uwp/seccomp/``; if the notices do
    not land beside it, the image redistributes Apache-2.0 code without its
    licence, even though the repository itself looks compliant.
    """
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY THIRD_PARTY_NOTICES.md /opt/uwp/seccomp/" in text


def test_the_notices_carry_the_full_apache_text():
    """§4(a) names "a copy of this License" — a link to apache.org is not one.

    Pinned on phrases that a summarised or truncated paste would lose, plus the
    appendix that distinguishes the canonical text from a partial copy.
    """
    text = NOTICES.read_text(encoding="utf-8")

    assert "Apache License" in text
    assert "Version 2.0, January 2004" in text
    assert "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION" in text
    assert "END OF TERMS AND CONDITIONS" in text
    assert "APPENDIX: How to apply the Apache License to your work" in text
    # The full grant runs to ~11 KB; anything much shorter is a summary.
    start = text.index("Apache License")
    assert len(text[start:]) > 10_000


def test_the_modified_apache_file_is_marked_as_modified():
    """§4(b): modified files must carry prominent notices of the change."""
    text = APACHE.read_text(encoding="utf-8")

    assert "comment" in text
    assert text.count("comment") >= 3


def test_the_notices_name_the_copyleft_packages_in_the_image():
    """x11vnc is GPL-2.0; the document's argument depends on naming it.

    If the image swaps the VNC stack, the "aggregation, not derivation" note
    must be revisited — this test is the reminder.
    """
    text = NOTICES.read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "x11vnc" in dockerfile
    assert "x11vnc" in text
    assert "GPL-2.0" in text


@pytest.mark.parametrize(
    "package",
    ["x11vnc", "novnc", "websockify", "xvfb", "fluxbox", "tini"],
)
def test_every_vnc_stack_package_is_named(package: str):
    """These are installed by name in the Dockerfile; all need attribution."""
    text = NOTICES.read_text(encoding="utf-8")

    assert re.search(rf"`{re.escape(package)}`", text), package
