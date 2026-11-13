"""Tests for the admin gate: only configured Telegram ids may use the bot."""
import pytest
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import User

from uwp.bot import texts
from uwp.bot.app import BotDeps, build_dispatcher
from uwp.bot.keyboards import site_card_kb, sites_list_kb
from uwp.bot.middleware import AdminOnlyMiddleware
from uwp.config import Settings
from uwp.domain import Site


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
    sites = [
        Site(id=1, slug="a", name="A", parser_key="steam_deals", interval_min_min=1,
             interval_max_min=2, enabled=True),
        Site(id=2, slug="b", name="B", parser_key="steam_deals", interval_min_min=1,
             interval_max_min=2),
    ]

    rows = sites_list_kb(sites).inline_keyboard

    assert [row[0].callback_data for row in rows] == ["site:1", "site:2", "site:add"]
    assert rows[0][0].text == "🟢 A"
    assert rows[1][0].text == "🔴 B"


def test_sites_keyboard_skips_sites_without_an_id():
    site = Site(slug="a", name="A", parser_key="steam_deals", interval_min_min=1,
                interval_max_min=2)

    rows = sites_list_kb([site]).inline_keyboard

    assert [row[0].callback_data for row in rows] == ["site:add"]


def test_gate_runs_as_outer_middleware():
    dp = build_dispatcher(
        Settings(admin_ids=[1]), BotDeps(sites=object(), registry=object())
    )

    # Outer middleware runs once per update, before handler resolution, so a
    # non-admin cannot get one refusal per matching handler.
    assert len(dp.message.outer_middleware) == 1
    assert len(dp.callback_query.outer_middleware) == 1
    assert len(dp.message.middleware) == 0


def test_site_card_keyboard_has_toggle_run_settings_back():
    site = Site(
        id=5, slug="a", name="A", parser_key="steam_deals",
        interval_min_min=1, interval_max_min=2, enabled=True,
    )

    flat = [b.callback_data for row in site_card_kb(site).inline_keyboard for b in row]

    assert flat[:3] == ["site:toggle:5", "site:run:5", "site:settings:5"]
    assert flat[-1] == "sites:list"


def test_build_dispatcher_registers_routers():
    dp = build_dispatcher(
        Settings(admin_ids=[1]),
        BotDeps(sites=object(), registry=object()),
    )

    names = [router.name for router in dp.sub_routers]

    assert names == ["common", "sites", "settings", "profiles", "fallback"]


def test_the_fallback_router_is_registered_last():
    """A catch-all before the FSM handlers would swallow wizard input."""
    dp = build_dispatcher(
        Settings(admin_ids=[1]),
        BotDeps(sites=object(), registry=object()),
    )

    names = [router.name for router in dp.sub_routers]

    assert names[-1] == "fallback"
    assert names.count("fallback") == 1


async def test_a_stale_button_press_does_not_print_a_traceback():
    """Telegram refuses an old callback; that is normal, not a crash."""
    from aiogram.exceptions import TelegramBadRequest

    from uwp.bot.app import handle_update_error

    class Event:
        exception = TelegramBadRequest(
            method=None,
            message=(
                "Telegram server says - Bad Request: query is too old and "
                "response timeout expired or query ID is invalid"
            ),
        )

    assert await handle_update_error(Event()) is True


async def test_a_real_handler_error_keeps_its_traceback():
    """Returning False lets aiogram log the exception, so bugs stay visible."""
    from uwp.bot.app import handle_update_error

    class Event:
        exception = ValueError("boom")

    assert await handle_update_error(Event()) is False
