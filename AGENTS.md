# AGENTS.md

Guidance for AI coding agents working on UltimateWebParser. Human-facing docs
live in `README.md` (English) with a Russian twin `README.ru.md`; this file is
about **how to change the code without breaking it**.

## What this project is

A self-hosted scraping service: Playwright drives a patched Chromium
(`fingerprint-chromium`) against sites, a Telegram bot controls it, results are
rendered to LibreOffice Calc `.ods` reports, and runs are scheduled with
per-site proxies and persistent browser profiles.

One process (`combined`) or two (`split`: `bot` + `worker`) share one SQLite
database in WAL mode under `./data`. The worker does the scraping; the bot owns
Telegram. In split mode the bot writes a *request* row and the worker performs
it — this asymmetry explains most of the code.

## Commands

```bash
# Tests: unit only by default, no network, no browser.
pytest
pytest tests/unit/test_vnc.py -q            # one file
pytest -q -k "websockify"                  # one topic

# Opt-in suites (both skipped unless the env var is set).
UWP_LIVE=1 pytest -m live                  # hits the real Steam store
UWP_TEST_BROWSER=/path/to/chrome pytest -m integration

# Lint. This is the only formatting gate; `ruff format` is NOT used.
ruff check src tests
```

`pyproject.toml` sets `pythonpath = ["src"]`, `asyncio_mode = "auto"`, and
`addopts = "-q"`, so async tests need no decorator and output is terse.

Use the project venv: `.venv/bin/python -m pytest`, `.venv/bin/python -m ruff`.
Do not create new virtualenvs; do not `pip install` into the system interpreter.

## Layout

```
src/uwp/
  cli.py            argparse CLI: every `uwp ...` subcommand
  app.py            composition root: builds Database, repos, services, bot
  config.py         pydantic Settings, env_prefix UWP_, creates data dirs
  domain.py         pydantic models + StrEnums (RunStatus, ErrorKind, ...)
  worker.py         job loop: claim, run, heartbeat, recover, retry
  runner.py         one run: profile, proxy bridge, browser, parser, rows, export
  scheduler.py      turns due sites into queued jobs
  browser.py        Chromium launch, profile identity, stale SingletonLock repair
  profiles.py       ProfileManager: create/lease/release profile rows + folders
  vnc.py            Xvfb + x11vnc + noVNC stack; port allocation; readiness
  proxybridge.py    loopback proxy that injects Proxy-Authorization
  env.py            browser env allowlist + PR_SET_DUMPABLE
  secretbox.py      Fernet/PBKDF2 encryption for stored passwords
  ods.py            .ods writer: styles, autofilter, links, filename sanitising
  reports.py        export policy (always/changes/never) and metadata sheet
  artifacts.py      screenshot/artifact retention
  events.py         durable notification queue
  labels.py         Russian labels shared by bot and .ods metadata
  timeutil.py       UTC storage format and local display
  seed.py           first-start admin rows + the disabled Steam site
  bot/              aiogram: app.py wiring, middleware.py admin gate,
                    handlers/{common,sites,settings,profiles}.py, keyboards, texts
  db/database.py    aiosqlite wrapper, WAL, numbered SQL migrations
  db/repo/*.py      one repository per table; no SQL above this layer
  services/*.py     bot-facing use cases over repos
  migrations/*.sql  append-only, numbered
  parsers/base.py   BaseParser, Column, ParseContext, ParseResult, ParseError
  parsers/registry.py  plug-in discovery; a broken parser is recorded, not raised
  parsers/steam_deals/ reference DOM parser with its own README
  parsers/soundcloud_trending/ reference intercepted-API parser, own README
docker/             Dockerfile assets: entrypoint.sh, seccomp profile, docs
docs/superpowers/   design docs and implementation plans (historical record)
tests/unit/         everything runs offline
tests/integration/  real Chromium, gated on UWP_TEST_BROWSER
tests/live/         real internet, gated on UWP_LIVE
```

Read `docs/superpowers/specs/` before changing behaviour that a spec covers —
several decisions there are deliberate and counter-intuitive (for example why
`items.absent` is nullable).

## Conventions

- **Tests first.** This repo was built TDD; a bugfix without a failing test that
  reproduces it will be sent back. Test names read as sentences
  (`test_default_factory_fails_when_websockify_cannot_bind`) and docstrings often
  record the *measured* symptom.
- **Comments explain why, not what.** Existing comments carry measurements
  ("measured: a second stack left a dead 6001 link"). Keep that: a new comment
  should prevent a future regression, not restate the code.
- **Line length 88** (ruff default). 101 lines in `src/` exceed it today; ruff
  does not enforce E501, so do not reformat them.
- **`from __future__ import annotations`** is used in some modules, not all.
  Match the file you are editing.
- **Migrations are append-only.** Add `NNN_name.sql`; never edit an applied one.
  The migration runner records `MAX(version)` in `schema_version`.
- **All SQL stays in `db/repo/`.** Services and handlers call repository
  methods. Repositories map rows with explicit `*_COLUMNS` tables (see
  `db/repo/_common.py`), not `**row`.
- **Datetimes are UTC-aware in Python**, stored as `YYYY-MM-DDTHH:MM:SSZ`
  (`timeutil.to_db` / `from_db`). Convert to local only for display.
- **Russian for anything a human reads**: bot texts, CLI output, `labels.py`,
  `.ods` headers, log messages. English for identifiers, docstrings and
  comments. Match the surrounding file. The one exception is the release page
  (below).
- **Release notes are English.** `docs/releases/v<version>.md` and the headings
  `scripts/release.sh` writes into them are English, because the page is
  published on GitHub next to the English `README.md` and the commit subjects
  under those headings are English. Measured on `v0.1b2`: Russian headings over
  English bullets read as two languages at once. Only the *page* is English —
  the script's own terminal output stays Russian like the rest of the CLI, and
  `v0.1b1` is left as the historical record it is.
- **Every README comes in two languages.** `README.md` is English (GitHub renders
  it), `X.ru.md` is the Russian twin. Edit both in the same commit and keep the
  section order identical — `tests/unit/test_readme_translations.py` compares
  section counts and cross-links, so a one-sided edit fails the suite. The same
  rule covers `docs/install.md`, `DISCLAIMER.md`, `SECURITY.md`,
  `THIRD_PARTY_NOTICES.md` and both parser READMEs; the list of pairs lives in
  `PAIRS` in that test file, so a new twin has to be registered there.
- **Releases are `beta|release|minor|patch|major`.** `scripts/release.sh`
  parses `X.Y` and `X.Y.Z` with an optional `bN`, and `tests/unit/test_release_script.py`
  pins every transition. `major` ships stable straight away (`0.1b3 -> 1.0.0`);
  `patch` refuses a two-part version rather than inventing a patch number.
- **No `--no-sandbox`.** The sandbox works through `docker/seccomp-uwp.json`.
  `UWP_NO_SANDBOX` is an operator escape hatch, never a default.
- **Commits** use Conventional Commit prefixes (`fix(vnc): ...`,
  `docs: ...`, `test: ...`) and describe the symptom, not the diff.

## Invariants that break things when violated

**One deployment per machine.** Both stacks publish host ports 6000–6100 for
noVNC. A second `websockify` dies with `EADDRINUSE` while Xvfb, Chromium and
x11vnc stay healthy — the session looks started and the link resets. Hence the
`_port_is_free` bind-probe before spawning and the post-accept re-check in
`_wait_for_websockify`. Do not "simplify" either away.

**A TCP accept does not prove it is *our* listener.** Any process can hold the
port. The readiness check must verify the spawned child is still alive.

**Port 6000 is never handed out.** Chromium refuses it (`ERR_UNSAFE_PORT`,
the X11 port), so `free_port` skips `UNSAFE_PORTS`.

**Split mode cannot do work synchronously.** The bot process has no GUI stack.
It books a `requested` VNC row; the worker's 5-second maintenance tick launches
it. The same applies to stopping: the bot only marks `stopping`, the worker
kills the processes. Code that touches real processes must run in the worker.

**Only the bot polls Telegram.** `UWP_BOT_TOKEN` is blanked in the worker
container on purpose, and the worker healthcheck passes `doctor --worker` to
skip the token check. Never wire token use into the worker.

**Admins are the union** of `UWP_ADMIN_IDS` and the `admins` table. The gate is
an outer middleware on `dp.update`, so it sees every update type. A non-admin
gets one refusal and the handler never runs.

**A profile is a directory with live cookies.** It must be leased before use
(`status=locked`, `lease_until`) and freed after. A stale lease is reclaimed by
`Worker.recover()` after `UWP_ORPHAN_THRESHOLD`. Never free a profile blindly —
`release_login` is conditional on `status=login` for exactly that reason.

**`external_key` is mandatory in every parser row.** It is the identity used
for new/changed detection. `runner._row_key` raises `ValueError` without it.
A row that vanishes and later returns is new again (`items.absent`).

**`auth_url` is the login page, `url` is the run page.** The VNC browser opens
`BaseParser.login_url` (`auth_url or url`), while `reports.report_meta` writes
`parser.url`. Do not merge the two to "simplify": a report whose `URL` row is a
login form points the reader at nothing. A login from the profile card resolves
the site through `sites.profile_id` and refuses when zero or several sites
match — guessing would spend the wrong proxy on a login.

**A self-drifting column must be `volatile=True`.** `export_mode=changes`
compares every non-volatile column, so a live counter (a review count, a viewer
number) makes it fire on every run. `volatile` keeps the column in the report
and out of the diff.

**Browser env is an allowlist.** `env.browser_env()` strips everything except
`BROWSER_ENV_KEYS`. Adding a variable there leaks it to page-controlled
processes. `env.hide_process_environment()` sets `PR_SET_DUMPABLE=0` so the VNC
browser (same uid 1000) cannot read `/proc/<worker>/environ`.

**The proxy bridge is unauthenticated on loopback.** It refuses internal hosts
(loopback, RFC 1918, link-local incl. `169.254.169.254`, `*.internal`,
`*.local`) so it cannot become a path to the Docker socket or cloud metadata.
Do not relax `_is_internal_host`.

**Chromium's `SingletonLock` must be cleared carefully.** After a container is
recreated the lock names a dead host/pid. `clear_stale_singleton_lock` removes
foreign-host locks unless the `SingletonSocket` still accepts, and same-host
locks only when the pid does not own `--user-data-dir` in `/proc/<pid>/cmdline`.

**The VNC login browser is a kiosk and fluxbox is its keyboard filter.** Kiosk
mode alone gives one window, no tabs, no context menu and a refused DevTools
(measured), but it still opens a full 1050×760 window on `Ctrl+N` and lands
945×1060 with `--fingerprint` unless `--window-size` pins the screen. So
`_GRABBED_CHORDS` in `vnc.py` must stay a data list, every grab must keep the
`:Exec true` command (fluxbox drops a binding whose command it cannot parse —
`:Noop` is rejected with "Invalid key/modifier"), and `F5`/`Ctrl+R`/`Alt+←` must
stay out of it. The panel and root menu are off by design: `toolbar.visible:
false` and an empty `menu` file. Chromium policies are not the tool here — a
machine-wide `DeveloperToolsAvailability=2` makes `launch_persistent_context`
time out after 180 s and stops every parser.

## Working on the code

- Run one small thing before believing a change works:
  `pytest tests/unit/test_<area>.py -q` and `ruff check src tests`.
- Never assert success from a test that passed only because a real local
  deployment was running. `_pretend_ready` in `tests/unit/test_vnc.py` stubs
  **every** readiness probe for that reason; if you add a new probe, stub it
  there and test it separately.
- Docker assets are asserted by `tests/unit/test_compose_config.py` (ports,
  volumes, seccomp, `:z`, no double `tini`). Change a compose file and that test
  tells you what the deployment contract is.
- When you touch `docker/`, keep the healthcheck routed through
  `/entrypoint.sh` so it runs as uid 1000, not root.

## Do not

- Do not commit `data/`, `.env`, `.superpowers/`, or `.worktrees/` (gitignored).
- Do not change `UWP_SECRET_KEY` handling to "just re-encrypt": a changed key
  makes existing proxy/VNC passwords unreadable, by design.
- Do not add a named Docker volume; `./data` is a bind mount and the docs say
  `down -v` is unnecessary.
- Do not run `ruff format`; it would rewrite files the project keeps unformatted.
- Do not add `--no-sandbox` to make Chromium start.
- Do not broaden the seccomp profile beyond the three documented relaxations;
  `docker/README-seccomp.md` lists them and why the stock rules fail.
- Do not delete a `running` VNC row to "clean up" — it keeps its port and
  profile reserved; stop the session, or let `sweep_orphans` handle it.

## Where to look first

| Task | File |
| --- | --- |
| Add a parser | `parsers/steam_deals/` (DOM) or `parsers/soundcloud_trending/` (intercepted API), with the README in that folder |
| Change scheduling | `scheduler.py`, `services/sites.py` |
| Change run/retry behaviour | `worker.py`, `runner.py` |
| Bot screen or button | `bot/handlers/`, `bot/keyboards.py`, `bot/texts.py` |
| VNC stack, ports, readiness | `vnc.py`, `services/vnc.py` |
| Report contents | `ods.py`, `reports.py` |
| Schema change | `migrations/`, then the matching `db/repo/` |
| Deployment | `compose*.yaml`, `Dockerfile`, `docker/entrypoint.sh` |
| Install instructions | `docs/install.md` + `install.ru.md` (Windows, WSL 2, why native is unsupported) |
| Lawful use | `DISCLAIMER.md` + `DISCLAIMER.ru.md`, quoted by the READMEs and the bot's `/help` |
| Documentation links | `scripts/check_markdown_links.py`, run by CI and `tests/unit/test_markdown_links.py` |
