# UltimateWebParser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Self-hosted Docker service that scrapes sites on a schedule through Playwright in fingerprint-chromium, is controlled from a Telegram bot, and sends Excel reports to the admin.

**Architecture:** One Python package `uwp` with a core (config, SQLite state, profiles, proxy bridge, runner, scheduler, worker, Excel, VNC) and pluggable parser modules discovered from `uwp/parsers/*/parser.py`. The bot and the worker are separate processes that share the SQLite database (WAL); a `combined` mode runs both in one process. Browser work happens only in the worker; the bot enqueues jobs and VNC requests and delivers events.

**Tech Stack:** Python 3.12+, Playwright (async), aiogram 3, aiosqlite, openpyxl, pydantic / pydantic-settings, beautifulsoup4, Xvfb + x11vnc + noVNC (websockify), Docker Compose.

**Spec:** `docs/superpowers/specs/2026-11-13-ultimate-web-parser-design.md`

## Global Constraints

- Package name `uwp`, distribution `ultimate-web-parser`, CLI entry point `uwp`. Python `>=3.12`; the Docker image uses `python:3.12-slim-bookworm`; local dev may run 3.14.
- All timestamps stored in SQLite are UTC ISO-8601 strings (`YYYY-MM-DDTHH:MM:SSZ`); display timezone is `UWP_TZ` (default `Europe/Moscow`).
- Browser: fingerprint-chromium `150.0.7871.186`, Linux tar.xz, sha256 `48eff860053efd9ad498d51370f0a1f59f983df39c02a72ad2a8682cdead4055`, URL `https://github.com/adryfish/fingerprint-chromium/releases/download/150.0.7871.186/ungoogled-chromium-150.0.7871.186-1-x86_64_linux.tar.xz`.
- Launch flags: `--fingerprint=<seed>`, `--fingerprint-platform=<windows|macos|linux>`, `--fingerprint-brand=<Chrome|Edge|Opera|Vivaldi>`, `--fingerprint-brand-version`, `--timezone`, `--lang`, `--accept-lang`, `--user-data-dir`, `--disable-non-proxied-udp`, `--proxy-server=http://127.0.0.1:<bridge_port>`, `--no-first-run`, `--no-default-browser-check`, `--disable-features=Translate`. Parsing is headless (`--headless=new`); VNC login is headful under Xvfb. In Docker add `--no-sandbox`.
- Clean profiles are one-shot per run: new `user-data-dir` under `data/profiles/tmp/<run_id>/`, random seed, folder deleted after the run, DB row `status='expired'`, purged after 24 h. Retries get a new clean identity.
- Persistent profiles keep a fixed `user-data-dir` and seed; seed/platform/brand cannot be edited after creation; a persistent profile is used by one run at a time.
- Defaults: `UWP_MAX_PARALLEL_RUNS=1`, `UWP_RUN_TIMEOUT=300` s, page open 30 s, navigation 60 s, worker heartbeat every 15 s, orphan threshold 90 s, `max_attempts=2`, retry pause `60 s × attempt`, VNC hard TTL 60 min, VNC idle stop 20 min, VNC port range `6000-6100`, event delivery TTL 3 days, exports retention 50 per site.
- Error kinds: `login_required`, `blocked`, `transient`, `parser_error`, `config_error`. Retry only `transient` (and `blocked` once); `login_required` never retries and offers the VNC button; partial rows are saved and the run status is `partial`.
- Excel file name: `{parser_title}_{site_name}_{YYYY-MM-DD_HH-MM}.xlsx` with Cyrillic transliterated to Latin, non-alphanumerics collapsed to `_`; overridable by `UWP_EXCEL_NAME_TEMPLATE`.
- Bot UI language is Russian; code, identifiers, log messages and commit messages are English. Conventional commits (`feat:`, `fix:`, `test:`, `chore:`, `docs:`).
- Bot access: only Telegram IDs in `UWP_ADMIN_IDS` (comma-separated) plus rows in `admins`; everyone else gets one refusal message with no details.
- Secrets: proxy passwords encrypted with Fernet when `UWP_SECRET_KEY` is set, otherwise stored plaintext and `uwp doctor` warns. Never log proxy passwords or VNC passwords at INFO.
- Data lives under `UWP_DATA_DIR` (default `./data`): `uwp.db`, `profiles/`, `exports/`, `artifacts/`.
- Tests: `pytest` for unit + integration (no network), `pytest -m live` for the real Steam run. Tests must not require a real Telegram token or network.

## Review Focus

- A run whose browser hangs: the profile must be released and the job requeued within ~90 s, never stuck `locked` forever.
- A parser that raises an unexpected exception: run becomes `parser_error` with a traceback to the admin, the worker keeps processing other sites.
- A site disabled while its job is queued or running: the job is canceled and the browser closed, not left running.
- A clean profile after a failed run: its folder is deleted and the next attempt gets a different seed.
- A non-admin Telegram user: every handler (commands, callbacks, FSM input) refuses without leaking site names or settings.

---

## File Structure

```
pyproject.toml
src/uwp/__init__.py
src/uwp/config.py
src/uwp/domain.py
src/uwp/timeutil.py
src/uwp/db/__init__.py
src/uwp/db/database.py
src/uwp/db/repo/__init__.py
src/uwp/db/repo/sites.py
src/uwp/db/repo/profiles.py
src/uwp/db/repo/proxies.py
src/uwp/db/repo/jobs.py
src/uwp/db/repo/runs.py
src/uwp/db/repo/items.py
src/uwp/db/repo/events.py
src/uwp/db/repo/admins.py
src/uwp/migrations/001_init.sql
src/uwp/browser.py
src/uwp/profiles.py
src/uwp/proxybridge.py
src/uwp/parsers/__init__.py
src/uwp/parsers/base.py
src/uwp/parsers/registry.py
src/uwp/parsers/steam_deals/__init__.py
src/uwp/parsers/steam_deals/parser.py
src/uwp/parsers/steam_deals/README.md
src/uwp/runner.py
src/uwp/excel.py
src/uwp/scheduler.py
src/uwp/worker.py
src/uwp/events.py
src/uwp/vnc.py
src/uwp/services/__init__.py
src/uwp/services/sites.py
src/uwp/services/profiles.py
src/uwp/services/proxies.py
src/uwp/services/runs.py
src/uwp/services/vnc.py
src/uwp/bot/__init__.py
src/uwp/bot/app.py
src/uwp/bot/middleware.py
src/uwp/bot/keyboards.py
src/uwp/bot/texts.py
src/uwp/bot/handlers/__init__.py
src/uwp/bot/handlers/common.py
src/uwp/bot/handlers/sites.py
src/uwp/bot/handlers/settings.py
src/uwp/bot/handlers/profiles.py
src/uwp/bot/handlers/proxies.py
src/uwp/bot/handlers/vnc.py
src/uwp/bot/handlers/runs.py
src/uwp/bot/delivery.py
src/uwp/cli.py
tests/conftest.py
tests/unit/...
tests/integration/...
tests/fixtures/steam_search.html
Dockerfile
compose.yaml
compose.split.yaml
docker/entrypoint.sh
.env.example
README.md
```

---

### Task 1: Repository skeleton and tooling

**Files:**
- Create: `pyproject.toml`, `src/uwp/__init__.py`, `tests/conftest.py`, `tests/unit/test_smoke.py`, `.gitignore`, `LICENSE`, `README.md` (stub)
- Test: `tests/unit/test_smoke.py`

**Interfaces:**
- Consumes: nothing.
- Produces: importable package `uwp` with `__version__: str = "0.1.0"`; `pytest` configured with `pythonpath = ["src"]`, markers `live` and `integration`; console script `uwp = "uwp.cli:main"` (the module arrives in Task 18 — until then the entry point is declared but unused).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_smoke.py
import uwp


def test_package_has_version():
    assert uwp.__version__ == "0.1.0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m venv .venv && .venv/bin/python -m pip install -q -e ".[dev]"` (after Step 3 creates pyproject) — for the RED phase run `.venv/bin/python -m pytest tests/unit/test_smoke.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'uwp'`

- [ ] **Step 3: Create `pyproject.toml` and the package stub**

`pyproject.toml`: build backend `hatchling`, `requires-python = ">=3.12"`, dependencies `playwright>=1.55`, `aiogram>=3.20`, `aiosqlite>=0.21`, `openpyxl>=3.1`, `pydantic>=2.9`, `pydantic-settings>=2.5`, `beautifulsoup4>=4.12`, `cryptography>=43`, `tzdata`; optional `dev` extra: `pytest>=8`, `pytest-asyncio>=0.24`, `httpx>=0.27`, `ruff>=0.6`. `[tool.pytest.ini_options]` with `pythonpath = ["src"]`, `asyncio_mode = "auto"`, `markers = ["live: hits the real internet", "integration: needs a browser"]`, `addopts = "-q"`. `[project.scripts] uwp = "uwp.cli:main"`. `[tool.hatch.build.targets.wheel] packages = ["src/uwp"]`.
`src/uwp/__init__.py`: `__version__ = "0.1.0"`.
`.gitignore`: `.venv/`, `__pycache__/`, `*.egg-info/`, `data/`, `.env`, `.superpowers/`, `.worktrees/`, `*.xlsx`, `.pytest_cache/`, `.ruff_cache/`.
`LICENSE`: MIT text. `README.md`: one-line title stub (filled in Task 20).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_smoke.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/uwp/__init__.py tests .gitignore LICENSE README.md
git commit -m "chore: scaffold uwp package and pytest tooling"
```

---

### Task 2: Configuration and domain models

**Files:**
- Create: `src/uwp/config.py`, `src/uwp/domain.py`, `src/uwp/timeutil.py`
- Test: `tests/unit/test_config.py`, `tests/unit/test_domain.py`, `tests/unit/test_timeutil.py`

**Interfaces:**
- Consumes: `uwp` package (Task 1).
- Produces:
  - `uwp.config.Settings` (pydantic-settings, `env_prefix="UWP_"`, `env_file=".env"`): `bot_token: str = ""`, `admin_ids: list[int] = []` (parsed from `"1,2"`), `data_dir: Path = Path("data")`, `tz: str = "Europe/Moscow"`, `browser_path: Path | None = None`, `browser_version: str = "150.0.7871.186"`, `browser_sha256: str = "<constraint sha>"`, `browser_url: str = "<constraint url>"`, `max_parallel_runs: int = 1`, `run_timeout: int = 300`, `page_timeout_ms: int = 30_000`, `navigation_timeout_ms: int = 60_000`, `heartbeat_interval: int = 15`, `orphan_threshold: int = 90`, `vnc_port_min: int = 6000`, `vnc_port_max: int = 6100`, `vnc_ttl_minutes: int = 60`, `vnc_idle_minutes: int = 20`, `public_host: str = "localhost"`, `secret_key: str | None = None`, `excel_name_template: str = "{parser_title}_{site_name}_{timestamp}"`, `exports_retention: int = 50`, `event_ttl_days: int = 3`, `headless: bool = True`, `no_sandbox: bool = False`, `log_level: str = "INFO"`. Properties: `db_path`, `profiles_dir`, `exports_dir`, `artifacts_dir` (all under `data_dir`), `load_settings(env: Mapping[str, str] | None = None) -> Settings` that creates the directories.
  - `uwp.domain`: enums `ProfileKind(clean|persistent)`, `ProfileStatus(free|locked|login|expired)`, `ProxyScheme(http|https|socks5)`, `JobKind(scheduled|manual)`, `JobStatus(queued|running|done|failed|canceled)`, `RunStatus(ok|partial|failed|canceled)`, `ErrorKind(login_required|blocked|transient|parser_error|config_error)`, `ExportMode(always|changes|never)`, `VncStatus(requested|running|stopping|stopped|failed)`; pydantic models `Proxy`, `Profile`, `Site`, `Job`, `Run`, `Item`, `Event`, `VncSession` mirroring the spec's fields (all `id: int`, timestamps `datetime` UTC).
  - `uwp.timeutil`: `utcnow() -> datetime`, `to_db(dt: datetime) -> str`, `from_db(s: str) -> datetime`, `format_local(dt: datetime, tz: str) -> str` (`"13.11.2026 01:20"`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_config.py
from pathlib import Path
from uwp.config import load_settings


def test_admin_ids_parsed_from_csv(tmp_path):
    s = load_settings({"UWP_ADMIN_IDS": "111, 222", "UWP_DATA_DIR": str(tmp_path)})
    assert s.admin_ids == [111, 222]


def test_directories_created(tmp_path):
    s = load_settings({"UWP_DATA_DIR": str(tmp_path / "d")})
    assert s.db_path == tmp_path / "d" / "uwp.db"
    assert s.profiles_dir.is_dir() and s.exports_dir.is_dir() and s.artifacts_dir.is_dir()


def test_defaults():
    s = load_settings({})
    assert s.run_timeout == 300 and s.vnc_port_min == 6000 and s.vnc_port_max == 6100
    assert s.tz == "Europe/Moscow" and s.max_parallel_runs == 1
```

```python
# tests/unit/test_domain.py
import pytest
from pydantic import ValidationError
from uwp.domain import Site, Profile, ProfileKind, ExportMode


def test_site_rejects_interval_min_greater_than_max():
    with pytest.raises(ValidationError):
        Site(id=1, slug="steam", name="Steam", parser_key="steam_deals",
             interval_min_min=90, interval_max_min=60)


def test_site_defaults():
    s = Site(id=1, slug="steam", name="Steam", parser_key="steam_deals",
             interval_min_min=45, interval_max_min=90)
    assert s.enabled is False and s.export_mode == ExportMode.always
    assert s.profile_mode == ProfileKind.clean and s.max_attempts == 2


def test_profile_kind_enum_values():
    assert ProfileKind.clean.value == "clean" and ProfileKind.persistent.value == "persistent"
```

```python
# tests/unit/test_timeutil.py
from datetime import datetime, timezone
from uwp.timeutil import to_db, from_db, format_local


def test_roundtrip():
    dt = datetime(2026, 11, 13, 1, 20, 5, tzinfo=timezone.utc)
    assert to_db(dt) == "2026-11-13T01:20:05Z"
    assert from_db(to_db(dt)) == dt


def test_format_local_moscow():
    dt = datetime(2026, 11, 12, 22, 20, tzinfo=timezone.utc)
    assert format_local(dt, "Europe/Moscow") == "13.11.2026 01:20"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_config.py tests/unit/test_domain.py tests/unit/test_timeutil.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'uwp.config'`

- [ ] **Step 3: Implement `config.py`, `domain.py`, `timeutil.py`**

`Settings` uses `pydantic_settings.BaseSettings` with `model_config = SettingsConfigDict(env_prefix="UWP_", env_file=".env", extra="ignore")`; `admin_ids` is `list[int]` with a `field_validator(mode="before")` splitting a comma string. `load_settings(env)` builds `Settings(**{k: v for k, v in env.items() if k.startswith("UWP_")})` (keys already carry the prefix, so pass them through `_env_file=None` and a custom source or strip the prefix — implement by constructing `Settings(_env_file=None, **{k[4:].lower(): v for k, v in env.items()})`), then `mkdir(parents=True, exist_ok=True)` for the four directories. Domain models use `model_config = ConfigDict(use_enum_values=False)`; `Site` has a `model_validator(mode="after")` rejecting `interval_min_min > interval_max_min` or values `< 1`. `timeutil` uses `datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)` and `zoneinfo.ZoneInfo`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add src/uwp/config.py src/uwp/domain.py src/uwp/timeutil.py tests/unit
git commit -m "feat: add settings, domain models and time helpers"
```

---

### Task 3: Database, migrations and repositories

**Files:**
- Create: `src/uwp/db/__init__.py`, `src/uwp/db/database.py`, `src/uwp/db/repo/__init__.py`, `src/uwp/db/repo/{sites,profiles,proxies,jobs,runs,items,events,admins}.py`, `src/uwp/migrations/001_init.sql`
- Test: `tests/unit/test_db.py`

**Interfaces:**
- Consumes: `uwp.domain`, `uwp.timeutil`.
- Produces:
  - `uwp.db.database.Database(path: Path)` with `async connect()`, `async close()`, `async migrate()`, `async execute(sql, params=()) -> int` (lastrowid), `async fetchone(sql, params=()) -> aiosqlite.Row | None`, `async fetchall(...) -> list[aiosqlite.Row]`, `async transaction()` (async context manager), and `async in_memory() -> Database` classmethod for tests.
  - Repositories constructed as `SitesRepo(db)` etc., each with async methods: `SitesRepo.get(id)`, `.get_by_slug(slug)`, `.list()`, `.create(site: Site) -> int`, `.update(id, **fields)`, `.delete(id)`, `.due(now: datetime) -> list[Site]`; `ProfilesRepo.get/list/create/update/delete`, `.lease(profile_id, until) -> bool` (UPDATE ... WHERE status='free'), `.release(profile_id)`, `.expired_clean(now)`; `ProxiesRepo.get/list/create/update/delete`; `JobsRepo.create(site_id, kind, priority=0) -> int | None` (returns `None` when a queued/running job for the site exists), `.claim(worker_id, now) -> Job | None`, `.heartbeat(job_id, now)`, `.finish(job_id, status, error=None)`, `.requeue(job_id, error)`, `.orphans(now, threshold)`, `.cancel_queued(site_id)`, `.get(id)`; `RunsRepo.create/update/get/list_for_site(limit)`; `ItemsRepo.upsert(site_id, external_key, payload, now) -> bool` (True if new), `.previous_payload(site_id, external_key) -> dict | None`, `.count(site_id)`; `EventsRepo.emit(kind, site_id, run_id, payload) -> int`, `.pending(admin_id, now, ttl_days) -> list[Event]`, `.mark_delivered(event_id, admin_id)`; `AdminsRepo.list/add/remove`.
  - Schema `001_init.sql`: tables `sites`, `profiles`, `proxies`, `jobs`, `runs`, `items`, `events`, `event_deliveries`, `admins`, `vnc_sessions`, `worker_heartbeat`, `schema_version`; unique index `ux_jobs_active ON jobs(site_id) WHERE status IN ('queued','running')`; unique `items(site_id, external_key)`; `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_db.py
import pytest
from uwp.db.database import Database
from uwp.db.repo.sites import SitesRepo
from uwp.db.repo.jobs import JobsRepo
from uwp.domain import Site


@pytest.fixture
async def db():
    d = await Database.in_memory()
    await d.migrate()
    yield d
    await d.close()


async def test_migrate_is_idempotent(db):
    await db.migrate()
    row = await db.fetchone("SELECT version FROM schema_version")
    assert row["version"] == 1


async def test_site_crud(db):
    repo = SitesRepo(db)
    sid = await repo.create(Site(slug="steam", name="Steam", parser_key="steam_deals",
                                 interval_min_min=45, interval_max_min=90))
    site = await repo.get(sid)
    assert site.slug == "steam" and site.enabled is False
    await repo.update(sid, enabled=True)
    assert (await repo.get(sid)).enabled is True
    assert [s.id for s in await repo.list()] == [sid]


async def test_only_one_active_job_per_site(db):
    sites = SitesRepo(db)
    jobs = JobsRepo(db)
    sid = await sites.create(Site(slug="s", name="S", parser_key="steam_deals",
                                  interval_min_min=1, interval_max_min=2))
    first = await jobs.create(sid, "scheduled")
    second = await jobs.create(sid, "manual")
    assert first is not None and second is None


async def test_claim_and_finish(db):
    sites, jobs = SitesRepo(db), JobsRepo(db)
    sid = await sites.create(Site(slug="s", name="S", parser_key="steam_deals",
                                  interval_min_min=1, interval_max_min=2))
    jid = await jobs.create(sid, "scheduled")
    claimed = await jobs.claim("w1", now=__import__("uwp.timeutil", fromlist=["utcnow"]).utcnow())
    assert claimed.id == jid and claimed.status == "running"
    assert await jobs.claim("w2", now=__import__("uwp.timeutil", fromlist=["utcnow"]).utcnow()) is None
    await jobs.finish(jid, "done")
    assert (await jobs.get(jid)).status == "done"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'uwp.db'`

- [ ] **Step 3: Implement the database layer**

`Database` wraps `aiosqlite.connect(path, isolation_level=None)`, sets `row_factory = aiosqlite.Row`, runs `PRAGMA journal_mode=WAL` (skipped for `:memory:`), `PRAGMA foreign_keys=ON`, `PRAGMA busy_timeout=5000`. `migrate()` reads `src/uwp/migrations/*.sql` sorted by name via `importlib.resources.files("uwp.migrations")`, applies those with number greater than `schema_version.version` inside one transaction each, and inserts the version. Repositories map rows to domain models with a shared `_row_to_model` helper per repo. `JobsRepo.claim` runs `UPDATE jobs SET status='running', claimed_by=?, claimed_at=?, heartbeat_at=? WHERE id = (SELECT id FROM jobs WHERE status='queued' ORDER BY priority DESC, created_at LIMIT 1) RETURNING id` inside a transaction, then fetches the row.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_db.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/uwp/db src/uwp/migrations tests/unit/test_db.py
git commit -m "feat: add sqlite database, migrations and repositories"
```

---

### Task 4: Parser contract and registry

**Files:**
- Create: `src/uwp/parsers/__init__.py`, `src/uwp/parsers/base.py`, `src/uwp/parsers/registry.py`
- Test: `tests/unit/test_registry.py`, `tests/unit/parsers/dummy/parser.py` (test fixture package), `tests/unit/parsers/broken/parser.py`

**Interfaces:**
- Consumes: nothing beyond stdlib + pydantic.
- Produces:
  - `uwp.parsers.base.Column(key: str, title: str, width: int = 18, fmt: str | None = None)` (dataclass).
  - `uwp.parsers.base.ParseError(Exception)` with `kind: ErrorKind`, `message: str`, `partial_rows: list[dict] = []`.
  - `uwp.parsers.base.ParseContext` dataclass: `page: Any`, `context: Any`, `settings: BaseModel`, `log: logging.Logger`, `artifacts_dir: Path`, `site: Any`, `profile: Any`, `known_keys: Callable[[], set[str]]`, `save_artifact: Callable[[str, bytes], Path]`.
  - `uwp.parsers.base.ParseResult(rows: list[dict], meta: dict = {}, notes: str | None = None)`.
  - `uwp.parsers.base.BaseParser` (ABC) with class attributes `key: str`, `title: str`, `description: str`, `url: str`, `requires_login: bool = False`, `settings_model: type[BaseModel]`, `columns: list[Column]`, classmethod `default_settings() -> dict` (returns `settings_model().model_dump()`), abstract `async run(ctx: ParseContext) -> ParseResult`.
  - `uwp.parsers.registry.ParserRegistry` with `discover(package: str = "uwp.parsers") -> ParserRegistry`, `get(key) -> BaseParser | None`, `all() -> list[BaseParser]`, `broken() -> dict[str, str]` (key → import error). Discovery imports every `uwp.parsers.<name>.parser` submodule, finds exactly one `BaseParser` subclass defined there; import errors and zero/multiple classes are recorded in `broken` and never raised.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_registry.py
from uwp.parsers.registry import ParserRegistry


def test_discovers_dummy_parser():
    reg = ParserRegistry.discover(package="tests.unit.parsers")
    assert reg.get("dummy").title == "Dummy"
    assert reg.get("dummy").default_settings() == {"limit": 5}


def test_broken_parser_does_not_raise():
    reg = ParserRegistry.discover(package="tests.unit.parsers")
    assert "broken" in reg.broken()
    assert reg.get("broken") is None
```

`tests/unit/parsers/dummy/parser.py` defines `class DummyParser(BaseParser)` with `key="dummy"`, `title="Dummy"`, `settings_model` a pydantic model `DummySettings(limit: int = 5)`, one `Column("title", "Title")`, and `run` returning `ParseResult(rows=[])`. `tests/unit/parsers/broken/parser.py` contains `raise RuntimeError("boom")` at import time. Both packages need `__init__.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'uwp.parsers'`

- [ ] **Step 3: Implement `base.py` and `registry.py`**

`discover` uses `importlib.import_module(f"{package}.{name}.parser")` for each subpackage of `package` (via `pkgutil.iter_modules(package.__path__)`), inspects `vars(module)` for classes that are subclasses of `BaseParser` and not `BaseParser` itself, and records `broken[name] = f"{type(e).__name__}: {e}"` on any exception. `BaseParser.__init_subclass__` is not needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_registry.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/uwp/parsers tests/unit/test_registry.py tests/unit/parsers
git commit -m "feat: add parser contract and discovery registry"
```

---

### Task 5: Steam deals parser

**Files:**
- Create: `src/uwp/parsers/steam_deals/__init__.py`, `src/uwp/parsers/steam_deals/parser.py`, `src/uwp/parsers/steam_deals/README.md`, `tests/fixtures/steam_search.html`
- Test: `tests/unit/test_steam_parser.py`

**Interfaces:**
- Consumes: `BaseParser`, `Column`, `ParseContext`, `ParseResult`, `ParseError` (Task 4).
- Produces:
  - `SteamSettings(BaseModel)`: `region: str = "us"`, `language: str = "english"`, `filter: Literal["topsellers", "newreleases", "specials"] = "topsellers"`, `max_pages: int = Field(2, ge=1, le=10)`, `max_price: float = Field(0, ge=0)` (0 = no limit), `min_discount_percent: int = Field(0, ge=0, le=100)`, `sort: Literal["relevance", "discount", "price"] = "relevance"`.
  - `SteamDealsParser(BaseParser)` with `key="steam_deals"`, `title="Steam — скидки"`, `url="https://store.steampowered.com/search/"`, `requires_login=False`, columns `app_id, title, url, discount_percent, price_original, price_final, currency, review_summary, review_percent, review_count, release_date, platforms, image_url`.
  - Module-level `build_search_url(settings: SteamSettings, start: int) -> str` and `parse_search_page(html: str, settings: SteamSettings) -> list[dict]` (pure, testable without a browser).
  - `run(ctx)` loops pages `start = 0, 50, …` up to `max_pages`, calls `ctx.page.goto(url, wait_until="domcontentloaded")`, `ctx.page.content()`, `parse_search_page`, stops early when a page yields no rows; returns `ParseResult(rows=..., meta={"pages": n, "url": first_url})`. If the first page yields zero rows, saves `ctx.save_artifact("empty.html", html.encode())` and raises `ParseError(kind="blocked", message="Steam returned no results (captcha or region block?)")`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_steam_parser.py
from pathlib import Path
from uwp.parsers.steam_deals.parser import SteamSettings, build_search_url, parse_search_page

FIXTURE = Path(__file__).parent.parent / "fixtures" / "steam_search.html"


def test_build_search_url():
    url = build_search_url(SteamSettings(region="de", language="german", max_pages=3), start=50)
    assert url.startswith("https://store.steampowered.com/search/?")
    assert "specials=1" in url and "cc=de" in url and "l=german" in url
    assert "start=50" in url and "filter=topsellers" in url


def test_parse_fixture_rows():
    rows = parse_search_page(FIXTURE.read_text(encoding="utf-8"), SteamSettings())
    assert len(rows) >= 40
    first = rows[0]
    assert first["app_id"] == "227300"
    assert first["title"] == "Euro Truck Simulator 2"
    assert first["url"] == "https://store.steampowered.com/app/227300/"
    assert first["discount_percent"] == 75
    assert first["price_final"] == 4.99 and first["price_original"] == 19.99
    assert first["currency"] == "USD"
    assert first["review_summary"] == "Overwhelmingly Positive"
    assert first["review_percent"] == 97 and first["review_count"] == 147597


def test_filters_by_min_discount_and_max_price():
    html = FIXTURE.read_text(encoding="utf-8")
    rows = parse_search_page(html, SteamSettings(min_discount_percent=80, max_price=5))
    assert rows and all(r["discount_percent"] >= 80 and r["price_final"] <= 5 for r in rows)


def test_rows_without_discount_are_skipped():
    rows = parse_search_page(FIXTURE.read_text(encoding="utf-8"), SteamSettings())
    assert all(r["discount_percent"] > 0 for r in rows)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_steam_parser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'uwp.parsers.steam_deals'`

- [ ] **Step 3: Capture the fixture and implement the parser**

Save a real page: `curl -A "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36" "https://store.steampowered.com/search/?filter=topsellers&specials=1&cc=us&l=english" -o tests/fixtures/steam_search.html` (≈700 KB; commit it). Parse with BeautifulSoup (`html.parser`): each `a.search_result_row` gives `data-ds-appid` (first id before any comma), `href` (strip query), `.title`, `.discount_pct` (`-75%` → 75), `.discount_original_price` / `.discount_final_price` (parse `$19.99` / `4,99€` into float + currency from the symbol or `data-price-final` cents), `.search_review_summary` class (`positive`/`mixed`/`negative`) and `data-tooltip-html` (`97% of the 147,597 user reviews`), `.search_released`, `.search_platforms` (`win mac linux` → `"Windows, macOS, Linux"`), `img` `src`. Skip rows without a discount. Sort: `discount` → by `discount_percent` desc, `price` → by `price_final` asc. `README.md` documents settings and columns for future maintainers.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_steam_parser.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/uwp/parsers/steam_deals tests/fixtures/steam_search.html tests/unit/test_steam_parser.py
git commit -m "feat: add Steam deals parser with offline fixture tests"
```

---

### Task 6: Profile identities and leasing

**Files:**
- Create: `src/uwp/profiles.py`
- Test: `tests/unit/test_profiles.py`

**Interfaces:**
- Consumes: `Settings`, `Profile`, `ProfileKind`, `ProfilesRepo`, `timeutil`.
- Produces:
  - `uwp.profiles.Identity` dataclass: `seed: int`, `platform: str`, `brand: str`, `brand_version: str`, `timezone: str`, `locale: str`, `accept_lang: str`, `viewport: tuple[int, int]`.
  - `random_identity(rng: random.Random | None = None, country: str | None = None) -> Identity` — picks one coherent machine profile from `MACHINE_PROFILES` (list of dicts: `windows`/`Chrome`/`en-US`/`America/New_York`/1920×1080, `windows`/`Edge`/`en-GB`/`Europe/London`, `macos`/`Chrome`/`en-US`/`America/Los_Angeles`/1512×982, `linux`/`Chrome`/`de-DE`/`Europe/Berlin`, `windows`/`Chrome`/`ru-RU`/`Europe/Moscow`, …) weighted by `weight`; seed is `rng.randint(1, 2**31 - 1)`; when `country` is given, restrict to profiles whose `country` matches, falling back to the full list.
  - `class ProfileManager(settings: Settings, repo: ProfilesRepo)` with `async create_persistent(name: str, identity: Identity | None = None, proxy_id: int | None = None) -> Profile`, `async create_clean(run_id: int, country: str | None = None) -> Profile` (dir `profiles/tmp/<run_id>`), `async lease(profile_id: int, ttl_seconds: int) -> bool`, `async release(profile_id: int)`, `async cleanup_clean(now: datetime) -> int` (deletes folders and rows of `kind=clean` with `expires_at < now`), `async delete(profile_id: int) -> None` (refuses persistent profiles that are `locked`).
  - `browser_flags(identity: Identity, user_data_dir: Path, proxy_url: str | None, headless: bool, no_sandbox: bool = False) -> list[str]` — the exact flag list from Global Constraints.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_profiles.py
import random
from pathlib import Path
from uwp.config import load_settings
from uwp.db.database import Database
from uwp.db.repo.profiles import ProfilesRepo
from uwp.profiles import ProfileManager, random_identity, browser_flags


def test_identity_is_coherent():
    for i in range(50):
        ident = random_identity(random.Random(i))
        assert ident.platform in {"windows", "macos", "linux"}
        assert 1 <= ident.seed <= 2**31 - 1
        assert ident.locale.split("-")[0] in ident.accept_lang
        assert ident.timezone in {"America/New_York", "Europe/London", "America/Los_Angeles",
                                  "Europe/Berlin", "Europe/Moscow", "Asia/Tokyo"}


def test_identity_country_filter():
    ident = random_identity(random.Random(1), country="DE")
    assert ident.timezone == "Europe/Berlin"


def test_browser_flags_contain_fingerprint_and_proxy(tmp_path):
    ident = random_identity(random.Random(7))
    flags = browser_flags(ident, tmp_path, "http://127.0.0.1:5555", headless=True)
    assert f"--fingerprint={ident.seed}" in flags
    assert f"--fingerprint-platform={ident.platform}" in flags
    assert f"--user-data-dir={tmp_path}" in flags
    assert "--proxy-server=http://127.0.0.1:5555" in flags
    assert "--headless=new" in flags and "--disable-non-proxied-udp" in flags


async def test_clean_profile_folder_removed_after_cleanup(tmp_path):
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path)})
    db = await Database.in_memory()
    await db.migrate()
    mgr = ProfileManager(settings, ProfilesRepo(db))
    p = await mgr.create_clean(run_id=42)
    assert Path(p.user_data_dir).is_dir()
    await mgr.release(p.id)
    removed = await mgr.cleanup_clean(now=__import__("uwp.timeutil", fromlist=["utcnow"]).utcnow() + __import__("datetime").timedelta(days=2))
    assert removed == 1 and not Path(p.user_data_dir).exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_profiles.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'uwp.profiles'`

- [ ] **Step 3: Implement `profiles.py`**

`create_clean` creates the directory, inserts a `Profile(kind=clean, status=locked, expires_at=now+24h)`. `release` sets `status='free'` for persistent and `status='expired'` for clean (folder stays until cleanup). `browser_flags` returns flags in the Global Constraints order, adding `--no-sandbox` only when requested and `--proxy-server` only when `proxy_url` is given.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_profiles.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/uwp/profiles.py tests/unit/test_profiles.py
git commit -m "feat: add profile identities, leasing and browser flags"
```

---

### Task 7: Browser launcher

**Files:**
- Create: `src/uwp/browser.py`
- Test: `tests/unit/test_browser.py`, `tests/integration/test_browser_launch.py`

**Interfaces:**
- Consumes: `browser_flags` (Task 6), `Settings`.
- Produces:
  - `uwp.browser.BrowserUnavailable(RuntimeError)`.
  - `uwp.browser.resolve_browser(settings: Settings) -> Path` — returns `settings.browser_path` if set and executable, else `shutil.which("fingerprint-chromium")`, else raises `BrowserUnavailable("fingerprint-chromium not found; run `uwp browser install`")`.
  - `uwp.browser.BrowserSession` with `context: BrowserContext`, `page: Page`, `async close()`.
  - `async launch(settings: Settings, identity: Identity, user_data_dir: Path, proxy_url: str | None, headless: bool | None = None) -> BrowserSession` — `async_playwright().start()`, `chromium.launch_persistent_context(user_data_dir=str(dir), executable_path=str(resolve_browser(settings)), args=browser_flags(...), headless=headless if not None else settings.headless, viewport={"width": w, "height": h}, locale=identity.locale, timezone_id=identity.timezone, ignore_default_args=["--enable-automation"])`, opens the first page or a new one, sets default timeouts from settings.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_browser.py
import pytest
from uwp.browser import resolve_browser, BrowserUnavailable
from uwp.config import load_settings


def test_missing_browser_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(BrowserUnavailable):
        resolve_browser(load_settings({"UWP_DATA_DIR": str(tmp_path)}))


def test_explicit_browser_path_wins(tmp_path):
    exe = tmp_path / "chrome"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    assert resolve_browser(load_settings({"UWP_DATA_DIR": str(tmp_path), "UWP_BROWSER_PATH": str(exe)})) == exe
```

```python
# tests/integration/test_browser_launch.py
import os
import pytest
from uwp.browser import launch
from uwp.config import load_settings
from uwp.profiles import random_identity

pytestmark = pytest.mark.integration
BROWSER = os.environ.get("UWP_TEST_BROWSER")


@pytest.mark.skipif(not BROWSER, reason="UWP_TEST_BROWSER not set")
async def test_fingerprint_platform_applied(tmp_path):
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path), "UWP_BROWSER_PATH": BROWSER})
    ident = random_identity(__import__("random").Random(3))
    session = await launch(settings, ident, tmp_path / "udd", proxy_url=None, headless=True)
    try:
        await session.page.goto("data:text/html,<div id=o></div>")
        platform = await session.page.evaluate("navigator.platform")
        webdriver = await session.page.evaluate("navigator.webdriver")
        assert platform == {"windows": "Win32", "macos": "MacIntel", "linux": "Linux x86_64"}[ident.platform]
        assert webdriver is False
    finally:
        await session.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_browser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'uwp.browser'`

- [ ] **Step 3: Implement `browser.py`**

`launch` must stop the Playwright driver in `close()` (`await self._pw.stop()`), close the context, and be safe to call twice. `ignore_default_args=["--enable-automation"]` keeps `navigator.webdriver` false alongside the browser's own spoofing.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_browser.py -v` and `UWP_TEST_BROWSER=/tmp/opencode/fp/chrome .venv/bin/python -m pytest tests/integration/test_browser_launch.py -v`
Expected: PASS (2 passed; integration 1 passed or skipped)

- [ ] **Step 5: Commit**

```bash
git add src/uwp/browser.py tests/unit/test_browser.py tests/integration/test_browser_launch.py
git commit -m "feat: launch fingerprint-chromium through Playwright"
```

---

### Task 8: Authenticated proxy bridge

**Files:**
- Create: `src/uwp/proxybridge.py`
- Test: `tests/unit/test_proxybridge.py`

**Interfaces:**
- Consumes: `Proxy`, `ProxyScheme`.
- Produces:
  - `uwp.proxybridge.ProxyBridge(proxy: Proxy)` with `async start() -> str` (returns `http://127.0.0.1:<port>`), `async stop()`, property `port: int`.
  - `uwp.proxybridge.ProxyError(RuntimeError)` for upstream failures.
  - `parse_proxy_url(url: str) -> Proxy` helper (`http://user:pass@host:8080`, `socks5://host:1080`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_proxybridge.py
import asyncio
import pytest
from uwp.domain import Proxy, ProxyScheme
from uwp.proxybridge import ProxyBridge, parse_proxy_url


def test_parse_proxy_url_with_auth():
    p = parse_proxy_url("http://u:p%40ss@1.2.3.4:8080")
    assert p.scheme == ProxyScheme.http and p.host == "1.2.3.4" and p.port == 8080
    assert p.username == "u" and p.password == "p@ss"


def test_parse_socks5():
    p = parse_proxy_url("socks5://1.2.3.4:1080")
    assert p.scheme == ProxyScheme.socks5 and p.username is None


async def test_connect_forwards_proxy_authorization():
    seen = {}

    async def upstream(reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        seen["head"] = head.decode()
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    up_port = server.sockets[0].getsockname()[1]
    bridge = ProxyBridge(Proxy(scheme=ProxyScheme.http, host="127.0.0.1", port=up_port,
                               username="user", password="secret"))
    url = await bridge.start()
    try:
        port = int(url.rsplit(":", 1)[1])
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
        await w.drain()
        resp = await r.readuntil(b"\r\n\r\n")
        assert b"200" in resp
        assert "Proxy-Authorization: Basic dXNlcjpzZWNyZXQ=" in seen["head"]
        w.close()
    finally:
        await bridge.stop()
        server.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_proxybridge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'uwp.proxybridge'`

- [ ] **Step 3: Implement `proxybridge.py`**

`start()` binds `127.0.0.1:0` with `asyncio.start_server`. For `CONNECT`: open a TCP connection to the upstream (for `http`/`https` upstreams, send `CONNECT host:port` with `Proxy-Authorization` when credentials exist and require a `200` response before piping; for `socks5`, perform the SOCKS5 greeting/auth/connect handshake with the target hostname). Reply `HTTP/1.1 200 Connection established` to the client, then `asyncio.gather` two `_pipe` coroutines. Plain HTTP requests (no CONNECT) are forwarded to the upstream as absolute-URI requests with `Proxy-Authorization` added. Upstream failures close the client connection and log at WARNING without credentials.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_proxybridge.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/uwp/proxybridge.py tests/unit/test_proxybridge.py
git commit -m "feat: add local proxy bridge with upstream authentication"
```

---

### Task 9: Run orchestration

**Files:**
- Create: `src/uwp/runner.py`
- Test: `tests/unit/test_runner.py`

**Interfaces:**
- Consumes: `ProfileManager`, `launch`, `ProxyBridge`, `ParserRegistry`, `RunsRepo`, `ItemsRepo`, `Settings`, `timeutil`.
- Produces:
  - `uwp.runner.RunOutcome` dataclass: `status: RunStatus`, `rows: list[dict]`, `new_count: int`, `error_kind: ErrorKind | None`, `error: str | None`, `duration_ms: int`, `meta: dict`, `notes: str | None`.
  - `uwp.runner.Runner(settings, profiles: ProfileManager, registry: ParserRegistry, runs: RunsRepo, items: ItemsRepo, launcher=launch)` with `async run(site: Site, job: Job, proxy: Proxy | None, cancel: asyncio.Event | None = None) -> RunOutcome`.
  - Behavior: create `Run` row (`status` running), allocate profile (clean → `create_clean(run_id)`; persistent → `lease` or raise `config_error` "profile busy"), start `ProxyBridge` when proxy given, launch browser, build `ParseContext` (`known_keys=lambda: items.keys(site.id)`, `save_artifact` writes into `artifacts/<run_id>/`), `await asyncio.wait_for(parser.run(ctx), settings.run_timeout)`, upsert items (`new_count`), map exceptions: `ParseError` → its kind (rows from `partial_rows` saved, status `partial`), `asyncio.TimeoutError` → `transient` "run timed out", `BrowserUnavailable` → `config_error`, anything else → `parser_error` with traceback in `error`. Always close browser, stop bridge, release profile, update `Run` row.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_runner.py
import asyncio
import pytest
from uwp.config import load_settings
from uwp.db.database import Database
from uwp.db.repo.profiles import ProfilesRepo
from uwp.db.repo.runs import RunsRepo
from uwp.db.repo.items import ItemsRepo
from uwp.domain import Site, Job, JobKind, JobStatus, RunStatus, ErrorKind
from uwp.parsers.base import BaseParser, Column, ParseContext, ParseResult, ParseError
from uwp.parsers.registry import ParserRegistry
from uwp.profiles import ProfileManager
from uwp.runner import Runner


class FakeParser(BaseParser):
    key, title, description, url = "fake", "Fake", "", "https://example.com"
    settings_model = __import__("pydantic").BaseModel
    columns = [Column("title", "Title")]
    behavior = "ok"

    async def run(self, ctx: ParseContext) -> ParseResult:
        if self.behavior == "login":
            raise ParseError(kind=ErrorKind.login_required, message="login wall")
        if self.behavior == "boom":
            raise ValueError("kaboom")
        if self.behavior == "slow":
            await asyncio.sleep(10)
        return ParseResult(rows=[{"external_key": "1", "title": "A"},
                                 {"external_key": "2", "title": "B"}])


class FakeSession:
    def __init__(self):
        self.closed = False
    async def close(self):
        self.closed = True


@pytest.fixture
async def env(tmp_path):
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path), "UWP_RUN_TIMEOUT": "1"})
    db = await Database.in_memory()
    await db.migrate()
    sessions = []

    async def fake_launch(*a, **kw):
        s = FakeSession()
        sessions.append(s)
        return s

    reg = ParserRegistry()
    reg._parsers = {"fake": FakeParser()}
    runner = Runner(settings, ProfileManager(settings, ProfilesRepo(db)), reg,
                    RunsRepo(db), ItemsRepo(db), launcher=fake_launch)
    site = Site(id=1, slug="s", name="S", parser_key="fake", interval_min_min=1, interval_max_min=2)
    job = Job(id=1, site_id=1, kind=JobKind.scheduled, status=JobStatus.running)
    yield runner, site, job, sessions, db
    await db.close()


async def test_ok_run_saves_items_and_releases_profile(env):
    runner, site, job, sessions, db = env
    out = await runner.run(site, job, proxy=None)
    assert out.status == RunStatus.ok and out.new_count == 2
    assert sessions[0].closed
    assert (await ProfilesRepo(db).list())[0].status == "expired"


async def test_login_required_is_not_retried_and_keeps_partial(env):
    runner, site, job, sessions, db = env
    FakeParser.behavior = "login"
    out = await runner.run(site, job, proxy=None)
    assert out.status == RunStatus.failed and out.error_kind == ErrorKind.login_required


async def test_unexpected_exception_is_parser_error(env):
    runner, site, job, sessions, db = env
    FakeParser.behavior = "boom"
    out = await runner.run(site, job, proxy=None)
    assert out.error_kind == ErrorKind.parser_error and "kaboom" in out.error


async def test_timeout_is_transient(env):
    runner, site, job, sessions, db = env
    FakeParser.behavior = "slow"
    out = await runner.run(site, job, proxy=None)
    assert out.error_kind == ErrorKind.transient and "timed out" in out.error
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'uwp.runner'`

- [ ] **Step 3: Implement `runner.py`**

Use `try/finally` so browser/bridge/profile cleanup runs on every path. `cancel` is checked before launch and after the parser returns; when set, status is `canceled`. `items.keys(site_id)` is added to `ItemsRepo` in this task (returns `set[str]`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_runner.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/uwp/runner.py src/uwp/db/repo/items.py tests/unit/test_runner.py
git commit -m "feat: orchestrate parser runs with profiles, proxies and error kinds"
```

---

### Task 10: Excel export

**Files:**
- Create: `src/uwp/excel.py`
- Test: `tests/unit/test_excel.py`

**Interfaces:**
- Consumes: `Column`, `Settings`, `Run`, `Site`.
- Produces:
  - `uwp.excel.slugify(text: str) -> str` (Cyrillic transliteration table, lowercase, `[^a-z0-9]+` → `_`, trimmed).
  - `uwp.excel.excel_filename(template: str, parser_title: str, site_name: str, when: datetime) -> str` → e.g. `Steam_sdeli_2026-11-13_01-20.xlsx`.
  - `uwp.excel.write_workbook(path: Path, rows: list[dict], columns: list[Column], meta: dict[str, str], new_keys: set[str] | None = None) -> Path` — sheet «Данные» (bold header, autofilter, freeze `A2`, widths from `Column.width`, hyperlinks for keys ending in `_url`, column «Новое» with `да`/`нет` when `new_keys` is not None, changed rows highlighted light yellow when `meta["changed_keys"]` lists them) and sheet «Метаданные» (key/value rows).
  - `uwp.excel.prune_exports(directory: Path, keep: int) -> int`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_excel.py
from datetime import datetime, timezone
from pathlib import Path
from openpyxl import load_workbook
from uwp.excel import slugify, excel_filename, write_workbook, prune_exports
from uwp.parsers.base import Column


def test_slugify_transliterates():
    assert slugify("Steam — скидки") == "steam_skidki"
    assert slugify("  Ünïcode / Test!! ") == "unicode_test"


def test_filename():
    when = datetime(2026, 11, 13, 1, 20, tzinfo=timezone.utc)
    assert excel_filename("{parser_title}_{site_name}_{timestamp}", "Steam — скидки", "Steam US", when) \
        == "Steam_skidki_Steam_US_2026-11-13_01-20.xlsx"


def test_workbook_contents(tmp_path):
    cols = [Column("title", "Title", 30), Column("url", "URL", 40), Column("price", "Price", 10)]
    rows = [{"external_key": "1", "title": "A", "url": "https://x/1", "price": 1.5},
            {"external_key": "2", "title": "B", "url": "https://x/2", "price": 2.0}]
    path = write_workbook(tmp_path / "t.xlsx", rows, cols, {"site": "S"}, new_keys={"2"})
    wb = load_workbook(path)
    ws = wb["Данные"]
    assert [c.value for c in ws[1]] == ["Title", "URL", "Price", "Новое"]
    assert ws["A2"].value == "A" and ws["D2"].value == "нет" and ws["D3"].value == "да"
    assert ws["B2"].hyperlink.target == "https://x/1"
    assert ws.freeze_panes == "A2" and ws.auto_filter.ref == "A1:D3"
    assert wb["Метаданные"]["A1"].value == "site" and wb["Метаданные"]["B1"].value == "S"


def test_prune_keeps_newest(tmp_path):
    for i in range(5):
        (tmp_path / f"f{i}.xlsx").write_bytes(b"x")
    assert prune_exports(tmp_path, keep=2) == 3
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f3.xlsx", "f4.xlsx"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_excel.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'uwp.excel'`

- [ ] **Step 3: Implement `excel.py`**

Transliteration map: `а→a б→b в→v г→g д→d е→e ё→e ж→zh з→z и→i й→y к→k л→l м→m н→n о→o п→p р→r с→s т→t у→u ф→f х→kh ц→ts ч→ch ш→sh щ→shch ъ→ ы→y ь→ э→e ю→yu я→ya` (lowercase; uppercase handled by `.lower()` first). Timestamp format `%Y-%m-%d_%H-%M` in `UWP_TZ`. `prune_exports` sorts by `st_mtime` and deletes the oldest beyond `keep`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_excel.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/uwp/excel.py tests/unit/test_excel.py
git commit -m "feat: write Excel reports with metadata and retention"
```

---

### Task 11: Scheduler

**Files:**
- Create: `src/uwp/scheduler.py`
- Test: `tests/unit/test_scheduler.py`

**Interfaces:**
- Consumes: `SitesRepo`, `JobsRepo`, `timeutil`.
- Produces:
  - `uwp.scheduler.next_run_at(now: datetime, interval_min: int, interval_max: int, rng: random.Random | None = None) -> datetime` — `now + timedelta(minutes=rng.randint(interval_min, interval_max))`.
  - `uwp.scheduler.Scheduler(sites: SitesRepo, jobs: JobsRepo)` with `async tick(now: datetime) -> list[int]` — for each enabled site with `next_run_at <= now`, create a scheduled job (skip when one is active), set `next_run_at = next_run_at(now, …)` (one catch-up run only), return created job ids; `async on_enabled(site_id, now)` sets `next_run_at = now`; `async on_disabled(site_id)` cancels queued jobs.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_scheduler.py
import random
from datetime import datetime, timedelta, timezone
from uwp.db.database import Database
from uwp.db.repo.sites import SitesRepo
from uwp.db.repo.jobs import JobsRepo
from uwp.domain import Site
from uwp.scheduler import Scheduler, next_run_at

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


def test_next_run_within_range():
    values = {next_run_at(NOW, 45, 90, random.Random(i)) for i in range(100)}
    assert all(NOW + timedelta(minutes=45) <= v <= NOW + timedelta(minutes=90) for v in values)
    assert len(values) > 50


def test_fixed_interval():
    assert next_run_at(NOW, 60, 60) == NOW + timedelta(minutes=60)


async def _site(repo, **kw):
    return await repo.create(Site(slug="s", name="S", parser_key="steam_deals",
                                  interval_min_min=45, interval_max_min=90, **kw))


async def test_tick_creates_one_job_for_due_site():
    db = await Database.in_memory(); await db.migrate()
    sites, jobs = SitesRepo(db), JobsRepo(db)
    sid = await _site(sites, enabled=True, next_run_at=NOW - timedelta(hours=3))
    sched = Scheduler(sites, jobs)
    created = await sched.tick(NOW)
    assert len(created) == 1
    assert await sched.tick(NOW) == []          # no duplicate while queued
    site = await sites.get(sid)
    assert site.next_run_at > NOW               # single catch-up, not a backlog
    await db.close()


async def test_disabled_site_is_skipped_and_queue_canceled():
    db = await Database.in_memory(); await db.migrate()
    sites, jobs = SitesRepo(db), JobsRepo(db)
    sid = await _site(sites, enabled=True, next_run_at=NOW)
    sched = Scheduler(sites, jobs)
    await sched.tick(NOW)
    await sched.on_disabled(sid)
    assert (await jobs.list_for_site(sid))[0].status == "canceled"
    await db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_scheduler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'uwp.scheduler'`

- [ ] **Step 3: Implement `scheduler.py`**

`tick` iterates `sites.due(now)` (enabled and `next_run_at <= now`), calls `jobs.create(site.id, "scheduled")`, and always advances `next_run_at` from `now` so a long downtime yields exactly one catch-up. `JobsRepo.list_for_site(site_id)` is added here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_scheduler.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/uwp/scheduler.py src/uwp/db/repo/jobs.py tests/unit/test_scheduler.py
git commit -m "feat: schedule runs with random intervals and single catch-up"
```

---

### Task 12: Worker loop

**Files:**
- Create: `src/uwp/worker.py`
- Test: `tests/unit/test_worker.py`

**Interfaces:**
- Consumes: `JobsRepo`, `SitesRepo`, `ProfilesRepo`, `ProxiesRepo`, `Runner`, `Scheduler`, `Settings`, `timeutil`.
- Produces:
  - `uwp.worker.Worker(settings, jobs, sites, profiles, proxies, runner, scheduler, worker_id: str)` with `async recover(now)` (requeue `running` jobs whose `heartbeat_at` is older than `orphan_threshold`, release their profiles, increment attempt), `async process_one(now) -> bool` (claim → load site/proxy → run → finish job with `done`/`failed`/`canceled`; on `transient`/`blocked` with `attempt < max_attempts` → `requeue` with `not_before = now + 60s*attempt`), `async run_forever(stop: asyncio.Event)` (loop: `recover`, `scheduler.tick`, `process_one` up to `max_parallel_runs` concurrently, heartbeat every `heartbeat_interval`, sleep 1 s when idle), `async heartbeat(job_id, now)`.
  - `JobsRepo` gains `not_before` column usage: `claim` only takes jobs with `not_before IS NULL OR not_before <= now`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_worker.py
import asyncio
from datetime import datetime, timedelta, timezone
from uwp.db.database import Database
from uwp.db.repo.sites import SitesRepo
from uwp.db.repo.jobs import JobsRepo
from uwp.db.repo.profiles import ProfilesRepo
from uwp.db.repo.proxies import ProxiesRepo
from uwp.domain import Site
from uwp.worker import Worker
from uwp.timeutil import utcnow

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


class FakeRunner:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0
    async def run(self, site, job, proxy, cancel=None):
        self.calls += 1
        return self.outcome


class FakeScheduler:
    async def tick(self, now):
        return []


async def _setup(tmp_path):
    db = await Database.in_memory(); await db.migrate()
    sites, jobs = SitesRepo(db), JobsRepo(db)
    sid = await sites.create(Site(slug="s", name="S", parser_key="steam_deals",
                                  interval_min_min=1, interval_max_min=2, enabled=True))
    return db, sites, jobs, sid


async def test_orphaned_running_job_is_requeued(tmp_path):
    db, sites, jobs, sid = await _setup(tmp_path)
    jid = await jobs.create(sid, "scheduled")
    await jobs.claim("dead", now=NOW - timedelta(minutes=10))
    w = Worker(None, jobs, sites, ProfilesRepo(db), ProxiesRepo(db), FakeRunner(None), FakeScheduler(), "w1")
    await w.recover(NOW)
    job = await jobs.get(jid)
    assert job.status == "queued" and job.attempt == 1
    await db.close()


async def test_transient_failure_is_requeued_with_backoff(tmp_path):
    from uwp.runner import RunOutcome
    from uwp.domain import RunStatus, ErrorKind
    db, sites, jobs, sid = await _setup(tmp_path)
    await jobs.create(sid, "scheduled")
    outcome = RunOutcome(status=RunStatus.failed, rows=[], new_count=0,
                         error_kind=ErrorKind.transient, error="502", duration_ms=1, meta={}, notes=None)
    w = Worker(None, jobs, sites, ProfilesRepo(db), ProxiesRepo(db), FakeRunner(outcome), FakeScheduler(), "w1")
    assert await w.process_one(NOW) is True
    job = (await jobs.list_for_site(sid))[0]
    assert job.status == "queued" and job.not_before == NOW + timedelta(seconds=60)
    await db.close()


async def test_login_required_fails_without_retry(tmp_path):
    from uwp.runner import RunOutcome
    from uwp.domain import RunStatus, ErrorKind
    db, sites, jobs, sid = await _setup(tmp_path)
    await jobs.create(sid, "scheduled")
    outcome = RunOutcome(status=RunStatus.failed, rows=[], new_count=0,
                         error_kind=ErrorKind.login_required, error="login", duration_ms=1, meta={}, notes=None)
    w = Worker(None, jobs, sites, ProfilesRepo(db), ProxiesRepo(db), FakeRunner(outcome), FakeScheduler(), "w1")
    await w.process_one(NOW)
    job = (await jobs.list_for_site(sid))[0]
    assert job.status == "failed" and job.attempt == 0
    await db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_worker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'uwp.worker'`

- [ ] **Step 3: Implement `worker.py`**

`recover` also releases profiles whose `lease_until < now`. `process_one` returns `False` when nothing was claimed. `run_forever` uses `asyncio.create_task` per claimed job capped by a semaphore of `max_parallel_runs`, and a separate heartbeat task per running job. Cancellation: before launching, check `jobs.get(job.id).status == "canceled"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_worker.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/uwp/worker.py src/uwp/db/repo/jobs.py tests/unit/test_worker.py
git commit -m "feat: add worker loop with recovery, retries and cancellation"
```

---

### Task 13: Event queue for the bot

**Files:**
- Create: `src/uwp/events.py`
- Test: `tests/unit/test_events.py`

**Interfaces:**
- Consumes: `EventsRepo`, `AdminsRepo`, `Settings`.
- Produces:
  - `uwp.events.EventBus(events: EventsRepo, admins: AdminsRepo, settings: Settings)` with `async emit(kind: str, site_id: int | None = None, run_id: int | None = None, payload: dict | None = None) -> int`, `async pending(admin_id: int, now: datetime) -> list[Event]`, `async mark_delivered(event_id: int, admin_id: int)`, `async purge(now: datetime) -> int`.
  - Event kinds (strings): `run_finished`, `run_failed`, `site_problem`, `vnc_ready`, `vnc_stopped`, `vnc_failed`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_events.py
from datetime import timedelta
from uwp.config import load_settings
from uwp.db.database import Database
from uwp.db.repo.events import EventsRepo
from uwp.db.repo.admins import AdminsRepo
from uwp.events import EventBus
from uwp.timeutil import utcnow


async def _bus():
    db = await Database.in_memory(); await db.migrate()
    return db, EventBus(EventsRepo(db), AdminsRepo(db), load_settings({}))


async def test_event_delivered_once_per_admin():
    db, bus = await _bus()
    eid = await bus.emit("run_finished", site_id=1, run_id=2, payload={"rows": 10})
    assert [e.id for e in await bus.pending(111, utcnow())] == [eid]
    await bus.mark_delivered(eid, 111)
    assert await bus.pending(111, utcnow()) == []
    assert [e.id for e in await bus.pending(222, utcnow())] == [eid]
    await db.close()


async def test_old_events_are_purged():
    db, bus = await _bus()
    await bus.emit("run_finished", payload={})
    assert await bus.purge(utcnow() + timedelta(days=4)) == 1
    await db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'uwp.events'`

- [ ] **Step 3: Implement `events.py`**

`pending` joins `events` with `event_deliveries` on `(event_id, admin_id)` and returns events created within `settings.event_ttl_days` that have no delivery row. `purge` deletes events older than the TTL and their deliveries.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_events.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/uwp/events.py tests/unit/test_events.py
git commit -m "feat: add durable event queue for bot notifications"
```

---

### Task 14: VNC login sessions

**Files:**
- Create: `src/uwp/vnc.py`
- Test: `tests/unit/test_vnc.py`

**Interfaces:**
- Consumes: `Settings`, `Profile`, `VncSession`, `VncStatus`, `ProfilesRepo`, `browser_flags`, `ProxyBridge`.
- Produces:
  - `uwp.vnc.VncInfo` dataclass: `session_id: int`, `port: int`, `password: str`, `url: str`, `expires_at: datetime`.
  - `uwp.vnc.free_port(min_port: int, max_port: int, taken: set[int]) -> int` (raises `RuntimeError("no free VNC port")`).
  - `uwp.vnc.vnc_url(public_host: str, port: int, password: str) -> str` → `http://{host}:{port}/vnc.html?autoconnect=1&resize=scale&password={password}`.
  - `uwp.vnc.VncManager(settings, profiles: ProfilesRepo, process_factory=None)` with `async start(profile: Profile, url: str, proxy_url: str | None = None, started_by: int | None = None) -> VncInfo`, `async stop(session_id: int) -> None`, `async tick(now: datetime) -> None` (hard TTL and idle stop), `async list() -> list[VncSession]`. `process_factory(display: int, port: int, password: str, profile: Profile, url: str, proxy_url: str | None) -> ProcessHandle` is injectable for tests; the default spawns `Xvfb :{display} -screen 0 1920x1080x24`, `fluxbox`, the browser (headful, same flags), `x11vnc -display :{display} -rfbauth <file> -localhost -forever -shared`, and `websockify --web /usr/share/novnc {port} localhost:{vnc_port}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_vnc.py
import pytest
from datetime import timedelta
from uwp.config import load_settings
from uwp.db.database import Database
from uwp.db.repo.profiles import ProfilesRepo
from uwp.domain import Profile, ProfileKind, ProfileStatus
from uwp.vnc import VncManager, free_port, vnc_url
from uwp.timeutil import utcnow


def test_free_port_skips_taken():
    assert free_port(6000, 6002, taken={6000, 6001}) == 6002
    with pytest.raises(RuntimeError):
        free_port(6000, 6001, taken={6000, 6001})


def test_vnc_url():
    assert vnc_url("example.org", 6042, "abc") == \
        "http://example.org:6042/vnc.html?autoconnect=1&resize=scale&password=abc"


class FakeProc:
    def __init__(self):
        self.stopped = False
    async def stop(self):
        self.stopped = True


async def test_start_and_stop_session(tmp_path):
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path), "UWP_VNC_PORT_MIN": "6100",
                              "UWP_VNC_PORT_MAX": "6101"})
    db = await Database.in_memory(); await db.migrate()
    repo = ProfilesRepo(db)
    pid = await repo.create(Profile(name="p", kind=ProfileKind.persistent, seed=1,
                                    platform="windows", brand="Chrome", brand_version="150",
                                    timezone="Europe/Moscow", locale="ru-RU",
                                    user_data_dir=str(tmp_path / "p"), status=ProfileStatus.free))
    procs = []
    mgr = VncManager(settings, repo, process_factory=lambda **kw: procs.append(FakeProc()) or procs[-1])
    info = await mgr.start(await repo.get(pid), "https://store.steampowered.com/login/")
    assert info.port in (6100, 6101) and len(info.password) == 12
    assert (await repo.get(pid)).status == "login"
    await mgr.stop(info.session_id)
    assert procs[0].stopped and (await repo.get(pid)).status == "free"
    await db.close()


async def test_hard_ttl_stops_session(tmp_path):
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path), "UWP_VNC_TTL_MINUTES": "1"})
    db = await Database.in_memory(); await db.migrate()
    repo = ProfilesRepo(db)
    pid = await repo.create(Profile(name="p", kind=ProfileKind.persistent, seed=1,
                                    platform="windows", brand="Chrome", brand_version="150",
                                    timezone="Europe/Moscow", locale="ru-RU",
                                    user_data_dir=str(tmp_path / "p"), status=ProfileStatus.free))
    procs = []
    mgr = VncManager(settings, repo, process_factory=lambda **kw: procs.append(FakeProc()) or procs[-1])
    await mgr.start(await repo.get(pid), "https://example.com")
    await mgr.tick(utcnow() + timedelta(minutes=2))
    assert procs[0].stopped and (await repo.get(pid)).status == "free"
    await db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_vnc.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'uwp.vnc'`

- [ ] **Step 3: Implement `vnc.py`**

`start` refuses non-persistent profiles (`ValueError("VNC login requires a persistent profile")`) and profiles not `free`. It picks a display `:100 + (port - vnc_port_min)`, writes the x11vnc password file (`x11vnc -storepasswd` equivalent: write the 8-char truncated password via `subprocess` `x11vnc -storepasswd <pw> <file>`), inserts a `VncSession(status=running, expires_at=now+ttl)`, sets the profile to `login`. `tick` stops sessions past `expires_at` or with `last_client_at` older than `vnc_idle_minutes` (the default process handle updates `last_client_at` from `websockify` connection count; the fake handle leaves it `None`, which means "no idle stop"). `stop` kills processes, removes the password file, sets session `stopped` and profile `free`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_vnc.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/uwp/vnc.py tests/unit/test_vnc.py
git commit -m "feat: manage VNC login sessions for persistent profiles"
```

---

### Task 15: Bot foundation, admin gate and site list

**Files:**
- Create: `src/uwp/services/__init__.py`, `src/uwp/services/sites.py`, `src/uwp/bot/__init__.py`, `src/uwp/bot/app.py`, `src/uwp/bot/middleware.py`, `src/uwp/bot/keyboards.py`, `src/uwp/bot/texts.py`, `src/uwp/bot/handlers/__init__.py`, `src/uwp/bot/handlers/common.py`, `src/uwp/bot/handlers/sites.py`
- Test: `tests/unit/test_bot_access.py`, `tests/unit/test_site_service.py`

**Interfaces:**
- Consumes: `Settings`, repos, `Scheduler`, `ParserRegistry`.
- Produces:
  - `uwp.services.sites.SiteService(sites, jobs, scheduler, registry)` with `async list() -> list[Site]`, `async create(parser_key: str, name: str | None = None) -> Site` (slug from parser key + counter, disabled, `next_run_at=None`), `async set_enabled(site_id, enabled: bool, now) -> Site` (calls `scheduler.on_enabled/on_disabled`), `async run_now(site_id) -> int | None` (manual job, returns job id or `None` if already active), `async update(site_id, **fields) -> Site`, `async delete(site_id)`.
  - `uwp.bot.middleware.AdminOnlyMiddleware(admin_ids: set[int])` — for `Message` and `CallbackQuery` events, if `event.from_user.id not in admin_ids` reply `texts.ACCESS_DENIED` once and raise `aiogram.exceptions.SkipHandler`; also injects `data["is_admin"] = True`.
  - `uwp.bot.keyboards`: `sites_list_kb(sites: list[Site]) -> InlineKeyboardMarkup` (one row per site: `🟢/🔴 {name}` → `site:{id}`, plus `➕ Добавить сайт` → `site:add`), `site_card_kb(site: Site) -> InlineKeyboardMarkup` (toggle `site:toggle:{id}`, run `site:run:{id}`, settings `site:settings:{id}`, back `sites:list`).
  - `uwp.bot.texts`: Russian strings `ACCESS_DENIED`, `SITES_HEADER`, `site_card(site, next_run_local) -> str`, `SITE_ALREADY_RUNNING`, `SITE_STARTED`.
  - `uwp.bot.app.build_dispatcher(settings, deps) -> Dispatcher` registering routers in order: `common`, `sites`, `settings`, `profiles`, `proxies`, `vnc`, `runs` (later tasks add their routers to this list).
  - `uwp.bot.app.run_bot(settings, deps) -> None` — `Dispatcher` + `Bot(token)`, `start_polling`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_bot_access.py
import pytest
from aiogram.types import Message, User, Chat
from uwp.bot.middleware import AdminOnlyMiddleware
from uwp.bot import texts


class FakeEvent:
    def __init__(self, uid):
        self.from_user = User(id=uid, is_bot=False, first_name="x")
        self.answers = []
    async def answer(self, text, **kw):
        self.answers.append(text)


async def test_non_admin_is_refused():
    mw = AdminOnlyMiddleware({111})
    ev = FakeEvent(999)
    with pytest.raises(Exception):
        await mw(lambda e, d: None, ev, {})
    assert ev.answers == [texts.ACCESS_DENIED]


async def test_admin_passes():
    mw = AdminOnlyMiddleware({111})
    ev = FakeEvent(111)
    data = {}
    await mw(lambda e, d: None, ev, data)
    assert data["is_admin"] is True and ev.answers == []
```

```python
# tests/unit/test_site_service.py
from datetime import datetime, timezone
from uwp.db.database import Database
from uwp.db.repo.sites import SitesRepo
from uwp.db.repo.jobs import JobsRepo
from uwp.scheduler import Scheduler
from uwp.services.sites import SiteService

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


async def test_create_is_disabled_and_enable_schedules_now():
    db = await Database.in_memory(); await db.migrate()
    sites, jobs = SitesRepo(db), JobsRepo(db)
    svc = SiteService(sites, jobs, Scheduler(sites, jobs), registry=None)
    site = await svc.create("steam_deals", name="Steam US")
    assert site.enabled is False and site.slug == "steam_us" and site.next_run_at is None
    site = await svc.set_enabled(site.id, True, NOW)
    assert site.enabled is True and site.next_run_at == NOW
    await db.close()


async def test_run_now_returns_none_when_active():
    db = await Database.in_memory(); await db.migrate()
    sites, jobs = SitesRepo(db), JobsRepo(db)
    svc = SiteService(sites, jobs, Scheduler(sites, jobs), registry=None)
    site = await svc.create("steam_deals")
    assert await svc.run_now(site.id) is not None
    assert await svc.run_now(site.id) is None
    await db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_bot_access.py tests/unit/test_site_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'uwp.bot'`

- [ ] **Step 3: Implement services and bot foundation**

`AdminOnlyMiddleware` implements `BaseMiddleware.__call__(handler, event, data)`; it detects `Message`/`CallbackQuery` by attribute presence (`hasattr(event, "from_user")`), answers `ACCESS_DENIED` (for callbacks use `event.answer(texts.ACCESS_DENIED, show_alert=True)`), then raises `SkipHandler`. Handlers in `common.py`: `/start`, `/help`, `/id`, `/sites` → `sites_list_kb`. `sites.py`: callback `sites:list`, `site:{id}` (card), `site:toggle:{id}`, `site:run:{id}`, `site:add` (lists parsers from the registry as `site:add:{key}`). All texts in `texts.py`; no user-visible English.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_bot_access.py tests/unit/test_site_service.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/uwp/services src/uwp/bot tests/unit/test_bot_access.py tests/unit/test_site_service.py
git commit -m "feat: add bot foundation with admin gate and site list"
```

---

### Task 16: Bot site settings

**Files:**
- Create: `src/uwp/bot/handlers/settings.py`
- Modify: `src/uwp/bot/keyboards.py`, `src/uwp/bot/texts.py`, `src/uwp/bot/app.py`
- Test: `tests/unit/test_settings_parse.py`

**Interfaces:**
- Consumes: `SiteService`, `ParserRegistry`, `ProfilesRepo`, `ProxiesRepo`.
- Produces:
  - `uwp.bot.handlers.settings.parse_interval(text: str) -> tuple[int, int]` — accepts `"45-90"`, `"45 90"`, `"60"`; raises `ValueError` for non-positive, `min > max`, or `max > 10080`.
  - `uwp.bot.handlers.settings.settings_kb(site: Site) -> InlineKeyboardMarkup` with buttons `set:interval:{id}`, `set:profile:{id}`, `set:proxy:{id}`, `set:export:{id}`, `set:params:{id}`, `set:back:{id}`.
  - `uwp.bot.handlers.settings.params_kb(parser: BaseParser, site: Site) -> InlineKeyboardMarkup` — one button per pydantic field of `settings_model` (`param:{site_id}:{field}`), `Literal` fields cycle values on tap, numeric fields open an FSM prompt.
  - FSM states `IntervalInput.waiting`, `ParamInput.waiting` (with `field` in state data).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_settings_parse.py
import pytest
from uwp.bot.handlers.settings import parse_interval


def test_parse_range():
    assert parse_interval("45-90") == (45, 90)
    assert parse_interval("45 90") == (45, 90)
    assert parse_interval("60") == (60, 60)


@pytest.mark.parametrize("bad", ["90-60", "0-10", "-5", "abc", "1-20000"])
def test_reject_bad(bad):
    with pytest.raises(ValueError):
        parse_interval(bad)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_settings_parse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'uwp.bot.handlers.settings'`

- [ ] **Step 3: Implement settings handlers**

Interval flow: button → FSM prompt «Введите интервал в минутах, например 45-90» → `parse_interval` → `SiteService.update(interval_min_min=…, interval_max_min=…)` → re-render card. Profile flow: list persistent profiles + «🧼 чистый» (`set:profile:{id}:clean` / `set:profile:{id}:{profile_id}`); choosing a persistent profile sets `profile_mode=persistent`; if the parser has `requires_login=True`, choosing clean is refused with a message. Proxy flow: list proxies + «без прокси». Export flow: cycles `always → changes → never`. Params flow: renders current values from `site.parser_settings`, validates via `settings_model(**merged)` and shows the pydantic error text on failure.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_settings_parse.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/uwp/bot tests/unit/test_settings_parse.py
git commit -m "feat: edit site interval, profile, proxy, export mode and parser params in bot"
```

---

### Task 17: Bot profiles, proxies, VNC and run history

**Files:**
- Create: `src/uwp/services/profiles.py`, `src/uwp/services/proxies.py`, `src/uwp/services/runs.py`, `src/uwp/services/vnc.py`, `src/uwp/bot/handlers/profiles.py`, `src/uwp/bot/handlers/proxies.py`, `src/uwp/bot/handlers/vnc.py`, `src/uwp/bot/handlers/runs.py`, `src/uwp/bot/delivery.py`
- Modify: `src/uwp/bot/app.py`, `src/uwp/bot/keyboards.py`, `src/uwp/bot/texts.py`
- Test: `tests/unit/test_proxy_service.py`, `tests/unit/test_vnc_service.py`

**Interfaces:**
- Consumes: `ProfileManager`, `ProxiesRepo`, `RunsRepo`, `VncManager`, `EventBus`, `Settings`.
- Produces:
  - `uwp.services.proxies.ProxyService(repo, settings)` with `async add(name, url) -> Proxy` (uses `parse_proxy_url`, encrypts password with Fernet when `settings.secret_key`), `async list()`, `async delete(id)`, `async test(id) -> tuple[bool, str]` (opens a TCP connection to the proxy host:port with 10 s timeout and reports latency or the error).
  - `uwp.services.vnc.VncService(manager, profiles, sites)` with `async request(profile_id, site_id=None, started_by=None) -> VncInfo` (picks the URL from the site's parser `url` or `https://example.com`), `async stop(session_id)`, `async list()`.
  - `uwp.bot.delivery.EventDelivery(bot, bus, settings)` with `async run(stop: asyncio.Event)` — every 5 s, for each admin in `settings.admin_ids`, fetch pending events and send: `run_finished` → `send_document` with the Excel path from payload + caption; `run_failed` → text + screenshot if present + «Повторить» button; `site_problem` → text; `vnc_ready` → text with URL/password; `vnc_stopped`/`vnc_failed` → text; then `mark_delivered`.
  - Handlers: `profiles:list`, `profile:new` (FSM name → `ProfileManager.create_persistent`), `profile:{id}` (card with «🖥 Войти», «🗑 Удалить»), `profile:login:{id}` → `VncService.request`; `proxies:list`, `proxy:new` (FSM `name url`), `proxy:test:{id}`, `proxy:del:{id}`; `vnc:done:{session_id}`, `vnc:extend:{session_id}`, `runs:{site_id}` (last 10 runs with status/time/rows), `run:retry:{site_id}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_proxy_service.py
from uwp.config import load_settings
from uwp.db.database import Database
from uwp.db.repo.proxies import ProxiesRepo
from uwp.services.proxies import ProxyService


async def test_add_encrypts_password_when_secret_set(tmp_path):
    db = await Database.in_memory(); await db.migrate()
    svc = ProxyService(ProxiesRepo(db), load_settings({"UWP_SECRET_KEY": "k" * 32}))
    p = await svc.add("DE-1", "http://user:pass@1.2.3.4:8080")
    assert p.password != "pass"
    assert await svc.reveal_password(p.id) == "pass"
    await db.close()


async def test_add_plaintext_without_secret(tmp_path):
    db = await Database.in_memory(); await db.migrate()
    svc = ProxyService(ProxiesRepo(db), load_settings({}))
    p = await svc.add("DE-1", "socks5://1.2.3.4:1080")
    assert p.password is None and p.scheme == "socks5"
    await db.close()
```

```python
# tests/unit/test_vnc_service.py
from uwp.config import load_settings
from uwp.db.database import Database
from uwp.db.repo.profiles import ProfilesRepo
from uwp.db.repo.sites import SitesRepo
from uwp.domain import Profile, ProfileKind, ProfileStatus, Site
from uwp.services.vnc import VncService


class FakeManager:
    def __init__(self):
        self.calls = []
    async def start(self, profile, url, proxy_url=None, started_by=None):
        self.calls.append((profile.id, url))
        return "info"


async def test_request_uses_site_parser_url(tmp_path):
    db = await Database.in_memory(); await db.migrate()
    profiles, sites = ProfilesRepo(db), SitesRepo(db)
    pid = await profiles.create(Profile(name="p", kind=ProfileKind.persistent, seed=1,
                                        platform="windows", brand="Chrome", brand_version="150",
                                        timezone="Europe/Moscow", locale="ru-RU",
                                        user_data_dir=str(tmp_path), status=ProfileStatus.free))
    sid = await sites.create(Site(slug="s", name="S", parser_key="steam_deals",
                                  interval_min_min=1, interval_max_min=2))
    mgr = FakeManager()
    svc = VncService(mgr, profiles, sites, parser_urls={"steam_deals": "https://store.steampowered.com/"})
    assert await svc.request(pid, site_id=sid) == "info"
    assert mgr.calls == [(pid, "https://store.steampowered.com/")]
    await db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_proxy_service.py tests/unit/test_vnc_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'uwp.services.proxies'`

- [ ] **Step 3: Implement services, handlers and delivery**

Fernet key derivation: `base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())`. `ProxyService.reveal_password` decrypts when the value starts with `gAAAAA` (Fernet token prefix), else returns as-is. Delivery uses `aiogram.Bot.send_document(chat_id, FSInputFile(path), caption=...)`; failures are logged and retried on the next tick (no `mark_delivered`). VNC card text includes the URL, password and remaining minutes; `vnc:extend` adds 30 minutes to `expires_at`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_proxy_service.py tests/unit/test_vnc_service.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/uwp/services src/uwp/bot tests/unit/test_proxy_service.py tests/unit/test_vnc_service.py
git commit -m "feat: manage profiles, proxies, VNC login and run history from bot"
```

---

### Task 18: CLI

**Files:**
- Create: `src/uwp/cli.py`
- Test: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `uwp.cli.main(argv: list[str] | None = None) -> int` using `argparse` subcommands: `init`, `doctor [--quiet]`, `browser install [--version] [--url] [--dest]`, `parsers list`, `site list|add|enable|disable|run|set`, `profile list|create|login|delete`, `proxy list|add|test`, `vnc list|stop`, `export <run_id|site_slug> [-o PATH]`, `run [--once]`, `bot`, `worker`, `db upgrade`. `uwp doctor` checks: data dir writable, browser executable present and `--version` runs, `Xvfb`/`x11vnc`/`websockify` present (warn only), `UWP_BOT_TOKEN` set, `UWP_ADMIN_IDS` non-empty, `UWP_SECRET_KEY` set (warn), DB migratable. Exit code 1 when any check fails, 0 otherwise; `--quiet` prints nothing on success.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_cli.py
from uwp.cli import main


def test_parsers_list(capsys, monkeypatch):
    monkeypatch.setenv("UWP_DATA_DIR", "/tmp/uwp-cli-test")
    assert main(["parsers", "list"]) == 0
    out = capsys.readouterr().out
    assert "steam_deals" in out


def test_doctor_fails_without_bot_token(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("UWP_BOT_TOKEN", raising=False)
    assert main(["doctor"]) == 1
    assert "UWP_BOT_TOKEN" in capsys.readouterr().out


def test_site_add_and_list(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("UWP_DATA_DIR", str(tmp_path))
    assert main(["init"]) == 0
    assert main(["site", "add", "steam_deals", "--name", "Steam US"]) == 0
    assert main(["site", "list"]) == 0
    assert "Steam US" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'uwp.cli'`

- [ ] **Step 3: Implement `cli.py`**

Each subcommand is a thin async function run via `asyncio.run`; output is plain text tables. `browser install` downloads to `settings.data_dir / "browser"` with `urllib.request`, verifies sha256, extracts with `tarfile` (safe extraction: reject absolute paths and `..`), and prints the resulting executable path. `init` migrates the DB, adds `UWP_ADMIN_IDS` to `admins`, and creates a disabled `steam_deals` site named «Steam — скидки». `run --once` starts the worker for one `process_one` cycle. `profile login` starts a VNC session and prints the URL/password, waiting for Ctrl+C to stop it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_cli.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/uwp/cli.py tests/unit/test_cli.py
git commit -m "feat: add uwp CLI with init, doctor, browser install and management commands"
```

---

### Task 19: Docker image and compose

**Files:**
- Create: `Dockerfile`, `compose.yaml`, `compose.split.yaml`, `docker/entrypoint.sh`, `.dockerignore`, `.env.example`
- Test: `tests/unit/test_compose_config.py`

**Interfaces:**
- Consumes: `uwp` CLI.
- Produces: image `ultimate-web-parser:latest` running as user `uwp` (uid 1000), `ENTRYPOINT ["/usr/bin/tini","--","/entrypoint.sh"]`, `CMD ["run"]`; `/data` volume; browser at `/opt/uwp/browser/chrome`; `UWP_BROWSER_PATH=/opt/uwp/browser/chrome`, `UWP_NO_SANDBOX=true`, `UWP_DATA_DIR=/data`. `compose.yaml` service `uwp` (ports `6000-6100:6000-6100`, `shm_size: 1gb`, `init: true`, `restart: unless-stopped`, healthcheck `uwp doctor --quiet` every 60 s). `compose.split.yaml` services `bot` (no ports) and `worker` (ports `6000-6100`), both mounting `./data:/data`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_compose_config.py
import yaml
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_combined_compose_has_ports_and_volume():
    cfg = yaml.safe_load((ROOT / "compose.yaml").read_text())
    svc = cfg["services"]["uwp"]
    assert "6000-6100:6000-6100" in svc["ports"]
    assert "./data:/data" in svc["volumes"]
    assert svc["shm_size"] == "1gb"


def test_split_compose_worker_owns_vnc_ports():
    cfg = yaml.safe_load((ROOT / "compose.split.yaml").read_text())
    assert "6000-6100:6000-6100" in cfg["services"]["worker"]["ports"]
    assert "ports" not in cfg["services"]["bot"]


def test_dockerfile_pins_browser_sha():
    text = (ROOT / "Dockerfile").read_text()
    assert "48eff860053efd9ad498d51370f0a1f59f983df39c02a72ad2a8682cdead4055" in text
    assert "150.0.7871.186" in text
```

(`pyyaml` is added to the `dev` extra in this task.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_compose_config.py -v`
Expected: FAIL with `FileNotFoundError: compose.yaml`

- [ ] **Step 3: Write the Docker files**

Dockerfile stages: `base` installs apt packages (`xvfb x11vnc novnc websockify fluxbox tini ca-certificates curl fonts-liberation fonts-noto-color-emoji libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libgbm1 libasound2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libpango-1.0-0 libcairo2 libatspi2.0-0`), creates user `uwp`; `browser` stage downloads the tar.xz with `ARG UWP_BROWSER_VERSION=150.0.7871.186`, verifies sha256, extracts to `/opt/uwp/browser`; final stage copies the venv (`pip install .`), the browser, `docker/entrypoint.sh`, sets `ENV UWP_BROWSER_PATH=/opt/uwp/browser/chrome UWP_NO_SANDBOX=true UWP_DATA_DIR=/data`, `USER uwp`, `VOLUME /data`. `entrypoint.sh`: `exec uwp "$@"` with `UWP_DATA_DIR` mkdir. `.env.example` lists every `UWP_*` variable with comments and safe defaults (empty token, empty admin ids).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_compose_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Build and smoke-test the image**

Run: `docker compose build && docker compose run --rm uwp doctor`
Expected: build succeeds; `doctor` reports the browser present and only warns about the missing bot token.

- [ ] **Step 6: Commit**

```bash
git add Dockerfile compose.yaml compose.split.yaml docker .dockerignore .env.example pyproject.toml tests/unit/test_compose_config.py
git commit -m "feat: add docker image and compose files for combined and split modes"
```

---

### Task 20: README, live Steam test and end-to-end smoke

**Files:**
- Modify: `README.md`, `tests/conftest.py`
- Create: `tests/live/test_steam_live.py`, `tests/integration/test_runner_steam.py`

**Interfaces:**
- Consumes: everything.
- Produces: `README.md` with quick start (`cp .env.example .env`, fill `UWP_BOT_TOKEN`/`UWP_ADMIN_IDS`, `docker compose up -d --build`, `/start` in the bot), how to get a Telegram ID, how to add a parser (folder + `BaseParser` + restart), how to log in via VNC (bot button and `docker compose exec uwp uwp profile login …`), TLS/reverse-proxy note for public hosts, `--no-sandbox` note, troubleshooting (`uwp doctor`, `uwp proxy test`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/live/test_steam_live.py
import os
import pytest
from uwp.parsers.steam_deals.parser import SteamSettings, build_search_url, parse_search_page

pytestmark = pytest.mark.live


@pytest.mark.skipif(not os.environ.get("UWP_LIVE"), reason="set UWP_LIVE=1 to hit Steam")
def test_real_steam_page_has_deals():
    import urllib.request
    req = urllib.request.Request(build_search_url(SteamSettings(max_pages=1), 0),
                                 headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
    rows = parse_search_page(html, SteamSettings(max_pages=1))
    assert len(rows) >= 10
```

```python
# tests/integration/test_runner_steam.py
import os
import pytest
from uwp.config import load_settings
from uwp.db.database import Database
from uwp.db.repo.profiles import ProfilesRepo
from uwp.db.repo.runs import RunsRepo
from uwp.db.repo.items import ItemsRepo
from uwp.domain import Site, Job, JobKind, JobStatus, RunStatus
from uwp.parsers.registry import ParserRegistry
from uwp.profiles import ProfileManager
from uwp.runner import Runner

pytestmark = pytest.mark.integration
BROWSER = os.environ.get("UWP_TEST_BROWSER")


@pytest.mark.skipif(not BROWSER, reason="UWP_TEST_BROWSER not set")
async def test_steam_run_produces_rows(tmp_path):
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path), "UWP_BROWSER_PATH": BROWSER})
    db = await Database.in_memory(); await db.migrate()
    runner = Runner(settings, ProfileManager(settings, ProfilesRepo(db)),
                    ParserRegistry.discover(), RunsRepo(db), ItemsRepo(db))
    site = Site(id=1, slug="steam", name="Steam", parser_key="steam_deals",
                interval_min_min=1, interval_max_min=2,
                parser_settings={"max_pages": 1})
    job = Job(id=1, site_id=1, kind=JobKind.scheduled, status=JobStatus.running)
    out = await runner.run(site, job, proxy=None)
    assert out.status == RunStatus.ok and len(out.rows) >= 10
    await db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/live tests/integration/test_runner_steam.py -v`
Expected: FAIL (README missing sections is not a test; the live test fails on import until `UWP_LIVE` handling is right — run with `UWP_LIVE=1` to see the real failure)

- [ ] **Step 3: Write the README and fix what the tests expose**

Fill every README section listed in Interfaces. If the integration run exposes a parser or runner bug, fix it in the owning module and note it in the report.

- [ ] **Step 4: Run the full verification**

Run: `.venv/bin/python -m pytest -v` then `UWP_LIVE=1 .venv/bin/python -m pytest -m live -v` then `UWP_TEST_BROWSER=/tmp/opencode/fp/chrome .venv/bin/python -m pytest -m integration -v`
Expected: all unit tests pass; live test ≥10 rows; integration test ≥10 rows.

- [ ] **Step 5: Commit**

```bash
git add README.md tests
git commit -m "docs: add README and live/integration Steam verification"
```

---

### Task 21: Playwright MCP for OpenCode

**Files:**
- Modify: `~/.config/opencode/opencode.json` (outside the repo; back up first to `opencode.json.bak-<timestamp>`)
- Test: manual verification (no repo test)

**Interfaces:**
- Consumes: nothing from the repo.
- Produces: an `mcp` entry `playwright` running `npx -y @playwright/mcp@0.0.85 --headless --isolated --executable-path /tmp/opencode/fp/chrome` (fallback to the bundled Playwright Chromium when the fingerprint build is absent), enabled for this project.

- [ ] **Step 1: Back up the config**

Run: `cp ~/.config/opencode/opencode.json ~/.config/opencode/opencode.json.bak-$(date +%Y%m%d-%H%M%S)`

- [ ] **Step 2: Add the MCP server**

Add under the top-level `"mcp"` key (create it if absent):

```json
"mcp": {
  "playwright": {
    "type": "local",
    "command": ["npx", "-y", "@playwright/mcp@0.0.85", "--headless", "--isolated"],
    "enabled": true
  }
}
```

- [ ] **Step 3: Verify**

Run: `opencode mcp list` (or restart OpenCode and check the MCP tools appear)
Expected: `playwright` listed as connected; a `browser_navigate` call to `https://example.com` returns the page title.

- [ ] **Step 4: Report**

No commit (config lives outside the repo). Note the backup path in the report.

---

## Self-Review

**Spec coverage:** §2 → Tasks 1–21; §6 data model → Task 3; §7 scheduler/queue/timeouts/error kinds → Tasks 9, 11, 12; §8 profiles → Task 6; §9 proxy bridge → Task 8; §10 VNC → Tasks 14, 17; §11 parser contract + Steam → Tasks 4, 5; §12 Excel → Task 10; §13 bot → Tasks 15–17; §14 CLI → Task 18; §15 Docker → Task 19; §16 tests → every task + Task 20; §17 decisions → Global Constraints; §18 risks → Task 18 (`doctor`, `proxy test`) and Task 20 (README troubleshooting).

**Review Focus coverage:** hung browser → Task 12 (`test_orphaned_running_job_is_requeued`) and Task 9 (`test_timeout_is_transient`); unexpected parser exception → Task 9 (`test_unexpected_exception_is_parser_error`); disable while queued → Task 11 (`test_disabled_site_is_skipped_and_queue_canceled`); clean profile after failure → Task 6 (`test_clean_profile_folder_removed_after_cleanup`) and Task 9 (`test_ok_run_saves_items_and_releases_profile`); non-admin → Task 15 (`test_non_admin_is_refused`).

**Type consistency:** `RunOutcome` fields used identically in Tasks 9 and 12; `VncInfo` used in Tasks 14 and 17; `ProfileManager.create_clean(run_id)` in Tasks 6 and 9; `ParserRegistry.get/all/broken` in Tasks 4, 15, 18.
