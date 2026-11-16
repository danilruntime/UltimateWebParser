# UltimateWebParser

[![tests](https://github.com/danilruntime/UltimateWebParser/actions/workflows/tests.yml/badge.svg)](https://github.com/danilruntime/UltimateWebParser/actions/workflows/tests.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python: 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![version: 0.1 beta](https://img.shields.io/badge/version-0.1%20beta-orange.svg)](pyproject.toml)

> **Status: 0.1 beta.** The service runs end to end and has been measured
> against a live Steam deployment, but the API and the database schema may still
> change between beta releases. Migrations are append-only, so an upgrade never
> loses rows; a downgrade is not supported.

The release notes of the current version, including what is known to be limited,
are in [`docs/releases/v0.1b1.md`](docs/releases/v0.1b1.md).


A self-hosted scraping service: Playwright drives a patched Chromium
(`fingerprint-chromium`) against sites, a Telegram bot controls it, results are
rendered to LibreOffice Calc `.ods` reports, and runs are scheduled with
per-site proxies and persistent browser profiles.

The first parser covers Steam discounts. A new site is a new folder under
`src/uwp/parsers/`; the core does not change.

**Русская версия:** [`README.ru.md`](README.ru.md).

**Contents:** [features](#what-it-does) · [quick start](#quick-start) ·
[environment](#environment-variables) · [run modes](#run-modes) ·
[maintenance](#maintenance) · [daily use](#daily-use) ·
[VNC](#manual-login-over-vnc) · [reports](#reports) ·
[your own parser](#adding-your-own-parser) · [CLI](#commands-without-the-bot) ·
[security](#security) · [development](#development-without-docker) ·
[troubleshooting](#troubleshooting)

---

## What it does

- **Parsers are plugins.** A new site is one folder with one class; the bot
  picks it up on restart.
- **Browser profiles.** Disposable (random fingerprint, clean cookies) or
  persistent — for sites that need a login, kept between runs.
- **A proxy per site.** `http`, `https`, `socks5`; passwords are encrypted in
  the database.
- **Manual login over VNC.** A browser on a virtual display; the link and
  password arrive in chat. Log in by hand once — the profile works on its own
  afterwards.
- **A bot with buttons.** Sites, schedule, profiles, proxies, run history.
- **`.ods` reports** with autofilters, clickable links, highlighted changed
  rows and a «Новое» column.

![The report LibreOffice Calc opens: Steam discounts with a discount column, prices in rubles and review summaries](docs/images/report.png)

*A report as Calc opens it: 50 rows, an autofilter on the header, links that
resolve to the store page, numbers formatted, no truncated cells.*

![The VNC desktop: Chromium on the Steam specials page, with the fluxbox panel below](docs/images/vnc-desktop.png)

*Manual login over VNC: Chromium on a virtual display behind noVNC, sandbox
enabled. This is what the link in chat opens.*

---

## Quick start

You need Docker with Compose v2 and nothing else.

```bash
git clone https://github.com/danilruntime/UltimateWebParser && cd UltimateWebParser
cp .env.example .env
$EDITOR .env          # fill in UWP_BOT_TOKEN and UWP_ADMIN_IDS
docker compose -f compose.split.yaml -f compose.split.hostnet.yaml up -d --build
```

A minute later, send `/start` to your bot.

What happens: the image is built (it contains `fingerprint-chromium` and Xvfb),
the database is created, and a disabled site «Steam — скидки» appears. Press
«🟢 Включить» (enable) — the first run starts immediately and the report arrives
in chat.

The bot's own interface is Russian: every button and message below is quoted in
the original, with an English gloss where the meaning is not obvious.

### Why these two compose files

| File | What it sets |
| --- | --- |
| `compose.split.yaml` | Two containers: `uwp-bot` (Telegram) and `uwp-worker` (browser, parsing, VNC). |
| `compose.split.hostnet.yaml` | Gives the bot the host network — otherwise it cannot reach a proxy on `127.0.0.1`. |

`uwp-bot` lives on the host network and can reach `socks5://127.0.0.1:2080`.
`uwp-worker` stays on the bridge network and publishes the VNC ports on the
host's loopback. The split is deliberate: move the worker to the host network
too and its `websockify` starts fighting docker-proxy for the same ports.

**Not everyone needs hostnet.** If `UWP_TELEGRAM_PROXY` is unset, or the proxy
listens on the host's LAN address rather than loopback, drop the second file:

```bash
docker compose -f compose.split.yaml up -d --build
```

### If you want a single container

Combined mode is simpler: one command, one process. The price is that the
worker gets the bot token, and restarting the bot interrupts runs:

```bash
docker compose up -d --build
```

Same ports; the differences are in [Run modes](#run-modes).

### Permissions on `./data`

Docker creates the directory as `root`, so the container starts as root, sets
the owner to `1000:1000` once, and immediately drops privileges to `uwp`. You
do not need to do anything by hand. Without permission to `chown` (an NFS
volume, for example), the container exits with code 2 and a hint:

```bash
mkdir -p data && sudo chown -R 1000:1000 data
```

### How to find your Telegram ID

The bot cannot tell you: it only shows `/id` to someone who is already an
admin, and it answers a stranger with a refusal without writing the id to the
log. Get the id any external way — from `@userinfobot`, for example — write it
into `.env` and bring the stack up:

```bash
$EDITOR .env          # UWP_ADMIN_IDS=111,222
docker compose -f compose.split.yaml -f compose.split.hostnet.yaml up -d
```

`UWP_ADMIN_IDS` is a comma-separated list (`111,222`). Besides `.env`, admins
can be added to the `admins` table: the bot admits the union of both lists, and
notifies only those with `admins.notify = 1`.

---

## Environment variables

Two are required: the token and the admins. The token is needed only by the
process that polls Telegram (in split mode, `bot`).

| Variable | Default | Purpose |
| --- | --- | --- |
| `UWP_BOT_TOKEN` | — | Token from @BotFather. The bot will not start without it. |
| `UWP_ADMIN_IDS` | empty | Who has access to the bot. |
| `UWP_SECRET_KEY` | — | Encryption of proxy and VNC passwords. Without a key they are stored in plaintext (the doctor warns). |
| `UWP_TELEGRAM_PROXY` | — | Proxy for the Bot API **only**. |
| `UWP_TZ` | `Europe/Moscow` | Timezone for dates in the bot and in reports. |
| `UWP_PUBLIC_HOST` | `localhost` | The host you open noVNC on. |
| `UWP_PUBLIC_SCHEME` | `http` | `https` if a TLS reverse proxy fronts VNC. |
| `UWP_DATA_DIR` | `data` (`/data` in the image) | Database, profiles, reports, artifacts. |
| `UWP_NO_SANDBOX` | `false` | Emergency exit hatch. The sandbox is **on** by default. |

The full list with comments is in `.env.example`.

### If `api.telegram.org` is unreachable

Without a proxy the bot dies on start with
`TelegramNetworkError: Request timeout error`, and the container enters a
restart loop (`docker compose ps` shows `Restarting`).

```bash
UWP_TELEGRAM_PROXY=socks5://127.0.0.1:2080
```

The proxy applies to the **Bot API only**. Scraping goes through the proxies
attached to sites — different settings. Schemes: `socks5://` (the
`aiohttp-socks` package is already in the image), `socks4://`, `http://`,
`https://`; username and password go straight into the URL.

Checking it:

```bash
docker compose -f compose.split.yaml -f compose.split.hostnet.yaml \
  exec bot /entrypoint.sh doctor | grep -i прокси
```

`doctor` actually opens `api.telegram.org` through the proxy and prints the
reason for failure (port, TLS timeout, wrong scheme). The password is masked in
the output. The container healthcheck runs `doctor --offline`: it checks only
the address format, because a third-party proxy may take 15 seconds to answer
and must not flip a working container to `unhealthy`.

The output is Russian like the rest of the operator-facing text, hence
`grep -i прокси` in the snippet above: `grep -i proxy` would match nothing.

**A proxy on the host's `127.0.0.1`.** In a bridged container `127.0.0.1` is
the container itself. Two ways out:

```bash
# 1. Give the container the host network (port 2080 is then 127.0.0.1)
docker compose -f compose.split.yaml -f compose.split.hostnet.yaml up -d

# 2. Listen on the host's LAN address and point .env at it
UWP_TELEGRAM_PROXY=socks5://192.168.1.10:2080
```

---

## Run modes

Both modes use the same image and the same `./data` directory.

|  | Split (recommended) | Combined |
| --- | --- | --- |
| Files | `compose.split.yaml` + `compose.split.hostnet.yaml` | `compose.yaml` |
| Containers | `uwp-bot`, `uwp-worker` | `uwp` |
| Proxy on `127.0.0.1` | yes, via the hostnet overlay | only with `compose.hostnet.yaml` |
| Restarting the bot | does not interrupt runs | interrupts them |
| Bot token in the worker | no | yes (single process) |

A bare `docker compose up` brings up combined mode: it has a single file, which
is why it stays Compose's default. Split needs explicit `-f`, but it survives a
bot restart and keeps the token out of the process that runs the browser.

**In split mode the «🖥 Войти в профиль» button does not start the browser
immediately.**
The bot writes a request to the database and `worker` brings up Xvfb + Chromium
within ~5 seconds on its next tick. Only the bot sends reports to Telegram: the
worker does not drain notifications, otherwise they would be lost.

> **One stack per machine.** Combined and split share ports 6000–6100. A second
> stack either fails to start (`port is already allocated`) or its `websockify`
> dies with `Address already in use` — and the VNC link resets. Keep exactly one
> running.

---

## Maintenance

All commands run from the project directory. To avoid repeating the long `-f`,
put it in a variable:

```bash
COMPOSE="docker compose -f compose.split.yaml -f compose.split.hostnet.yaml"
```

| What you need | Command |
| --- | --- |
| Status | `$COMPOSE ps` |
| Logs | `$COMPOSE logs -f worker` |
| Stop, keeping containers | `$COMPOSE stop` |
| Start again | `$COMPOSE start` |
| Stop and remove | `$COMPOSE down` |
| Bring up again | `$COMPOSE up -d` |
| Rebuild after `git pull` | `$COMPOSE up -d --build` |

**`stop` ≠ `down`.** `stop` ends the processes but keeps the containers:
`start` restores everything. `down` removes the containers — the next `up`
creates new ones (different hostname, clean network). Data survives either way:
`./data` is a host directory and the project has no named volumes, so `down -v`
is unnecessary and would not "clean" anything.

**Close VNC sessions before `down`**, or `running` rows will be left behind.

```bash
docker exec uwp-worker /entrypoint.sh vnc list
docker exec uwp-worker /entrypoint.sh vnc stop <id>
```

The worker sweeps such rows on its next start (`sweep_orphans`), but until then
the profile stays busy.

---

## Daily use

Everything is done with buttons in the bot. The persistent keyboard at the
bottom has four buttons: «🌐 Сайты», «👤 Профили», «🌍 Прокси», «❓ Помощь».

- **🌐 Сайты** (sites) — the list; each site has a card with «🟢 Включить» /
  «🔴 Выключить», «▶️ Запустить сейчас», «⚙️ Настройки»,
  «🖥 Войти в профиль», «📜 История» and «🗑 Удалить».
- **⚙️ Настройки** — interval (`45-90`), profile, proxy, report mode, parser
  settings.
- **📜 История** — the site's recent runs; a failed one has «🔁 Повторить».
- **👤 Профили** — create a persistent profile and open VNC.
- **🌍 Прокси** — add and test.

The interval is in minutes: `60` is every 60 minutes, `45-90` is random within
that range. A manual run does not shift the schedule: it joins the queue with
priority, and the next scheduled run counts from its own time.

Chat commands: `/start`, `/help`, `/id`, `/sites`, `/runs`.

---

## Manual login over VNC

Sites with a login use a persistent profile. Create one and request a session:

```bash
docker exec uwp-worker /entrypoint.sh profile create Steam-RU
docker exec uwp-worker /entrypoint.sh profile list
docker exec uwp-worker /entrypoint.sh profile login 1
```

`profile login` brings up the stack in this very process and holds it: the link
and password are printed right away, and Ctrl+C closes the session. The bot is
more convenient — the link arrives as a message, the worker owns the session,
and there is nothing to interrupt.

The same exists in the bot: «👤 Профили» → profile → «🖥 Войти», or a
site card → «🖥 Войти в профиль» (then the browser opens the site's page and
takes its proxy).

A link like `http://<UWP_PUBLIC_HOST>:6042/vnc.html#autoconnect=1` opens in a
browser. The password travels in the fragment (`#password=...`) and is not sent
to the server. Log in to the site and press «✅ Готово» — the processes stop and
the profile is released.

A session lives 60 minutes (extendable by 30) and closes after 20 minutes
without clients. The password is 8 characters: RFB checks only the first eight
anyway, and a longer string would display characters the server ignores.

### Access from another machine

Ports 6000–6100 are published **on the host's `127.0.0.1` only**: noVNC speaks
HTTP and must not be exposed to the network. Tunnel:

```bash
ssh -N -D 1080 user@server
```

Point your browser (or a separate profile in it) at SOCKS5 `127.0.0.1:1080`:
`http://localhost:6042/vnc.html` goes to the server and no port is opened
outward. The alternative is a reverse proxy with TLS and Basic Auth, with
`UWP_PUBLIC_HOST` and `UWP_PUBLIC_SCHEME=https` pointing at it.

### Small things that save time

- Port **6000 is never handed out**: browsers treat it as unsafe (it is the X11
  port), so the first session gets 6001.
- In split mode the ports belong to the `worker` container.
- Every session writes a log to `/data/vnc/<profile-id>-<port>.log`.
- If the browser could not take the profile, the session fails immediately with
  the reason in chat instead of showing an empty desktop.
- The site password is stored only in the browser profile; it is not in the
  database.

---

## Reports

After a run the worker writes an `.ods` to `data/exports/<site-slug>/` and
queues an event; the bot sends the file to the admins. The name is
`{parser}_{site}_{YYYY-MM-DD_HH-MM}.ods`, and the template is changed by
`UWP_REPORT_NAME_TEMPLATE`. The last `UWP_EXPORTS_RETENTION` (50) per site are
kept. The directories `data/artifacts/<run-id>` (a screenshot of a failed run
and whatever else the parser saved) are trimmed to the last
`UWP_ARTIFACTS_RETENTION` (20).

The report mode is set per site:

- `always` — after every run (the default);
- `changes` — only if there are new or changed rows;
- `never` — do not write.

The file always contains the full snapshot and the «Новое» column; changed rows
are highlighted. The mode decides only whether to write a file, not what goes
into it.

«Новое» means "was not in the previous run", not "never seen". A row that
disappeared from the site and later came back (a discount ended and began
again) is marked new again. The «Исчезло» column in the metadata counts how many
rows did not appear in this particular run.

If the mode says no file is needed (`changes` with no changes, `never`), the
bot stays silent. If a report was supposed to appear but writing failed, you
get «⚠️ Отчёт не создан» with the reason. A partial run is marked
«⚠️ Прогон частичный» and lists what could not be collected.

Downloading a report by hand:

```bash
docker exec uwp-worker /entrypoint.sh export steam_skidki -o /data/report.ods
docker cp uwp-worker:/data/report.ods .
```

---

## Adding your own parser

1. Create a folder `src/uwp/parsers/<key>/` with `__init__.py` and `parser.py`.
2. In `parser.py` declare exactly one `BaseParser` subclass:

```python
from typing import ClassVar
from pydantic import BaseModel, Field
from uwp.parsers.base import BaseParser, Column, ParseContext, ParseResult


class MySettings(BaseModel):
    max_pages: int = Field(default=1, ge=1, le=5, description="Сколько страниц листать")


class MyParser(BaseParser):
    key = "my_site"
    title = "Мой сайт"
    description = "Что собираем"
    url = "https://example.com/"
    requires_login = False
    settings_model = MySettings
    columns: ClassVar[list[Column]] = [
        Column("title", "Название", width=40),
        Column("url", "Ссылка", width=60),
        Column("price", "Цена", width=12, fmt="0.00"),
    ]

    async def run(self, ctx: ParseContext) -> ParseResult:
        settings: MySettings = ctx.settings
        await ctx.page.goto(self.url, wait_until="domcontentloaded")
        rows = [{"external_key": "id-1", "title": "…", "url": "…", "price": 1.0}]
        return ParseResult(rows=rows, meta={"pages": 1})
```

The strings in the sample are Russian for a reason: `title`, `description` and
the column headers are shown to humans — in the bot and as `.ods` headers. Code
identifiers stay English.

3. Restart the service: `$COMPOSE restart worker`. The parser appears in
   `uwp parsers list` and in the bot («🌐 Сайты» → «➕ Добавить сайт»).

Rules:

- **`external_key` is mandatory in every row** — new/changed detection is based
  on it (for Steam it is `app_id`). It does not appear in the report unless it
  is in `columns`.
- Raise errors with a type: `ParseError(kind=ErrorKind.blocked, message="…",
  partial_rows=[…])`. Kinds: `login_required`, `blocked`, `transient`,
  `parser_error`, `config_error` — retries and notifications depend on the kind.
- `columns` define the report: order, headers, width (`width`) and numeric
  format (`fmt`, e.g. `"0.00"`). The keys `url` and `*_url` are rendered as
  links. If a column's value drifts between runs by itself (a review count, a
  viewer number), mark it `volatile=True`: it stays in the report but stops
  counting as a change for the "changes only" mode.
- A broken parser does not take the service down: it is marked `broken` and
  stays visible in the list.
- Write a README next to the parser — as in `src/uwp/parsers/steam_deals/`.

A screenshot for investigating problems: `ctx.save_artifact("screenshot.png", data)`.

---

## Commands without the bot

The image ships a `uwp` CLI. In split mode almost everything belongs to the
worker:

```bash
docker exec uwp-worker /entrypoint.sh doctor         # environment diagnostics
docker exec uwp-worker /entrypoint.sh parsers list   # installed parsers
docker exec uwp-worker /entrypoint.sh site list      # sites and schedules
docker exec uwp-worker /entrypoint.sh site run steam_skidki
docker exec uwp-worker /entrypoint.sh profile list   # persistent profiles
docker exec uwp-worker /entrypoint.sh proxy test 1   # test a proxy
docker exec uwp-worker /entrypoint.sh vnc list
docker exec uwp-worker /entrypoint.sh export 12 -o /data/run12.ods
docker cp uwp-worker:/data/run12.ods .
```

`doctor` checks the data directories, the browser binary and its launch,
Xvfb/x11vnc/websockify (a warning only), the token, the admins, the encryption
key and the database migrations. Exit code 1 if something is broken — the
healthcheck relies on that.

The worker deliberately has no token, so its healthcheck calls
`doctor --offline --worker`: the `--worker` flag drops the `UWP_BOT_TOKEN`
check.

In combined mode the same commands go through
`docker compose exec uwp /entrypoint.sh …`.

`/entrypoint.sh` in `exec` is not decoration: without it the command runs as
root and `doctor` confirms writes to directories that uid 1000 cannot write to.

---

## Security

- **VNC ports are loopback-only.** noVNC speaks HTTP with an 8-character
  password and is not exposed outward. For remote access, use an SSH tunnel or a
  TLS reverse proxy.
- **Chromium sandbox.** The compose files mount `docker/seccomp-uwp.json` — a
  Docker profile with three relaxations, without which Chromium cannot build its
  sandbox from namespaces. What exactly is relaxed and why is in
  `docker/README-seccomp.md`. When starting with a bare `docker run`, pass
  `--security-opt seccomp=./docker/seccomp-uwp.json`, otherwise the browser
  honestly refuses to start. The emergency option `UWP_NO_SANDBOX=true` starts
  the browser, but the renderer ends up in namespaces shared with the container
  and can see `/data` — profiles with live cookies and the database.
- **The process environment is hidden.** The CLI sets `PR_SET_DUMPABLE=0`, so
  `/proc/<pid>/environ` cannot be read even by processes of the same uid.
  Without that, the VNC browser (same uid 1000) could read `UWP_BOT_TOKEN` and
  `UWP_SECRET_KEY`.
- **The token belongs to the bot only.** In split mode `UWP_BOT_TOKEN` is
  deliberately empty in the worker: only `bot` polls Telegram.
- **Admins.** The gate sits at the update level: the bot ignores everyone not in
  `UWP_ADMIN_IDS`/`admins` and answers with one polite refusal to any update
  type. The bot token is as much a secret as a password.
- **The encryption key.** Set `UWP_SECRET_KEY` before adding proxies: without it
  passwords are stored in plaintext, and changing the key makes the old ones
  unreadable. Use a long random key (`openssl rand -hex 32`): it is stretched by
  PBKDF2, so a database leak does not allow a fast brute force, but a weak key is
  still guessable.
- **Files.** `data` is created with mode `0700`, the database with `0600`, and
  processes run with `umask 077`: profiles with live cookies, the WAL and VNC
  passwords are not readable by other users of the host. The VNC password is
  wiped when the session stops, and the row is deleted after
  `UWP_VNC_HISTORY_DAYS` (7 days).
- **The proxy bridge.** Chromium cannot send `Proxy-Authorization` in a
  `CONNECT` tunnel, so the proxy password is added by a local bridge on
  `127.0.0.1`. The bridge does not authenticate the client (Chromium could not
  present a password), so it refuses to open internal addresses — loopback,
  RFC 1918, link-local (including `169.254.169.254`), `*.internal`/`*.local`.
  That way no process in the container can turn a paid proxy into a path to the
  Docker socket or cloud metadata.
- **Reports.** Values from HTML end up in the `.ods`; only `http(s)` links become
  clickable, while `javascript:`/`file:`/`data:` stay text. The file name
  template is sanitised: `../` cannot write outside `data/exports`.
- **Cleanup.** Once an hour the worker deletes expired clean profiles, old VNC
  session rows and extra `data/artifacts` directories; reports beyond the limit
  are trimmed on every new write.

The above is what is protected. What is **deliberately not protected**, and
from whom — including the threat model and the known limitations (the
8-character RFB password, noVNC over HTTP, unencrypted profiles) — is in
[`SECURITY.md`](SECURITY.md).

---

## Development without Docker

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
uwp browser install      # downloads fingerprint-chromium into data/browser
uwp doctor
uwp init && uwp run      # bot + worker
```

Tests:

```bash
pytest                                                    # unit tests, no network, no browser
UWP_LIVE=1 pytest -m live                                 # live Steam page
UWP_TEST_BROWSER=/path/to/chrome pytest -m integration    # a run in a real browser
ruff check src tests
```

`ruff format` is not used in this project — only `ruff check`.

---

## Making a release

A release is a git tag: pushing one makes `.github/workflows/release.yml` build
the wheel, run the suite on the tagged commit, and publish the GitHub release
page from `docs/releases/v<version>.md`. No token is needed locally.

```bash
./scripts/release.sh beta      # 0.1b1 -> 0.1b2   next beta on the same line
./scripts/release.sh release   # 0.1b1 -> 0.1     drop the beta label
./scripts/release.sh minor     # 0.1b1 -> 0.2b1   start a new line
```

The script bumps `pyproject.toml` and `src/uwp/__init__.py` (the version lives
in both, and a test fails if they drift), points both READMEs at the new notes
file, writes those notes from the commits since the last tag, runs the suite,
then commits and pushes the tag. A red suite means no tag — and the working
tree is restored, so the next attempt starts clean.

Two flags exist for looking before you leap:

- `--dry-run` prints what would change and writes nothing.
- `--no-push` stops after the commit and the tag, so you can inspect them.

Passing a comment is impossible: the notes are generated. Edit
`docs/releases/v<version>.md` and run the workflow manually (from `main`, with
the tag as an input) to republish a page.

---

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| The bot is silent | `$COMPOSE logs -f bot`; check `UWP_BOT_TOKEN`; `uwp doctor`. |
| «⛔ Доступ только для администратора» in the bot | Your id is not in `UWP_ADMIN_IDS`/`admins`. The bot will not show it: get it from `@userinfobot`. |
| The container is `Restarting` | Most likely no access to `api.telegram.org`: set `UWP_TELEGRAM_PROXY`. |
| `fingerprint-chromium not found` | `uwp browser install` or check `UWP_BROWSER_PATH`. |
| A run fails with `blocked` | A captcha or a region block: add a proxy, change the parser region. |
| A run fails with `login_required` | A persistent profile and a VNC login are needed; then attach the profile to the site. |
| No report arrived | Check the report mode (`changes` stays silent with no changes) and `uwp export <id>`. |
| The VNC link does not open | The port is published on `127.0.0.1` only: from another machine you need an SSH tunnel or a TLS proxy, or `UWP_PUBLIC_HOST` is wrong; `uwp vnc list`. In split mode the `worker` raises the session — wait a few seconds. If the VNC log says `Address already in use`, a second stack is running on the machine. |
| The VNC link resets | `websockify` did not take the port (usually because of a second stack). The session fails with the reason in chat; check that exactly one stack is running. |
| `uwp vnc stop` "does nothing" | The command only marks the session; the worker kills the processes on its next tick (up to 5 s). |
| noVNC opened but the screen is empty | Look at `/data/vnc/<profile-id>-<port>.log` in the container. The port is from 6000–6100, excluding 6000. |
| `uwp: /data is not writable by uid 1000` | The directory belongs to another uid. On the host: `sudo chown -R 1000:1000 data`. |
| `database is locked` | Do not run two workers on one volume. |
| A profile is "busy" | A run is in progress or a lease is stuck: wait for `UWP_ORPHAN_THRESHOLD`, or restart the worker. |

More detail — in `uwp doctor` and the logs (`UWP_LOG_LEVEL=DEBUG`).

---

## License

MIT — the full text is in [`LICENSE`](LICENSE). This covers the code of this
project.

Two included files are not ours and are not covered by MIT:
`docker/seccomp-uwp.json` is a derivative of Docker's profile (Apache-2.0,
modified), and the fixtures `tests/fixtures/steam_search*.html` are Steam
markup (Valve Corporation, no licence granted, used as test data). What else is
installed at build time and under which licences is in
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md), along with the full Apache-2.0
text.
