"""Every README has a Russian twin, and the twins stay in step.

The repository documents itself in English (GitHub renders ``README.md``) with a
Russian copy beside it. Two translations drift the moment someone edits one side,
and a stale translation quietly lies: it describes a setting that no longer
exists, or omits a warning. Section counts and heading order are the cheap
structural signal; a broken cross-link is the other.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

PAIRS = [
    ("README.md", "README.ru.md"),
    ("docs/README.md", "docs/README.ru.md"),
    ("docker/README-seccomp.md", "docker/README-seccomp.ru.md"),
    ("src/uwp/parsers/steam_deals/README.md", "src/uwp/parsers/steam_deals/README.ru.md"),
    (
        "src/uwp/parsers/soundcloud_trending/README.md",
        "src/uwp/parsers/soundcloud_trending/README.ru.md",
    ),
    ("SECURITY.md", "SECURITY.ru.md"),
    ("THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.ru.md"),
    ("docs/install.md", "docs/install.ru.md"),
    ("DISCLAIMER.md", "DISCLAIMER.ru.md"),
]


def _headings(path: Path) -> list[str]:
    return [
        line.rstrip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith(("## ", "### "))
    ]


@pytest.mark.parametrize(("english", "russian"), PAIRS)
def test_the_russian_twin_exists(english: str, russian: str) -> None:
    assert (ROOT / english).is_file()
    assert (ROOT / russian).is_file()


@pytest.mark.parametrize(("english", "russian"), PAIRS)
def test_both_versions_have_the_same_sections(english: str, russian: str) -> None:
    """A section added to one side only is a translation that lies by omission."""
    en = _headings(ROOT / english)
    ru = _headings(ROOT / russian)

    assert len(en) == len(ru), f"{english} has {len(en)} sections, {russian} has {len(ru)}"


@pytest.mark.parametrize(("english", "russian"), PAIRS)
def test_each_version_links_to_the_other(english: str, russian: str) -> None:
    """Without the header link a reader lands in the wrong language for good."""
    en_text = (ROOT / english).read_text(encoding="utf-8")
    ru_text = (ROOT / russian).read_text(encoding="utf-8")
    en_name = Path(english).name
    ru_name = Path(russian).name

    assert ru_name in en_text, f"{english} does not link to {ru_name}"
    assert en_name in ru_text, f"{russian} does not link to {en_name}"


@pytest.mark.parametrize(("english", "russian"), PAIRS)
def test_cross_links_resolve(english: str, russian: str) -> None:
    """A translated link is the easiest thing to get wrong and never notice."""
    for name in (english, russian):
        path = ROOT / name
        text = path.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)#:]+?)\)", text):
            if target.startswith("http"):
                continue
            assert (path.parent / target).exists(), f"{name} links to missing {target}"


@pytest.mark.parametrize(("english", "russian"), PAIRS)
def test_code_fences_are_balanced(english: str, russian: str) -> None:
    """An odd fence swallows the rest of the page on GitHub."""
    for name in (english, russian):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert text.count("```") % 2 == 0, f"{name} has an unbalanced code fence"


def test_the_english_readme_carries_the_numbers_that_matter():
    """Pinned facts: a translation is where a silent 6000→6001 edit hides."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    for fact in ("6000", "6100", "6001", "0700", "0600", "umask 077", "PBKDF2"):
        assert fact in text


@pytest.mark.parametrize("name", ["README.md", "README.ru.md"])
def test_the_quick_start_clones_the_real_repository(name: str):
    """``git clone <repo>`` is a placeholder that survives until someone runs it.

    The command is the first thing a reader copies, so a leftover angle-bracket
    placeholder is the most expensive documentation bug in the file.
    """
    text = (ROOT / name).read_text(encoding="utf-8")

    assert "git clone" in text, f"{name} lost its quick start"
    assert "git clone <" not in text, f"{name} still has a clone placeholder"
    assert "https://github.com/danilruntime/UltimateWebParser" in text


def _heading_levels(path: Path) -> list[int]:
    return [
        len(line) - len(line.lstrip("#"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith(("## ", "### "))
    ]


@pytest.mark.parametrize(("english", "russian"), PAIRS)
def test_the_sections_appear_in_the_same_order(english: str, russian: str) -> None:
    """Equal counts still allow a swap; the nesting has to line up too."""
    assert _heading_levels(ROOT / english) == _heading_levels(ROOT / russian)


@pytest.mark.parametrize(("english", "russian"), PAIRS)
def test_both_versions_have_the_same_number_of_code_blocks(english: str, russian: str) -> None:
    """A block that survives in one language only is usually a lost example."""
    en = (ROOT / english).read_text(encoding="utf-8").count("```")
    ru = (ROOT / russian).read_text(encoding="utf-8").count("```")

    assert en == ru, f"{english} has {en} fences, {russian} has {ru}"


def _quoted_labels(lang):
    from uwp.bot import keyboards, texts
    from uwp.ods import new_column_title

    return [
        keyboards.BTN_SITES[lang],
        keyboards.BTN_PROFILES[lang],
        keyboards.BTN_PROXIES[lang],
        keyboards.BTN_HELP[lang],
        keyboards.BTN_LANGUAGE[lang],
        texts.add_site(lang),
        texts.delete(lang),
        texts.run_now(lang),
        texts.settings(lang),
        texts.toggle_off(lang),
        texts.toggle_on(lang),
        new_column_title(lang),
    ]


def test_quoted_bot_labels_match_the_code():
    """A translated label describes a button that does not exist.

    Measured: the first translation of this file translated the labels, and the
    documented buttons no longer existed anywhere in the bot. The English page
    therefore has to quote the English bot, and the Russian page the Russian
    one: a label is a screenshot of the interface, not prose.
    """
    from uwp.i18n import Lang

    for name, lang in (("README.md", Lang.en), ("README.ru.md", Lang.ru)):
        text = (ROOT / name).read_text(encoding="utf-8")
        missing = [label for label in _quoted_labels(lang) if label not in text]
        assert missing == [], f"{name} does not quote {missing}"


@pytest.mark.parametrize(("english", "russian"), PAIRS)
def test_the_kiosk_caption_matches_the_page_in_the_picture(english: str, russian: str):
    """A caption that names a different page than the screenshot is a lie.

    The screenshot was recaptured on the sign-in page while the caption still
    said "the Steam deals page" — a reader comparing the two sees the form, not
    the list, and stops trusting the rest of the paragraph. Measured before the
    fix: `docs/images/vnc-desktop.png` showed `store.steampowered.com/login/`.
    """
    for name in (english, russian):
        text = (ROOT / name).read_text(encoding="utf-8")
        # Every caption directly under the kiosk image, as written in both twins.
        for line in text.splitlines():
            if "vnc-desktop.png" in line and line.lstrip().startswith("!"):
                caption = line.lower()
                names_sign_in = any(
                    word in caption for word in ("sign-in", "sign in", "login", "вход")
                )
                assert names_sign_in, (
                    f"{name}: the kiosk image alt text must name the sign-in page"
                )
