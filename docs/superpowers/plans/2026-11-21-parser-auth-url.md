# Auth-URL в парсере — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** VNC-вход открывает объявленную парсером страницу входа (`auth_url`), а не URL прогона и не `example.com`, и берёт прокси того сайта, к которому привязан профиль.

**Architecture:** `BaseParser` получает `auth_url` и свойство `login_url` (`auth_url or url`), `url` остаётся URL прогона и единственным источником `report.meta.url`. `VncService` больше не знает `FALLBACK_URL`: он берёт сайт из `site_id` или — при входе с карточки профиля — единственный сайт с `sites.profile_id = profile.id`, и отказывает внятным предложением, если привязки нет, сайтов несколько или парсер недоступен.

**Tech Stack:** Python 3.12, aiosqlite, pydantic, aiogram 3, pytest (asyncio auto), ruff.

**Spec:** `docs/superpowers/specs/2026-11-21-parser-auth-url-design.md` (утверждена 2026-11-21).

## Global Constraints

- `auth_url` по умолчанию — пустая строка; пустая означает «взять `url`». Сторонний парсер без нового атрибута обязан работать без правок.
- `BaseParser.url` не меняется и остаётся единственным значением `report.meta.url`.
- `FALLBACK_URL = "https://example.com"` удаляется из `src/uwp/services/vnc.py` целиком.
- Русский для всего, что читает человек (тексты бота, вывод CLI, `errors.py`); английский для идентификаторов и docstring.
- Обе языковые версии README правятся в одном коммите: `test_readme_translations.py` сравнивает счётчики секций и кросс-ссылки.
- Никакой миграции: связь site→profile уже есть (`sites.profile_id`).
- Тесты сначала. Проверки: `.venv/bin/python -m pytest <файл> -q`, `.venv/bin/python -m ruff check src tests`. `ruff format` не запускать.
- `:Exec true`, kiosk-флаги и прочий VNC-стек не трогаются: меняется только выбор URL.

## Review Focus

- Профиль привязан к двум сайтам: вход обязан отказать, а не выбрать «первый» — у сайтов разные прокси, и вход через чужой прокси выглядит как взлом аккаунта (Task 3).
- `uwp profile login` на непривязанном профиле: предложение и код выхода `1`, а не traceback и не `example.com` (Task 4).
- Сайт с недоступным парсером (сломанный пакет, удалённый плагин): отказ с именем парсера, а не пустая страница (Task 3).
- Отчёт при заданном `auth_url`: в шапке по-прежнему `parser.url`, иначе в `.ods` попадёт страница входа (Task 1).
- Свежесозданный профиль и кнопка «🖥 Войти»: текст в языке читателя (Task 4), и VNC-стек при этом не запускается (Task 3, `manager.calls == []`) — иначе на машине без Xvfb админ ждёт таймаут.
- Вход из профиля по привязанному сайту берёт прокси сайта, а не профиля: страница входа гео-зависима (Task 3).

---

### Task 1: `auth_url` в контракте парсера

**Files:**
- Modify: `src/uwp/parsers/base.py`
- Modify: `src/uwp/parsers/steam_deals/parser.py:341-346`
- Test: `tests/unit/test_registry.py`, `tests/unit/test_steam_parser.py`, `tests/unit/test_reports.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `BaseParser.auth_url: str = ""`; `BaseParser.login_url -> str` (property, `auth_url or url`); `SteamDealsParser.auth_url == "https://store.steampowered.com/login/"`. `reports.report_meta` is unchanged and keeps writing `parser.url`.

- [ ] **Step 1: Write the failing tests**

В `tests/unit/test_registry.py`, рядом с `test_base_parser_defaults_without_settings_model`:

```python
def test_login_url_falls_back_to_the_run_url():
    """A third-party parser written before ``auth_url`` must keep working."""
    assert _MinimalParser().auth_url == ""
    assert _MinimalParser().login_url == "https://example.com/"


def test_login_url_prefers_the_declared_auth_page():
    class WithLogin(_MinimalParser):
        auth_url = "https://example.com/login"

    assert WithLogin().login_url == "https://example.com/login"
```

В `tests/unit/test_steam_parser.py`, в `test_parser_metadata_and_columns` после проверки `parser.url`:

```python
    # The VNC login must open the form, not the discounted search page.
    assert parser.auth_url == "https://store.steampowered.com/login/"
    assert parser.login_url == "https://store.steampowered.com/login/"
```

В `tests/unit/test_reports.py` рядом с `FakeParser` объявить парсер со страницей входа, а рядом с `test_meta_has_no_proxy_password_and_shows_the_local_time` — тест:

```python
class LoginParser(FakeParser):
    """Declares a login page; the report must still show the run URL."""

    key = "login"
    auth_url = "https://example.com/login"


def test_meta_url_stays_the_run_url_when_a_login_page_is_declared():
    meta = report_meta(
        site=_site(),
        parser=LoginParser(),
        profile=None,
        proxy=None,
        when=WHEN,
        tz="Europe/Moscow",
        duration_ms=1500,
        rows=ROWS,
        new_keys={"1"},
        changed_keys=set(),
    )

    assert meta["URL"] == "https://example.com/list"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_registry.py tests/unit/test_steam_parser.py tests/unit/test_reports.py -q`
Expected: FAIL — `AttributeError: 'SteamDealsParser' object has no attribute 'auth_url'`. Тест метаданных падает вместе с ним: `LoginParser` нельзя объявить, пока `auth_url` не разрешён в `BaseParser`.

- [ ] **Step 3: Implement `auth_url` and `login_url`**

В `src/uwp/parsers/base.py` после `url: str`:

```python
    # The page a VNC login opens. Empty means "the parser has no login form of
    # its own", and ``login_url`` then hands back the run URL. ``url`` itself
    # must stay the run URL: it is what the report's ``URL`` row shows.
    auth_url: str = ""
    requires_login: bool = False
```

и рядом с `description_for`:

```python
    @property
    def login_url(self) -> str:
        """The page a VNC login opens: ``auth_url`` or the run ``url``."""
        return self.auth_url or self.url
```

В `src/uwp/parsers/steam_deals/parser.py` рядом с `url = SEARCH_URL`:

```python
    auth_url = "https://store.steampowered.com/login/"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_registry.py tests/unit/test_steam_parser.py tests/unit/test_reports.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/uwp/parsers/base.py src/uwp/parsers/steam_deals/parser.py \
        tests/unit/test_registry.py tests/unit/test_steam_parser.py \
        tests/unit/test_reports.py
git commit -m "feat(parsers): let a parser declare its login page separately"
```

---

### Task 2: `SitesRepo.by_profile`

**Files:**
- Modify: `src/uwp/db/repo/sites.py:93-106`
- Test: `tests/unit/test_db.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `SitesRepo.by_profile(profile_id: int) -> list[Site]` — сайты, чей `profile_id` совпадает, по возрастанию `id`.

- [ ] **Step 1: Write the failing test**

В `tests/unit/test_db.py` после `test_site_due`:

```python
async def test_sites_by_profile(db):
    """"Log in with this profile" needs the sites that actually use it."""
    repo = SitesRepo(db)
    pid = await ProfilesRepo(db).create(make_profile("login"))
    mine = await repo.create(make_site("mine", profile_id=pid))
    await repo.create(make_site("other"))
    also = await repo.create(make_site("also-mine", profile_id=pid))

    assert [s.id for s in await repo.by_profile(pid)] == [mine, also]
    assert await repo.by_profile(9999) == []
```

`make_profile` и `ProfilesRepo` в этом файле уже импортированы.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_db.py::test_sites_by_profile -q`
Expected: FAIL — `AttributeError: 'SitesRepo' object has no attribute 'by_profile'`.

- [ ] **Step 3: Implement `by_profile`**

В `src/uwp/db/repo/sites.py` после `list()`:

```python
    async def by_profile(self, profile_id: int) -> list[Site]:
        """Sites this profile is attached to; a login needs exactly one."""
        rows = await self.db.fetchall(
            "SELECT * FROM sites WHERE profile_id = ? ORDER BY id", (profile_id,)
        )
        return [_row_to_site(r) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_db.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/uwp/db/repo/sites.py tests/unit/test_db.py
git commit -m "feat(db): list the sites attached to a profile"
```

---

### Task 3: `VncService` открывает страницу входа

**Files:**
- Modify: `src/uwp/services/vnc.py:19-101`
- Modify: `src/uwp/i18n/errors.py` (оба языковых блока, рядом с `error.vnc.session_not_found`)
- Modify: `src/uwp/app.py:222`
- Test: `tests/unit/test_vnc_service.py`, `tests/unit/test_app_wiring.py`

**Interfaces:**
- Consumes: `SitesRepo.by_profile` (Task 2), `BaseParser.login_url` (Task 1, только для проверки сборки в конце).
- Produces: `VncService(..., parser_auth_urls: dict[str, str] | None = None, ...)` — прежний `parser_urls` переименован; `VncService.request(profile_id, site_id=None, started_by=None, *, lang=None)` при отсутствии сайта поднимает `ValueError` с готовым предложением, а не открывает заглушку; собранный `rt.deps.vnc.parser_auth_urls["steam_deals"] == "https://store.steampowered.com/login/"`.

- [ ] **Step 1: Write the failing tests**

В `tests/unit/test_vnc_service.py` заменить `test_request_uses_the_parser_url` и оба теста, ожидающих `https://example.com`, на приведённые ниже. Хелпер `_site` получает слаг, иначе второй сайт упадёт на `UNIQUE(slug)`:

```python
async def _site(repo, slug="s"):
    return await repo.create(
        Site(
            slug=slug,
            name=slug.upper(),
            parser_key="steam_deals",
            interval_min_min=1,
            interval_max_min=2,
        )
    )
```

```python
async def test_request_opens_the_parser_auth_url(env):
    _db, profiles, sites, sessions, tmp_path = env
    pid = await _profile(profiles, tmp_path)
    sid = await _site(sites)
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path)})
    manager = FakeManager()
    svc = VncService(
        manager, profiles, sites, settings, sessions,
        parser_auth_urls={"steam_deals": "https://store.steampowered.com/login/"},
    )

    info = await svc.request(pid, site_id=sid, started_by=7)

    assert info == "info"
    assert manager.calls == [(pid, "https://store.steampowered.com/login/")]


async def test_request_from_a_profile_uses_its_single_site(env):
    """The profile card has no site id: the attached site supplies page and proxy."""
    _db, profiles, sites, sessions, tmp_path = env
    pid = await _profile(profiles, tmp_path)
    site = await _site(sites)
    proxy = Proxy(name="P", scheme=ProxyScheme.http, host="127.0.0.1", port=3128)
    from uwp.db.repo.proxies import ProxiesRepo

    proxy_id = await ProxiesRepo(_db).create(proxy)
    await sites.update(site, profile_id=pid, proxy_id=proxy_id)
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path)})
    manager = FakeManager()
    svc = VncService(
        manager, profiles, sites, settings, sessions,
        parser_auth_urls={"steam_deals": "https://store.steampowered.com/login/"},
    )

    await svc.request(pid)

    assert manager.calls == [(pid, "https://store.steampowered.com/login/")]
    assert manager.proxy_ids == [proxy_id]


async def test_request_from_a_profile_without_a_site_is_refused(env):
    """example.com told nobody which site to log in to, and the stack still ran."""
    _db, profiles, sites, sessions, tmp_path = env
    pid = await _profile(profiles, tmp_path)
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path)})
    manager = FakeManager()
    svc = VncService(manager, profiles, sites, settings, sessions)

    with pytest.raises(ValueError, match="не привязан"):
        await svc.request(pid)

    assert manager.calls == []


async def test_request_from_a_profile_with_two_sites_is_refused(env):
    _db, profiles, sites, sessions, tmp_path = env
    pid = await _profile(profiles, tmp_path)
    first = await _site(sites, "a")
    second = await _site(sites, "b")
    await sites.update(first, profile_id=pid)
    await sites.update(second, profile_id=pid)
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path)})
    svc = VncService(FakeManager(), profiles, sites, settings, sessions)

    with pytest.raises(ValueError, match="нескольким сайтам"):
        await svc.request(pid)


async def test_request_refuses_a_site_whose_parser_is_missing(env):
    """A removed or broken parser used to fall back to an unrelated page."""
    _db, profiles, sites, sessions, tmp_path = env
    pid = await _profile(profiles, tmp_path)
    sid = await _site(sites)
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path)})
    svc = VncService(FakeManager(), profiles, sites, settings, sessions)

    with pytest.raises(ValueError, match="steam_deals"):
        await svc.request(pid, site_id=sid)


async def test_request_refuses_an_unknown_site_id(env):
    _db, profiles, sites, sessions, tmp_path = env
    pid = await _profile(profiles, tmp_path)
    settings = load_settings(
        {"UWP_DATA_DIR": str(tmp_path), "UWP_DEFAULT_LANGUAGE": "en"}
    )
    svc = VncService(FakeManager(), profiles, sites, settings, sessions)

    with pytest.raises(ValueError, match="site 999 not found"):
        await svc.request(pid, site_id=999)
```

`test_request_passes_the_site_proxy_to_the_session` дополнить `parser_auth_urls={"steam_deals": "https://store.steampowered.com/login/"}` — иначе отказ вместо запуска.

`test_deferred_request_only_books_a_session` заменить ожидание:

```python
    site = await _site(sites)
    await sites.update(site, profile_id=pid)
    svc = VncService(
        manager, profiles, sites, settings, sessions,
        parser_auth_urls={"steam_deals": "https://store.steampowered.com/login/"},
        eager_launch=False,
    )

    await svc.request(pid)

    assert manager.calls == [(pid, "https://store.steampowered.com/login/")]
    assert manager.launched == []
```

Плюс тест языка (как у `test_the_missing_profile_error_follows_the_deployment_language`):

```python
async def test_the_unattached_profile_refusal_follows_the_deployment_language(env):
    _db, profiles, sites, sessions, tmp_path = env
    pid = await _profile(profiles, tmp_path)
    settings = load_settings(
        {"UWP_DATA_DIR": str(tmp_path), "UWP_DEFAULT_LANGUAGE": "en"}
    )
    svc = VncService(FakeManager(), profiles, sites, settings, sessions)

    with pytest.raises(ValueError, match="not attached to any site"):
        await svc.request(pid)
```

И в `tests/unit/test_app_wiring.py` — тест сборки, чтобы `app.py` не остался со старым именем аргумента:

```python
async def test_runtime_feeds_the_login_pages_to_vnc(tmp_path):
    """The bot and the worker are wired separately; this is the bot's half."""
    rt = await _runtime(tmp_path)
    try:
        assert rt.deps.vnc.parser_auth_urls["steam_deals"] == (
            "https://store.steampowered.com/login/"
        )
    finally:
        await rt.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_vnc_service.py tests/unit/test_app_wiring.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'parser_auth_urls'`.

- [ ] **Step 3: Add the three error keys**

В `src/uwp/i18n/errors.py`, в русском блоке рядом с `error.vnc.session_not_found`:

```python
        "error.vnc.profile_without_site": (
            "профиль не привязан ни к одному сайту: привяжите его в настройках сайта"
        ),
        "error.vnc.profile_many_sites": (
            "профиль привязан к нескольким сайтам: войдите с карточки нужного сайта"
        ),
        "error.vnc.site_parser_unavailable": (
            "парсер сайта «{parser}» недоступен: обновите или включите его"
        ),
```

и в английском:

```python
        "error.vnc.profile_without_site": (
            "the profile is not attached to any site: attach it in the site settings"
        ),
        "error.vnc.profile_many_sites": (
            "the profile is attached to several sites: log in from the site you need"
        ),
        "error.vnc.site_parser_unavailable": (
            "the site's parser “{parser}” is unavailable: update or enable it"
        ),
```

- [ ] **Step 4: Implement the resolution in `VncService`**

В `src/uwp/services/vnc.py`:

1. Удалить `FALLBACK_URL = "https://example.com"` и его импорт-соседей не трогать.
2. Переименовать параметр конструктора `parser_urls` → `parser_auth_urls` и поле `self.parser_urls` → `self.parser_auth_urls`; docstring — «pages a VNC login opens, per parser key».
3. Добавить приватный метод:

```python
    async def _login_site(
        self, profile_id: int, site_id: int | None, lang: Lang
    ) -> Site:
        """The site whose auth page the login opens.

        A login started from a site card names it; the profile card does not,
        so a profile attached to exactly one site lends that site's page and
        proxy. Two attached sites are refused rather than guessed: their
        proxies can differ, and a login through the wrong country looks like a
        hijacked account.
        """
        if site_id is not None:
            site = await self.sites.get(site_id)
            if site is None:
                raise ValueError(
                    t(lang, "error.site.not_found", id=site_id)
                )
            return site
        attached = await self.sites.by_profile(profile_id)
        if not attached:
            raise ValueError(t(lang, "error.vnc.profile_without_site"))
        if len(attached) > 1:
            raise ValueError(t(lang, "error.vnc.profile_many_sites"))
        return attached[0]
```

4. Тело `request()`: после получения `profile` заменить блок выбора URL на

```python
        reader = lang or self.lang
        site = await self._login_site(profile_id, site_id, reader)
        url = self.parser_auth_urls.get(site.parser_key)
        if url is None:
            raise ValueError(
                t(reader, "error.vnc.site_parser_unavailable", parser=site.parser_key)
            )
        proxy_id = site.proxy_id or profile.proxy_id
```

`Site` добавить в импорт: `from uwp.domain import Site, VncSession`.

5. В `src/uwp/app.py:222` переименовать аргумент сборки, иначе приложение не поднимется:

```python
        parser_auth_urls={p.key: p.login_url for p in registry.all()},
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_vnc_service.py tests/unit/test_app_wiring.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/uwp/services/vnc.py src/uwp/i18n/errors.py src/uwp/app.py \
        tests/unit/test_vnc_service.py tests/unit/test_app_wiring.py
git commit -m "fix(vnc): open the site's login page instead of example.com"
```

---

### Task 4: Бот и CLI

**Files:**
- Modify: `src/uwp/cli.py:678-726, 962-963`
- Modify: `src/uwp/i18n/cli.py` (оба языковых блока: новый ключ `cli.help.profile_login_site`)
- Test: `tests/unit/test_bot_refusals.py`, `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: `VncService.request(..., site_id=...)` (Task 3), `ParserRegistry` (Task 1).
- Produces: `uwp profile login <id> [--site <slug>]`; кнопка «🖥 Войти» из карточки профиля отвечает отказом в языке читателя, если привязки нет (правки обработчика не нужны — он уже рендерит `coded_error`, а сервис отдаёт готовое предложение).

- [ ] **Step 1: Write the failing tests**

В `tests/unit/test_bot_refusals.py` после `test_a_missing_login_profile_is_refused_in_the_readers_language`:

```python
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
```

В `tests/unit/test_cli.py` после `test_profile_create_reports_a_duplicate_instead_of_a_traceback`:

```python
def test_profile_login_says_when_the_profile_has_no_site(capsys, monkeypatch, tmp_path):
    """``uwp profile login`` opened example.com and never named the reason."""
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    main(["profile", "create", "Steam1"])

    assert main(["profile", "login", "1"]) == 1
    assert "не привязан" in capsys.readouterr().out


def test_profile_login_rejects_an_unknown_site_slug(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    main(["profile", "create", "Steam1"])

    assert main(["profile", "login", "1", "--site", "nope"]) == 1
    assert "не найден" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_bot_refusals.py tests/unit/test_cli.py -q`
Expected: бот-тест падает на `("https://example.com"…)`-подобном ответе (стек не стартует, поэтому ответ — первый `answers[0]`); CLI-тест падает с `SystemExit`/`unrecognized arguments: --site`.

- [ ] **Step 3: Extend the CLI command**

В `src/uwp/i18n/cli.py`, в `cli.help.profile_login` добавить упоминание сайта, и новый ключ рядом с `cli.profile.not_found`:

```python
        "cli.help.profile_login": "войти через VNC (сайт: --site <slug>)",
        "cli.profile.no_site": (
            "❌ Профиль {id} не привязан ни к одному сайту: укажите "
            "--site <slug> или привяжите его в настройках сайта."
        ),
```

```python
        "cli.help.profile_login": "log in through VNC (site: --site <slug>)",
        "cli.profile.no_site": (
            "❌ Profile {id} is not attached to any site: pass --site <slug> "
            "or attach it in the site settings."
        ),
```

В `src/uwp/cli.py`:

1. В `build_parser`, рядом с `gain.add_argument("profile_id", type=int)`:

```python
    gain.add_argument(
        "--site", default=None, help=tr("cli.help.profile_login_site")
    )
```

и ключ `cli.help.profile_login_site`: ru `"сайт, чью страницу входа открыть"`, en `"the site whose login page to open"`.

2. `cmd_profile_login(settings, profile_id, site_slug=None)`:
   - `_dispatch_profile` передаёт `args.site`;
   - в `action` перед `svc = …` собрать страницы входа из реестра:
     `from uwp.parsers.registry import ParserRegistry` и
     `parser_auth_urls={p.key: p.login_url for p in ParserRegistry.discover().all()}`;
   - если задан `--site`, найти его по слагу: `_find_site(await SitesRepo(db).list(), site_slug)`; не найден → `_print(t(lang, "cli.site.not_found", slug=site_slug))`, `return 1`;
   - вызов обернуть в `try/except ValueError`, потому что сервис теперь отказывает и без `site_id`:

```python
        try:
            info = await svc.request(
                profile_id, site_id=site.id if site is not None else None
            )
        except ValueError as exc:
            _print(f"❌ {exc}")
            return 1
```

3. Отдельного текста про непривязанный профиль в CLI не заводить: `svc.request` без `site_id` сам ищет привязку и отдаёт `error.vnc.profile_without_site` в языке развёртывания, а строка `❌ {exc}` называет причину. Второй ключ с тем же смыслом разошёлся бы с первым.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_bot_refusals.py tests/unit/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/uwp/cli.py src/uwp/i18n/cli.py tests/unit/test_bot_refusals.py tests/unit/test_cli.py
git commit -m "feat(cli): let profile login name the site whose page to open"
```

---

### Task 5: Документация

**Files:**
- Modify: `README.md`, `README.ru.md`
- Modify: `docs/README.md`, `docs/README.ru.md`
- Modify: `src/uwp/parsers/steam_deals/README.md`, `src/uwp/parsers/steam_deals/README.ru.md`
- Modify: `docs/superpowers/specs/2026-11-21-parser-auth-url-design.md` (статус → «утверждена 2026-11-21»)
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: everything above.
- Produces: ни одного места, обещающего `example.com` или «браузер откроет страницу сайта» без привязки профиля.

- [ ] **Step 1: README (Manual login over VNC)**

`README.md`, в разделе «Manual login over VNC» заменить абзац про два пути на:

```markdown
A site with a login uses a persistent profile. Attach the profile to the site
in the site's settings first: the login then opens the site's own login page
(the parser declares it as `auth_url`) and takes the site's proxy. Start it
from the site card («🖥 Log in to profile») or from «👤 Profiles» → a profile →
«🖥 Log in». A profile attached to no site is refused with a hint, and one
attached to several sites is refused too — their proxies can differ, so pick
the site you mean.
```

`README.ru.md` — тот же абзац по-русски в том же месте, с тем же смыслом.

- [ ] **Step 2: docs README (parser contract + план/спека в таблицах)**

`docs/README.md`, в «Writing your own parser» после `url = "https://example.com/"`:

```python
    auth_url = "https://example.com/login"
```

и после абзаца про язык — предложение:

```markdown
`url` is the page the parser scrapes and the one the report's `URL` row shows;
`auth_url` is the page a VNC login opens. Leave `auth_url` out and the login
uses `url` — that is what a parser written before the attribute relies on.
```

`docs/README.ru.md` — то же в своём разделе.

В обеих таблицах (спеки и планы) добавить строки о `2026-11-21-parser-auth-url-design.md` и `2026-11-21-parser-auth-url.md` в том же формате, что соседние.

- [ ] **Step 3: README парсера Steam**

В `src/uwp/parsers/steam_deals/README.md`, в «How it works», первым пунктом:

```markdown
- `auth_url` points at `https://store.steampowered.com/login/`: a VNC login
  opens the form, while `url` stays the discounted search page the report
  names.
```

`README.ru.md` — то же по-русски.

- [ ] **Step 4: AGENTS.md**

В разделе инвариантов, после абзаца про `external_key`:

```markdown
**`auth_url` is the login page, `url` is the run page.** The VNC browser opens
`BaseParser.login_url` (`auth_url or url`), while `reports.report_meta` writes
`parser.url`. Do not merge the two to "simplify": a report whose `URL` row is a
login form points the reader at nothing. A login from the profile card resolves
the site through `sites.profile_id` and refuses when zero or two sites match —
guessing would spend the wrong proxy on a login.
```

- [ ] **Step 5: Mark the spec approved and check the docs tests**

Run: `.venv/bin/python -m pytest tests/unit/test_readme_translations.py tests/unit/test_i18n.py -q`
Expected: PASS (счётчики секций и ключи совпадают).

- [ ] **Step 6: Commit**

```bash
git add README.md README.ru.md docs/README.md docs/README.ru.md AGENTS.md \
        src/uwp/parsers/steam_deals/README.md \
        src/uwp/parsers/steam_deals/README.ru.md \
        docs/superpowers/specs/2026-11-21-parser-auth-url-design.md
git commit -m "docs: describe the parser's separate login page"
```

---

### Task 6: Полная проверка

**Files:** none (проверка).

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, `0 failed`; прежние 3 skip допустимы.

- [ ] **Step 2: Run the linter**

Run: `.venv/bin/python -m ruff check src tests`
Expected: `All checks passed!`

- [ ] **Step 3: Grep for the removed fallback**

Run: `grep -rn "FALLBACK_URL\|parser_urls" src tests`
Expected: пусто (кроме исторических `docs/superpowers/`).

- [ ] **Step 4: Commit any fix**

Если шаги 1–3 потребовали правок — отдельный коммит `fix(vnc): …` с измеренным симптомом в теле.
