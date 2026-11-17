"""The parser contract itself: ``Column`` defaults and the translated header."""
from typing import ClassVar

import pytest

from uwp.i18n import Lang
from uwp.parsers.base import BaseParser, Column, _EmptySettings


def test_a_column_is_tracked_by_default():
    column = Column("title", "Название")

    assert column.volatile is False


def test_a_column_can_be_declared_volatile():
    column = Column("views", "Просмотры", 12, volatile=True)

    assert column.volatile is True


def test_a_column_without_a_key_keeps_its_own_title():
    """A third-party parser sets no ``title_key``; its header must still render.

    ``title`` stays required and keeps whatever the author wrote, in either
    language, so an already-installed parser keeps producing a usable report.
    """
    column = Column("title", "Название")

    assert column.title_key is None
    assert column.title_for(Lang.en) == "Название"
    assert column.title_for(Lang.ru) == "Название"


def test_a_column_with_a_key_is_translated_per_language():
    column = Column("url", "Ссылка", 45, title_key="parser.steam_deals.column.url")

    assert column.title_for(Lang.ru) == "Ссылка"
    assert column.title_for(Lang.en) == "Link"


def test_a_missing_column_key_raises_instead_of_leaking_the_other_language():
    column = Column("url", "Ссылка", title_key="parser.steam_deals.column.missing")

    with pytest.raises(KeyError, match="parser.steam_deals.column.missing"):
        column.title_for(Lang.en)


class _KeyedParser(BaseParser):
    key, url = "keyed", "https://example.com/"
    title, description = "Заголовок", "Описание"
    title_key = "parser.steam_deals.title"
    description_key = "parser.steam_deals.description"
    settings_model = _EmptySettings
    columns: ClassVar[list[Column]] = []

    async def run(self, ctx):  # pragma: no cover - never called here
        raise NotImplementedError


class _BareParser(BaseParser):
    key, url = "bare", "https://example.com/"
    title, description = "Bare title", "Bare description"
    settings_model = _EmptySettings
    columns: ClassVar[list[Column]] = []

    async def run(self, ctx):  # pragma: no cover - never called here
        raise NotImplementedError


def test_the_parser_title_and_description_are_translated():
    parser = _KeyedParser()

    assert parser.title_for(Lang.ru) == "Steam — скидки"
    assert parser.title_for(Lang.en) == "Steam — deals"
    assert parser.description_for(Lang.en).startswith("Discounted games")


def test_a_parser_without_keys_keeps_its_own_title():
    """The seeded site and every third-party parser rely on this fallback."""
    parser = _BareParser()

    assert parser.title_for(Lang.en) == "Bare title"
    assert parser.description_for(Lang.en) == "Bare description"
