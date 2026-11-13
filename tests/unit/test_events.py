"""Tests for the durable event queue used by the bot."""
from datetime import timedelta

import pytest

from uwp.config import load_settings
from uwp.db.database import Database
from uwp.db.repo.admins import AdminsRepo
from uwp.db.repo.events import EventsRepo
from uwp.events import EventBus
from uwp.timeutil import utcnow


@pytest.fixture
async def bus():
    db = await Database.in_memory()
    await db.migrate()
    yield db, EventBus(EventsRepo(db), AdminsRepo(db), load_settings({}))
    await db.close()


async def test_event_delivered_once_per_admin(bus):
    _db, bus = bus
    eid = await bus.emit("run_finished", site_id=None, run_id=None, payload={"rows": 10})

    assert [e.id for e in await bus.pending(111, utcnow())] == [eid]
    await bus.mark_delivered(eid, 111)
    assert await bus.pending(111, utcnow()) == []
    # Another admin still sees it.
    assert [e.id for e in await bus.pending(222, utcnow())] == [eid]


async def test_pending_is_ordered_by_id(bus):
    _db, bus = bus
    first = await bus.emit("run_finished", payload={})
    second = await bus.emit("run_failed", payload={})

    assert [e.id for e in await bus.pending(1, utcnow())] == [first, second]


async def test_old_events_are_purged(bus):
    _db, bus = bus
    await bus.emit("run_finished", payload={})
    later = utcnow() + timedelta(days=4)

    assert await bus.purge(later) == 1
    assert await bus.pending(1, later) == []


async def test_purge_removes_deliveries(bus):
    db, bus = bus
    eid = await bus.emit("run_finished", payload={})
    await bus.mark_delivered(eid, 7)

    await bus.purge(utcnow() + timedelta(days=4))

    rows = await db.fetchall("SELECT * FROM event_deliveries WHERE event_id = ?", (eid,))
    assert rows == []


async def test_emit_persists_payload_and_links(bus):
    _db, bus = bus
    eid = await bus.emit("site_problem", site_id=None, run_id=None, payload={"why": "x"})

    event = (await bus.pending(5, utcnow()))[0]
    assert event.id == eid
    assert event.kind == "site_problem"
    assert event.payload == {"why": "x"}
