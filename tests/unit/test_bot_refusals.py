"""Service refusals reach the chat in the reader's language, not the deployment's.

``ProfilesService``, ``ProxyService`` and ``VncService`` raise ``ValueError``
with a finished sentence, and a handler prints it verbatim. The services used to
render with ``settings.default_language``, so an ``en`` admin on a default ``ru``
deployment read «профиль с именем «dup» уже есть» in their English chat.
"""

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import User

from uwp.bot import texts
from uwp.bot.app import BotDeps
from uwp.bot.handlers import profiles as profile_handlers
from uwp.config import load_settings
from uwp.db.database import Database
from uwp.db.repo.profiles import ProfilesRepo
from uwp.db.repo.proxies import ProxiesRepo
from uwp.db.repo.sites import SitesRepo
from uwp.db.repo.vnc import VncRepo
from uwp.i18n import Lang
from uwp.profiles import ProfileManager
from uwp.services.profiles import ProfilesService
from uwp.services.proxies import ProxyService
from uwp.services.vnc import VncService


class FakeManager:
    """The VNC stack is irrelevant here: only the service's own refusals are."""

    async def reserve_and_launch(self, profile, url, started_by=None, proxy_id=None):
        return "info"

    async def reserve(self, profile, url, started_by=None, proxy_id=None):
        return "info"

    async def request_stop(self, session_id):
        return None

    async def list(self):
        return []


class _Message:
    def __init__(self, text=None):
        self.text = text
        self.from_user = User(id=1, is_bot=False, first_name="x")
        self.answers: list[str] = []
        self.edits: list[str] = []

    async def answer(self, text=None, reply_markup=None, **kw):
        self.answers.append(text)

    async def edit_text(self, text=None, reply_markup=None, **kw):
        self.edits.append(text)


class _Callback:
    def __init__(self, data):
        self.data = data
        self.from_user = User(id=1, is_bot=False, first_name="x")
        self.message = _Message()
        self.answers: list[tuple[str | None, dict]] = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))


@pytest.fixture
async def env(tmp_path):
    """A default-``ru`` deployment, exactly the measured case from the review."""
    db = await Database.in_memory()
    await db.migrate()
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path)})
    profiles_repo = ProfilesRepo(db)
    vnc = VncService(
        FakeManager(),
        profiles_repo,
        SitesRepo(db),
        settings,
        VncRepo(db),
    )
    deps = BotDeps(
        sites=None,
        registry=None,
        profiles=ProfilesService(
            ProfileManager(settings, profiles_repo), profiles_repo, settings
        ),
        proxies=ProxyService(ProxiesRepo(db), settings),
        vnc=vnc,
    )
    yield profile_handlers.build_router(settings, deps), deps, settings
    await db.close()


def _callback(router, name):
    return next(
        observer.callback
        for observer in router.callback_query.handlers
        if observer.callback.__name__ == name
    )


def _message_handler(router, name):
    return next(
        observer.callback
        for observer in router.message.handlers
        if observer.callback.__name__ == name
    )


def _state():
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=1, user_id=1),
    )


async def test_a_duplicate_profile_name_is_refused_in_the_readers_language(env):
    router, deps, _settings = env
    await deps.profiles.create("dup")
    handler = _message_handler(router, "create_profile")

    english = _Message("dup")
    await handler(english, state=_state(), lang=Lang.en)
    russian = _Message("dup")
    await handler(russian, state=_state(), lang=Lang.ru)

    assert english.answers == ["❌ " + texts.esc(
        "a profile named “dup” already exists"
    )]
    assert "уже есть" in russian.answers[0]


async def test_an_empty_profile_name_is_refused_in_the_readers_language(env):
    router, _deps, _settings = env
    handler = _message_handler(router, "create_profile")

    english = _Message("   ")
    await handler(english, state=_state(), lang=Lang.en)
    russian = _Message("   ")
    await handler(russian, state=_state(), lang=Lang.ru)

    assert english.answers == [
        "❌ " + texts.esc("the profile name cannot be empty")
    ]
    assert "не может быть пустым" in russian.answers[0]


async def test_a_locked_profile_is_refused_in_the_readers_language(env):
    router, deps, _settings = env
    profile = await deps.profiles.create("A")
    await deps.profiles.manager.lease(profile.id, ttl_seconds=600)
    handler = _callback(router, "delete_profile")

    english = _Callback(f"profile:del:{profile.id}")
    await handler(english, lang=Lang.en)
    russian = _Callback(f"profile:del:{profile.id}")
    await handler(russian, lang=Lang.ru)

    english_text, english_kw = english.answers[0]
    assert "is in use by a run" in english_text
    assert english_kw == {"show_alert": True}
    assert "занят прогоном" in russian.answers[0][0]


async def test_a_duplicate_proxy_name_is_refused_in_the_readers_language(env):
    router, deps, _settings = env
    await deps.proxies.add("DE-1", "http://1.2.3.4:8080")
    handler = _message_handler(router, "create_proxy")

    english = _Message("DE-1 http://1.2.3.4:8080")
    await handler(english, state=_state(), lang=Lang.en)
    russian = _Message("DE-1 http://1.2.3.4:8080")
    await handler(russian, state=_state(), lang=Lang.ru)

    assert "already exists" in english.answers[0]
    assert "уже есть" in russian.answers[0]


async def test_a_malformed_proxy_url_is_refused_in_the_readers_language(env):
    """The parse refusal is the other ``ProxyService.add`` path."""
    router, _deps, _settings = env
    handler = _message_handler(router, "create_proxy")

    english = _Message("P ftp://1.2.3.4:21")
    await handler(english, state=_state(), lang=Lang.en)
    russian = _Message("P ftp://1.2.3.4:21")
    await handler(russian, state=_state(), lang=Lang.ru)

    assert "unsupported proxy scheme" in english.answers[0]
    assert "неподдерживаемая схема" in russian.answers[0]


async def test_a_missing_login_profile_is_refused_in_the_readers_language(env):
    router, _deps, _settings = env
    handler = _callback(router, "start_login")

    english = _Callback("profile:login:999")
    await handler(english, lang=Lang.en)
    russian = _Callback("profile:login:999")
    await handler(russian, lang=Lang.ru)

    english_text, english_kw = english.answers[0]
    assert english_text == "profile 999 not found"
    assert english_kw == {"show_alert": True}
    assert russian.answers[0][0] == "профиль 999 не найден"


async def test_an_unattached_profile_login_is_refused_in_the_readers_language(env):
    """The profile card used to open example.com instead of saying anything."""
    router, deps, _settings = env
    profile = await deps.profiles.create("Steam1")
    handler = _callback(router, "start_login")

    english = _Callback(f"profile:login:{profile.id}")
    await handler(english, lang=Lang.en)
    russian = _Callback(f"profile:login:{profile.id}")
    await handler(russian, lang=Lang.ru)

    assert english.answers[0] == (
        "the profile is not attached to any site: attach it in the site settings",
        {"show_alert": True},
    )
    assert russian.answers[0] == (
        "профиль не привязан ни к одному сайту: привяжите его в настройках сайта",
        {"show_alert": True},
    )


async def test_an_expired_extend_button_answers_instead_of_raising(env):
    """After the sweep the session is gone; the spinner used to time out.

    Measured: pressing «⏱ +30 мин» on a card older than an hour raised
    ``ValueError`` out of the handler, so Telegram never got an answer.
    """
    router, _deps, _settings = env
    handler = _callback(router, "vnc_extend")

    english = _Callback("vnc:extend:5")
    await handler(english, lang=Lang.en)
    russian = _Callback("vnc:extend:5")
    await handler(russian, lang=Lang.ru)

    english_text, english_kw = english.answers[0]
    assert english_text == "session 5 not found"
    assert english_kw == {"show_alert": True}
    assert russian.answers[0][0] == "сессия 5 не найдена"
