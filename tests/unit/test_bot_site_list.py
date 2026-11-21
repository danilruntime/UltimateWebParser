"""One site list, whichever button opened it.

Measured: the «🌐 Сайты» reply button drew a bold name with the next run on its
own indented line, while «⬅️ Назад» from a site card drew ``name — след. …``.
The same list in two shapes reads as if the back button changed the data, so
both paths must render the very same text.
"""

from datetime import UTC, datetime

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import User

from uwp.bot import texts
from uwp.bot.handlers import common
from uwp.bot.handlers import sites as sites_handlers
from uwp.domain import Site
from uwp.i18n import Lang

WEDNESDAY = datetime(2026, 11, 21, 13, 13, tzinfo=UTC)


class _Sites:
    def __init__(self, sites):
        self._sites = sites

    async def list(self):
        return self._sites


class _Deps:
    def __init__(self, sites):
        self.sites = _Sites(sites)
        self.registry = object()
        self.profiles = _Sites([])
        self.proxies = _Sites([])
        self.runs = _Sites([])


class _Settings:
    tz = "Europe/Moscow"


class _Message:
    def __init__(self, text=None):
        self.text = text
        self.from_user = User(id=1, is_bot=False, first_name="x")
        self.answers = []
        self.edits = []
        self.markups = []

    async def answer(self, text=None, reply_markup=None, **kw):
        self.answers.append(text)
        self.markups.append(reply_markup)

    async def edit_text(self, text=None, reply_markup=None, **kw):
        self.edits.append(text)
        self.markups.append(reply_markup)


class _Callback:
    def __init__(self, data):
        self.data = data
        self.from_user = User(id=1, is_bot=False, first_name="x")
        self.message = _Message()
        self.answers = []

    async def answer(self, text=None, **kw):
        self.answers.append((text, kw))


def _state():
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=1, user_id=1),
    )


def _site(site_id, name, *, enabled=True, next_run_at=WEDNESDAY):
    return Site(
        id=site_id,
        slug=f"s{site_id}",
        name=name,
        parser_key="steam_deals",
        enabled=enabled,
        interval_min_min=1,
        interval_max_min=2,
        next_run_at=next_run_at,
    )


def _handler(router, table, name):
    return next(
        observer.callback
        for observer in getattr(router, table).handlers
        if observer.callback.__name__ == name
    )


@pytest.mark.parametrize("lang", [Lang.ru, Lang.en])
async def test_the_menu_button_and_the_back_button_render_the_same_screen(lang):
    """Two renderers is how the list drifted apart; one screen ends that."""
    sites = [_site(1, "Steam скидки"), _site(2, "Steam новинки", enabled=False)]
    deps = _Deps(sites)
    settings = _Settings()

    message = _Message()
    await _handler(
        common.build_router(settings, deps), "message", "menu_sites"
    )(message, state=_state(), lang=lang)

    callback = _Callback("sites:list")
    await _handler(
        sites_handlers.build_router(settings, deps), "callback_query", "show_list"
    )(callback, state=_state(), lang=lang)

    assert callback.message.edits == [message.answers[-1]]


def test_the_next_run_sits_on_its_own_indented_line():
    """The shape the reply button has always drawn: name, then `   следующий:`."""
    sites = [_site(1, "Steam скидки")]

    text = texts.sites_list(Lang.ru, sites, _Settings())

    assert text == (
        "🌐 Сайты\n\n"
        "🟢 <b>Steam скидки</b>\n"
        "   следующий: 21.11.2026 16:13"
    )


def test_a_site_without_a_next_run_gets_no_placeholder_line():
    """«— след. —» was the back button's own invention; a missing run is silence."""
    sites = [_site(1, "Steam скидки", enabled=False, next_run_at=None)]

    text = texts.sites_list(Lang.en, sites, _Settings())

    assert text == "🌐 Sites\n\n🔴 <b>Steam скидки</b>"
