# UltimateWebParser — дизайн

Дата: 2026-11-13
Статус: на утверждении

## 1. Зачем

Нужен самохостящийся сервис, который по расписанию обходит разные сайты через
Playwright в anti-detect браузере (fingerprint-chromium), собирает данные и
присылает админу табличные отчёты LibreOffice Calc (`.ods`) в Telegram. Сайтов будет много и они разные,
поэтому парсеры должны добавляться как самостоятельные модули, без правок ядра.

Управление — Telegram-бот с кнопками: включить/выключить парсинг, поменять
интервал, выбрать профиль и прокси, запустить вручную, зайти в профиль руками
для логина.

Первый парсер для проверки — скидки на игры в Steam (`store.steampowered.com`),
без авторизации.

## 2. Что должно получиться

- `docker compose up -d` — и сервис работает на любой машине с Docker.
- Один процесс (или два в split-режиме): Telegram-бот + планировщик + воркер
  браузерных задач.
- Парсеры лежат в `uwp/parsers/<key>/parser.py`, сами объявляют свои настройки,
  колонки и логику. Ядро про конкретные сайты ничего не знает.
- Профили двух видов:
  - **чистый (clean)** — новый `user-data-dir` + случайный seed на каждый
    прогон, папка удаляется после прогона (см. §8);
  - **постоянный (persistent)** — фиксированный `user-data-dir` + фиксированный
    seed, чтобы логин и cookies жили между прогонами.
- У сайта задаётся профиль (конкретный или «чистый из пула») и прокси.
- Расписание: интервал «от–до» минут, следующий запуск = сейчас + случайное
  число из диапазона. Плюс ручной запуск.
- Ручной логин: бот поднимает временный VNC-сервер на свободном порту и даёт
  ссылку на noVNC в браузере; по кнопке «Готово» (или по таймауту) сессия
  гасится, cookies остаются в профиле.
- Отчёт `.ods` приходит админу после прогона; имя файла осмысленное.
- Бот отвечает только админам из allowlist.

## 3. Границы

**Входит в v1:** ядро (БД, профили, прокси, планировщик, воркер, отчёты `.ods`, VNC),
бот, CLI, парсер Steam, Docker, тесты, README.

**Не входит:** веб-панель, несколько пользователей/ролей, распределённый
воркер на несколько машин, OCR/капча-сервисы, автозакупки, S3, Postgres.

**Anti-goals:** парсеры не должны требовать правок ядра; бот не должен
содержать бизнес-логику парсинга; профиль не должен «плыть» между прогонами
(seed и user-data-dir фиксированы); никаких секретов в git.

## 4. Технические решения

- Python 3.12 в образе (локально проверено и на 3.14), asyncio.
- Playwright (async API) + `executable_path` на fingerprint-chromium.
  Проверено: Chromium 150.0.7871.186, флаги отпечатка работают.
- aiogram 3 для бота, SQLite (WAL) для состояния, odfpy для отчётов `.ods`.
- Один Docker-образ, два процесса. Режим `combined` (по умолчанию) и
  `split` (`bot` + `worker` сервисы на общем volume).
- VNC: Xvfb + fluxbox + x11vnc + noVNC (websockify), всё внутри контейнера,
  порты из диапазона `UWP_VNC_PORT_MIN..MAX` проброшены в compose.
- Прокси: локальный bridge-процесс (HTTP CONNECT + SOCKS5 upstream, с
  авторизацией), Chromium ходит на `127.0.0.1:<port>`. Нужно потому, что
  `--proxy-server` у fingerprint-chromium не умеет логин/пароль.

## 5. Архитектура

```
                 ┌──────────────────────────── Docker container ─┐
 Telegram ──────▶│ bot (aiogram)                                 │
                 │   │ enqueue/cancel/настройки                  │
                 │   ▼                                           │
                 │ SQLite (WAL): sites, profiles, proxies, jobs, │
                 │               runs, items, vnc_sessions       │
                 │   ▲                                           │
                 │   │ claim jobs, heartbeat                    │
                 │ worker (scheduler + runner)                   │
                 │   ├─ proxy bridge (127.0.0.1)                 │
                 │   ├─ Playwright → fingerprint-chromium         │
                 │   ├─ parser module → rows                     │
                 │   ├─ odfpy → .ods                             │
                 │   └─ notify bot → sendDocument                │
                 │ vnc host: Xvfb + x11vnc + websockify          │
                 └───────────────────────────────────────────────┘
```

Модули:

```
src/uwp/
  config.py          настройки из env (+ .env), валидация
  db.py              подключение, миграции (schema_version), репозитории
  models.py          pydantic-модели домена
  scheduler.py       расчёт next_run_at, постановка плановых задач
  worker.py          цикл: claim → run → heartbeat → finish; retry/backoff
  runner.py          оркестрация одного прогона (профиль, прокси, браузер)
  browser.py         сборка флагов, запуск persistent context
  profiles.py        создание/ротация/аренда профилей, lock/lease
  proxybridge.py     локальный прокси с авторизацией upstream
  vnc.py             Xvfb/x11vnc/noVNC сессии, порты, TTL, stop
  ods.py             запись ods, шаблон имени, лист «Метаданные»
  notify.py          события worker → bot (таблица events + long-poll)
  parsers/
    base.py          BaseParser, ParseContext, ParseResult, Column
    registry.py      автообнаружение парсеров
    steam_deals/     parser.py, README.md
  bot/
    app.py           dispatcher, middleware админа
    keyboards.py     инлайн-клавиатуры
    handlers/        sites.py, site_settings.py, profiles.py, proxies.py,
                     vnc.py, runs.py, common.py
  cli.py             uwp <команда>
tests/
  unit/ …            без сети
  integration/ …     локальные HTML-фикстуры, Playwright
  live/ …            помечено @pytest.mark.live, реальный Steam
```

## 6. Домен и данные

**Site** — экземпляр парсера: `id`, `slug`, `name`, `parser_key`,
`parser_settings` (JSON), `enabled`, `profile_mode` (`clean`|`persistent`),
`profile_id`, `proxy_id`, `interval_min_min`, `interval_max_min`,
`next_run_at`, `export_mode` (`always`|`changes`|`never`),
`max_attempts`, `last_run_at`, `last_status`, `consecutive_failures`,
`match_profile_to_proxy_country` (bool, default false).
Один парсер может быть создан несколько раз (например, Steam US и Steam RU).

**Profile** — `id`, `name`, `kind` (`clean`|`persistent`), `seed` (int32),
`platform` (`windows`|`macos`|`linux`), `brand` (`Chrome`|`Edge`|…),
`brand_version`, `timezone`, `locale`, `user_data_dir`, `proxy_id` (nullable),
`status` (`free`|`locked`|`login`|`expired`), `lease_until`, `last_used_at`,
`expires_at` (для clean: now + 24 ч после прогона).

**Proxy** — `id`, `name`, `scheme` (`http`|`https`|`socks5`), `host`, `port`,
`username`, `password` (шифрование Fernet ключом из env, если задан
`UWP_SECRET_KEY`, иначе plaintext + предупреждение в `uwp doctor`), `country`
(ISO-код, необязательно, для отображения и подбора отпечатка).

**Job** — `id`, `site_id`, `kind` (`scheduled`|`manual`), `status`
(`queued`|`running`|`done`|`failed`|`canceled`), `priority`, `attempt`,
`claimed_by`, `claimed_at`, `heartbeat_at`, `error`, `created_at`.

**Run** — `id`, `job_id`, `site_id`, `started_at`, `finished_at`, `status`,
`rows_count`, `new_count`, `report_path`, `artifact_dir`, `error`,
`duration_ms`, `profile_id`, `proxy_id`.

**Item** — `id`, `site_id`, `external_key`, `payload` (JSON), `first_seen_at`,
`last_seen_at`. Нужен для «новых» позиций и режима `changes`.

**VncSession** — `id`, `profile_id`, `port`, `display`, `password`,
`token`, `status`, `started_at`, `expires_at`, `started_by`.

Миграции — нумерованные SQL в `uwp/migrations/`, применяются на старте.

## 7. Intel: планировщик и очередь

- Планировщик раз в 15 с смотрит `enabled=1 AND next_run_at<=now` и создаёт
  `Job(kind=scheduled)`, не плодя дубликатов (уникальный индекс на
  `site_id, status='queued'`).
- После завершения прогона `next_run_at = now + uniform(min, max)`.
  Для `interval_min == interval_max` — фиксированный интервал.
- Воркер забирает задачи по `priority DESC, created_at`; берёт lease на профиль
  в БД (`status='locked'`, `lease_until`), продлевает heartbeat каждые 30 с.
  Просроченные lease освобождаются при старте и в цикле.
- Concurrency: `UWP_MAX_PARALLEL_RUNS` (по умолчанию 1). Сайт, чей профиль
  занят (например, идёт ручной логин), пропускается до следующего тика.
- Ошибки: до `max_attempts` (по умолчанию 2) с паузой 60 с × attempt,
  затем `failed` и уведомление админу; после 3 подряд провалов бот помечает
  сайт «⚠️ проблемы» (без авто-выключения).
- Cancel: флаг в БД, воркер проверяет между шагами и рвёт контекст.
- Все времена в БД — UTC; бот показывает в `UWP_TZ` (по умолчанию
  `Europe/Moscow`).

Переходы состояния сайта:

- создан/включён → `next_run_at = now` (первый прогон сразу, чтобы админ
  увидел результат, не ожидая интервала);
- ручной запуск создаёт `Job(kind=manual)` и **не** сдвигает `next_run_at`;
  если по сайту уже есть `queued`/`running` job, ручной запуск приклеивается
  к нему (бот отвечает «уже выполняется») вместо второй параллельной задачи;
- после простоя (бот лежал) просроченный план даёт **один** догоняющий
  прогон, а не очередь из всех пропущенных; следующий считается от `now`;
- выключение сайта отменяет его `queued` jobs и снимает `running` (воркер
  увидит флаг и остановится);
- ретраи — та же `Job` с `attempt+1`, а не новая.

Таймауты и «зависший» воркер:

- на прогон — `UWP_RUN_TIMEOUT` (по умолчанию 300 с), на открытие страницы —
  30 с, на навигацию — 60 с; по таймауту браузер закрывается принудительно,
  прогон → `failed (timeout)`, профиль освобождается, возможен retry;
- heartbeat воркера пишется в `worker_heartbeat` каждые 15 с; если heartbeat
  старше 90 с, другой воркер (или старт процесса) считает `running` jobs
  осиротевшими и возвращает их в `queued` (attempt+1), а `locked` профили
  освобождает. Так зависший браузер не держит профиль вечно;
- при старте процесса — та же процедура восстановления.

Ошибки прогона делятся на виды, и от вида зависит поведение:

| kind | пример | retry | уведомление |
|---|---|---|---|
| `login_required` | парсер видит форму входа | нет | кнопка «🖥 Войти в профиль» |
| `blocked` | капча, 403, «unusual traffic» | 1 раз, потом стоп | скриншот + совет по прокси |
| `transient` | таймаут, 502, обрыв | да, с backoff | только после исчерпания |
| `parser_error` | исключение в коде парсера | нет | трейсбек админу |
| `config_error` | нет браузера, битые настройки | нет | как чинить |

Парсер может вернуть частичный результат вместе с ошибкой: строки
сохраняются в `Run` и попадают в отчёт, но прогон помечается `partial`
(жёлтый статус в боте), а не `done`.

## 8. Профили и отпечатки

Флаги запуска: `--fingerprint=<seed>`, `--fingerprint-platform`,
`--fingerprint-brand`, `--fingerprint-brand-version`, `--timezone`, `--lang`,
`--accept-lang`, `--user-data-dir`, `--disable-non-proxied-udp`,
`--proxy-server=http://127.0.0.1:<bridge>`, `--no-first-run`,
`--no-default-browser-check`, `--disable-features=Translate`.
Парсинг — headless (`--headless=new`); VNC-логин — headful под Xvfb.

- Persistent: `data/profiles/<uuid>/` — создаётся один раз, seed и параметры
  хранятся в БД; повторный запуск даёт тот же отпечаток и те же cookies.
- Clean: одноразовый профиль на прогон. Создаём запись `Profile(kind=clean)`
  с случайным seed и согласованным набором (платформа + brand + язык +
  таймзона + viewport из одного «профиля машины», например
  `Windows 11 / Chrome / en-US / America/New_York`, выбирается взвешенно по
  реальному распределению), папка `data/profiles/tmp/<run_id>/`, после
  прогона (успех или провал) папка удаляется, запись помечается `expired`
  и чистится через сутки. Никакого переиспользования между прогонами и
  сайтами: у каждого прогона своя личность. Retry после ошибки — новый
  чистый профиль (старые cookies и fingerprint не тащим).
- Persistent-профиль нельзя одновременно использовать двумя сайтами и
  нельзя редактировать seed/платформу после создания (иначе «тот же»
  отпечаток меняется). Один профиль может быть выбран у нескольких сайтов,
  но выполняться они будут по очереди.
- «Согласованность» важнее географии: clean-отпечаток не обязан совпадать
  с регионом парсера. Для Steam `region=ru` влияет только на `cc` в URL и
  валюту, а не на платформу/язык браузера. Если хочется совпадения — у
  прокси есть поле `country`, и для сайта можно включить
  `match_profile_to_proxy_country=true` (тогда таймзона/язык берутся под
  страну прокси, а регион парсера — нет).
- Логин-профиль нельзя использовать, пока идёт VNC-сессия; плановый прогон
  переносится.
- Привязка прокси: `site.proxy_id` важнее `profile.proxy_id`. Если у профиля
  есть свой прокси, а сайт задаёт другой — бот предупреждает (смена IP при
  тех же cookies выглядит подозрительно).

## 9. Прокси-bridge

`uwp/proxybridge.py` поднимает на `127.0.0.1:0` HTTP-прокси:
`CONNECT host:port` и plain HTTP, апстрим `http/https/socks5`, при
необходимости с `Proxy-Authorization`. DNS решается на стороне апстрима для
socks5; для HTTP-прокси — через CONNECT по имени хоста. Логирование трафика —
только факт подключения и байты, без URL-путей (приватность).
Живёт только на время прогона/VNC-сессии. Тесты: фейковый апстрим + реальный
`curl --proxy` в integration-тесте.

## 10. VNC-логин

Сценарий:

1. Админ жмёт «🖥 Войти в профиль» → бот проверяет, что профиль persistent,
   не занят парсингом, и создаёт `VncSession`.
2. `vnc.py` занимает display (`:100+N`), порт из `UWP_VNC_PORT_MIN..MAX`
   (проверка на занятость), генерирует пароль (12 символов) и token.
3. Запускает Xvfb, fluxbox, fingerprint-chromium (headful, тот же
   user-data-dir/seed/прокси) с `--start-maximized`, затем x11vnc
   (`-rfbauth`, `-localhost`) и websockify, отдающий noVNC.
4. Бот присылает: ссылку `http://<UWP_PUBLIC_HOST>:<port>/vnc.html?...`,
   пароль и кнопки «Готово», «Продлить», «Открыть заново».
5. «Готово» → гасим процессы, чистим X-локи, `profile.status='free'`,
   бот подтверждает. Автостоп: 60 мин жёстко, 20 мин без подключённого
   клиента (проверка `x11vnc -query` / счётчик соединений).
6. Если браузер упал — сессия помечается failed, админ получает причину
   и кнопку перезапуска.

`uwp profile login <profile> [--url …]` делает то же из CLI (для локального
запуска без Docker; выдаёт ссылку в stdout). Для локального режима нужны
`xvfb`, `x11vnc`, `novnc`/`websockify`; `uwp doctor` скажет, чего нет.

Безопасность: сессию может начать только админ; порт и пароль случайные;
TTL; x11vnc слушает только localhost, наружу смотрит только websockify;
в README — рекомендация ставить за reverse-proxy с TLS, если хост публичный.

**Split-режим.** VNC-сессии живут только в контейнере `worker` (там же, где
браузер). Бот не запускает процессы — он создаёт `VncSession(status=requested)`
и ждёт, пока воркер её подхватит (long-poll по таблице), затем получает
`port/token/password` и присылает ссылку. Порт публикует сервис `worker`
(диапазон `6000-6100:6000-6100`), а не `bot`. `uwp vnc stop` из CLI пишет
`status=stopping` в БД — воркер гасит. `uwp vnc list` читает БД и показывает
сессии обоих режимов. В combined-режиме всё в одном процессе.
Прокси для логина: если сайтов несколько, берём `site.proxy_id` сайта, с
которого нажали кнопку; если кнопка нажата из карточки профиля — `profile.proxy_id`.

## 11. Парсеры: контракт

```python
class BaseParser(ABC):
    key: str                      # "steam_deals"
    title: str                    # "Steam — скидки"
    description: str
    url: str
    requires_login: bool = False
    settings_model: type[BaseModel]   # pydantic, с описаниями для UI
    columns: list[Column]             # key, title, width, fmt

    @classmethod
    def default_settings(cls) -> dict: ...

    async def run(self, ctx: ParseContext) -> ParseResult: ...
```

`ParseContext`: `page`, `context`, `settings`, `log`, `artifacts_dir`,
`site`, `profile`, `known_keys()`, `save_artifact(name, bytes)`.
`ParseResult`: `rows: list[dict]`, `meta: dict`, `notes: str | None`.
Ошибки парсер бросает типизированно: `ParseError(kind=..., message=...)` с
`kind` из таблицы §7; частичные данные возвращаются в `ParseResult` вместе
с `notes`. Всё, что не `ParseError`, считается `parser_error` и уходит
админу с трейсбеком.

- Обнаружение: `registry.discover()` обходит `uwp/parsers/*/parser.py`,
  импортирует и требует ровно один класс-наследник `BaseParser`.
  Ошибка импорта не роняет сервис — парсер помечается `broken` и виден в
  `uwp parsers list` и в боте.
- Настройки сайта валидируются `settings_model`; UI формы генерируется из
  pydantic-полей (тип, default, описание, ge/le, Literal → кнопки).
- Новый парсер = папка + перезапуск. `uwp parsers list`, `uwp site add <key>`.
- Колонки отчёта берутся из `columns`; плюс служебные `url` и `collected_at`.
- Одинаковые строки между прогонами определяются по `external_key`
  (парсер сам задаёт, например `app_id`).

### Steam

`https://store.steampowered.com/search/` с `specials=1`,
`filter=topsellers`, `cc=<region>`, `l=<language>`, пагинация `start=` по 50.
Настройки: `region` (us/ru/de/…, default us), `language` (english/russian/…),
`filter` (topsellers/newreleases/specials), `max_pages` 1–10, `max_price`
(0 = без лимита), `min_discount_percent`, `sort` (relevance/discount/price).
Колонки: `app_id`, `title`, `url`, `discount_percent`, `price_original`,
`price_final`, `currency`, `review_summary`, `review_percent`, `review_count`,
`release_date`, `platforms`, `image_url`. Фильтрация по скидке/цене — в
парсере. `requires_login=False`, работает на чистом профиле.
Проверено: страница отдаётся, 50 карточек, нужные поля в разметке есть.

## 12. Отчёты LibreOffice Calc (`.ods`)

Базовый формат — OpenDocument Spreadsheet (`.ods`), родной для LibreOffice Calc; пишется через `odfpy` без запуска `soffice`. Лист «Данные» (заголовки жирным, автофильтр, freeze panes,
ширины, числовые форматы, ссылки кликабельны) и лист «Метаданные»
(сайт, парсер, профиль, прокси (без пароля), время, длительность, всего/новых,
URL, версия). Имя: `{parser_title}_{site_name}_{YYYY-MM-DD_HH-MM}.ods`,
slug-транслитерация, `UWP_REPORT_NAME_TEMPLATE` переопределяет.
`export_mode`: `always` (по умолчанию), `changes` (только если есть хотя бы
одна новая или изменившаяся строка), `never`. В режиме `changes` сравниваем
**все поля** из `columns` кроме `collected_at`: строка считается изменившейся,
если её `payload` отличается от прошлого прогона; в отчёт кладём полный
текущий срез с колонкой «Новое» (`да`/`нет`) и подсветкой изменённых строк,
а исчезнувшие строки — только счётчиком в «Метаданных», без отдельных строк.
Файлы складываются в `data/exports/<site>/` и отправляются админу; хранится
последние N (default 50).

## 13. Telegram-бот

Доступ: middleware сверяет `from_user.id` с allowlist (`UWP_ADMIN_IDS` +
таблица `admins`). Чужим — один вежливый отказ, без деталей. Команды
`/start`, `/sites`, `/runs`, `/help`, `/id`. Меню — инлайн-кнопки.

```
📋 Сайты (3)                    [➕ Добавить сайт]

🟢 Steam — скидки                        🔴 Steam RU
   профиль: чистый · прокси: DE-1         профиль: Steam-RU
   каждые 45–90 мин · след. 01:20         каждые 60 мин · выкл
   [⏸ Выключить] [▶️ Запустить] [⚙️]

⚙️ Steam — скидки
Статус: 🟢 включён
Интервал: 45–90 мин (след. 01:20)
Профиль: 🧼 чистый (ротация)
Прокси: 🌐 DE-1 (http, Германия)
Отчёт: после каждого прогона
[⏱ Интервал] [👤 Профиль] [🌐 Прокси]
[🧾 Отчёт] [🖥 Войти в профиль] [🧪 Параметры]
[▶️ Запустить сейчас] [🕘 История] [⬅️ Назад]

🧪 Параметры: Steam — скидки
Регион: us · Язык: english · Страниц: 2
Мин. скидка: 50% · Макс. цена: 30
[🇺🇸 Регион] [🔤 Язык] [📄 Страниц]
[💯 Мин. скидка] [💵 Макс. цена] [⬅️ Назад]

🖥 Вход в профиль Steam-RU
http://host:6042/vnc.html?autoconnect=1…
Пароль: 7fK2mQ9xLp3a
⏳ Осталось 58 мин
[✅ Готово] [⏱ +30 мин] [🔄 Перезапустить]
```

Прогресс прогона: у ручного запуска бот редактирует исходное сообщение
(«🌐 Открываю страницу 1/2…»). У планового прогресс-сообщения нет — по
завершении бот присылает `.ods` с подписью (сколько строк/новых, время,
предупреждения). При ошибке — текст + скриншот + кнопка «Повторить».
FSM-состояния — только для ввода чисел/строк (диапазон, имя, прокси).
Все списки — callback-кнопки с короткими id, данные в БД.

Кому приходит: всем из allowlist, у кого включены уведомления (по умолчанию
всем). Доставка идёт через таблицу `events` (`id`, `kind`, `site_id`,
`run_id`, `payload`, `created_at`, `delivered_to`) — воркер не зависит от
сети; бот long-poll'ит и ретраит, пока админ не получит. Если бот был
выключен, события доставляются при старте (ограничение: не старше 3 дней).

Что можно делать из бота (v1): добавить/удалить сайт-экземпляр (выбор
парсера из списка), включить/выключить, интервал, профиль, прокси,
`export_mode`, параметры парсера, ручной запуск, история, вход в профиль,
создание/удаление persistent-профиля и прокси (мастера на FSM), тест прокси.
При «Добавить сайт» создаётся **выключенный** экземпляр с дефолтными
настройками парсера и чистым профилем; если парсеру нужен логин, бот сразу
предлагает создать профиль и войти. CLI даёт то же самое плюс массовые
операции — UI бота не является единственным путём.

## 14. CLI

```
uwp init                       создать БД, админа, сайт Steam (выключен)
uwp doctor                     проверить env, браузер, X, noVNC, права
uwp browser install [--version 150.0.7871.186] [--url …]
uwp parsers list
uwp site list|add|enable|disable|run|set
uwp profile list|create|login|delete
uwp proxy list|add|test
uwp vnc list|stop
uwp bot | worker | run          split-режим
uwp export <run_id|site> [-o …]
uwp db upgrade
```

`uwp run` без аргументов — combined-режим (бот + воркер в одном loop).

## 15. Docker

- Базовый образ `python:3.12-slim-bookworm`; apt: xvfb, x11vnc, novnc,
  websockify, fluxbox, fonts (Liberation, Noto Color Emoji), зависимости
  Chromium (libnss3, libatk*, libgbm, libasound2, libxkbcommon и т.д.),
  tini.
- fingerprint-chromium скачивается на этапе сборки в `/opt/uwp/browser`
  (ARG `UWP_BROWSER_VERSION`, sha256-проверка), поэтому запуск не требует сети.
- `./data` — единственный volume: БД, профили, экспорты, артефакты.
- Compose: сервис `uwp` (combined), порты `6000-6100`, env_file `.env`,
  `shm_size: 1gb`, `init: true`, healthcheck (`uwp doctor --quiet`),
  `restart: unless-stopped`. Профиль `split` — сервисы `bot` и `worker`.
- Non-root пользователь, `UWP_PROFILES_DIR`, `UWP_EXPORTS_DIR` в `/data`.
- README: `.env.example`, `docker compose up -d --build`,
  как узнать Telegram ID, как поставить за TLS.

## 16. Тесты и проверка

- Unit: настройки/env, миграции, планировщик (диапазон, отсутствие дублей,
  пропуск выключенных), имя отчёта, запись ods, реестр парсеров (broken),
  генерация seed/наборов отпечатков, lease профилей, retry/backoff,
  proxybridge (фейковый апстрим), парсер Steam на сохранённой HTML-фикстуре.
- Integration: локальный http-сервер с фикстурами + Playwright +
  fingerprint-chromium (headless), полный прогон runner → ods.
- Live (`-m live`): реальный Steam, 1 страница, проверка ≥10 строк.
- Smoke Docker: собрать образ, `uwp doctor`, `uwp run --site steam --once`,
  проверить появление ods.
- VNC проверяю вручную: поднять сессию, открыть noVNC в браузере, увидеть
  страницу логина, остановить.
- Definition of done: `pytest` зелёный, Docker поднимается, реальный Steam
  отдаёт `.ods`, VNC-ссылка открывается, бот отвечает только админу.

## 17. Решения, которые принимаю я (можно отменить)

- Язык интерфейса бота — русский; код и имена — английские.
- SQLite, не Postgres: одна машина, объём данных небольшой.
- `combined`-режим по умолчанию, `split` — опцией.
- Отчёт `.ods` после каждого прогона; `changes` — настройкой сайта.
- Прокси всегда через локальный bridge (единый путь, поддержка авторизации).
- Clean-профили — пул с TTL 7 дней, минимум 24 ч жизни после использования.
- Пароли прокси шифруются, если задан `UWP_SECRET_KEY`; иначе plaintext
  с предупреждением в `uwp doctor`.
- Парсер Steam по умолчанию: регион `us`, язык `english`, 2 страницы,
  скидка ≥ 0, топ-продажи со скидками.
- Лицензия MIT, ветка `main`, conventional commits.
- Playwright MCP добавляю в `~/.config/opencode/opencode.json` для отладки
  браузера в этой среде.
- Clean-профиль — **одноразовый на прогон** (не пул): папка удаляется после
  прогона, ретрай получает новую личность.
- Прогресс в чате — только для ручных запусков; плановые присылают результат
  и ошибки всем админам из allowlist.
- Бот в v1 умеет всё основное (сайты, persistent-профили, прокси, параметры,
  VNC, история); CLI — для массовых операций и автоматизации.
- Включённый сайт запускается сразу; ручной запуск не сдвигает расписание;
  после простоя — один догоняющий прогон; выключение отменяет очередь.
- Таймаут прогона 300 с + heartbeat 15/90 с, осиротевшие задачи
  возвращаются в очередь при старте и в цикле.
- Ошибки типизированы (`login_required`, `blocked`, `transient`,
  `parser_error`, `config_error`), частичные данные сохраняются и
  экспортируются со статусом `partial`.
- В split-режиме VNC поднимает воркер, бот только просит и показывает ссылку.
- У прокси есть необязательное поле `country`; отпечаток под страну
  подгоняется только если у сайта включён `match_profile_to_proxy_country`.

## 18. Риски

- fingerprint-chromium обновляется часто; версия и URL фиксируются в
  конфиге, апгрейд — осознанное действие, тест `uwp doctor`.
- noVNC внутри контейнера требует проброса диапазона портов; при облачном
  фаерволе порты надо открыть или поставить reverse-proxy.
- Steam может показывать капчу/другую валюту при «плохом» прокси — это
  лечится регионом/прокси, парсер сохраняет скриншот при пустой выдаче.
- Прокси-провайдеры с жёсткой авторизацией иногда ломают CONNECT — bridge
  логирует причину, `uwp proxy test` помогает до запуска.
