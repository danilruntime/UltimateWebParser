# AGENTS.md

Guidance for AI coding agents working on UltimateWebParser. Human-facing docs
(Russian) live in `README.md`; this file is about **how to change the code
without breaking it**.

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
  parsers/steam_deals/ reference parser with its own README
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
  comments. Match the surrounding file.
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
| Add a parser | `parsers/steam_deals/` + `README.md` in that folder |
| Change scheduling | `scheduler.py`, `services/sites.py` |
| Change run/retry behaviour | `worker.py`, `runner.py` |
| Bot screen or button | `bot/handlers/`, `bot/keyboards.py`, `bot/texts.py` |
| VNC stack, ports, readiness | `vnc.py`, `services/vnc.py` |
| Report contents | `ods.py`, `reports.py` |
| Schema change | `migrations/`, then the matching `db/repo/` |
| Deployment | `compose*.yaml`, `Dockerfile`, `docker/entrypoint.sh` |
