"""The permanent keyboard must match mid-wizard and abandon the wizard."""

import pytest

from uwp.bot import keyboards, texts
from uwp.bot.handlers import common
from uwp.i18n import Lang

# The five buttons and the text each one must render in that language, in one
# table so a handler wired to a neighbour's button cannot pass unnoticed.
_MENU = {
    Lang.ru: {
        "menu_sites": (keyboards.BTN_SITES[Lang.ru], texts.no_sites(Lang.ru)),
        "menu_profiles": (
            keyboards.BTN_PROFILES[Lang.ru],
            texts.no_profiles(Lang.ru),
        ),
        "menu_proxies": (keyboards.BTN_PROXIES[Lang.ru], texts.no_proxies(Lang.ru)),
        "menu_help": (keyboards.BTN_HELP[Lang.ru], texts.help(Lang.ru)),
        "menu_language": (
            keyboards.BTN_LANGUAGE[Lang.ru],
            texts.language_screen(Lang.ru),
        ),
    },
    Lang.en: {
        "menu_sites": (keyboards.BTN_SITES[Lang.en], texts.no_sites(Lang.en)),
        "menu_profiles": (
            keyboards.BTN_PROFILES[Lang.en],
            texts.no_profiles(Lang.en),
        ),
        "menu_proxies": (keyboards.BTN_PROXIES[Lang.en], texts.no_proxies(Lang.en)),
        "menu_help": (keyboards.BTN_HELP[Lang.en], texts.help(Lang.en)),
        "menu_language": (
            keyboards.BTN_LANGUAGE[Lang.en],
            texts.language_screen(Lang.en),
        ),
    },
}

_HANDLER_NAMES = {
    "menu_sites",
    "menu_profiles",
    "menu_proxies",
    "menu_help",
    "menu_language",
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
    """Just the surface a menu handler touches: ``text``, ``from_user``, ``answer``."""

    def __init__(self, text=None, user_id=1):
        from aiogram.types import User

        self.text = text
        self.from_user = User(id=user_id, is_bot=False, first_name="x")
        self.answers = []
        self.markups = []

    async def answer(self, text=None, reply_markup=None, **kwargs):
        self.answers.append(text)
        self.markups.append(reply_markup)


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


def test_all_five_reply_buttons_have_handlers():
    router = common.build_router(settings=None, deps=_Deps())

    names = {observer.callback.__name__ for observer in _menu_handlers(router)}

    assert names == _HANDLER_NAMES


def test_the_handlers_are_not_restricted_to_an_empty_state():
    """A StateFilter here would let a mid-wizard tap reach the wizard handler."""
    from aiogram.filters import StateFilter

    router = common.build_router(settings=None, deps=_Deps())

    for observer in _menu_handlers(router):
        assert not any(
            isinstance(flt.callback, StateFilter) for flt in observer.filters
        ), observer.callback.__name__


@pytest.mark.parametrize("lang", [Lang.ru, Lang.en])
@pytest.mark.parametrize("name", sorted(_HANDLER_NAMES))
async def test_tapping_a_menu_button_abandons_an_active_wizard(lang, name):
    """The whole point of the task: a mid-wizard tap drops the wizard.

    Invokes the real callback, so dropping ``await state.clear()`` from any one
    handler fails here — unlike the introspection tests above. The English run
    also proves the label and the rendered text are the English ones.
    """
    router = common.build_router(settings=None, deps=_Deps())
    handler = _handler(router, name)
    label, expected = _MENU[lang][name]

    state = _state()
    await state.set_state("ProfileNameInput:waiting")
    assert await state.get_state() is not None

    message = _Message(text=label)
    await handler(message, state=state, lang=lang)

    assert await state.get_state() is None
    assert message.answers == [expected]


@pytest.mark.parametrize("lang", [Lang.ru, Lang.en])
@pytest.mark.parametrize("name", sorted(_HANDLER_NAMES))
def test_each_handler_matches_only_its_own_button(lang, name):
    """Resolve the real ``F`` filter: correct button true, neighbours false.

    Every label in both languages is offered, so a filter that only accepts the
    Russian label fails on the English one (and vice versa).
    """
    router = common.build_router(settings=None, deps=_Deps())
    observer = next(
        observer
        for observer in _menu_handlers(router)
        if observer.callback.__name__ == name
    )
    [filter_object] = observer.filters

    matched = {
        label
        for table in _MENU.values()
        for label, _ in table.values()
        if filter_object.callback(_Message(text=label))
    }

    # Ruling: a filter accepts its button in *both* languages, so a user who
    # switched language without pressing /start keeps working. It must still
    # reject every neighbour, in both languages.
    expected = {table[name][0] for table in _MENU.values()}
    if name == "menu_language":
        expected = set(keyboards.BTN_LANGUAGE.values())
    assert matched == expected
