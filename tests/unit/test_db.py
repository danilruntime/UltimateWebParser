"""Tests for the SQLite database layer, migrations and repositories."""
from datetime import UTC, datetime, timedelta

import pytest

from uwp.db.database import Database
from uwp.db.repo.admins import AdminsRepo
from uwp.db.repo.events import EventsRepo
from uwp.db.repo.items import ItemsRepo
from uwp.db.repo.jobs import JobsRepo
from uwp.db.repo.profiles import ProfilesRepo
from uwp.db.repo.proxies import ProxiesRepo
from uwp.db.repo.runs import RunsRepo
from uwp.db.repo.sites import SitesRepo
from uwp.domain import (
    Event,
    ExportMode,
    JobStatus,
    Profile,
    ProfileKind,
    ProfileStatus,
    Proxy,
    ProxyScheme,
    Run,
    RunStatus,
    Site,
)
from uwp.timeutil import utcnow

NOW = datetime(2026, 11, 13, 1, 20, tzinfo=UTC)


@pytest.fixture
async def db():
    d = await Database.in_memory()
    await d.migrate()
    yield d
    await d.close()


def make_site(slug: str = "steam", **kw) -> Site:
    kw.setdefault("interval_min_min", 45)
    kw.setdefault("interval_max_min", 90)
    return Site(slug=slug, name=slug.title(), parser_key="steam_deals", **kw)


def make_profile(name: str = "p1", **kw) -> Profile:
    return Profile(name=name, kind=ProfileKind.clean, seed=42, platform="windows",
                   brand="Chrome", brand_version="150", timezone="Europe/Moscow",
                   locale="ru-RU", user_data_dir=f"/tmp/{name}", **kw)


async def test_migrate_is_idempotent(db):
    await db.migrate()
    first = await db.fetchone("SELECT MAX(version) AS version FROM schema_version")
    await db.migrate()
    row = await db.fetchone("SELECT MAX(version) AS version FROM schema_version")
    assert row["version"] == first["version"]
    # And it is the newest migration on disk, not a hardcoded number.
    from uwp.db.database import _load_migrations

    assert row["version"] == max(version for version, _name, _sql in _load_migrations())


async def test_failing_migration_rolls_back_and_keeps_connection_usable(db, monkeypatch):
    import sqlite3

    from uwp.db import database as database_module

    real_load_migrations = database_module._load_migrations
    bad = (
        99,
        "099_bad.sql",
        (
            "CREATE TABLE half_applied (id INTEGER PRIMARY KEY);\n"
            "INSERT INTO no_such_table (x) VALUES (1);\n"
        ),
    )
    monkeypatch.setattr(
        database_module, "_load_migrations", lambda: [*real_load_migrations(), bad]
    )
    with pytest.raises(sqlite3.OperationalError):
        await db.migrate()
    assert (await db.fetchone("SELECT 1 AS one"))["one"] == 1
    assert await db.fetchone("SELECT * FROM schema_version WHERE version = 99") is None
    assert await db.fetchone(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='half_applied'"
    ) is None
    await db.execute(
        "INSERT INTO admins (telegram_id, added_at) VALUES (?, ?)", (1, "2026-11-13T00:00:00Z")
    )
    assert (await db.fetchone("SELECT COUNT(*) AS n FROM admins"))["n"] == 1


async def test_migrate_creates_all_tables(db):
    rows = await db.fetchall("SELECT name FROM sqlite_master WHERE type='table'")
    names = {r["name"] for r in rows}
    assert {
        "sites", "profiles", "proxies", "jobs", "runs", "items", "events",
        "event_deliveries", "admins", "vnc_sessions", "worker_heartbeat",
        "schema_version",
    } <= names


async def test_foreign_keys_enabled(db):
    assert (await db.fetchone("PRAGMA foreign_keys"))[0] == 1


async def test_execute_returns_lastrowid(db):
    rid = await db.execute(
        "INSERT INTO admins (telegram_id, added_at) VALUES (?, ?)", (1, "2026-11-13T00:00:00Z")
    )
    assert rid == 1


async def test_transaction_rolls_back_on_error(db):
    with pytest.raises(RuntimeError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO admins (telegram_id, added_at) VALUES (?, ?)",
                (1, "2026-11-13T00:00:00Z"),
            )
            raise RuntimeError("boom")
    assert await db.fetchone("SELECT * FROM admins") is None


async def test_site_crud(db):
    repo = SitesRepo(db)
    sid = await repo.create(Site(slug="steam", name="Steam", parser_key="steam_deals",
                                 interval_min_min=45, interval_max_min=90))
    site = await repo.get(sid)
    assert site.slug == "steam" and site.enabled is False
    await repo.update(sid, enabled=True)
    assert (await repo.get(sid)).enabled is True
    assert [s.id for s in await repo.list()] == [sid]


async def test_site_roundtrip_all_fields(db):
    repo = SitesRepo(db)
    site = make_site(
        parser_settings={"region": "us", "pages": 3},
        enabled=True,
        profile_mode=ProfileKind.persistent,
        interval_min_min=10,
        interval_max_min=20,
        next_run_at=NOW,
        export_mode=ExportMode.changes,
        max_attempts=5,
        last_run_at=NOW - timedelta(hours=1),
        last_status="ok",
        consecutive_failures=2,
        match_profile_to_proxy_country=True,
    )
    sid = await repo.create(site)
    got = await repo.get(sid)
    assert got == site.model_copy(update={"id": sid})
    assert got.parser_settings == {"region": "us", "pages": 3}
    assert got.profile_mode is ProfileKind.persistent
    assert got.export_mode is ExportMode.changes
    assert got.next_run_at == NOW


async def test_site_get_by_slug_and_delete(db):
    repo = SitesRepo(db)
    sid = await repo.create(make_site("steam"))
    assert (await repo.get_by_slug("steam")).id == sid
    assert await repo.get_by_slug("nope") is None
    await repo.delete(sid)
    assert await repo.get(sid) is None


async def test_site_due(db):
    repo = SitesRepo(db)
    due = await repo.create(make_site("due", enabled=True, next_run_at=NOW - timedelta(minutes=1)))
    await repo.create(make_site("future", enabled=True, next_run_at=NOW + timedelta(minutes=5)))
    await repo.create(make_site("disabled", enabled=False, next_run_at=NOW - timedelta(minutes=1)))
    await repo.create(make_site("never", enabled=True, next_run_at=None))
    assert [s.id for s in await repo.due(NOW)] == [due]


async def test_only_one_active_job_per_site(db):
    sites = SitesRepo(db)
    jobs = JobsRepo(db)
    sid = await sites.create(make_site("s"))
    first = await jobs.create(sid, "scheduled")
    second = await jobs.create(sid, "manual")
    assert first is not None and second is None


async def test_active_job_allows_new_job_after_finish(db):
    sites, jobs = SitesRepo(db), JobsRepo(db)
    sid = await sites.create(make_site("s"))
    first = await jobs.create(sid, "scheduled")
    await jobs.finish(first, JobStatus.done)
    assert await jobs.create(sid, "manual") is not None


async def test_claim_and_finish(db):
    sites, jobs = SitesRepo(db), JobsRepo(db)
    sid = await sites.create(make_site("s"))
    jid = await jobs.create(sid, "scheduled")
    claimed = await jobs.claim("w1", now=utcnow())
    assert claimed.id == jid and claimed.status == JobStatus.running
    assert claimed.claimed_by == "w1" and claimed.claimed_at is not None
    assert await jobs.claim("w2", now=utcnow()) is None
    await jobs.finish(jid, "done")
    assert (await jobs.get(jid)).status == JobStatus.done


async def test_claim_respects_not_before_and_priority(db):
    sites, jobs = SitesRepo(db), JobsRepo(db)
    low = await sites.create(make_site("low"))
    high = await sites.create(make_site("high"))
    future = await sites.create(make_site("future"))
    await jobs.create(low, "scheduled", priority=0)
    await jobs.create(high, "manual", priority=10)
    await jobs.create(future, "scheduled", priority=99, not_before=NOW + timedelta(minutes=10))
    claimed = await jobs.claim("w1", now=NOW)
    assert claimed.site_id == high
    assert await jobs.claim("w2", now=NOW) is not None
    assert await jobs.claim("w3", now=NOW) is None
    assert (await jobs.claim("w4", now=NOW + timedelta(minutes=11))).site_id == future


async def test_heartbeat_requeue_and_orphans(db):
    sites, jobs = SitesRepo(db), JobsRepo(db)
    sid = await sites.create(make_site("s"))
    jid = await jobs.create(sid, "scheduled")
    await jobs.claim("w1", now=NOW)
    await jobs.heartbeat(jid, NOW + timedelta(seconds=15))
    assert (await jobs.get(jid)).heartbeat_at == NOW + timedelta(seconds=15)
    assert [j.id for j in await jobs.orphans(NOW + timedelta(seconds=30), timedelta(seconds=90))] == []
    assert [j.id for j in await jobs.orphans(NOW + timedelta(minutes=5), timedelta(seconds=90))] == [jid]
    await jobs.requeue(jid, "timeout")
    job = await jobs.get(jid)
    assert job.status == JobStatus.queued and job.error == "timeout"
    assert job.claimed_by is None and job.attempt == 1


async def test_cancel_queued(db):
    sites, jobs = SitesRepo(db), JobsRepo(db)
    sid = await sites.create(make_site("s"))
    jid = await jobs.create(sid, "scheduled")
    await jobs.cancel_queued(sid)
    assert (await jobs.get(jid)).status == JobStatus.canceled
    assert await jobs.create(sid, "manual") is not None


async def test_profile_crud_and_lease(db):
    repo = ProfilesRepo(db)
    pid = await repo.create(make_profile())
    p = await repo.get(pid)
    assert p.kind is ProfileKind.clean and p.status is ProfileStatus.free
    assert [x.id for x in await repo.list()] == [pid]
    until = NOW + timedelta(minutes=10)
    assert await repo.lease(pid, until) is True
    leased = await repo.get(pid)
    assert leased.status is ProfileStatus.locked and leased.lease_until == until
    assert await repo.lease(pid, until) is False
    await repo.release(pid)
    released = await repo.get(pid)
    assert released.status is ProfileStatus.free and released.lease_until is None
    await repo.update(pid, status=ProfileStatus.expired)
    assert (await repo.get(pid)).status is ProfileStatus.expired
    await repo.delete(pid)
    assert await repo.get(pid) is None


async def test_profile_expired_clean(db):
    repo = ProfilesRepo(db)
    stale = await repo.create(make_profile("stale", expires_at=NOW - timedelta(hours=1)))
    fresh = await repo.create(make_profile("fresh", expires_at=NOW + timedelta(hours=1)))
    await repo.expired_clean(NOW)
    assert (await repo.get(stale)).status is ProfileStatus.expired
    assert (await repo.get(fresh)).status is ProfileStatus.free


async def test_proxy_crud(db):
    repo = ProxiesRepo(db)
    pid = await repo.create(Proxy(name="p", scheme=ProxyScheme.socks5, host="1.2.3.4",
                                  port=1080, username="u", password="s", country="RU"))
    p = await repo.get(pid)
    assert p.scheme is ProxyScheme.socks5 and p.country == "RU"
    await repo.update(pid, port=1081)
    assert (await repo.get(pid)).port == 1081
    assert [x.id for x in await repo.list()] == [pid]
    await repo.delete(pid)
    assert await repo.get(pid) is None


async def test_run_crud(db):
    sites, jobs, runs = SitesRepo(db), JobsRepo(db), RunsRepo(db)
    sid = await sites.create(make_site("s"))
    jid = await jobs.create(sid, "scheduled")
    rid = await runs.create(Run(job_id=jid, site_id=sid, status=RunStatus.ok,
                                started_at=NOW))
    run = await runs.get(rid)
    assert run.status is RunStatus.ok and run.rows_count == 0
    await runs.update(rid, status=RunStatus.partial, rows_count=10, new_count=3,
                      finished_at=NOW + timedelta(seconds=5), duration_ms=5000)
    run = await runs.get(rid)
    assert run.status is RunStatus.partial and run.new_count == 3
    assert run.finished_at == NOW + timedelta(seconds=5)
    assert [r.id for r in await runs.list_for_site(sid, limit=10)] == [rid]


async def test_items_upsert(db):
    sites, items = SitesRepo(db), ItemsRepo(db)
    sid = await sites.create(make_site("s"))
    assert (await items.upsert(sid, "k1", {"price": 10}, NOW)).created is True
    assert (await items.upsert(sid, "k1", {"price": 12}, NOW + timedelta(hours=1))).created is False
    assert await items.count(sid) == 1
    assert await items.previous_payload(sid, "k1") == {"price": 12}
    assert await items.previous_payload(sid, "missing") is None
    assert (await items.upsert(sid, "k2", {"price": 1}, NOW)).created is True
    assert await items.count(sid) == 2


async def test_items_upsert_reports_a_returned_row_as_new(db):
    sites, items = SitesRepo(db), ItemsRepo(db)
    sid = await sites.create(make_site("s"))

    first = await items.upsert(sid, "k1", {"price": 10}, NOW)
    assert (first.created, first.returned, first.is_new) == (True, False, True)

    again = await items.upsert(sid, "k1", {"price": 10}, NOW + timedelta(minutes=5))
    assert (again.created, again.returned, again.is_new) == (False, False, False)

    await items.settle(sid, set())
    back = await items.upsert(sid, "k1", {"price": 10}, NOW + timedelta(minutes=10))
    assert (back.created, back.returned, back.is_new) == (False, True, True)


async def test_items_present_keys_hold_the_previous_run(db):
    sites, items = SitesRepo(db), ItemsRepo(db)
    sid = await sites.create(make_site("s"))

    for key in ("a", "b", "c"):
        await items.upsert(sid, key, {"v": key}, NOW)
    assert await items.present_keys(sid) == {"a", "b", "c"}

    # A run that sees only ``a``: ``b`` and ``c`` are gone as of this run.
    await items.settle(sid, {"a"})
    assert await items.present_keys(sid) == {"a"}

    # ``b`` comes back: present again, and reported as new.
    come_back = await items.upsert(sid, "b", {"v": "b"}, NOW + timedelta(minutes=10))
    assert come_back.is_new is True
    assert await items.present_keys(sid) == {"a", "b"}


async def test_migration_backfills_absent_for_history(db, monkeypatch):
    from uwp.db import database as database_module

    real = database_module._load_migrations
    # Stop at the schema this feature was designed against: no ``absent`` yet.
    monkeypatch.setattr(
        database_module, "_load_migrations", lambda: [m for m in real() if m[0] <= 3]
    )
    await db.migrate()
    sid = await SitesRepo(db).create(make_site("s"))
    await db.execute(
        "INSERT INTO items (site_id, external_key, payload, first_seen_at, last_seen_at)"
        " VALUES (?, 'old', '{}', ?, ?)",
        (sid, "2026-11-01T00:00:00Z", "2026-11-01T00:00:00Z"),
    )

    monkeypatch.setattr(database_module, "_load_migrations", real)
    await db.migrate()

    row = await db.fetchone("SELECT absent FROM items WHERE external_key = 'old'")
    # Presence was never recorded for history, so it must read as neither
    # vanished nor returned on the first run after the upgrade.
    assert row["absent"] is None
    assert await ItemsRepo(db).present_keys(sid) == set()


async def test_history_rows_are_normalised_by_the_first_run_that_misses_them(db, monkeypatch):
    """A legacy NULL row must still be reported when it comes back.

    Leaving it NULL forever would hide a returning game for the whole history
    — exactly the case this feature exists for. The first run that does not
    see it settles it to ``absent``, without counting it as a disappearance
    (its presence was never established).
    """
    from uwp.db import database as database_module

    real = database_module._load_migrations
    monkeypatch.setattr(
        database_module, "_load_migrations", lambda: [m for m in real() if m[0] <= 3]
    )
    await db.migrate()
    sid = await SitesRepo(db).create(make_site("s"))
    await db.execute(
        "INSERT INTO items (site_id, external_key, payload, first_seen_at, last_seen_at)"
        " VALUES (?, 'old', '{}', ?, ?)",
        (sid, "2026-11-01T00:00:00Z", "2026-11-01T00:00:00Z"),
    )
    monkeypatch.setattr(database_module, "_load_migrations", real)
    await db.migrate()
    items = ItemsRepo(db)

    assert await items.settle(sid, set()) == 0
    row = await db.fetchone("SELECT absent FROM items WHERE external_key = 'old'")
    assert row["absent"] == 1

    back = await items.upsert(sid, "old", {}, NOW)
    assert back.is_new is True


async def test_settle_counts_only_rows_that_were_present(db):
    sites, items = SitesRepo(db), ItemsRepo(db)
    sid = await sites.create(make_site("s"))
    for key in ("a", "b", "c"):
        await items.upsert(sid, key, {"v": key}, NOW)

    # ``b`` and ``c`` were present last run and are gone now.
    assert await items.settle(sid, {"a"}) == 2
    assert await items.present_keys(sid) == {"a"}

    # Same input again: nothing new disappeared.
    assert await items.settle(sid, {"a"}) == 0
    assert await items.present_keys(sid) == {"a"}

    # ``a`` is dropped too.
    assert await items.settle(sid, set()) == 1
    assert await items.present_keys(sid) == set()


async def test_items_upsert_keeps_first_seen(db):
    sites, items = SitesRepo(db), ItemsRepo(db)
    sid = await sites.create(make_site("s"))
    await items.upsert(sid, "k1", {"v": 1}, NOW)
    await items.upsert(sid, "k1", {"v": 2}, NOW + timedelta(hours=1))
    row = await db.fetchone("SELECT * FROM items WHERE site_id=? AND external_key='k1'", (sid,))
    assert row["first_seen_at"] == "2026-11-13T01:20:00Z"
    assert row["last_seen_at"] == "2026-11-13T02:20:00Z"


async def test_events_emit_and_pending(db):
    sites, events = SitesRepo(db), EventsRepo(db)
    sid = await sites.create(make_site("s"))
    eid = await events.emit("run_failed", sid, None, {"error": "x"}, now=NOW)
    old = await events.emit("run_failed", sid, None, {}, now=NOW - timedelta(days=10))
    pending = await events.pending(admin_id=1, now=NOW, ttl_days=7)
    assert [e.id for e in pending] == [eid]
    assert pending[0].kind == "run_failed" and pending[0].payload == {"error": "x"}
    await events.mark_delivered(eid, 1)
    assert await events.pending(admin_id=1, now=NOW, ttl_days=7) == []
    assert [e.id for e in await events.pending(admin_id=2, now=NOW, ttl_days=7)] == [eid]
    assert [e.id for e in await events.pending(admin_id=2, now=NOW, ttl_days=30)] == [eid, old]


async def test_event_model_roundtrip(db):
    events = EventsRepo(db)
    eid = await events.emit("info", None, None, {"a": 1}, now=NOW)
    [event] = await events.pending(admin_id=1, now=NOW, ttl_days=1)
    assert isinstance(event, Event)
    assert event.id == eid and event.payload == {"a": 1} and event.created_at == NOW
    assert event.site_id is None and event.run_id is None


async def test_admins(db):
    repo = AdminsRepo(db)
    await repo.add(10)
    await repo.add(20)
    await repo.add(10)
    assert await repo.list() == [10, 20]
    await repo.remove(10)
    assert await repo.list() == [20]
