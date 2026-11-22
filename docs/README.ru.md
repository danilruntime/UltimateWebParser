# <a id="documentation"></a>Документация

[`../README.ru.md`](../README.ru.md) отвечает на три вопроса и останавливается:
что это, подходит ли вам и как получить первый отчёт. Всё, что отодвинуло бы
первую команду за пределы первого экрана, живёт здесь — детали эксплуатации,
замеры, стоящие за проектными решениями, и планы реализации, написанные, пока
строился код.

Планы — историческая запись, а не инструкции. Они сохранены потому, что числа
в них служат доказательством для нескольких правил в `AGENTS.md`: тот, кто
захочет «упростить» проверку готовности VNC, должен иметь возможность прочитать,
почему она вообще существует.

**English version:** [`README.md`](README.md).

## <a id="operating-guide"></a>Руководство по эксплуатации

### <a id="permissions-on-data"></a>Права на `./data`

Docker создаёт каталог как `root`, поэтому контейнер стартует от root, один раз
выставляет владельца `1000:1000` и сразу сбрасывает привилегии до `uwp`.
Вручную ничего делать не нужно. Если прав на `chown` нет (например, на томе NFS),
контейнер завершается с кодом 2 и подсказкой:

```bash
mkdir -p data && sudo chown -R 1000:1000 data
```

`uwp: /data is not writable by uid 1000` значит, что каталог принадлежит другому
uid; та же команда это исправляет.

### <a id="how-to-find-your-telegram-id"></a>Как узнать свой Telegram id

Бот подсказать не может: `/id` он показывает только тому, кто уже админ, а
постороннему отвечает отказом, не записывая id в лог. Возьмите id любым внешним
способом — например, у `@userinfobot` — впишите его в `.env` и поднимите стек:

```bash
$EDITOR .env          # UWP_ADMIN_IDS=111,222
docker compose -f compose.split.yaml -f compose.split.hostnet.yaml up -d
```

`UWP_ADMIN_IDS` — список id через запятую (`111,222`). Кроме `.env`, админов можно
добавить в таблицу `admins`: бот пускает тех, кто есть хотя бы в одном из двух
списков, а уведомления шлёт только тем, у кого `admins.notify = 1`.

### <a id="the-telegram-proxy-in-detail"></a>Прокси Telegram: подробности

Без прокси бот падает на старте с `TelegramNetworkError: Request timeout error`,
и контейнер уходит в цикл перезапуска (`docker compose ps` показывает
`Restarting`).

```bash
UWP_TELEGRAM_PROXY=socks5://127.0.0.1:2080
```

Прокси действует **только на Bot API**. Скрапинг идёт через прокси, привязанные
к сайтам, — это отдельная настройка. Схемы: `socks5://` (пакет `aiohttp-socks` уже
есть в образе), `socks4://`, `http://`, `https://`; логин и пароль пишутся прямо
в URL.

Как это проверить:

```bash
docker compose -f compose.split.yaml -f compose.split.hostnet.yaml \
  exec bot /entrypoint.sh doctor | grep -i proxy
```

`doctor` действительно открывает `api.telegram.org` через прокси и печатает
причину сбоя (порт, таймаут TLS, неверная схема). Пароль в выводе маскируется.
Healthcheck контейнера запускает `doctor --offline`, и тот проверяет только формат
адреса: полная проверка на медленном стороннем прокси может занять до 12 секунд
(таймаут 10 с плюс жёсткая страховка в 2 с), а это не должно переводить рабочий
контейнер в `unhealthy`.

Язык вывода задаёт `UWP_DEFAULT_LANGUAGE`. При значении по умолчанию `ru` метка —
«прокси Telegram», поэтому `grep -i proxy` ничего не найдёт, а `grep -i прокси`
найдёт; при `en` — наоборот.

**Прокси на `127.0.0.1` хоста.** В контейнере на bridge-сети `127.0.0.1` — это
сам контейнер. Выходов два:

```bash
# 1. Дать контейнеру сеть хоста (тогда порт 2080 — это 127.0.0.1)
docker compose -f compose.split.yaml -f compose.split.hostnet.yaml up -d

# 2. Слушать LAN-адрес хоста и указать его в .env
UWP_TELEGRAM_PROXY=socks5://192.168.1.10:2080
```

### <a id="keeping-the-stack-up"></a>Как держать стек в рабочем состоянии

Все команды выполняются из каталога проекта. Чтобы не повторять длинный `-f`,
положите его в переменную:

```bash
COMPOSE="docker compose -f compose.split.yaml -f compose.split.hostnet.yaml"
```

| Что нужно | Команда |
| --- | --- |
| Состояние | `$COMPOSE ps` |
| Логи | `$COMPOSE logs -f worker` |
| Остановить, сохранив контейнеры | `$COMPOSE stop` |
| Запустить снова | `$COMPOSE start` |
| Остановить и удалить | `$COMPOSE down` |
| Поднять снова | `$COMPOSE up -d` |
| Пересобрать после `git pull` | `$COMPOSE up -d --build` |

**`stop` ≠ `down`.** `stop` завершает процессы, но оставляет контейнеры: `start`
возвращает всё как было. `down` удаляет контейнеры — следующий `up` создаёт
новые (другое имя хоста, чистая сеть). Данные переживают и то и другое: `./data`
— каталог хоста, а именованных томов в проекте нет, поэтому `down -v` не нужен и
ничего бы не «почистил».

**Закройте сессии VNC до `down`**, иначе останутся строки в статусе `running`.

```bash
docker exec uwp-worker /entrypoint.sh vnc list
docker exec uwp-worker /entrypoint.sh vnc stop <id>
```

Воркер убирает такие строки при следующем старте (`sweep_orphans`), но до тех пор
профиль занят.

**Не переводите воркер в сеть хоста.** `compose.split.hostnet.yaml` отдаёт сеть
хоста только `bot`, и это намеренно: воркер продолжает публиковать полосу VNC
через Docker, а воркер с `network_mode: host` заставил бы `websockify` занять те же
порты напрямую, пока их держит `docker-proxy`. Сессия тогда умирает на
`Address already in use`, хотя Xvfb, Chromium и x11vnc живы, — админ получает
ссылку, которая сбрасывает соединение. `compose.hostnet.yaml` (hostnet-overlay для
combined) действительно запускает всё в сети хоста; это работает потому, что
публикации портов не остаётся, и он выставляет `UWP_VNC_LISTEN_HOST=127.0.0.1`,
чтобы noVNC не оказался на всех интерфейсах.

### <a id="what-the-worker-cleans-up"></a>Что убирает воркер

Уборка идёт на первом тике обслуживания воркера, а затем раз в час
(`CLEANUP_SECONDS = 3600` в `app.py`); сбой любого отдельного шага воркер
переживает:

- **истёкшие одноразовые профили** — каталог и строка одноразового профиля, у
  которого истёк `expires_at`, удаляются (`profiles.cleanup_clean`); постоянные
  профили не трогаются;
- **старые строки сессий VNC** — строки в `stopped`/`failed` удаляются, когда
  `started_at` старше `UWP_VNC_HISTORY_DAYS` (7 дней); пароль в них уже стёрт при
  остановке, см. раздел про VNC ниже;
- **каталоги артефактов** — `data/artifacts/<run-id>` подрезается до последних
  `UWP_ARTIFACTS_RETENTION` (20).

Отчёты в этот часовой проход не входят: `data/exports/<site-slug>/` подрезается
до последних `UWP_EXPORTS_RETENTION` (50) при **каждой записи**, поэтому сайт,
который прогоняется редко, держит старые файлы до следующего отчёта.

### <a id="vnc-sessions-in-detail"></a>Сессии VNC в подробностях

Сайтам со входом нужен постоянный профиль. Создайте его и запросите сессию:

```bash
docker exec uwp-worker /entrypoint.sh profile create Steam-RU
docker exec uwp-worker /entrypoint.sh profile list
docker exec uwp-worker /entrypoint.sh profile login 1 --site steam_skidki
```

`profile login` поднимает стек в этом же процессе и держит его: ссылка и пароль
печатаются сразу, а Ctrl+C закрывает сессию. Через бота удобнее — ссылка приходит
сообщением, сессией владеет воркер, и прерывать нечего.

`--site` принимает слаг или числовой id и называет сайт, чью страницу входа
откроет браузер; если у сайта задан свой прокси, берётся он, иначе — прокси
профиля. Без `--site` сервис сам ищет привязку профиля: ровно один привязанный
сайт даёт и страницу входа, и прокси; если привязки нет — отказ, потому что взять
страницу и прокси неоткуда, а если сайтов несколько — отказ, потому что у них
могут быть разные прокси и угадывание вошло бы в аккаунт не из той страны. По
тому же правилу работает «🖥 Войти» с карточки профиля.

Ссылка вида `http://<UWP_PUBLIC_HOST>:6042/vnc.html#autoconnect=1` открывается в
браузере. Пароль передаётся во фрагменте (`#password=...`) и на сервер не уходит.
Войдите на сайт и нажмите «✅ Готово»: процессы останавливаются, профиль
освобождается.

Ссылка открывает киоск: одно окно Chromium на весь экран с сайтом, без вкладок,
адресной строки, панели и меню рабочего стола и без DevTools. Перезагрузка (`F5`,
`Ctrl+R`) и история (`Alt+←`) остаются странице; все остальные сочетания браузера
перехватывает `fluxbox` раньше, чем их увидит Chromium, потому что один только
kiosk-режим на `Ctrl+N` открывает полноценное окно (замерено). Политики Chromium
не используются: `DeveloperToolsAvailability=2` валит `launch_persistent_context`
по таймауту 180 с и останавливает парсеры, а политика — машинная, не на профиль.

Это граница интерфейса, а не песочница. Страница по-прежнему принимает ввод;
окружение воркера скрыто от браузера другим слоем (allowlist `browser_env` и
`PR_SET_DUMPABLE=0`).

Файл, скачанный в этом окне, попадает в `data/downloads` на хосте. Каталог
прописывается в `Default/Preferences` профиля (`download.default_directory` и
`prompt_for_download: false`) до запуска Chromium: замерено, что у свежего
профиля ключа `download` нет вовсе, поэтому Chromium спрашивает место, киоск
никогда не показывает диалог «Сохранить как», и клик записывается как
`interrupt_reason=40` (`USER_CANCELED`) без файла. Нужны оба ключа: только с
каталогом диалог всё равно появляется, только с флагом Chromium выбирает
XDG-каталог, которого нет в образе. На прогоны парсеров это не влияет: ни один
парсер не нажимает ссылку скачивания, а Playwright отказывает в загрузках сам
(`accept_downloads` по умолчанию выключен).

Сессия живёт 60 минут (кнопка «⏱ +30 мин» продлевает), а если клиент
подключался — закрывается через 20 минут после отключения последнего. Пароль —
8 символов: RFB всё равно проверяет только первые восемь, а более длинная строка
показывала бы символы, которые сервер игнорирует.

Пароль живёт меньше, чем строка сессии: `stop()` пишет `password = ""`, когда
помечает строку `stopped`, а упавшая сессия стирается так же — в `launch()`. Сама
строка остаётся историей, пока часовая уборка не удалит завершённые строки старше
`UWP_VNC_HISTORY_DAYS` (7 дней), — именно это и не даёт копии `data/uwp.db`
хранить все когда-либо выданные пароли.

#### <a id="access-from-another-machine"></a>Доступ с другой машины

Порты 6000–6100 опубликованы **только на `127.0.0.1` хоста**: noVNC говорит по
HTTP и не должен быть виден в сети. Туннель:

```bash
ssh -N -D 1080 user@server
```

Направьте браузер (или отдельный профиль в нём) на SOCKS5 `127.0.0.1:1080`:
`http://localhost:6042/vnc.html` уйдёт на сервер, и наружу не откроется ни один
порт. Альтернатива — reverse proxy с TLS и Basic Auth, а `UWP_PUBLIC_HOST` и
`UWP_PUBLIC_SCHEME=https` должны указывать на него.

#### <a id="small-things-that-save-time"></a>Мелочи, которые экономят время

- Порт **6000 не выдаётся никогда**: браузеры считают его небезопасным (это порт
  X11), поэтому первая сессия получает 6001.
- В split-режиме порты принадлежат контейнеру `worker`.
- Каждая сессия пишет лог в `/data/vnc/<profile-id>-<port>.log`.
- Если браузер не смог занять профиль, сессия сразу падает с причиной в чате,
  вместо того чтобы показать пустой рабочий стол.
- Пароль сайта хранится только в профиле браузера; в базе его нет.

### <a id="report-files"></a>Файлы отчётов

После прогона воркер пишет `.ods` в `data/exports/<site-slug>/` и ставит событие
в очередь; бот отправляет файл админам. Имя —
`{parser_title}_{site_name}_{YYYY-MM-DD_HH-MM}.ods`, подстановки —
`{parser_title}`, `{site_name}` и `{timestamp}`, а шаблон меняется через
`UWP_REPORT_NAME_TEMPLATE`. На каждый сайт хранятся последние
`UWP_EXPORTS_RETENTION` (50) файлов. Каталоги `data/artifacts/<run-id>` (скриншот
неудачного прогона и всё остальное, что сохранил парсер) подрезаются до последних
`UWP_ARTIFACTS_RETENTION` (20).

Режим отчёта задаётся для каждого сайта:

- `always` — после каждого прогона (по умолчанию);
- `changes` — только если есть новые или изменившиеся строки;
- `never` — не писать.

В файл всегда попадает полный срез и колонка «Новое»; изменившиеся строки
подсвечены. Режим решает только, писать ли файл, а не что в нём.

«Новое» значит «не было в прошлом прогоне», а не «не встречалось никогда».
Строка, которая пропала с сайта и позже вернулась (скидка кончилась и началась
снова), снова помечается новой. Колонка «Исчезло» на листе метаданных считает,
сколько строк не встретилось именно в этом прогоне.

Если режим говорит, что файл не нужен (`changes` без изменений, `never`), бот
молчит. Если отчёт должен был появиться, но запись не удалась, приходит
«⚠️ Отчёт не создан» с причиной. Частичный прогон помечается
«⚠️ Прогон частичный» и сообщается всегда, даже если режим экспорта обычно
молчит: часть строк потеряна, и это не должно выглядеть как «ничего не
изменилось». Каких именно id не хватило, видно в логе воркера.

Скачать отчёт вручную:

```bash
docker exec uwp-worker /entrypoint.sh export steam_skidki -o /data/report.ods
docker cp uwp-worker:/data/report.ods .
```

### <a id="command-line"></a>Командная строка

В образе есть CLI `uwp`. В split-режиме почти всё принадлежит воркеру:

```bash
docker exec uwp-worker /entrypoint.sh doctor         # диагностика окружения
docker exec uwp-worker /entrypoint.sh parsers list   # установленные парсеры
docker exec uwp-worker /entrypoint.sh site list      # сайты и расписания
docker exec uwp-worker /entrypoint.sh site run steam_skidki
docker exec uwp-worker /entrypoint.sh profile list   # постоянные профили
docker exec uwp-worker /entrypoint.sh proxy test 1   # проверить прокси
docker exec uwp-worker /entrypoint.sh vnc list
docker exec uwp-worker /entrypoint.sh export 12 -o /data/run12.ods
docker cp uwp-worker:/data/run12.ods .
```

`doctor` проверяет каталоги данных, бинарник браузера и его запуск,
Xvfb/x11vnc/websockify (только предупреждение), токен, админов, ключ шифрования и
миграции базы. Код выхода 1, если что-то сломано, — healthcheck на это
рассчитывает.

У воркера намеренно нет токена, поэтому его healthcheck вызывает
`doctor --offline --worker`: флаг `--worker` убирает проверку `UWP_BOT_TOKEN`.

В combined-режиме те же команды идут через
`docker compose exec uwp /entrypoint.sh …`.

`/entrypoint.sh` в `exec` — не украшение: без него команда выполняется от root, и
`doctor` подтверждает запись в каталоги, куда uid 1000 писать не может.

### <a id="writing-your-own-parser"></a>Свой парсер

1. Создайте каталог `src/uwp/parsers/<key>/` с `__init__.py` и `parser.py`.
2. В `parser.py` объявите ровно один подкласс `BaseParser`:

```python
from typing import ClassVar
from pydantic import BaseModel, Field
from uwp.parsers.base import BaseParser, Column, ParseContext, ParseResult


class MySettings(BaseModel):
    max_pages: int = Field(
        default=1,
        ge=1,
        le=5,
        description="How many pages",
        json_schema_extra={"title_key": "parser.my_site.field.max_pages"},
    )


class MyParser(BaseParser):
    key = "my_site"
    title = "My site"
    description = "What we collect"
    title_key = "parser.my_site.title"
    description_key = "parser.my_site.description"
    url = "https://example.com/"
    auth_url = "https://example.com/login"
    requires_login = False
    settings_model = MySettings
    columns: ClassVar[list[Column]] = [
        Column("title", "Title", width=40, title_key="parser.my_site.column.title"),
        Column("url", "Link", width=60),
        Column("price", "Price", width=12, fmt="0.00"),
    ]

    async def run(self, ctx: ParseContext) -> ParseResult:
        settings: MySettings = ctx.settings
        await ctx.page.goto(self.url, wait_until="domcontentloaded")
        rows = [{"external_key": "id-1", "title": "…", "url": "…", "price": 1.0}]
        return ParseResult(rows=rows, meta={"pages": 1})
```

Имя парсера, подписи настроек и заголовки колонок рендерятся на языке
пользователя — в боте и в шапке `.ods`; описание переносится вместе с парсером и
появится, когда его понадобится показать на экране. Парсер без ключей работает
как написан: показываются его собственные `title`, `description` и `Column.title`.
Парсер, который живёт в этом репозитории, указывает `title_key`, `description_key`,
`Column.title_key` и `Field(..., json_schema_extra={"title_key": ...})` на каталог
(`uwp.i18n.parsers`), чтобы эти строки следовали языку отчёта; литеральные строки
остаются запасным вариантом. Идентификаторы в коде остаются английскими. Ключей
`parser.my_site.*` из примера ещё нет: добавьте их в `uwp.i18n.parsers` сразу для
`ru` и `en`, прежде чем на них ссылаться, — а пока не добавили, уберите аргументы
`*_key`, и парсер возьмёт `title`, `description` и `Column.title`, как и любой
сторонний.

`url` — это страница, которую парсер собирает, и та, что показана в строке `URL`
отчёта; `auth_url` — страница, которую открывает вход через VNC. Не указывайте
`auth_url` — и вход возьмёт `url`: на это рассчитывает парсер, написанный до
появления атрибута.

В репозитории два парсера: `steam_deals` разбирает DOM через `BeautifulSoup`, а
`soundcloud_trending` показывает другую частую форму — страница сама забирает
свой JSON, поэтому парсер перехватывает ответ и берёт точные значения вместо
округлённого текста в DOM.

3. Перезапустите сервис: `$COMPOSE restart worker`. Парсер появится в
   `uwp parsers list` и в боте («🌐 Сайты» → «➕ Добавить сайт»).

Правила:

- **`external_key` обязателен в каждой строке** — на нём держится определение
  новых и изменившихся строк (у Steam это `app_id`). В отчёт он не попадает,
  если его нет в `columns`.
- Ошибки бросайте с типом: `ParseError(kind=ErrorKind.blocked, message="…",
  partial_rows=[…])`. Типы: `login_required`, `blocked`, `transient`,
  `parser_error`, `config_error` — от типа зависят повторы и уведомления.
- `columns` задают отчёт: порядок, заголовки, ширину (`width`) и числовой формат
  (`fmt`, например `"0.00"`). Ключи `url` и `*_url` рендерятся ссылками. Если
  значение колонки само по себе дрейфует между прогонами (счётчик отзывов, число
  зрителей), пометьте её `volatile=True`: она останется в отчёте, но перестанет
  считаться изменением для режима «только изменения».
- Сломанный парсер не роняет сервис: он помечается `broken` и остаётся видимым в
  списке.
- Напишите README рядом с парсером — как в `src/uwp/parsers/steam_deals/`
  (DOM) или `src/uwp/parsers/soundcloud_trending/` (перехват API).

Скриншот для разбора проблем: `ctx.save_artifact("screenshot.png", data)`.

### <a id="making-a-release"></a>Выпуск релиза

Релиз — это git-тег: пуш тега заставляет `.github/workflows/release.yml` собрать
wheel, прогнать набор тестов на помеченном коммите и опубликовать страницу
GitHub-релиза из `docs/releases/v<version>.md`. Локально токен не нужен.

```bash
./scripts/release.sh beta      # 0.1b1 -> 0.1b2   следующая бета на той же линии
./scripts/release.sh release   # 0.1b1 -> 0.1     снять метку беты
./scripts/release.sh minor     # 0.1b1 -> 0.2b1   начать новую линию
```

Скрипт поднимает версию в `pyproject.toml` и `src/uwp/__init__.py` (версия живёт в
обоих файлах, и тест падает, если они разошлись), переставляет ссылки в обоих
README на новый файл заметок, пишет заметки из коммитов с прошлого тега,
прогоняет набор тестов, затем коммитит и пушит тег. Если тесты красные — тега нет,
а рабочее дерево восстанавливается, так что следующая попытка начинается с
чистого листа.

Есть два флага, чтобы сначала посмотреть:

- `--dry-run` печатает, что изменилось бы, и ничего не пишет.
- `--no-push` останавливается после коммита и тега, чтобы их можно было изучить.

Передать комментарий невозможно: заметки генерируются. Отредактируйте
`docs/releases/v<version>.md` и запустите workflow вручную (с `main`, тег как
входной параметр), чтобы опубликовать страницу заново.

### <a id="when-something-breaks"></a>Когда что-то ломается

| Симптом | Что делать |
| --- | --- |
| Бот молчит | `$COMPOSE logs -f bot`; проверьте `UWP_BOT_TOKEN`; `uwp doctor`. |
| В боте «⛔ Доступ только для администратора.» | Вашего id нет в `UWP_ADMIN_IDS`/`admins`. Бот его не покажет: возьмите у `@userinfobot`. |
| Контейнер в `Restarting` | Скорее всего нет доступа к `api.telegram.org`: задайте `UWP_TELEGRAM_PROXY`. |
| `fingerprint-chromium not found` | `uwp browser install` или проверьте `UWP_BROWSER_PATH`. |
| Прогон падает с `blocked` | Капча или блок региона: добавьте прокси, смените регион парсера. |
| Прогон падает с `login_required` | Нужны постоянный профиль и вход через VNC; затем привяжите профиль к сайту. |
| Отчёт не пришёл | Проверьте режим отчёта (`changes` молчит без изменений) и `uwp export <id>`. |
| Ссылка VNC не открывается | Порт опубликован только на `127.0.0.1`: с другой машины нужен SSH-туннель или TLS-прокси, либо неверен `UWP_PUBLIC_HOST`; `uwp vnc list`. В split-режиме сессию поднимает `worker` — подождите несколько секунд. Если в логе VNC `Address already in use`, на машине работает второй стек. |
| Ссылка VNC сбрасывается | `websockify` не занял порт (обычно из-за второго стека). Сессия падает с причиной в чате; проверьте, что работает ровно один стек. |
| `uwp vnc stop` «ничего не делает» | Команда только помечает сессию; процессы убивает воркер на своём следующем тике (до 5 с). |
| noVNC открылся, но экран пуст | Смотрите `/data/vnc/<profile-id>-<port>.log` в контейнере. Порт — из 6000–6100, кроме 6000. |
| `uwp: /data is not writable by uid 1000` | Каталог принадлежит другому uid. На хосте: `sudo chown -R 1000:1000 data`. |
| `database is locked` | Не запускайте два воркера на одном томе. |
| Профиль «занят» | Идёт прогон или застряла аренда: подождите `UWP_ORPHAN_THRESHOLD` или перезапустите воркер. |

Подробности — в `uwp doctor` и логах (`UWP_LOG_LEVEL=DEBUG`). Собственные
сообщения бота следуют языку админа, поэтому тот же отказ в английском чате
читается «⛔ Administrators only.».

## <a id="images-used-by-the-readmes"></a>Изображения в README

| Файл | Что показывает |
|---|---|
| [`images/report.png`](images/report.png) | настоящий отчёт `.ods`, открытый в LibreOffice Calc |
| [`images/vnc-desktop.png`](images/vnc-desktop.png) | VNC-киоск: одно окно Chromium, без панели и вкладок, песочница включена |

Оба сняты с работающего сервиса, а не нарисованы: отчёт — с 50 строк реального
прогона по Steam, киоск — с того же образа, который использует развёртывание.

## <a id="design-specs"></a>Проектные спецификации

Спецификация фиксирует проблему, альтернативы и выбранную форму *до* кода.
Написаны по-русски; логика видна из порядка аргументов.

| Документ | О чём |
|---|---|
| [`superpowers/specs/2026-11-13-ultimate-web-parser-design.md`](superpowers/specs/2026-11-13-ultimate-web-parser-design.md) | весь сервис: схема базы, цикл воркера, профили, прокси, VNC, отчёты |
| [`superpowers/specs/2026-11-14-persistent-menu-and-vnc-desktop-design.md`](superpowers/specs/2026-11-14-persistent-menu-and-vnc-desktop-design.md) | постоянная клавиатура и полноразмерный рабочий стол VNC |
| [`superpowers/specs/2026-11-21-vnc-kiosk-design.md`](superpowers/specs/2026-11-21-vnc-kiosk-design.md) | VNC-вход как киоск: одно окно, без панели и меню, без DevTools |
| [`superpowers/specs/2026-11-16-volatile-columns-in-changes-mode-design.md`](superpowers/specs/2026-11-16-volatile-columns-in-changes-mode-design.md) | почему дрейфующий счётчик не должен заставлять `changes` срабатывать каждый прогон |
| [`superpowers/specs/2026-11-21-parser-auth-url-design.md`](superpowers/specs/2026-11-21-parser-auth-url-design.md) | страница входа парсера как `auth_url`, отдельно от URL прогона |
| [`superpowers/specs/2026-11-22-soundcloud-parser-design.md`](superpowers/specs/2026-11-22-soundcloud-parser-design.md) | второй парсер: точные счётчики из API самой страницы и почему запасного DOM-пути нет |

## <a id="implementation-plans"></a>Планы реализации

План превращает спецификацию в упорядоченные проверяемые задачи. Каждый
выполнялся через TDD; измеренный симптом процитирован в коммите, который его
закрыл.

| Документ | О чём |
|---|---|
| [`superpowers/plans/2026-11-13-ultimate-web-parser.md`](superpowers/plans/2026-11-13-ultimate-web-parser.md) | сборка сервиса из пустого репозитория |
| [`superpowers/plans/2026-11-14-persistent-menu-and-vnc-desktop.md`](superpowers/plans/2026-11-14-persistent-menu-and-vnc-desktop.md) | постоянное меню, рабочий стол VNC и семь отложенных дефектов |
| [`superpowers/plans/2026-11-21-vnc-kiosk.md`](superpowers/plans/2026-11-21-vnc-kiosk.md) | браузер-киоск, перехват сочетаний и правки документации |
| [`superpowers/plans/2026-11-15-returned-items-and-new-column.md`](superpowers/plans/2026-11-15-returned-items-and-new-column.md) | строки, которые пропадают и возвращаются, и колонка «Новое» |
| [`superpowers/plans/2026-11-15-sandbox-and-steam-regions.md`](superpowers/plans/2026-11-15-sandbox-and-steam-regions.md) | три послабления seccomp и региональный парсинг Steam |
| [`superpowers/plans/2026-11-16-volatile-columns-in-changes-mode.md`](superpowers/plans/2026-11-16-volatile-columns-in-changes-mode.md) | `Column(volatile=True)` |
| [`superpowers/plans/2026-11-21-parser-auth-url.md`](superpowers/plans/2026-11-21-parser-auth-url.md) | `auth_url` и вход, который отказывает при неоднозначном профиле |
| [`superpowers/plans/2026-11-22-soundcloud-trending-parser.md`](superpowers/plans/2026-11-22-soundcloud-trending-parser.md) | парсер SoundCloud: hydration, перехват ответов, частичные прогоны |

## <a id="elsewhere"></a>В других файлах

| Документ | Почему он не здесь |
|---|---|
| [`../AGENTS.md`](../AGENTS.md) | инструкции по изменению кода; лежат в корне, чтобы агент их нашёл |
| [`../SECURITY.ru.md`](../SECURITY.ru.md) | модель угроз, рядом с кодом, который она описывает |
| [`../THIRD_PARTY_NOTICES.ru.md`](../THIRD_PARTY_NOTICES.ru.md) | лицензионные обязательства, в корне, где их ждёт получатель |
| [`../docker/README-seccomp.ru.md`](../docker/README-seccomp.ru.md) | как пересобрать seccomp-профиль |
