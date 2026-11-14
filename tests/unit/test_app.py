"""Tests for the runtime wiring shared by the bot and the worker."""
import asyncio
from datetime import timedelta

import pytest

from uwp.app import build_runtime
from uwp.config import load_settings
from uwp.domain import (
    Profile,
    ProfileKind,
    ProfileStatus,
    VncSession,
    VncStatus,
)
from uwp.timeutil import utcnow


@pytest.fixture
async def runtime(tmp_path):
    rt = await build_runtime(
        load_settings({"UWP_DATA_DIR": str(tmp_path)}), with_bot=False
    )
    yield rt
    await rt.close()


async def _orphan(runtime, status, lease_until=None):
    """A persistent profile plus the unfinished session a dead worker left."""
    profile_id = await runtime.vnc.profiles.create(
        Profile(
            name=f"login-{status.value}",
            kind=ProfileKind.persistent,
            seed=1,
            platform="windows",
            brand="Chrome",
            brand_version="150",
            timezone="Europe/Moscow",
            locale="ru-RU",
            user_data_dir=str(runtime.settings.profiles_dir / f"login-{status.value}"),
        )
    )
    await runtime.vnc.profiles.update(
        profile_id, status=status.value, lease_until=lease_until
    )
    session_id = await runtime.vnc.sessions.create(
        VncSession(
            profile_id=profile_id,
            port=6100,
            display=100,
            password="p",
            token=f"t{profile_id}",
            status=VncStatus.running,
            expires_at=utcnow() + timedelta(minutes=30),
        )
    )
    return profile_id, session_id


async def _run_one_worker_cycle(runtime):
    """Start ``run_worker`` and stop it as soon as its first cycle finished."""
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.05)
        stop.set()

    task = asyncio.create_task(stopper())
    await runtime.run_worker(stop)
    await task


async def test_run_worker_sweeps_orphans_and_frees_their_profiles(runtime):
    profile_id, session_id = await _orphan(runtime, ProfileStatus.login)

    await _run_one_worker_cycle(runtime)

    assert (await runtime.vnc.sessions.get(session_id)).status == VncStatus.failed
    assert (await runtime.vnc.profiles.get(profile_id)).status == ProfileStatus.free


async def test_run_worker_never_frees_a_profile_a_scheduled_run_leased(runtime):
    """The sweep must free through the conditional path, not ``status = free``."""
    profile_id, session_id = await _orphan(
        runtime, ProfileStatus.locked, lease_until=utcnow() + timedelta(hours=1)
    )

    await _run_one_worker_cycle(runtime)

    assert (await runtime.vnc.sessions.get(session_id)).status == VncStatus.failed
    assert (await runtime.vnc.profiles.get(profile_id)).status == ProfileStatus.locked


async def test_run_worker_launches_a_booking_made_before_it_started(runtime):
    """The bot may book in split mode; the worker's sweep must not fail that row."""
    profile_id = await runtime.vnc.profiles.create(
        Profile(
            name="early-booking",
            kind=ProfileKind.persistent,
            seed=1,
            platform="windows",
            brand="Chrome",
            brand_version="150",
            timezone="Europe/Moscow",
            locale="ru-RU",
            user_data_dir=str(runtime.settings.profiles_dir / "early-booking"),
        )
    )
    await runtime.vnc.profiles.update(profile_id, status=ProfileStatus.login.value)
    session_id = await runtime.vnc.sessions.create(
        VncSession(
            profile_id=profile_id,
            port=6101,
            display=101,
            password="p",
            token="early",
            status=VncStatus.requested,
            expires_at=utcnow() + timedelta(minutes=30),
        )
    )

    launched: list[int] = []

    async def fake_launch(session, proxy_url=None):
        launched.append(session.id)
        await runtime.vnc.sessions.update(session.id, status=str(VncStatus.running))

    runtime.vnc.launch = fake_launch  # type: ignore[method-assign]
    await _run_one_worker_cycle(runtime)

    # The sweep left it alone and the first tick launched it.
    assert launched == [session_id]
    assert (await runtime.vnc.sessions.get(session_id)).status == VncStatus.running


async def test_run_worker_starts_even_when_the_sweep_fails(runtime, monkeypatch):
    """A failed sweep must not leave a ``combined`` process with no worker at all."""
    calls: list[int] = []

    async def boom():
        raise RuntimeError("database is locked")

    async def fake_run_forever(stop):
        calls.append(1)
        stop.set()

    monkeypatch.setattr(runtime.vnc.sessions, "sweep_orphans", boom)
    monkeypatch.setattr(runtime.worker, "run_forever", fake_run_forever)

    await runtime.run_worker(asyncio.Event())

    assert calls == [1]


async def test_runtime_wires_every_collaborator(runtime):
    assert runtime.deps.sites is not None
    assert runtime.deps.profiles is not None
    assert runtime.deps.proxies is not None
    assert runtime.deps.vnc is not None
    assert runtime.deps.runs is not None
    assert runtime.registry.get("steam_deals") is not None


async def test_runtime_migrates_the_database(runtime):
    row = await runtime.db.fetchone("SELECT MAX(version) AS v FROM schema_version")

    assert row["v"] >= 1


async def test_run_once_survives_an_empty_queue(runtime):
    await runtime.run_once()


async def test_with_bot_requires_a_token(tmp_path):
    from uwp.app import build_runtime as build

    with pytest.raises(RuntimeError, match="UWP_BOT_TOKEN"):
        await build(load_settings({"UWP_DATA_DIR": str(tmp_path), "UWP_BOT_TOKEN": ""}))
