"""Tests for run orchestration: profiles, browser lifecycle, error kinds, items."""
import asyncio
import logging
from pathlib import Path
from typing import ClassVar

import pytest

from uwp.browser import BrowserUnavailable
from uwp.config import load_settings
from uwp.db.database import Database
from uwp.db.repo.items import ItemsRepo
from uwp.db.repo.profiles import ProfilesRepo
from uwp.db.repo.runs import RunsRepo
from uwp.domain import (
    ErrorKind,
    Job,
    JobKind,
    JobStatus,
    ProfileKind,
    ProfileStatus,
    Proxy,
    ProxyScheme,
    RunStatus,
    Site,
)
from uwp.parsers.base import (
    BaseParser,
    Column,
    ParseContext,
    ParseError,
    ParseResult,
    _EmptySettings,
)
from uwp.parsers.registry import ParserRegistry
from uwp.profiles import ProfileManager
from uwp.runner import Runner


class FakeParser(BaseParser):
    key, title, description, url = "fake", "Fake", "", "https://example.com"
    settings_model = _EmptySettings
    columns: ClassVar[list[Column]] = [Column("title", "Title")]
    behavior = "ok"
    seen_ctx: ClassVar[ParseContext | None] = None
    rows: ClassVar[list[dict]] = [
        {"external_key": "1", "title": "A"},
        {"external_key": "2", "title": "B"},
    ]

    async def run(self, ctx: ParseContext) -> ParseResult:
        FakeParser.seen_ctx = ctx
        if self.behavior == "login":
            raise ParseError(kind=ErrorKind.login_required, message="login wall")
        if self.behavior == "partial":
            raise ParseError(
                kind=ErrorKind.blocked,
                message="captcha",
                partial_rows=[{"external_key": "9", "title": "P"}],
            )
        if self.behavior == "boom":
            raise ValueError("kaboom")
        if self.behavior == "slow":
            await asyncio.sleep(10)
        return ParseResult(
            rows=[dict(row) for row in FakeParser.rows],
            meta={"pages": 1},
            notes="fine",
        )


class FakePage:
    """Records ``screenshot`` calls; the runner must tolerate a broken page."""

    def __init__(self, fail: bool = False):
        self.paths: list[str] = []
        self.fail = fail

    async def screenshot(self, path=None):
        if self.fail:
            raise RuntimeError("page is gone")
        self.paths.append(path)
        Path(path).write_bytes(b"png")


class FakeSession:
    def __init__(self, page=None):
        self.closed = False
        self.context = object()
        self.page = page or object()

    async def close(self):
        self.closed = True


class FakeBridge:
    def __init__(self, proxy):
        self.proxy = proxy
        self.started = False
        self.stopped = False

    async def start(self) -> str:
        self.started = True
        return "http://127.0.0.1:1234"

    async def stop(self) -> None:
        self.stopped = True


@pytest.fixture
async def env(tmp_path):
    FakeParser.behavior = "ok"
    FakeParser.rows = [
        {"external_key": "1", "title": "A"},
        {"external_key": "2", "title": "B"},
    ]
    FakeParser.seen_ctx = None
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path), "UWP_RUN_TIMEOUT": "1"})
    db = await Database.in_memory()
    await db.migrate()
    sessions: list[FakeSession] = []
    launches: list[dict] = []
    bridges: list[FakeBridge] = []

    async def fake_launch(*a, **kw):
        launches.append({"args": a, "kwargs": kw})
        s = FakeSession(FakePage())
        sessions.append(s)
        return s

    def bridge_factory(proxy):
        bridge = FakeBridge(proxy)
        bridges.append(bridge)
        return bridge

    reg = ParserRegistry()
    reg.register(FakeParser())
    runner = Runner(
        settings,
        ProfileManager(settings, ProfilesRepo(db)),
        reg,
        RunsRepo(db),
        ItemsRepo(db),
        launcher=fake_launch,
        bridge_factory=bridge_factory,
    )
    site = Site(id=1, slug="s", name="S", parser_key="fake", interval_min_min=1, interval_max_min=2)
    job = Job(id=1, site_id=1, kind=JobKind.scheduled, status=JobStatus.running)
    await db.execute(
        "INSERT INTO sites (id, slug, name, parser_key, interval_min_min, interval_max_min)"
        " VALUES (1, 's', 'S', 'fake', 1, 2)"
    )
    await db.execute(
        "INSERT INTO jobs (id, site_id, kind, status, created_at)"
        " VALUES (1, 1, 'scheduled', 'running', '2026-01-01T00:00:00Z')"
    )
    yield runner, site, job, sessions, db, launches, bridges
    await db.close()


async def test_ok_run_saves_items_and_releases_profile(env):
    runner, site, job, sessions, db, _, _ = env
    out = await runner.run(site, job, proxy=None)
    assert out.status == RunStatus.ok and out.new_count == 2
    assert out.rows == [{"external_key": "1", "title": "A"}, {"external_key": "2", "title": "B"}]
    assert out.meta == {"pages": 1} and out.notes == "fine"
    assert out.error_kind is None and out.error is None
    assert sessions[0].closed
    assert (await ProfilesRepo(db).list())[0].status == ProfileStatus.expired
    run = (await RunsRepo(db).list_for_site(1))[0]
    assert run.status == RunStatus.ok
    assert run.rows_count == 2 and run.new_count == 2
    assert run.finished_at is not None and run.duration_ms is not None
    assert await ItemsRepo(db).keys(1) == {"1", "2"}


async def test_second_run_counts_no_new_items(env):
    runner, site, job, *_ = env
    await runner.run(site, job, proxy=None)
    out = await runner.run(site, job, proxy=None)
    assert out.status == RunStatus.ok and out.new_count == 0


async def test_a_row_that_comes_back_is_new_again(env):
    runner, site, job, _, db, *_ = env
    await runner.run(site, job, proxy=None)

    # The sale ends: Steam's search page drops the game entirely.
    FakeParser.rows = []
    gone = await runner.run(site, job, proxy=None)
    assert gone.status == RunStatus.ok and gone.new_count == 0

    # The same sale returns with identical data.
    FakeParser.rows = [
        {"external_key": "1", "title": "A"},
        {"external_key": "2", "title": "B"},
    ]
    back = await runner.run(site, job, proxy=None)

    assert back.status == RunStatus.ok
    assert back.new_count == 2
    # «Новое» is "not in the previous run", not "never seen before".
    assert await ItemsRepo(db).count(1) == 2


async def test_vanished_counts_only_rows_missing_from_this_run(env):
    runner, site, job, *_ = env
    await runner.run(site, job, proxy=None)  # stores rows 1 and 2

    FakeParser.rows = [{"external_key": "1", "title": "A"}]
    _, _, vanished = await runner._save_rows(site, list(FakeParser.rows))
    assert vanished == 1

    # Row 2 has been gone since the previous run, so it is not a *new*
    # disappearance: the old code compared against all history and kept
    # reporting 1 on every run forever.
    _, _, vanished = await runner._save_rows(site, list(FakeParser.rows))
    assert vanished == 0


async def test_login_required_is_not_retried_and_keeps_partial(env):
    runner, site, job, sessions, db, _, _ = env
    FakeParser.behavior = "login"
    out = await runner.run(site, job, proxy=None)
    assert out.status == RunStatus.failed and out.error_kind == ErrorKind.login_required
    assert out.error == "login wall"
    assert sessions[0].closed
    run = (await RunsRepo(db).list_for_site(1))[0]
    assert run.status == RunStatus.failed and run.error.startswith("login_required: ")


async def test_parse_error_with_partial_rows_is_partial(env):
    runner, site, job, sessions, db, _, _ = env
    FakeParser.behavior = "partial"
    out = await runner.run(site, job, proxy=None)
    assert out.status == RunStatus.partial and out.error_kind == ErrorKind.blocked
    assert out.rows == [{"external_key": "9", "title": "P"}]
    assert out.new_count == 1
    assert sessions[0].closed
    run = (await RunsRepo(db).list_for_site(1))[0]
    assert run.status == RunStatus.partial and run.rows_count == 1


async def test_unexpected_exception_is_parser_error(env):
    runner, site, job, sessions, db, _, _ = env
    FakeParser.behavior = "boom"
    out = await runner.run(site, job, proxy=None)
    assert out.status == RunStatus.failed
    assert out.error_kind == ErrorKind.parser_error and "kaboom" in out.error
    assert "Traceback" in out.error
    assert sessions[0].closed
    run = (await RunsRepo(db).list_for_site(1))[0]
    assert run.status == RunStatus.failed


async def test_timeout_is_transient(env):
    runner, site, job, sessions, db, _, _ = env
    FakeParser.behavior = "slow"
    out = await runner.run(site, job, proxy=None)
    assert out.status == RunStatus.failed
    assert out.error_kind == ErrorKind.transient and "timed out" in out.error
    assert sessions[0].closed
    assert (await ProfilesRepo(db).list())[0].status == ProfileStatus.expired


async def test_browser_unavailable_is_config_error(env):
    runner, site, job, _, db, _, _ = env

    async def no_browser(*a, **kw):
        raise BrowserUnavailable("fingerprint-chromium not found")

    runner.launcher = no_browser
    out = await runner.run(site, job, proxy=None)
    assert out.status == RunStatus.failed and out.error_kind == ErrorKind.config_error
    assert "fingerprint-chromium" in out.error
    assert (await ProfilesRepo(db).list())[0].status == ProfileStatus.expired


async def test_cancel_before_launch_skips_browser(env):
    runner, site, job, sessions, db, launches, _ = env
    cancel = asyncio.Event()
    cancel.set()
    out = await runner.run(site, job, proxy=None, cancel=cancel)
    assert out.status == RunStatus.canceled
    assert launches == [] and sessions == []
    run = (await RunsRepo(db).list_for_site(1))[0]
    assert run.status == RunStatus.canceled


async def test_cancel_after_parser_discards_rows(env):
    runner, site, job, sessions, db, _, _ = env
    cancel = asyncio.Event()

    class CancelingParser(FakeParser):
        async def run(self, ctx):
            cancel.set()
            return await super().run(ctx)

    runner.registry.register(CancelingParser())
    out = await runner.run(site, job, proxy=None, cancel=cancel)
    assert out.status == RunStatus.canceled
    assert out.rows == [] and out.new_count == 0
    assert sessions[0].closed
    assert await ItemsRepo(db).count(1) == 0


async def test_parse_context_is_wired(env, tmp_path):
    runner, site, job, *_ = env
    await runner.run(site, job, proxy=None)
    ctx = FakeParser.seen_ctx
    assert ctx.site is site
    assert ctx.profile.kind == ProfileKind.clean
    assert ctx.artifacts_dir == Path(tmp_path) / "artifacts" / str(ctx.artifacts_dir.name)
    assert ctx.artifacts_dir.is_dir()
    assert await ctx.known_keys() == {"1", "2"}
    path = ctx.save_artifact("shot.txt", b"hello")
    assert path.parent == ctx.artifacts_dir and path.read_bytes() == b"hello"
    assert isinstance(ctx.log, logging.Logger)


async def test_persistent_profile_is_leased_and_released(env):
    runner, site, job, _, db, _, _ = env
    profile = await runner.profiles.create_persistent("main")
    site = site.model_copy(update={"profile_mode": ProfileKind.persistent, "profile_id": profile.id})
    out = await runner.run(site, job, proxy=None)
    assert out.status == RunStatus.ok
    stored = await ProfilesRepo(db).get(profile.id)
    assert stored.status == ProfileStatus.free and stored.lease_until is None


async def test_busy_persistent_profile_is_config_error(env):
    runner, site, job, _, _, launches, _ = env
    profile = await runner.profiles.create_persistent("main")
    assert await runner.profiles.lease(profile.id, 60)
    site = site.model_copy(update={"profile_mode": ProfileKind.persistent, "profile_id": profile.id})
    out = await runner.run(site, job, proxy=None)
    assert out.status == RunStatus.failed and out.error_kind == ErrorKind.config_error
    assert "profile busy" in out.error
    assert launches == []


async def test_proxy_bridge_is_started_and_stopped(env):
    runner, site, job, _, db, launches, bridges = env
    proxy = Proxy(name="p", scheme=ProxyScheme.http, host="10.0.0.1", port=8080)
    out = await runner.run(site, job, proxy=proxy)
    assert out.status == RunStatus.ok
    assert len(bridges) == 1 and bridges[0].started and bridges[0].stopped
    assert launches[0]["args"][3] == "http://127.0.0.1:1234"
    run = (await RunsRepo(db).list_for_site(1))[0]
    assert run.proxy_id is None  # proxy row was never stored


async def test_missing_proxy_bridge_is_config_error(env, monkeypatch):
    runner, site, job, _, db, launches, _ = env
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "uwp.proxybridge":
            raise ImportError("no bridge")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    runner.bridge_factory = None
    proxy = Proxy(name="p", scheme=ProxyScheme.http, host="10.0.0.1", port=8080)
    out = await runner.run(site, job, proxy=proxy)
    assert out.status == RunStatus.failed and out.error_kind == ErrorKind.config_error
    assert "proxy bridge unavailable" in out.error
    assert launches == []
    assert (await ProfilesRepo(db).list())[0].status == ProfileStatus.expired


async def test_unknown_parser_is_config_error(env):
    runner, site, job, _, _, launches, _ = env
    site = site.model_copy(update={"parser_key": "nope"})
    out = await runner.run(site, job, proxy=None)
    assert out.status == RunStatus.failed and out.error_kind == ErrorKind.config_error
    assert "nope" in out.error
    assert launches == []


async def test_failed_run_saves_a_screenshot_for_the_admin(env, tmp_path):
    runner, site, job, sessions, _, _, _ = env
    FakeParser.behavior = "login"

    out = await runner.run(site, job, proxy=None)

    shot = tmp_path / "artifacts" / str(out.run_id) / "screenshot.png"
    assert out.status == RunStatus.failed
    assert shot.is_file()
    assert sessions[0].page.paths == [str(shot)]
    assert out.artifacts_dir == shot.parent


async def test_a_broken_screenshot_does_not_change_the_outcome(env):
    runner, site, job, sessions, _, _, _ = env
    FakeParser.behavior = "login"
    runner.launcher = lambda *a, **kw: _broken_session(sessions)

    out = await runner.run(site, job, proxy=None)

    assert out.status == RunStatus.failed and out.error_kind == ErrorKind.login_required


async def _broken_session(sessions):
    session = FakeSession(FakePage(fail=True))
    sessions.append(session)
    return session
