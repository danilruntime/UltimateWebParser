"""The permanent keyboard must match mid-wizard and abandon the wizard."""

import pytest

from uwp.bot import keyboards, texts
from uwp.bot.handlers import common

# The four buttons and the text each one must render, in one table so a handler
# wired to a neighbour's button cannot pass unnoticed.
_MENU = {
    "menu_sites": (keyboards.BTN_SITES, texts.NO_SITES),
    "menu_profiles": (keyboards.BTN_PROFILES, texts.NO_PROFILES),
    "menu_proxies": (keyboards.BTN_PROXIES, texts.NO_PROXIES),
    "menu_help": (keyboards.BTN_HELP, texts.HELP),
}


class _Service:
    async def list(self):
        return []


class _Deps:
    def __init__(self):
        self.sites = _Service()
        self.profiles = _Service()
        self.proxies = _Service()
        self.runs = _Service()


class _Message:
    """Just the surface a menu handler touches: ``text`` and ``answer``."""

    def __init__(self, text=None):
        self.text = text
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)


def _menu_handlers(router):
    return [
        observer
        for observer in router.message.handlers
        if observer.callback.__name__.startswith("menu_")
    ]


def _handler(router, name):
    return next(
        observer.callback
        for observer in _menu_handlers(router)
        if observer.callback.__name__ == name
    )


def _state():
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=1, user_id=1),
    )


def test_all_four_reply_buttons_have_handlers():
    router = common.build_router(settings=None, deps=_Deps())

    names = {observer.callback.__name__ for observer in _menu_handlers(router)}

    assert names == {"menu_sites", "menu_profiles", "menu_proxies", "menu_help"}


def test_the_handlers_are_not_restricted_to_an_empty_state():
    """A StateFilter here would let a mid-wizard tap reach the wizard handler."""
    from aiogram.filters import StateFilter

    router = common.build_router(settings=None, deps=_Deps())

    for observer in _menu_handlers(router):
        assert not any(
            isinstance(flt.callback, StateFilter) for flt in observer.filters
        ), observer.callback.__name__


@pytest.mark.parametrize(("name", "expected"), sorted(
    (name, text) for name, (_, text) in _MENU.items()
))
async def test_tapping_a_menu_button_abandons_an_active_wizard(name, expected):
    """The whole point of the task: a mid-wizard tap drops the wizard.

    Invokes the real callback, so dropping ``await state.clear()`` from any one
    handler fails here — unlike the introspection tests above.
    """
    router = common.build_router(settings=None, deps=_Deps())
    handler = _handler(router, name)

    state = _state()
    await state.set_state("ProfileNameInput:waiting")
    assert await state.get_state() is not None

    message = _Message(text=_MENU[name][0])
    await handler(message, state=state)

    assert await state.get_state() is None
    assert message.answers == [expected]


@pytest.mark.parametrize("name", sorted(_MENU))
def test_each_handler_matches_only_its_own_button(name):
    """Resolve the real ``F`` filter: correct button true, neighbours false."""
    router = common.build_router(settings=None, deps=_Deps())
    observer = next(
        observer
        for observer in _menu_handlers(router)
        if observer.callback.__name__ == name
    )
    [filter_object] = observer.filters
    own_label = _MENU[name][0]

    matched = {
        label
        for label, _ in _MENU.values()
        if filter_object.callback(_Message(text=label))
    }

    assert matched == {own_label}
