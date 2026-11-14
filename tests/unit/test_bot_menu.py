"""The permanent keyboard must match mid-wizard and abandon the wizard."""

from uwp.bot.handlers import common


class _Service:
    async def list(self):
        return []


class _Deps:
    def __init__(self):
        self.sites = _Service()
        self.profiles = _Service()
        self.proxies = _Service()
        self.runs = _Service()


def _menu_handlers(router):
    return [
        observer
        for observer in router.message.handlers
        if observer.callback.__name__.startswith("menu_")
    ]


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
