# Documentation

[`../README.md`](../README.md) answers three questions and stops: what this is,
whether it fits you, and how to get the first report. Everything that would push
that first command below the fold lives here — the operating detail, the
measurements behind design decisions, and the implementation plans written while
the code was being built.

The plans are a historical record, not instructions. They are kept because the
numbers in them are the evidence for several rules in `AGENTS.md` — a future
contributor who wants to "simplify" the VNC readiness check should be able to
read why it exists.

**Русская версия:** [`README.ru.md`](README.ru.md).

## Operating guide

### Permissions on `./data`

Docker creates the directory as `root`, so the container starts as root, sets
the owner to `1000:1000` once, and immediately drops privileges to `uwp`. You do
not need to do anything by hand. Without permission to `chown` (an NFS volume,
for example), the container exits with code 2 and a hint:

```bash
mkdir -p data && sudo chown -R 1000:1000 data
```

`uwp: /data is not writable by uid 1000` means the directory belongs to another
uid; the same command fixes it.

### How to find your Telegram ID

The bot cannot tell you: it only shows `/id` to someone who is already an admin,
and it answers a stranger with a refusal without writing the id to the log. Get
the id any external way — from `@userinfobot`, for example — write it into
`.env` and bring the stack up:

```bash
$EDITOR .env          # UWP_ADMIN_IDS=111,222
docker compose -f compose.split.yaml -f compose.split.hostnet.yaml up -d
```

`UWP_ADMIN_IDS` is a comma-separated list (`111,222`). Besides `.env`, admins
can be added to the `admins` table: the bot admits the union of both lists, and
notifies only those with `admins.notify = 1`.

### The Telegram proxy, in detail

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
  exec bot /entrypoint.sh doctor | grep -i proxy
```

`doctor` actually opens `api.telegram.org` through the proxy and prints the
reason for failure (port, TLS timeout, wrong scheme). The password is masked in
the output. The container healthcheck runs `doctor --offline`, which validates
only the address format: a full check can spend up to 12 seconds (a 10 s
timeout plus a 2 s hard guard) on a slow third-party proxy, and that must not
flip a working container to `unhealthy`.

The output follows `UWP_DEFAULT_LANGUAGE`. With the default `ru` the label is
«прокси Telegram», so `grep -i proxy` finds nothing and `grep -i прокси` does;
with `en` it is the other way round.

**A proxy on the host's `127.0.0.1`.** In a bridged container `127.0.0.1` is
the container itself. Two ways out:

```bash
# 1. Give the container the host network (port 2080 is then 127.0.0.1)
docker compose -f compose.split.yaml -f compose.split.hostnet.yaml up -d

# 2. Listen on the host's LAN address and point .env at it
UWP_TELEGRAM_PROXY=socks5://192.168.1.10:2080
```

### Keeping the stack up

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

**Do not move the worker onto the host network.** `compose.split.hostnet.yaml`
gives the host network to `bot` only, on purpose: the worker keeps publishing
the VNC band through Docker, and a worker with `network_mode: host` would have
`websockify` binding the same ports directly as `docker-proxy` holds them. The
session then dies at `Address already in use` while Xvfb, Chromium and x11vnc
stay healthy — the admin sees a link that resets. `compose.hostnet.yaml` (the
combined overlay) does run everything on the host network; it works because no
port publishing remains, and it sets `UWP_VNC_LISTEN_HOST=127.0.0.1` so noVNC
does not land on every interface.

### What the worker cleans up

Housekeeping runs on the worker's first maintenance tick and then once an hour
(`CLEANUP_SECONDS = 3600` in `app.py`), and the worker survives a failure in any
single step:

- **expired clean profiles** — the folder and the row of a disposable profile
  past `expires_at` are removed (`profiles.cleanup_clean`); persistent profiles
  are never touched;
- **old VNC session rows** — rows in `stopped`/`failed` are deleted once
  `started_at` is older than `UWP_VNC_HISTORY_DAYS` (7 days); the password in
  them was already blanked at stop, see the VNC section below;
- **artifact directories** — `data/artifacts/<run-id>` is trimmed to the last
  `UWP_ARTIFACTS_RETENTION` (20). Only directories that hold a file count
  against that number: a run creates its directory when it saves something, so
  an empty one is a capture that failed and is dropped regardless of `keep`.

Reports are not part of that hourly pass: `data/exports/<site-slug>/` is pruned
to the last `UWP_EXPORTS_RETENTION` (50) on **every write**, so a site that runs
rarely keeps its old files until the next report.

### VNC sessions in detail

Sites with a login use a persistent profile. Create one and request a session:

```bash
docker exec uwp-worker /entrypoint.sh profile create Steam-RU
docker exec uwp-worker /entrypoint.sh profile list
docker exec uwp-worker /entrypoint.sh profile login 1 --site steam_skidki
```

`profile login` brings up the stack in this very process and holds it: the link
and password are printed right away, and Ctrl+C closes the session. The bot is
more convenient — the link arrives as a message, the worker owns the session,
and there is nothing to interrupt.

`--site` takes a slug or a numeric id and names the site whose login page the
browser opens; a site's own proxy is used when it has one, otherwise the
profile's. Without `--site` the service resolves the profile's attachment:
exactly one attached site gives both the login page and the proxy; none
attached is refused because there is no site to take them from, and several
are refused because they may use different proxies and a guess would log the
account in from the wrong country. That is the same rule the profile card's
«🖥 Log in» follows.

A link like `http://<UWP_PUBLIC_HOST>:6042/vnc.html#autoconnect=1` opens in a
browser. The password travels in the fragment (`#password=...`) and is not sent
to the server. Log in to the site and press «✅ Done»: the processes stop and
the profile is released.

The link opens a kiosk: one full-screen Chromium window with the site, no tab
strip, no location bar, no desktop panel or menu, and no DevTools. Reload (`F5`,
`Ctrl+R`) and history (`Alt+←`) are left to the page; every other browser chord
is grabbed by `fluxbox` before Chromium sees it, because kiosk mode alone still
opens a full window on `Ctrl+N` (measured). Chromium policy files are not used:
`DeveloperToolsAvailability=2` makes `launch_persistent_context` time out after
180 s and stops the parsers, and a policy is machine-wide rather than per
profile.

The window does not save files. A download is refused before it reaches the
disk: the profile's `Default/Preferences` is written with
`download.prompt_for_download: true` and **no** `download.default_directory`
before Chromium starts, so Chromium has nowhere to put the file and the
"Save as" dialog does not exist in kiosk mode. Measured in the container: a
page's own `a.click()` saved a 50 KB `SteamSetup.exe` with no gesture from the
admin, and `download.restrictions` did not stop it (3, 4 and 7 all wrote the
file); the prompt-without-directory pair leaves the click unanswered and the
page navigable, and Chromium records it as `interrupt_reason=40`
(`USER_CANCELED`). `default_directory` is removed rather than overwritten,
because a profile from an earlier build already names `data/downloads` and any
directory at all brings the file back. Parse runs are not affected: nothing in
a parser clicks a download link, and Playwright refuses downloads on its own
(`accept_downloads` is off by default).

This is a UI boundary, not a sandbox. The visitor still types into the page; the
worker's environment is hidden from the browser on another layer (`browser_env`
allowlist and `PR_SET_DUMPABLE=0`).

A session lives 60 minutes (extendable by 30) and, once a client has connected,
closes 20 minutes after the last one disconnects. The password is 8 characters:
RFB checks only the first eight anyway, and a longer string would display
characters the server ignores.

The password has a shorter life than the session row: `stop()` writes
`password = ""` as it marks the row `stopped`, and `launch()` wipes a session
that failed the same way. The row itself survives as history until the hourly
housekeeping deletes finished rows older than `UWP_VNC_HISTORY_DAYS` (7 days) —
that is what keeps a copy of `data/uwp.db` from carrying every password ever
issued.

#### Access from another machine

Ports 6000–6100 are published **on the host's `127.0.0.1` only**: noVNC speaks
HTTP and must not be exposed to the network. Tunnel:

```bash
ssh -N -D 1080 user@server
```

Point your browser (or a separate profile in it) at SOCKS5 `127.0.0.1:1080`:
`http://localhost:6042/vnc.html` goes to the server and no port is opened
outward. The alternative is a reverse proxy with TLS and Basic Auth, with
`UWP_PUBLIC_HOST` and `UWP_PUBLIC_SCHEME=https` pointing at it.

#### Small things that save time

- Port **6000 is never handed out**: browsers treat it as unsafe (it is the X11
  port), so the first session gets 6001.
- In split mode the ports belong to the `worker` container.
- Every session writes a log to `/data/vnc/<profile-id>-<port>.log`.
- If the browser could not take the profile, the session fails immediately with
  the reason in chat instead of showing an empty desktop.
- The site password is stored only in the browser profile; it is not in the
  database.

### Report files

After a run the worker writes an `.ods` to `data/exports/<site-slug>/` and
queues an event; the bot sends the file to the admins. The name is
`{parser_title}_{site_name}_{YYYY-MM-DD_HH-MM}.ods` — the placeholders are
`{parser_title}`, `{site_name}` and `{timestamp}`, and the template is changed
by `UWP_REPORT_NAME_TEMPLATE`. The last `UWP_EXPORTS_RETENTION` (50) per site are
kept. The directories `data/artifacts/<run-id>` (a screenshot of a failed run
and whatever else the parser saved) are trimmed to the last
`UWP_ARTIFACTS_RETENTION` (20). A screenshot that could not be taken is logged
at `WARNING`; wait for it is capped at 5 s, because a crashed renderer never
answers.

The report mode is set per site:

- `always` — after every run (the default);
- `changes` — only if there are new or changed rows;
- `never` — do not write.

The file always contains the full snapshot and the «New» column; changed rows
are highlighted. The mode decides only whether to write a file, not what goes
into it.

«New» means "was not in the previous run", not "never seen". A row that
disappeared from the site and later came back (a discount ended and began
again) is marked new again. The «Vanished» column in the metadata counts how
many rows did not appear in this particular run.

If the mode says no file is needed (`changes` with no changes, `never`), the
bot stays silent. If a report was supposed to appear but writing failed, you
get «⚠️ Report not created» with the reason. A partial run is marked
«⚠️ Partial run» and is always reported, even when the export mode would
otherwise keep quiet — rows were lost, and that must not look like "nothing
changed". The ids that did not arrive stay in the worker log.

Downloading a report by hand:

```bash
docker exec uwp-worker /entrypoint.sh export steam_skidki -o /data/report.ods
docker cp uwp-worker:/data/report.ods .
```

### Command line

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

### Writing your own parser

1. Create a folder `src/uwp/parsers/<key>/` with `__init__.py` and `parser.py`.
2. In `parser.py` declare exactly one `BaseParser` subclass:

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

The parser name, the settings labels and the column headers are rendered per
language, both in the bot and as `.ods` headers; the description is carried along
and will be shown when a screen needs it. A parser without keys works as written:
its own `title`, `description` and `Column.title` are used. One that ships in this
repository points `title_key`, `description_key`, `Column.title_key` and
`Field(..., json_schema_extra={"title_key": ...})` at the catalogue
(`uwp.i18n.parsers`) so those strings follow the report language, while the
literal strings stay as the fallback. Code identifiers stay English. The
`parser.my_site.*` keys in the sample do not exist yet — add them to
`uwp.i18n.parsers` for both `ru` and `en` before pointing at them, or leave the
`*_key` arguments out until you do, and the parser falls back to `title`,
`description` and `Column.title` exactly as a third-party one does.

`url` is the page the parser scrapes and the one the report's `URL` row shows;
`auth_url` is the page a VNC login opens. Leave `auth_url` out and the login
uses `url` — that is what a parser written before the attribute relies on.

The repository ships two parsers: `steam_deals` reads a DOM with
`BeautifulSoup`, and `soundcloud_trending` shows the other common shape — a page
that already fetches its own JSON, so the parser intercepts the response and
reads exact values instead of the rounded text in the DOM.

3. Restart the service: `$COMPOSE restart worker`. The parser appears in
   `uwp parsers list` and in the bot («🌐 Sites» → «➕ Add site»).

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
- Write a README next to the parser — as in `src/uwp/parsers/steam_deals/`
  (DOM) or `src/uwp/parsers/soundcloud_trending/` (intercepted API).

A screenshot for investigating problems: `ctx.save_artifact("screenshot.png", data)`.

### Making a release

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

### When something breaks

| Symptom | What to do |
| --- | --- |
| The bot is silent | `$COMPOSE logs -f bot`; check `UWP_BOT_TOKEN`; `uwp doctor`. |
| «⛔ Administrators only.» in the bot | Your id is not in `UWP_ADMIN_IDS`/`admins`. The bot will not show it: get it from `@userinfobot`. |
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

More detail — in `uwp doctor` and the logs (`UWP_LOG_LEVEL=DEBUG`). The bot's
own messages follow the admin's language, so the refusal above reads
«⛔ Доступ только для администратора.» in a Russian chat.

## Images used by the READMEs

| File | What it shows |
|---|---|
| [`images/report.png`](images/report.png) | a real `.ods` report opened in LibreOffice Calc |
| [`images/vnc-desktop.png`](images/vnc-desktop.png) | the VNC kiosk: one Chromium window, no panel or tabs, sandbox enabled |

Both were captured from the running service, not drawn: the report from 50 rows
of a real Steam run, the kiosk from the same image the deployment uses.

## Design specs

A spec states the problem, the alternatives and the chosen shape *before* the
code. Russian-language; the reasoning is in the ordering of the arguments.

| Document | Covers |
|---|---|
| [`superpowers/specs/2026-11-13-ultimate-web-parser-design.md`](superpowers/specs/2026-11-13-ultimate-web-parser-design.md) | the whole service: database schema, worker loop, profiles, proxies, VNC, reports |
| [`superpowers/specs/2026-11-14-persistent-menu-and-vnc-desktop-design.md`](superpowers/specs/2026-11-14-persistent-menu-and-vnc-desktop-design.md) | the reply keyboard and the full-size VNC desktop |
| [`superpowers/specs/2026-11-21-vnc-kiosk-design.md`](superpowers/specs/2026-11-21-vnc-kiosk-design.md) | the VNC login as a kiosk: one window, no panel or menu, no DevTools |
| [`superpowers/specs/2026-11-16-volatile-columns-in-changes-mode-design.md`](superpowers/specs/2026-11-16-volatile-columns-in-changes-mode-design.md) | why a drifting counter must not make `changes` fire every run |
| [`superpowers/specs/2026-11-21-parser-auth-url-design.md`](superpowers/specs/2026-11-21-parser-auth-url-design.md) | the parser's login page as `auth_url`, separate from the run URL |
| [`superpowers/specs/2026-11-22-soundcloud-parser-design.md`](superpowers/specs/2026-11-22-soundcloud-parser-design.md) | a second parser: exact counters from the page's own API, and why there is no DOM fallback |

## Implementation plans

A plan turns a spec into ordered, verifiable tasks. Each one was executed
test-first; the measured symptom is quoted in the commit that closed it.

| Document | Covers |
|---|---|
| [`superpowers/plans/2026-11-13-ultimate-web-parser.md`](superpowers/plans/2026-11-13-ultimate-web-parser.md) | building the service from an empty repository |
| [`superpowers/plans/2026-11-14-persistent-menu-and-vnc-desktop.md`](superpowers/plans/2026-11-14-persistent-menu-and-vnc-desktop.md) | the permanent menu, the VNC desktop, and seven deferred defects |
| [`superpowers/plans/2026-11-21-vnc-kiosk.md`](superpowers/plans/2026-11-21-vnc-kiosk.md) | the kiosk browser, the grabbed chords, and the docs to match |
| [`superpowers/plans/2026-11-15-returned-items-and-new-column.md`](superpowers/plans/2026-11-15-returned-items-and-new-column.md) | rows that vanish and come back, and the «Новое» column |
| [`superpowers/plans/2026-11-15-sandbox-and-steam-regions.md`](superpowers/plans/2026-11-15-sandbox-and-steam-regions.md) | the three seccomp relaxations, and regional Steam parsing |
| [`superpowers/plans/2026-11-16-volatile-columns-in-changes-mode.md`](superpowers/plans/2026-11-16-volatile-columns-in-changes-mode.md) | `Column(volatile=True)` |
| [`superpowers/plans/2026-11-21-parser-auth-url.md`](superpowers/plans/2026-11-21-parser-auth-url.md) | `auth_url`, and a login that refuses an ambiguous profile |
| [`superpowers/plans/2026-11-22-soundcloud-trending-parser.md`](superpowers/plans/2026-11-22-soundcloud-trending-parser.md) | the SoundCloud parser: hydration, response interception, partial runs |

## Elsewhere

| Document | Why it is not here |
|---|---|
| [`../AGENTS.md`](../AGENTS.md) | instructions for changing the code; kept at the root so an agent finds it |
| [`../SECURITY.md`](../SECURITY.md) | the threat model, next to the code it describes |
| [`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md) | licence obligations, at the root where a recipient expects them |
| [`../docker/README-seccomp.md`](../docker/README-seccomp.md) | how to rebuild the seccomp profile |
