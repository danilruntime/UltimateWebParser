"""The bot speaks both languages: resolution, the screen and the switch.

The catalogue parity test in ``test_i18n.py`` proves that keys and placeholders
line up. These tests prove the other half: that the English side is actually
English, that the resolved language reaches the handler, and that pressing a
language button changes what the *next* update renders.
"""

import logging
import re
from datetime import UTC, datetime

import pytest
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import User

from uwp.bot import keyboards, texts
from uwp.bot.app import BotDeps, build_dispatcher
from uwp.bot.handlers import common
from uwp.bot.middleware import AdminOnlyMiddleware, LanguageMiddleware
from uwp.config import load_settings
from uwp.db.database import Database
from uwp.db.repo.admins import AdminsRepo
from uwp.domain import Profile, Proxy, Site
from uwp.i18n import Lang, catalog_keys, normalize
from uwp.services.vnc import VncInfo

CYRILLIC = re.compile(r"[А-Яа-яЁё]")


# --- the English half is English ------------------------------------------


def test_no_english_bot_string_contains_cyrillic():
    """Catches a copy-pasted Russian value in the ``Lang.en`` half.

    A missing key already raises; a value pasted from the other column renders
    happily and only a human notices, so the check has to be mechanical. The one
    allowed exception is the Russian language's own name: a language menu is
    written in the language it offers, in every locale.
    """
    with_cyrillic = {
        key for key in catalog_keys()[Lang.en] if CYRILLIC.search(_english(key))
    }

    assert with_cyrillic == {"bot.language.ru"}


def _english(key: str) -> str:
    from uwp.i18n import t

    return t(Lang.en, key)


def test_the_language_names_are_written_in_their_own_language():
    """An English screen saying «Немецкий» would not help an English reader."""
    assert texts.language_option(Lang.en, Lang.ru) == "🇷🇺 Русский"
    assert texts.language_option(Lang.en, Lang.en) == "🇬🇧 English"
    assert CYRILLIC.search(texts.language_screen(Lang.en)) is None


def test_every_reply_keyboard_label_differs_between_languages():
    """A copied label would make both keyboards look the same and hide a bug."""
    for label in (keyboards.BTN_SITES, keyboards.BTN_PROFILES,
                  keyboards.BTN_PROXIES, keyboards.BTN_HELP):
        assert set(label) == {Lang.ru, Lang.en}
        assert label[Lang.ru] != label[Lang.en]


# --- the reply keyboard and the menu --------------------------------------


def test_the_reply_keyboard_has_a_language_row():
    kb = keyboards.reply_menu_kb(Lang.ru)

    assert [len(row) for row in kb.keyboard] == [2, 2, 1]
    assert kb.keyboard[-1][0].text == keyboards.BTN_LANGUAGE[Lang.ru]
    assert kb.keyboard[-1][0].text == "🌐 Язык"
    assert kb.keyboard[0][0].text == "🌐 Сайты"


def test_the_english_reply_keyboard_is_english():
    labels = [b.text for row in keyboards.reply_menu_kb(Lang.en).keyboard for b in row]

    assert labels == [
        "🌐 Sites",
        "👤 Profiles",
        "🌍 Proxies",
        "❓ Help",
        "🌐 Language",
    ]


def test_the_inline_menu_keeps_every_item_and_adds_the_language():
    callbacks = [
        b.callback_data
        for row in keyboards.menu_kb(Lang.ru).inline_keyboard
        for b in row
    ]

    assert callbacks == ["sites:list", "profiles:list", "proxies:list", "lang:screen"]


def test_the_language_screen_ticks_the_current_one():
    rows = keyboards.language_kb(Lang.ru).inline_keyboard
    labels = {b.callback_data: b.text for row in rows for b in row if b.callback_data}

    assert labels["lang:set:ru"].endswith("✓")
    assert not labels["lang:set:en"].endswith("✓")
    assert labels["lang:set:en"].startswith("🇬🇧")


# --- the middleware --------------------------------------------------------


class _Event:
    """A message-shaped event: a sender plus the one method the gate calls."""

    def __init__(self, uid=None, language_code=None):
        if uid is not None:
            self.from_user = User(
                id=uid, is_bot=False, first_name="x", language_code=language_code
            )
        self.answers = []

    async def answer(self, text=None, **kw):
        self.answers.append(text)


def _handler_recorder():
    seen = {}

    async def handler(event, data):
        seen.update(data)
        return "ran"

    return handler, seen


async def test_the_stored_choice_wins_over_the_telegram_locale():
    async def resolve(uid):
        return "en"

    middleware = LanguageMiddleware(resolve, default=Lang.ru)
    handler, seen = _handler_recorder()

    await middleware(handler, _Event(1, "ru"), {})

    assert seen["lang"] is Lang.en


async def test_a_telegram_locale_is_used_when_no_choice_is_stored():
    async def resolve(uid):
        return None

    middleware = LanguageMiddleware(resolve, default=Lang.ru)
    handler, seen = _handler_recorder()

    await middleware(handler, _Event(1, "en-GB"), {})

    assert seen["lang"] is Lang.en


async def test_an_unsupported_locale_falls_back_to_the_default():
    async def resolve(uid):
        return None

    middleware = LanguageMiddleware(resolve, default=Lang.ru)
    handler, seen = _handler_recorder()

    await middleware(handler, _Event(1, "de"), {})

    assert seen["lang"] is Lang.ru


async def test_an_anonymous_update_gets_the_default_language():
    async def resolve(uid):
        raise AssertionError("a sender-less update must not be resolved")

    middleware = LanguageMiddleware(resolve, default=Lang.en)
    handler, seen = _handler_recorder()

    await middleware(handler, object(), {})

    assert seen["lang"] is Lang.en


async def test_a_failing_repository_does_not_eat_the_update(caplog):
    """A locked database must still produce an answer, not a silent drop.

    Ruling: the fallback stays, but the failure must be visible. A warning that
    nobody greps for looks like normal noise, so the lookup failure is logged at
    ``ERROR`` with the admin id and what was attempted.
    """

    async def resolve(uid):
        raise RuntimeError("database is locked")

    middleware = LanguageMiddleware(resolve, default=Lang.ru)
    handler, seen = _handler_recorder()

    with caplog.at_level(logging.ERROR, logger="uwp.bot.middleware"):
        await middleware(handler, _Event(1, "en"), {})

    assert seen["lang"] is Lang.en
    (record,) = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert record.levelno == logging.ERROR
    assert "1" in record.getMessage()
    assert "language" in record.getMessage()


async def test_the_choice_survives_a_restart(tmp_path):
    """A fresh resolver, reading the same database, returns the stored ``en``.

    This is the whole point of ``admins.language``: the Telegram client's
    ``language_code`` is not in an update forever, and a restart must not reset
    an admin to Russian.
    """
    db = await Database.in_memory()
    await db.migrate()
    repo = AdminsRepo(db)
    try:
        await repo.set_language(777, "en")

        # Nothing but the stored choice: no language_code on the event.
        middleware = LanguageMiddleware(repo.language, default=Lang.ru)
        handler, seen = _handler_recorder()
        await middleware(handler, _Event(777), {})

        assert seen["lang"] is Lang.en
    finally:
        await db.close()


async def test_the_language_middleware_runs_after_the_gate(monkeypatch):
    """A stranger must be refused before any language lookup hits the database.

    Behavioural, not just registration order: the gate could be registered first
    and still let the language middleware read the row (an outer middleware that
    returns early is the only thing that prevents it), so the assertion is on the
    resolver's call log.
    """
    from aiogram import Bot
    from aiogram.types import Chat, Message, Update, User

    calls: list[int] = []

    class _RecordingAdmins:
        async def language(self, telegram_id):
            calls.append(telegram_id)
            return "en"

    async def fake_answer(self, text=None, **kw):
        return True

    monkeypatch.setattr(Message, "answer", fake_answer)
    deps = BotDeps(
        sites=_Service(),
        registry=object(),
        admins=_RecordingAdmins(),
    )
    dp = build_dispatcher(
        load_settings({"UWP_ADMIN_IDS": "1"}), deps, admin_ids={1}
    )
    bot = Bot("123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    update = Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=0,
            chat=Chat(id=999, type="private"),
            from_user=User(id=999, is_bot=False, first_name="x", language_code="en"),
            text="/help",
        ),
    )
    try:
        assert await dp.feed_update(bot, update) is UNHANDLED
    finally:
        await bot.session.close()

    assert calls == []


# --- the refusal is localised ---------------------------------------------


async def test_a_strangers_refusal_follows_their_own_locale():
    middleware = AdminOnlyMiddleware({111}, default=Lang.ru)
    event = _Event(999, "en")

    await middleware(lambda e, d: None, event, {})

    assert event.answers == [texts.access_denied(Lang.en)]


async def test_a_stranger_without_a_known_locale_gets_the_default():
    middleware = AdminOnlyMiddleware({111}, default=Lang.en)
    event = _Event(999, "de")

    await middleware(lambda e, d: None, event, {})

    assert event.answers == [texts.access_denied(Lang.en)]


# --- the language screen ---------------------------------------------------


class _Service:
    async def list(self):
        return []


class _Admins:
    def __init__(self, language_code=None):
        self.written: list[tuple[int, str]] = []
        self.language_code = language_code

    async def set_language(self, telegram_id, code):
        self.written.append((telegram_id, code))

    async def language(self, telegram_id):
        return self.language_code


class _Deps:
    def __init__(self, admins=None):
        self.sites = _Service()
        self.profiles = _Service()
        self.proxies = _Service()
        self.runs = _Service()
        self.admins = admins


class _Message:
    def __init__(self, text=None, user_id=1):
        self.text = text
        self.from_user = User(id=user_id, is_bot=False, first_name="x")
        self.answers = []
        self.edits = []
        self.kwargs = []
        self.markups = []

    async def answer(self, text=None, reply_markup=None, **kw):
        self.answers.append(text)
        self.markups.append(reply_markup)

    async def edit_text(self, text=None, reply_markup=None, **kw):
        self.edits.append(text)
        self.kwargs.append(kw)

    async def reply(self, text=None, **kw):
        self.answers.append(text)


class _Callback:
    def __init__(self, data, user_id=1):
        self.data = data
        self.from_user = User(id=user_id, is_bot=False, first_name="x")
        self.message = _Message()
        self.answers: list[tuple[str, dict]] = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))


def _state():
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=1, user_id=1),
    )


def _callback_handler(router, data_prefix):
    return next(
        observer.callback
        for observer in router.callback_query.handlers
        if observer.callback.__name__ == data_prefix
    )


async def test_the_language_screen_lists_both_languages():
    router = common.build_router(settings=None, deps=_Deps())
    handler = next(
        observer.callback
        for observer in router.message.handlers
        if observer.callback.__name__ == "menu_language"
    )
    message = _Message()

    await handler(message, state=_state(), lang=Lang.ru)

    labels = [
        b.text
        for row in message.markups[0].inline_keyboard
        for b in row
        if b.callback_data
    ]
    assert any("Русский" in label and label.endswith("✓") for label in labels)
    assert any("English" in label and not label.endswith("✓") for label in labels)


async def test_setting_the_language_writes_the_repo_and_switches_the_keyboard():
    admins = _Admins()
    router = common.build_router(settings=None, deps=_Deps(admins=admins))
    handler = _callback_handler(router, "set_language")
    callback = _Callback("lang:set:en")

    await handler(callback, lang=Lang.ru)

    assert admins.written == [(1, "en")]
    # The screen is re-rendered in the new language, not the one it was opened in.
    assert callback.message.edits == [texts.language_screen(Lang.en)]
    # The reply keyboard under the chat input is replaced immediately, otherwise
    # the user keeps pressing Russian buttons on an English screen.
    assert callback.message.markups[-1].keyboard[0][0].text == "🌐 Sites"
    assert callback.message.answers == [texts.language_saved(Lang.en)]


async def test_an_unknown_language_code_is_refused_and_not_written():
    admins = _Admins()
    router = common.build_router(settings=None, deps=_Deps(admins=admins))
    handler = _callback_handler(router, "set_language")
    callback = _Callback("lang:set:de")

    await handler(callback, lang=Lang.ru)

    assert admins.written == []
    text, kwargs = callback.answers[0]
    assert text == texts.language_unknown(Lang.ru)
    assert kwargs["show_alert"] is True
    assert normalize("de") is None


async def test_a_missing_admins_repo_answers_an_error_instead_of_crashing():
    """Tests build ``BotDeps`` by hand; a real deployment always has the repo."""
    router = common.build_router(settings=None, deps=_Deps(admins=None))
    handler = _callback_handler(router, "set_language")
    callback = _Callback("lang:set:ru")

    await handler(callback, lang=Lang.ru)

    text, kwargs = callback.answers[0]
    assert text == texts.language_unavailable(Lang.ru)
    assert kwargs["show_alert"] is True


async def test_a_failing_write_answers_an_error_and_does_not_switch(caplog):
    """A locked database must not leave the admin staring at a dead button."""

    class _BrokenAdmins:
        async def set_language(self, telegram_id, code):
            raise RuntimeError("database is locked")

        async def language(self, telegram_id):
            return None

    router = common.build_router(settings=None, deps=_Deps(admins=_BrokenAdmins()))
    handler = _callback_handler(router, "set_language")
    callback = _Callback("lang:set:en")

    with caplog.at_level(logging.ERROR, logger="uwp.bot.handlers.common"):
        await handler(callback, lang=Lang.ru)

    text, kwargs = callback.answers[0]
    assert text == texts.language_save_failed(Lang.ru)
    assert kwargs["show_alert"] is True
    # Nothing was re-rendered: the screen still shows the old language.
    assert callback.message.edits == []
    assert [record.levelno for record in caplog.records] == [logging.ERROR]


# --- VNC card -------------------------------------------------------------


def test_the_vnc_session_card_is_localised():
    info = VncInfo(
        session_id=1,
        port=6100,
        password="pw",
        url="http://127.0.0.1:6100/vnc.html",
        expires_at=datetime.now(UTC),
    )

    assert "Вход на сайт" in texts.vnc_session(Lang.ru, info, 30)
    assert "Log in to the site" in texts.vnc_session(Lang.en, info, 30)


# --- end to end: the resolved language reaches a real handler --------------


async def _feed_help(monkeypatch, admins, uid=111, language_code=None):
    """Drive the real dispatcher with /help and return what the admin saw."""
    from aiogram import Bot
    from aiogram.types import Chat, Message, Update, User

    answered: list[str] = []

    async def fake_answer(self, text=None, **kw):
        answered.append(text)
        return True

    monkeypatch.setattr(Message, "answer", fake_answer)
    deps = BotDeps(sites=_Service(), registry=object(), admins=admins)
    dp = build_dispatcher(
        load_settings({"UWP_ADMIN_IDS": str(uid)}), deps, admin_ids={uid}
    )
    bot = Bot("123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    update = Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=0,
            chat=Chat(id=uid, type="private"),
            from_user=User(
                id=uid,
                is_bot=False,
                first_name="x",
                language_code=language_code,
            ),
            text="/help",
        ),
    )
    try:
        await dp.feed_update(bot, update)
    finally:
        await bot.session.close()
    return answered


async def test_a_stored_english_choice_changes_a_real_handler_output(monkeypatch):
    """Without the middleware wiring, every handler would still speak Russian."""
    answered = await _feed_help(monkeypatch, _Admins(language_code="en"))

    assert answered == [texts.help(Lang.en)]


async def test_no_stored_choice_uses_the_clients_locale(monkeypatch):
    """No stored row, but the Telegram client says ``en``: the client wins."""

    class _NoChoice:
        async def language(self, telegram_id):
            return None

    answered = await _feed_help(monkeypatch, _NoChoice(), language_code="en")

    assert answered == [texts.help(Lang.en)]


async def test_no_stored_choice_and_no_known_locale_uses_the_default(monkeypatch):
    """Neither a stored row nor a supported client locale: deployment default.

    ``load_settings`` leaves ``default_language`` at ``ru``, so an admin whose
    client speaks German sees Russian rather than an untranslated guess.
    """

    class _NoChoice:
        async def language(self, telegram_id):
            return None

    answered = await _feed_help(monkeypatch, _NoChoice(), language_code="de")

    assert answered == [texts.help(Lang.ru)]


# --- layout ----------------------------------------------------------------


def _site(**kw):
    return Site(
        **{
            "id": 3,
            "slug": "s",
            "name": "S",
            "parser_key": "steam_deals",
            "interval_min_min": 45,
            "interval_max_min": 90,
            **kw,
        }
    )


def test_the_site_card_escapes_a_hostile_name():
    """A site name is user input; the bot speaks HTML, so `<b>` would render."""
    card = texts.site_card(Lang.en, _site(name="<b>A & B</b>"), None)

    assert "<b>A & B</b>" not in card
    assert "&lt;b&gt;A &amp; B&lt;/b&gt;" in card


def _profile():
    return Profile(
        id=1,
        name="P",
        kind="persistent",
        seed=1,
        platform="Win32",
        brand="Chromium",
        brand_version="140",
        timezone="Europe/Moscow",
        locale="ru-RU",
        user_data_dir="/tmp/p",
    )


def _proxy():
    return Proxy(name="X", scheme="http", host="proxy.example", port=8080)


def test_the_delete_confirmation_has_its_own_yes_label():
    """The confirm screen must not offer the same label as the button that opened it."""
    site = _site()

    openers = {
        b.callback_data: b.text
        for row in keyboards.site_card_kb(site, Lang.ru).inline_keyboard
        for b in row
    }
    confirm = keyboards.confirm_delete_kb(site, Lang.ru).inline_keyboard[0][0]

    assert openers["site:del:3"] == texts.delete(Lang.ru) == "🗑 Удалить"
    assert confirm.text == texts.confirm_delete_yes(Lang.ru) == "🗑 Да, удалить"
    assert confirm.callback_data == "site:delok:3"
    assert confirm.text != openers["site:del:3"]


def test_the_proxy_card_keeps_its_punctuation_out_of_the_catalogue():
    """Separators live in the joining template, so a translator cannot lose them."""
    proxy = Proxy(
        name="X",
        scheme="http",
        host="proxy.example",
        port=8080,
        username="bob",
        country="NL",
    )

    card = texts.proxy_card(Lang.en, proxy)

    assert card == (
        "<b>🌍 X</b>\n"
        "<blockquote>http://proxy.example:8080, user bob\n"
        "Country: NL</blockquote>"
    )


def test_the_proxy_card_keeps_the_russian_separators():
    proxy = Proxy(
        name="X",
        scheme="http",
        host="proxy.example",
        port=8080,
        username="bob",
        country="NL",
    )

    assert texts.proxy_card(Lang.ru, proxy) == (
        "<b>🌍 X</b>\n"
        "<blockquote>http://proxy.example:8080, пользователь bob\n"
        "Страна: NL</blockquote>"
    )


@pytest.mark.parametrize("lang", [Lang.ru, Lang.en])
@pytest.mark.parametrize(
    "render",
    [
        lambda lang: texts.site_card(lang, _site(), None),
        lambda lang: texts.profile_card(lang, _profile()),
        lambda lang: texts.proxy_card(lang, _proxy()),
    ],
    ids=["site", "profile", "proxy"],
)
def test_every_card_shares_one_layout(lang, render):
    """A profile or proxy card left in the old shape looks like another screen."""
    first, rest = render(lang).split("\n", 1)

    assert first.startswith("<b>") and first.endswith("</b>")
    assert rest.startswith("<blockquote>") and rest.endswith("</blockquote>")


def test_a_card_disables_the_link_preview():
    """Telegram would unfold a preview of the first link and push buttons away."""
    assert keyboards.NO_PREVIEW.is_disabled is True


async def test_the_site_card_handler_passes_the_no_preview_option():
    """The constant alone changes nothing: the handler must hand it to Telegram."""
    from uwp.bot.handlers import sites as sites_handlers

    class _OneSite:
        async def get(self, site_id):
            return _site()

    class _Settings:
        tz = "UTC"

    class _SiteDeps:
        sites = _OneSite()
        registry = object()

    router = sites_handlers.build_router(_Settings(), _SiteDeps())
    handler = _callback_handler(router, "show_card")
    callback = _Callback("site:3")

    await handler(callback, lang=Lang.en)

    assert callback.message.kwargs[-1]["link_preview_options"] is keyboards.NO_PREVIEW


async def test_the_language_command_opens_the_same_screen():
    router = common.build_router(settings=None, deps=_Deps())
    handler = next(
        observer.callback
        for observer in router.message.handlers
        if observer.callback.__name__ == "command_language"
    )
    message = _Message()

    await handler(message, lang=Lang.en)

    assert message.answers == [texts.language_screen(Lang.en)]
    labels = [
        b.text
        for row in message.markups[0].inline_keyboard
        for b in row
        if b.callback_data
    ]
    assert "🇬🇧 English ✓" in labels


async def test_adding_a_site_stores_the_name_in_the_admins_language():
    """The picker said “Steam — deals”, so the stored site must say it too.

    The service is built once from ``settings.default_language``; only the
    handler knows which admin is pressing. A service-level test that constructs
    the service with ``lang=Lang.en`` cannot see that wiring, so this one feeds
    the real ``site:adddefault:`` callback with a service built for Russian.
    """
    from uwp.bot.handlers import sites as sites_handlers
    from uwp.db.repo.jobs import JobsRepo
    from uwp.db.repo.sites import SitesRepo
    from uwp.parsers.registry import ParserRegistry
    from uwp.scheduler import Scheduler
    from uwp.services.sites import SiteService

    db = await Database.in_memory()
    await db.migrate()
    try:
        sites, jobs = SitesRepo(db), JobsRepo(db)
        service = SiteService(
            sites,
            jobs,
            Scheduler(sites, jobs),
            registry=ParserRegistry.discover(),
            lang=Lang.ru,
        )

        class _SiteDeps:
            pass

        deps = _SiteDeps()
        deps.sites = service
        deps.registry = service.registry

        class _Settings:
            tz = "UTC"

        router = sites_handlers.build_router(_Settings(), deps)
        handler = _callback_handler(router, "add_site_default")
        callback = _Callback("site:adddefault:steam_deals")

        await handler(callback, state=_state(), lang=Lang.en)

        (site,) = await sites.list()
        assert site.name == "Steam — deals"
    finally:
        await db.close()


async def test_the_proxy_check_alert_speaks_the_admins_language():
    """The handler is where the language is known; nothing pinned that line.

    It used to print the service's Russian prose and drop its ``lang`` argument,
    which is invisible to a service-level test. ``connection_error`` carries an
    OS detail with an ``&``: an alert has no parse mode, so escaping it would
    show the admin ``&amp;``.
    """
    from uwp.bot.handlers import profiles as profiles_handlers
    from uwp.services.proxies import ProxyCheck

    class _Proxies:
        def __init__(self, check):
            self.check = check

        async def test(self, proxy_id):
            return self.check

    class _Vnc:
        pass

    async def alert(check, lang):
        class _Deps:
            pass

        deps = _Deps()
        deps.proxies = _Proxies(check)
        deps.profiles = _Deps()
        deps.vnc = _Vnc()
        deps.runs = _Deps()

        class _Settings:
            tz = "UTC"

        router = profiles_handlers.build_router(_Settings(), deps)
        handler = _callback_handler(router, "test_proxy")
        callback = _Callback("proxy:test:3")

        await handler(callback, lang=lang)
        (text, kwargs), = callback.answers
        assert kwargs["show_alert"] is True
        return text

    ok = ProxyCheck(ok=True, reason="ok", ms=42)
    assert await alert(ok, Lang.en) == "✅ reachable, 42 ms"
    assert await alert(ok, Lang.ru) == "✅ доступен, 42 мс"

    timed_out = ProxyCheck(ok=False, reason="timeout", detail="10")
    assert await alert(timed_out, Lang.en) == "❌ timeout 10 s"

    refused = ProxyCheck(
        ok=False, reason="connection_error", detail="no route & host down"
    )
    assert await alert(refused, Lang.en) == "❌ connection error: no route & host down"


# --- a stored secret we cannot read ---------------------------------------


def _login_deps(vnc):
    class _Deps:
        pass

    deps = _Deps()
    deps.sites = _Service()
    deps.profiles = _Service()
    deps.proxies = _Service()
    deps.runs = _Service()
    deps.registry = object()
    deps.vnc = vnc
    return deps


class _Settings:
    tz = "UTC"
    vnc_ttl_minutes = 30


@pytest.mark.parametrize(
    "lang, expected",
    [
        (Lang.ru, "не удалось расшифровать секрет: проверьте UWP_SECRET_KEY"),
        (Lang.en, "cannot decrypt the stored secret: check UWP_SECRET_KEY"),
    ],
)
async def test_the_profile_login_alert_renders_an_unreadable_secret(lang, expected):
    """A rotated ``UWP_SECRET_KEY`` is by design, so its sentence must translate.

    ``SecretUnreadable`` is a ``ValueError`` whose ``str()`` is an English
    developer line. The generic ``except ValueError`` printed that line into a
    Russian chat; the handler must render ``code`` instead.
    """
    from uwp.bot.handlers import profiles as profiles_handlers
    from uwp.secretbox import SecretUnreadable

    class _Vnc:
        async def request(self, *args, **kwargs):
            raise SecretUnreadable("error.secret.wrong_key")

    router = profiles_handlers.build_router(_Settings(), _login_deps(_Vnc()))
    handler = _callback_handler(router, "start_login")
    callback = _Callback("profile:login:4")

    await handler(callback, lang=lang)

    (text, kwargs), = callback.answers
    assert text == expected
    assert "the stored secret cannot be decrypted" not in text
    assert kwargs["show_alert"] is True


@pytest.mark.parametrize(
    "lang, expected",
    [
        (Lang.ru, "не удалось расшифровать секрет: проверьте UWP_SECRET_KEY"),
        (Lang.en, "cannot decrypt the stored secret: check UWP_SECRET_KEY"),
    ],
)
async def test_the_site_login_alert_renders_an_unreadable_secret(lang, expected):
    """The second login path reaches the same secret and used to print the same line."""
    from uwp.bot.handlers import sites as sites_handlers
    from uwp.secretbox import SecretUnreadable

    class _Sites:
        async def get(self, site_id):
            return _site(profile_id=7)

    class _Vnc:
        async def request(self, *args, **kwargs):
            raise SecretUnreadable("error.secret.wrong_key")

    deps = _login_deps(_Vnc())
    deps.sites = _Sites()
    router = sites_handlers.build_router(_Settings(), deps)
    handler = _callback_handler(router, "login_from_site")
    callback = _Callback("site:login:3")

    await handler(callback, lang=lang)

    (text, kwargs), = callback.answers
    assert text == expected
    assert "the stored secret cannot be decrypted" not in text
    assert kwargs["show_alert"] is True


@pytest.mark.parametrize(
    "lang, expected",
    [
        (
            Lang.ru,
            "профиль уже занят: закройте сессию входа или дождитесь её завершения",
        ),
        (
            Lang.en,
            "the profile is already in use: finish the login session or wait",
        ),
    ],
)
async def test_a_profile_that_cannot_open_is_answered_in_the_admin_language(
    lang, expected
):
    """``check_profile`` used to raise English ``ValueError``s into this alert."""
    from uwp.bot.handlers import profiles as profiles_handlers
    from uwp.vnc import VncLoginError

    class _Vnc:
        async def request(self, *args, **kwargs):
            raise VncLoginError("profile is not free: locked", "error.vnc.profile_busy")

    router = profiles_handlers.build_router(_Settings(), _login_deps(_Vnc()))
    handler = _callback_handler(router, "start_login")
    callback = _Callback("profile:login:4")

    await handler(callback, lang=lang)

    (text, kwargs), = callback.answers
    assert text == expected
    assert kwargs["show_alert"] is True


async def test_the_login_button_of_a_failed_run_passes_its_site_to_the_service():
    """The delivery button knows the site; a profile card does not.

    Losing it turned the one button that names the site into the one refusal
    that says the site cannot be guessed.
    """
    from uwp.bot.handlers import profiles as profiles_handlers

    class _Vnc:
        def __init__(self):
            self.requests: list[tuple[int, int | None]] = []

        async def request(self, profile_id, site_id=None, **kwargs):
            self.requests.append((profile_id, site_id))
            return VncInfo(
                session_id=1,
                port=6100,
                password="pw",
                url="http://127.0.0.1:6100/vnc.html",
                expires_at=datetime.now(UTC),
            )

    vnc = _Vnc()
    router = profiles_handlers.build_router(_Settings(), _login_deps(vnc))
    handler = _callback_handler(router, "start_login")

    await handler(_Callback("profile:login:7:3"), lang=Lang.ru)
    await handler(_Callback("profile:login:7"), lang=Lang.ru)

    assert vnc.requests == [(7, 3), (7, None)]


@pytest.mark.parametrize(
    "lang, expected",
    [(Lang.ru, "13.11.2026 01:20"), (Lang.en, "2026-11-13 01:20")],
)
async def test_the_site_list_prints_the_date_the_way_the_reader_writes_it(
    lang, expected
):
    """The helper alone is not enough: the handler must pass the admin's language."""
    from uwp.bot.handlers import sites as sites_handlers

    class _Sites:
        async def list(self):
            return [_site(next_run_at=datetime(2026, 11, 12, 22, 20, tzinfo=UTC))]

    class _Deps:
        pass

    deps = _Deps()
    deps.sites = _Sites()
    deps.registry = object()

    class _Settings:
        tz = "Europe/Moscow"

    router = sites_handlers.build_router(_Settings(), deps)
    handler = _callback_handler(router, "show_list")
    callback = _Callback("sites:list")

    await handler(callback, state=_state(), lang=lang)

    (text,) = callback.message.edits
    assert expected in text
