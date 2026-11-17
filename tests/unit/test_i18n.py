"""The catalogue is the only source of human-facing words, in both languages."""

import re
import sys
import types

import pytest

from uwp import i18n
from uwp.i18n import (
    DEFAULT_LANG,
    SUPPORTED,
    Lang,
    catalog_keys,
    has_key,
    normalize,
    t,
)

PLACEHOLDER = re.compile(r"\{(\w+)\}")


@pytest.fixture(autouse=True)
def _fresh_catalogue():
    """Tests that swap the domain list must not read the previous merge."""
    i18n._catalog.cache_clear()
    yield
    i18n._catalog.cache_clear()


def test_normalize_maps_only_the_two_supported_languages():
    assert normalize("ru") is Lang.ru
    assert normalize("ru-RU") is Lang.ru
    assert normalize("en-GB") is Lang.en
    assert normalize("de") is None
    assert normalize("") is None
    assert normalize(None) is None
    # Ukrainian is not Russian: guessing would serve the wrong language.
    assert normalize("uk") is None


def test_the_default_language_is_russian_and_both_are_supported():
    assert DEFAULT_LANG is Lang.ru
    assert SUPPORTED == ("ru", "en")


def test_t_raises_on_an_unknown_key():
    with pytest.raises(KeyError, match="bot.missing"):
        t(Lang.ru, "bot.missing")


def test_t_formats_placeholders_for_both_languages():
    assert t(Lang.ru, "bot.error.not_found", name="Сайт") == "Сайт не найден"
    assert t(Lang.en, "bot.error.not_found", name="Site") == "Site not found"


def test_t_without_kwargs_returns_the_template_verbatim():
    """A literal ``{`` must not reach ``str.format`` and raise there."""
    assert t(Lang.ru, "bot.error.not_found") == "{name} не найден"


def test_has_key_reports_what_the_catalogue_holds():
    assert has_key(Lang.ru, "bot.error.not_found") is True
    assert has_key("en", "cli.ok") is True
    assert has_key(Lang.en, "bot.missing") is False


def test_the_catalogue_is_not_empty():
    """An empty catalogue would make every parity assertion vacuously true."""
    keys = catalog_keys()
    assert keys[Lang.ru], "refusing to pass on an empty catalogue"


def test_every_key_exists_in_both_languages():
    keys = catalog_keys()
    assert keys[Lang.ru] == keys[Lang.en]


def test_placeholders_match_between_languages():
    """``{name}`` lost in translation is a format-time crash in a live chat."""
    for key in catalog_keys()[Lang.ru]:
        ru = PLACEHOLDER.findall(t(Lang.ru, key))
        en = PLACEHOLDER.findall(t(Lang.en, key))
        assert sorted(ru) == sorted(en), key


def test_several_domains_may_not_define_the_same_key(monkeypatch):
    """One domain silently overwriting another is a translation bug.

    Nothing fails at import time — the chat simply shows a string from the
    wrong area — so the merge has to refuse the duplicate outright.
    """
    monkeypatch.setattr(i18n, "_DOMAINS", ("_dup_a", "_dup_b"))
    for name, text in (("_dup_a", "A"), ("_dup_b", "B")):
        module = types.ModuleType(f"uwp.i18n.{name}")
        module.CATALOG = {Lang.ru: {"dup.key": text}, Lang.en: {"dup.key": text}}
        monkeypatch.setitem(sys.modules, f"uwp.i18n.{name}", module)

    with pytest.raises(RuntimeError, match="dup.key"):
        catalog_keys()


def test_an_empty_catalogue_is_refused(monkeypatch):
    monkeypatch.setattr(i18n, "_DOMAINS", ())

    with pytest.raises(RuntimeError, match="empty"):
        catalog_keys()
