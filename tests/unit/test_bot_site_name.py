"""The bot asks for a site name when adding one and can rename a site."""

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import User

from uwp.bot import keyboards, texts
from uwp.bot.handlers import settings as settings_handlers
from uwp.bot.handlers import sites as sites_handlers
from uwp.db.database import Database
from uwp.db.repo.jobs import JobsRepo
from uwp.db.repo.sites import SitesRepo
from uwp.i18n import Lang
from uwp.parsers.registry import ParserRegistry
from uwp.scheduler import Scheduler
from uwp.services.sites import SiteService


def test_the_add_prompt_offers_the_parser_name_and_a_way_back():
    kb = keyboards.site_name_prompt_kb("steam_deals", "Steam — deals", Lang.en)

    flat = [b for row in kb.inline_keyboard for b in row]
    assert [b.callback_data for b in flat] == [
        "site:adddefault:steam_deals",
        "sites:list",
    ]
    assert "Steam — deals" in flat[0].text
    assert "Cancel" in flat[1].text


def test_the_add_prompt_is_translated():
    assert "назвать" in texts.ask_site_name(Lang.ru, "Steam — скидки").lower()
    assert "called" in texts.ask_site_name(Lang.en, "Steam — deals").lower()
    assert "название" in texts.site_name_saved(Lang.ru, "Моё").lower()
    assert "name" in texts.site_name_saved(Lang.en, "Mine").lower()


def test_the_keep_default_button_label_is_not_html_escaped():
    """An inline-button label is not parsed as HTML: ``&`` must stay ``&``.

    A parser title such as ``AT&T deals`` would otherwise reach the admin as
    ``AT&amp;T deals``, because ``keep_default_name`` used the message-text
    escaping on a label.
    """
    label = texts.keep_default_name(Lang.en, "AT&T deals")

    assert "AT&T" in label
    assert "&amp;" not in label


class _Message:
    def __init__(self, text=None):
        self.text = text
        self.from_user = User(id=1, is_bot=False, first_name="x")
        self.answers = []
        self.markups = []
        self.edits = []

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


def _handler(router, table, name):
    return next(
        observer.callback
        for observer in getattr(router, table).handlers
        if observer.callback.__name__ == name
    )


class _Settings:
    tz = "UTC"


@pytest.fixture
async def bot():
    db = await Database.in_memory()
    await db.migrate()
    sites, jobs = SitesRepo(db), JobsRepo(db)
    service = SiteService(
        sites, jobs, Scheduler(sites, jobs), registry=ParserRegistry.discover()
    )

    class _Deps:
        pass

    deps = _Deps()
    deps.sites = service
    deps.registry = service.registry
    try:
        sites_router = sites_handlers.build_router(_Settings(), deps)
        settings_router = settings_handlers.build_router(_Settings(), deps)
        yield (sites_router, settings_router), service, sites
    finally:
        await db.close()


async def test_pressing_an_parser_asks_for_a_name_and_creates_nothing(bot):
    (router, _settings_router), _service, sites = bot
    state = _state()
    callback = _Callback("site:add:steam_deals")

    await _handler(router, "callback_query", "add_site")(callback, state, lang=Lang.ru)

    assert await sites.list() == []
    assert await state.get_state() == sites_handlers.SiteNameInput.waiting.state
    assert "Steam — скидки" in callback.message.edits[-1]
    texts_buttons = [
        b.callback_data
        for row in callback.message.markups[-1].inline_keyboard
        for b in row
    ]
    assert "site:adddefault:steam_deals" in texts_buttons


async def test_a_typed_name_creates_the_site(bot):
    (router, _settings_router), _service, sites = bot
    state = _state()
    await _handler(router, "callback_query", "add_site")(
        _Callback("site:add:steam_deals"), state, lang=Lang.en
    )
    message = _Message(text="Steam US")

    await _handler(router, "message", "save_site_name")(message, state, lang=Lang.en)

    (site,) = await sites.list()
    assert site.name == "Steam US"
    assert message.answers[-1] == texts.site_added(Lang.en, "Steam US")
    assert await state.get_state() is None


async def test_a_blank_name_is_refused_and_the_wizard_stays_open(bot):
    (router, _settings_router), _service, sites = bot
    state = _state()
    await _handler(router, "callback_query", "add_site")(
        _Callback("site:add:steam_deals"), state, lang=Lang.ru
    )
    message = _Message(text="   ")

    await _handler(router, "message", "save_site_name")(message, state, lang=Lang.ru)

    assert await sites.list() == []
    assert "пустым" in message.answers[-1]
    assert await state.get_state() == sites_handlers.SiteNameInput.waiting.state


async def test_the_default_button_creates_the_parser_named_site_in_the_admins_language(bot):
    (router, _settings_router), _service, sites = bot
    state = _state()
    await _handler(router, "callback_query", "add_site")(
        _Callback("site:add:steam_deals"), state, lang=Lang.en
    )

    await _handler(router, "callback_query", "add_site_default")(
        _Callback("site:adddefault:steam_deals"), state, lang=Lang.en
    )

    (site,) = await sites.list()
    assert site.name == "Steam — deals"
    assert await state.get_state() is None


async def test_cancel_disarms_the_wizard_and_creates_nothing(bot):
    """Pressing Cancel must clear the state, not just redraw the list.

    Otherwise the prompt's ``sites:list`` button leaves ``SiteNameInput`` armed
    and the admin's next typed message — even a menu word — becomes a site.
    """
    (router, _settings_router), _service, sites = bot
    state = _state()
    await _handler(router, "callback_query", "add_site")(
        _Callback("site:add:steam_deals"), state, lang=Lang.en
    )
    callback = _Callback("sites:list")

    await _handler(router, "callback_query", "show_list")(callback, state, lang=Lang.en)

    assert await state.get_state() is None
    assert await sites.list() == []


async def test_the_name_button_asks_for_the_current_name(bot):
    (_sites_router, router), service, _sites = bot
    site = await service.create("steam_deals", name="Steam US")
    state = _state()
    callback = _Callback(f"set:name:{site.id}")

    await _handler(router, "callback_query", "settings_ask_name")(
        callback, state, lang=Lang.ru
    )

    assert "Steam US" in callback.message.answers[-1]
    assert await state.get_state() == settings_handlers.RenameInput.waiting.state


async def test_a_new_name_is_saved_and_the_settings_screen_comes_back(bot):
    (_sites_router, router), service, _sites = bot
    site = await service.create("steam_deals", name="Steam US")
    state = _state()
    await _handler(router, "callback_query", "settings_ask_name")(
        _Callback(f"set:name:{site.id}"), state, lang=Lang.ru
    )
    message = _Message(text="Подарки")

    await _handler(router, "message", "settings_save_name")(
        message, state, lang=Lang.ru
    )

    assert (await service.get(site.id)).name == "Подарки"
    assert message.answers[-1] == texts.site_name_saved(Lang.ru, "Подарки")
    assert await state.get_state() is None
