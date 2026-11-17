"""The Apache appendix is quoted, not blank, and nobody should "fix" it.

A user reported the project's ``LICENSE`` as broken because
``THIRD_PARTY_NOTICES.md`` carries ``Copyright [yyyy] [name of copyright owner]``.
That line is not a forgotten placeholder: it is the verbatim Appendix A of the
licence that ships with ``docker/seccomp-uwp.json`` (Copyright Docker, Inc.), and
filling it in would misquote that licence. These tests pin the reasoning in
place so the next reader with the same report does not "repair" it again.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

NOTICES = ["THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.ru.md"]

# The licence body is the English original in both twins, so one anchor finds
# the appendix whatever language the surrounding document is written in.
APPENDIX_MARKER = "APPENDIX: How to apply the Apache License to your work"


def test_the_project_licence_names_the_holder():
    """The real MIT header; the appendix placeholder is not a substitute."""
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")

    assert "Copyright (c) 2026 danilruntime" in text
    assert "[yyyy]" not in text


@pytest.mark.parametrize("name", NOTICES)
def test_the_apache_appendix_placeholders_are_left_verbatim(name: str):
    """Exactly one placeholder, and it belongs to the quoted licence.

    Measured: the report that started this task pointed at the appendix line as
    if it were the project's own header. A copy moved above the appendix would
    read as project prose; a second copy would imply one of them is editable.
    """
    text = (ROOT / name).read_text(encoding="utf-8")

    assert text.count("[yyyy]") == 1
    assert text.index("[yyyy]") > text.index(APPENDIX_MARKER)


def test_the_notice_explains_why_the_placeholder_stays():
    """The paragraph is the defence against the next "fix"; pin its substance.

    Whitespace and blockquote markers are normalised so re-wrapping the
    paragraph does not break the test — the substance is pinned, not the layout.
    """
    text = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    flat = " ".join(text.replace("> ", "").split())

    assert "not this project's copyright line and must not be filled in" in flat
    assert "`docker/seccomp-uwp.json`" in flat
    assert "Appendix A of the Apache License 2.0" in flat
