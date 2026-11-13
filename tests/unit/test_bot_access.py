"""Tests for the admin gate: only configured Telegram ids may use the bot."""
import pytest
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import User

from uwp.bot import texts
from uwp.bot.middleware import AdminOnlyMiddleware


class FakeEvent:
    def __init__(self, uid):
        self.from_user = User(id=uid, is_bot=False, first_name="x")
        self.answers = []

    async def answer(self, text=None, **kw):
        self.answers.append(text)


async def test_non_admin_is_refused():
    mw = AdminOnlyMiddleware({111})
    event = FakeEvent(999)

    async def handler(event, data):
        raise AssertionError("handler must not run")

    with pytest.raises(SkipHandler):
        await mw(handler, event, {})

    assert event.answers == [texts.ACCESS_DENIED]


async def test_admin_passes_and_is_flagged():
    mw = AdminOnlyMiddleware({111})
    event = FakeEvent(111)
    data: dict = {}

    async def handler(event, data):
        return "ran"

    assert await mw(handler, event, data) == "ran"
    assert data["is_admin"] is True
    assert event.answers == []


async def test_message_without_user_is_refused():
    class Anonymous:
        from_user = None

        def __init__(self):
            self.answers = []

        async def answer(self, text=None, **kw):
            self.answers.append(text)

    mw = AdminOnlyMiddleware({111})
    event = Anonymous()

    with pytest.raises(SkipHandler):
        await mw(lambda e, d: None, event, {})

    assert event.answers == [texts.ACCESS_DENIED]


def test_sites_keyboard_offers_every_site_and_add():
    from uwp.bot.keyboards import sites_list_kb
    from uwp.domain import Site

    sites = [
        Site(slug="a", name="A", parser_key="steam_deals", interval_min_min=1,
             interval_max_min=2, enabled=True),
        Site(slug="b", name="B", parser_key="steam_deals", interval_min_min=1,
             interval_max_min=2),
    ]

    rows = sites_list_kb(sites).inline_keyboard

    assert [row[0].callback_data for row in rows] == ["site:None", "site:None", "site:add"]
    assert rows[0][0].text == "🟢 A"
    assert rows[1][0].text == "🔴 B"


def test_site_card_keyboard_has_toggle_run_settings_back():
    from uwp.bot.keyboards import site_card_kb
    from uwp.domain import Site

    site = Site(
        id=5, slug="a", name="A", parser_key="steam_deals",
        interval_min_min=1, interval_max_min=2, enabled=True,
    )

    flat = [b.callback_data for row in site_card_kb(site).inline_keyboard for b in row]

    assert flat[:3] == ["site:toggle:5", "site:run:5", "site:settings:5"]
    assert flat[-1] == "sites:list"


def test_build_dispatcher_registers_routers():
    from uwp.bot.app import BotDeps, build_dispatcher
    from uwp.config import Settings

    dp = build_dispatcher(
        Settings(admin_ids=[1]),
        BotDeps(sites=object(), registry=object()),
    )

    names = [router.name for router in dp.sub_routers]

    assert names == ["common", "sites", "settings", "profiles"]
