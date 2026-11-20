# Site Custom Name Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An admin can give a site their own name when adding it in the bot and rename it later, without touching the CLI.

**Architecture:** The name is already a column (`sites.name`) and every screen reads it; the work is a shared validator in `SiteService`, two FSM input flows in the bot (add and rename), and one keyboard button on the settings screen. `slug` stays untouched so report folders and CLI arguments keep pointing at the same site.

**Tech Stack:** Python 3.12, aiogram 3 (FSM `StatesGroup`), aiosqlite repositories, pydantic domain models, pytest.

**Spec:** `docs/superpowers/specs/2026-11-20-site-custom-name-design.md`

## Global Constraints

- Name limit is exactly 64 characters; it lives in one constant, `MAX_SITE_NAME`, in `src/uwp/services/sites.py`.
- Validation order: `strip`/collapse whitespace first, then empty check, then length check.
- `slug` must never change on rename.
- Russian for everything a human reads (bot texts, CLI output, `labels.py`); English for identifiers and docstrings. Release notes are English but are not touched here.
- Both `README.md` and `README.ru.md` change in the same commit, same section order.
- Tests first; run `.venv/bin/python -m pytest tests/unit/<file>.py -q` and `.venv/bin/python -m ruff check src tests`.
- No new migration: `sites.name` exists and `SitesRepo._COLUMNS` already maps it.

## Review Focus

- A name typed with a newline or tab: Telegram rejects `\n` in button text, so stored names must be single-line.
- A name of 65+ characters: the list keyboard would fail with `TelegramBadRequest` instead of refusing the input.
- Whitespace-only input (a stray space message): must re-ask, not create a site with an empty name.
- A name containing `<b>&</b>`: it appears on the list, the card and the delete confirmation, all HTML.
- Renaming an existing site: report folder (`data/exports/<slug>/`) must stay where it was, and the "default name" button must use the admin's language, not the deployment's.

---

### Task 1: Validate site names in the service

**Files:**
- Modify: `src/uwp/services/sites.py`
- Modify: `src/uwp/i18n/errors.py`
- Test: `tests/unit/test_site_service.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `MAX_SITE_NAME: int = 64`; `clean_site_name(name: str, lang: Lang = DEFAULT_LANG) -> str`; `SiteService.rename(site_id: int, name: str, *, lang: Lang | None = None) -> Site`. `SiteService.create(parser_key, name, lang)` now refuses empty/over-long names instead of storing them.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_site_service.py` (uses the existing `env` fixture and `_registry`):

```python
def test_clean_site_name_collapses_whitespace():
    from uwp.services.sites import clean_site_name

    assert clean_site_name("  Steam \n US\t(скидки) ") == "Steam US (скидки)"


def test_clean_site_name_rejects_an_empty_name_in_english():
    from uwp.services.sites import clean_site_name

    with pytest.raises(ValueError, match="cannot be empty"):
        clean_site_name("   \n ", Lang.en)


def test_clean_site_name_rejects_a_name_over_the_button_limit():
    from uwp.services.sites import MAX_SITE_NAME, clean_site_name

    assert clean_site_name("x" * MAX_SITE_NAME) == "x" * MAX_SITE_NAME
    with pytest.raises(ValueError, match="64"):
        clean_site_name("x" * (MAX_SITE_NAME + 1))


async def test_create_refuses_an_empty_explicit_name(env):
    _db, sites, jobs = env
    svc = SiteService(sites, jobs, Scheduler(sites, jobs), registry=_registry())

    with pytest.raises(ValueError, match="пустым"):
        await svc.create("steam_deals", name=" ")

    assert await svc.list() == []


async def test_create_stores_the_given_name(env):
    _db, sites, jobs = env
    svc = SiteService(sites, jobs, Scheduler(sites, jobs), registry=_registry())

    site = await svc.create("steam_deals", name="  Мои   скидки ")

    assert site.name == "Мои скидки"
    assert site.slug == "steam_deals"


async def test_rename_changes_the_name_and_keeps_the_slug(env):
    _db, sites, jobs = env
    svc = SiteService(sites, jobs, Scheduler(sites, jobs), registry=_registry())
    site = await svc.create("steam_deals")
    slug = site.slug

    renamed = await svc.rename(site.id, "Подарки", lang=Lang.ru)

    assert renamed.name == "Подарки"
    assert renamed.slug == slug
    assert (await sites.get(site.id)).name == "Подарки"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_site_service.py -q`
Expected: FAIL with `ImportError: cannot import name 'clean_site_name'` and `AttributeError: 'SiteService' object has no attribute 'rename'`.

- [ ] **Step 3: Add the two error keys**

In `src/uwp/i18n/errors.py`, next to `error.site.not_found` in both language halves:

```python
"error.site.name_empty": "название сайта не может быть пустым",
"error.site.name_too_long": "название сайта длиннее {max} символов",
```

```python
"error.site.name_empty": "the site name cannot be empty",
"error.site.name_too_long": "the site name is longer than {max} characters",
```

- [ ] **Step 4: Implement the validator and `rename()`**

In `src/uwp/services/sites.py`:

```python
MAX_SITE_NAME = 64


def clean_site_name(name: str, lang: Lang = DEFAULT_LANG) -> str:
    """Trim, collapse any whitespace run to one space, then check the limits.

    ``split()`` is deliberate: Telegram refuses ``\\n`` in a button label and the
    name sits on the site-list keyboard, so a pasted multi-line title has to
    become one line here, not fail at render time.
    """
    cleaned = " ".join((name or "").split())
    if not cleaned:
        raise ValueError(t(lang, "error.site.name_empty"))
    if len(cleaned) > MAX_SITE_NAME:
        raise ValueError(t(lang, "error.site.name_too_long", max=MAX_SITE_NAME))
    return cleaned
```

In `create()`, replace `title = name or parser.title_for(...)` with:

```python
title = (
    clean_site_name(name, lang or self.lang)
    if name is not None
    else parser.title_for(lang or self.lang)
)
```

Add below `update()`:

```python
async def rename(self, site_id: int, name: str, *, lang: Lang | None = None) -> Site:
    """Change the display name; ``slug`` stays because reports live under it."""
    await self._require(site_id)
    await self.sites.update(site_id, name=clean_site_name(name, lang or self.lang))
    return await self._require(site_id)
```

Note `_require` raises `error.site.not_found` with the *service's* language; keep that as is — the bot's handlers already turn a missing site into an alert before calling a service.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_site_service.py -q`
Expected: PASS (all tests, old and new).

- [ ] **Step 6: Commit**

```bash
git add src/uwp/services/sites.py src/uwp/i18n/errors.py tests/unit/test_site_service.py
git commit -m "feat(sites): validate a site name in one place and allow renaming"
```

---

### Task 2: Bot strings and the add-name keyboard

**Files:**
- Modify: `src/uwp/i18n/bot.py`
- Modify: `src/uwp/bot/texts.py`
- Modify: `src/uwp/bot/keyboards.py`
- Create: `tests/unit/test_bot_site_name.py`

**Interfaces:**
- Consumes: `MAX_SITE_NAME` does not appear here; strings only.
- Produces: `texts.ask_site_name(lang, default)`, `texts.keep_default_name(lang, default)`, `texts.site_name_saved(lang, name)`, `texts.name_button(lang)`, `texts.ask_site_name_rename(lang, current)`; `keyboards.site_name_prompt_kb(parser_key: str, default_title: str, lang) -> InlineKeyboardMarkup` whose buttons are `site:adddefault:<parser_key>` and `sites:list`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_bot_site_name.py`:

```python
"""The bot asks for a site name when adding one and can rename a site."""

from uwp.bot import keyboards, texts
from uwp.i18n import Lang


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_bot_site_name.py -q`
Expected: FAIL with `AttributeError: module 'uwp.bot.keyboards' has no attribute 'site_name_prompt_kb'`.

- [ ] **Step 3: Add catalogue keys**

In `src/uwp/i18n/bot.py` under the sites section, Russian:

```python
"bot.sites.ask_name": (
    "Как назвать сайт? Отправьте название сообщением или оставьте «{name}»."
),
"bot.sites.name_default": "✅ Оставить «{name}»",
"bot.sites.name_saved": "✅ Название сайта: {name}.",
```

English:

```python
"bot.sites.ask_name": (
    "What should the site be called? Send a name or keep “{name}”."
),
"bot.sites.name_default": "✅ Keep “{name}”",
"bot.sites.name_saved": "✅ Site name: {name}.",
```

Under the settings section, Russian:

```python
"bot.settings.name_button": "✏️ Название",
"bot.settings.ask_name": "Отправьте новое название сайта (сейчас: «{name}»):",
```

English:

```python
"bot.settings.name_button": "✏️ Name",
"bot.settings.ask_name": "Send a new name for the site (now: “{name}”):",
```

- [ ] **Step 4: Add the facade functions**

In `src/uwp/bot/texts.py` under `# --- sites ---`:

```python
def ask_site_name(lang, default) -> str:
    return t(lang, "bot.sites.ask_name", name=esc(default))


def keep_default_name(lang, default) -> str:
    return t(lang, "bot.sites.name_default", name=esc(default))


def site_name_saved(lang, name) -> str:
    return t(lang, "bot.sites.name_saved", name=esc(name))
```

and under `# --- settings ---`:

```python
def name_button(lang) -> str:
    return t(lang, "bot.settings.name_button")


def ask_site_name_rename(lang, current) -> str:
    return t(lang, "bot.settings.ask_name", name=esc(current))
```

- [ ] **Step 5: Add the keyboard builder**

In `src/uwp/bot/keyboards.py` after `parsers_kb`:

```python
def site_name_prompt_kb(parser_key, default_title, lang) -> InlineKeyboardMarkup:
    """The «keep the parser name» shortcut and a way out of the wizard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.keep_default_name(lang, default_title),
                    callback_data=f"site:adddefault:{parser_key}",
                )
            ],
            [InlineKeyboardButton(text=texts.cancel(lang), callback_data="sites:list")],
        ]
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_bot_site_name.py tests/unit/test_i18n.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/uwp/i18n/bot.py src/uwp/bot/texts.py src/uwp/bot/keyboards.py tests/unit/test_bot_site_name.py
git commit -m "feat(bot): ask for a site name and translate the prompt"
```

---

### Task 3: The add wizard collects a name

**Files:**
- Modify: `src/uwp/bot/handlers/sites.py`
- Modify: `tests/unit/test_bot_language.py` (existing handler test)
- Test: `tests/unit/test_bot_site_name.py`

**Interfaces:**
- Consumes: `keyboards.site_name_prompt_kb`, `texts.ask_site_name`, `texts.site_name_saved`, `SiteService.create(..., name=...)`.
- Produces: FSM state `SiteNameInput.waiting` with `data["parser_key"]`; handlers `add_site` (now a prompt), `add_site_default` (callback `site:adddefault:<parser_key>`), `save_site_name` (message).

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_bot_site_name.py`:

```python
import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import User

from uwp.bot.handlers import sites as sites_handlers
from uwp.db.database import Database
from uwp.db.repo.jobs import JobsRepo
from uwp.db.repo.sites import SitesRepo
from uwp.i18n import Lang
from uwp.parsers.registry import ParserRegistry
from uwp.scheduler import Scheduler
from uwp.services.sites import SiteService


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
        yield sites_handlers.build_router(_Settings(), deps), service, sites
    finally:
        await db.close()


async def test_pressing_an_parser_asks_for_a_name_and_creates_nothing(bot):
    router, _service, sites = bot
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
    router, _service, sites = bot
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
    router, _service, sites = bot
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
    router, _service, sites = bot
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_bot_site_name.py -q`
Expected: FAIL — `add_site` still creates the site immediately, `add_site_default`/`save_site_name` do not exist, `SiteNameInput` is undefined.

- [ ] **Step 3: Rewrite the add flow**

In `src/uwp/bot/handlers/sites.py`:

```python
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message


class SiteNameInput(StatesGroup):
    waiting = State()
```

Replace `add_site` with:

```python
    @router.callback_query(F.data.startswith("site:add:"))
    async def add_site(callback: CallbackQuery, state: FSMContext, lang: Lang) -> None:
        key = callback.data.split(":", 2)[2]
        parser = registry.get(key)
        if parser is None:
            # A stale picker from a deployment whose parser folder was removed.
            await callback.answer(texts.parser_not_found(lang), show_alert=True)
            return
        await state.set_state(SiteNameInput.waiting)
        await state.update_data(parser_key=key)
        title = parser.title_for(lang)
        await callback.message.edit_text(
            texts.ask_site_name(lang, title),
            reply_markup=keyboards.site_name_prompt_kb(key, title, lang),
        )
        await callback.answer()

    @router.message(SiteNameInput.waiting)
    async def save_site_name(
        message: Message, state: FSMContext, lang: Lang
    ) -> None:
        data = await state.get_data()
        try:
            site = await sites_service.create(
                data["parser_key"], name=message.text or "", lang=lang
            )
        except ValueError as exc:
            parser = registry.get(data["parser_key"])
            title = parser.title_for(lang) if parser else data["parser_key"]
            await message.answer(
                f"❌ {texts.esc(exc)}\n{texts.ask_site_name(lang, title)}"
            )
            return
        await state.clear()
        await message.answer(
            texts.site_added(lang, site.name),
            reply_markup=keyboards.site_card_kb(site, lang),
        )

    @router.callback_query(F.data.startswith("site:adddefault:"))
    async def add_site_default(
        callback: CallbackQuery, state: FSMContext, lang: Lang
    ) -> None:
        key = callback.data.split(":", 2)[2]
        await state.clear()
        try:
            site = await sites_service.create(key, lang=lang)
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await callback.message.edit_text(
            texts.site_added(lang, site.name),
            reply_markup=keyboards.site_card_kb(site, lang),
        )
        await callback.answer()
```

- [ ] **Step 4: Move the existing language test onto the default button**

In `tests/unit/test_bot_language.py`, in `test_adding_a_site_stores_the_name_in_the_admins_language`, the callback becomes `site:adddefault:steam_deals`, the handler name becomes `add_site_default`, and the call gains a state argument:

```python
        handler = _callback_handler(router, "add_site_default")
        callback = _Callback("site:adddefault:steam_deals")

        await handler(callback, state=_state(), lang=Lang.en)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_bot_site_name.py tests/unit/test_bot_language.py tests/unit/test_bot_menu.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/uwp/bot/handlers/sites.py tests/unit/test_bot_site_name.py tests/unit/test_bot_language.py
git commit -m "feat(bot): ask for a site name before creating the site"
```

---

### Task 4: Rename from the settings screen

**Files:**
- Modify: `src/uwp/bot/handlers/settings.py`
- Modify: `src/uwp/bot/handlers/sites.py` only if a circular import appears (do not add one pre-emptively)
- Test: `tests/unit/test_settings_parse.py`
- Test: `tests/unit/test_bot_site_name.py`

**Interfaces:**
- Consumes: `SiteService.rename(site_id, name, lang=...)`, `texts.name_button`, `texts.ask_site_name_rename`, `texts.site_name_saved`.
- Produces: settings button `set:name:<site_id>`; FSM state `RenameInput.waiting` with `data["site_id"]`; its own message handler so it cannot collide with the add wizard's state.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_settings_parse.py`, update `test_settings_kb_has_every_target`:

```python
    assert flat == [
        "set:name:3",
        "set:interval:3",
        "set:profile:3",
        "set:proxy:3",
        "set:export:3",
        "set:params:3",
        "set:back:3",
    ]
```

Append to `tests/unit/test_bot_site_name.py`:

```python
from uwp.bot.handlers import settings as settings_handlers


async def test_the_name_button_asks_for_the_current_name(bot):
    router, service, _sites = bot
    site = await service.create("steam_deals", name="Steam US")
    state = _state()
    callback = _Callback(f"set:name:{site.id}")

    await _handler(router, "callback_query", "settings_ask_name")(
        callback, state, lang=Lang.ru
    )

    assert "Steam US" in callback.message.answers[-1]
    assert await state.get_state() == settings_handlers.RenameInput.waiting.state


async def test_a_new_name_is_saved_and_the_settings_screen_comes_back(bot):
    router, service, _sites = bot
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
```

Note: `bot` builds only the sites router. Before these two tests, extend the fixture to include the settings router:

```python
        sites_router = sites_handlers.build_router(_Settings(), deps)
        settings_router = settings_handlers.build_router(_Settings(), deps)
        yield (sites_router, settings_router), service, sites
```

and change the three existing add-flow tests to unpack `(sites_router, _settings_router), service, sites` (their `_handler(...)` calls keep using `sites_router`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_settings_parse.py tests/unit/test_bot_site_name.py -q`
Expected: FAIL — no `set:name:3` in the settings keyboard, `RenameInput` and the two handlers are missing.

- [ ] **Step 3: Add the button and the state**

In `src/uwp/bot/handlers/settings.py`:

```python
class RenameInput(StatesGroup):
    waiting = State()
```

In `settings_kb`, first row:

```python
            [
                InlineKeyboardButton(
                    text=texts.name_button(lang),
                    callback_data=f"set:name:{site.id}",
                )
            ],
```

- [ ] **Step 4: Add the two handlers**

In `build_router` of `src/uwp/bot/handlers/settings.py`, after `ask_interval`:

```python
    @router.callback_query(F.data.regexp(r"^set:name:\d+$"))
    async def settings_ask_name(
        callback: CallbackQuery, state: FSMContext, lang: Lang
    ) -> None:
        site_id = int(callback.data.rsplit(":", 1)[1])
        site = await service.get(site_id)
        if site is None:
            await callback.answer(texts.site_not_found(lang), show_alert=True)
            return
        await state.set_state(RenameInput.waiting)
        await state.update_data(site_id=site_id)
        await callback.message.answer(texts.ask_site_name_rename(lang, site.name))
        await callback.answer()

    @router.message(RenameInput.waiting)
    async def settings_save_name(
        message: Message, state: FSMContext, lang: Lang
    ) -> None:
        data = await state.get_data()
        try:
            site = await service.rename(
                data["site_id"], message.text or "", lang=lang
            )
        except ValueError as exc:
            # Missing site and a rejected name both land here; ``coded_error``
            # keeps a service sentence as it is and translates a catalogue code.
            await message.answer(f"❌ {texts.esc(exc)}")
            return
        await state.clear()
        await message.answer(
            texts.site_name_saved(lang, site.name),
            reply_markup=settings_kb(site, lang),
        )
```

The add wizard's `SiteNameInput` and this `RenameInput` must stay separate states: both routers register a bare `@router.message(<state>)` handler, and one shared state would make the same message create a site *and* rename one.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_settings_parse.py tests/unit/test_bot_site_name.py tests/unit/test_bot_language.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/uwp/bot/handlers/settings.py tests/unit/test_settings_parse.py tests/unit/test_bot_site_name.py
git commit -m "feat(bot): rename a site from its settings screen"
```

---

### Task 5: CLI validation, docs and the full gate

**Files:**
- Modify: `src/uwp/cli.py:539-570`
- Modify: `README.md`, `README.ru.md`
- Test: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: `clean_site_name` from Task 1.
- Produces: nothing new; `uwp site set --name` now applies the same rules as the bot and prints the new name.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_cli.py`:

```python
def test_site_rename_normalises_the_name(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    main(["init"])

    assert main(["site", "set", "steam_skidki", "--name", "  Мой   Сайт "]) == 0
    out = capsys.readouterr().out
    assert "Мой Сайт" in out

    assert main(["site", "list"]) == 0
    assert "Мой Сайт" in capsys.readouterr().out


def test_site_rename_refuses_an_empty_name(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    main(["init"])

    assert main(["site", "set", "steam_skidki", "--name", "   "]) == 1

    assert "пустым" in capsys.readouterr().out


def test_site_rename_refuses_a_name_telegram_would_reject(capsys, monkeypatch, tmp_path):
    """The CLI writes the same column the bot renders on a button."""
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    main(["init"])

    assert main(["site", "set", "steam_skidki", "--name", "x" * 65]) == 1

    assert "64" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_cli.py -q -k rename`
Expected: FAIL — the whitespace name is stored verbatim, and the 65-character name is accepted.

- [ ] **Step 3: Validate before writing and print the new name**

In `src/uwp/cli.py`, `cmd_site_set`:

```python
from uwp.services.sites import clean_site_name
```

replace

```python
    if name is not None:
        fields["name"] = name
```

with

```python
    if name is not None:
        try:
            fields["name"] = clean_site_name(name, lang)
        except ValueError as exc:
            _print(f"❌ {exc}")
            return 1
```

and replace the success line so it reports what was written instead of the stale row:

```python
        await repo.update(site.id, **fields)
        _print(
            t(
                lang,
                "cli.site.updated",
                name=fields.get("name", site.name),
            )
        )
```

- [ ] **Step 4: Update both READMEs**

`README.md`, in the «🌐 Sites» bullet, add one clause before the card list:

```
- **🌐 Sites** — the list, plus «➕ Add site»; adding asks for a name
  («✅ Keep “Steam — deals”» keeps the parser's own), and «✏️ Name» on the
  site's settings renames it later.
```

`README.ru.md`, same bullet:

```
- **🌐 Сайты** — список и «➕ Добавить сайт»; при добавлении бот спрашивает
  название («✅ Оставить «Steam — скидки»» — имя парсера), а «✏️ Название» в
  настройках сайта переименовывает его позже.
```

Do not add or remove headings: `tests/unit/test_readme_translations.py` counts them.

- [ ] **Step 5: Run the full gate**

Run: `.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check src tests`
Expected: PASS, no ruff findings.

- [ ] **Step 6: Commit**

```bash
git add src/uwp/cli.py README.md README.ru.md tests/unit/test_cli.py docs/superpowers/specs/2026-11-20-site-custom-name-design.md
git commit -m "feat(cli): apply the bot's site-name rules to site set --name"
```
