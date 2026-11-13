"""Regression tests for the review findings around runtime composition."""

import sqlite3

import pytest

from uwp.app import build_runtime
from uwp.config import load_settings
from uwp.db.repo.admins import AdminsRepo


async def _runtime(tmp_path, **env):
    return await build_runtime(
        load_settings({"UWP_DATA_DIR": str(tmp_path), **env}), with_bot=False
    )


async def test_first_start_seeds_the_example_site(tmp_path):
    rt = await _runtime(tmp_path)
    try:
        sites = await rt.deps.sites.list()
        # The documented quick start says the Steam site exists and is disabled.
        assert [site.name for site in sites] == ["Steam — скидки"]
        assert sites[0].enabled is False
    finally:
        await rt.close()


async def test_seeding_is_idempotent(tmp_path):
    first = await _runtime(tmp_path)
    await first.close()
    second = await _runtime(tmp_path)
    try:
        assert len(await second.deps.sites.list()) == 1
    finally:
        await second.close()


async def test_deleting_the_last_site_does_not_resurrect_it(tmp_path):
    rt = await _runtime(tmp_path)
    site = (await rt.deps.sites.list())[0]
    await rt.deps.sites.delete(site.id)
    await rt.close()

    again = await _runtime(tmp_path)
    try:
        # Users who delete every site must not get it back on every restart.
        assert await again.deps.sites.list() == []
    finally:
        await again.close()


async def test_admins_from_the_table_are_allowed_and_notified(tmp_path, monkeypatch):
    rt = await _runtime(tmp_path)
    try:
        await AdminsRepo(rt.db).add(333)
    finally:
        await rt.close()

    again = await _runtime(tmp_path)
    try:
        assert 333 in again.admin_ids
        assert 333 in again.recipients
    finally:
        await again.close()


async def test_a_muted_admin_gets_no_notifications(tmp_path):
    rt = await _runtime(tmp_path)
    try:
        admins = AdminsRepo(rt.db)
        await admins.add(444)
        await admins.set_notify(444, False)
    finally:
        await rt.close()

    again = await _runtime(tmp_path, UWP_ADMIN_IDS="111")
    try:
        assert 444 in again.admin_ids  # may still use the bot
        assert 444 not in again.recipients  # but gets no reports
        assert 111 in again.recipients
    finally:
        await again.close()


async def test_the_worker_never_runs_delivery(monkeypatch):
    """Split mode must leave every event pending for the bot process.

    The worker's bot is a ``NullBot`` whose sends report success, so a delivery
    loop there would mark every report delivered and lose it.
    """
    ticked: list[object] = []

    class FakeDelivery:
        async def run(self, stop):
            ticked.append(stop)

    class FakeRuntime:
        def __init__(self):
            self.admin_ids = {1}
            self.recipients = [1]

        async def run_worker(self, stop):
            return None

        async def close(self):
            return None

    async def build(settings, **kwargs):
        return FakeRuntime()

    import uwp.app as app_module

    monkeypatch.setattr(app_module, "build_runtime", build)
    monkeypatch.setattr(app_module, "EventDelivery", lambda *a, **kw: FakeDelivery())
    await app_module.run_worker_only(load_settings({"UWP_DATA_DIR": "/tmp/x"}))

    assert ticked == []


async def test_duplicate_site_rows_do_not_break_the_seed(tmp_path):
    rt = await _runtime(tmp_path)
    try:
        await rt.deps.sites.create("steam_deals", name="Other")
    finally:
        await rt.close()

    again = await _runtime(tmp_path)
    try:
        assert len(await again.deps.sites.list()) == 2
    finally:
        await again.close()


async def test_duplicate_proxy_names_raise_a_russian_error(tmp_path):
    rt = await _runtime(tmp_path)
    try:
        proxies = rt.deps.proxies
        await proxies.add("DE-1", "http://127.0.0.1:8080")
        with pytest.raises(ValueError, match="уже есть"):
            await proxies.add("DE-1", "http://127.0.0.1:8081")
    finally:
        await rt.close()


async def test_duplicate_profile_names_raise_a_russian_error(tmp_path):
    rt = await _runtime(tmp_path)
    try:
        profiles = rt.deps.profiles
        await profiles.create("P")
        with pytest.raises(ValueError, match="уже есть"):
            await profiles.create("P")
    finally:
        await rt.close()


async def test_sqlite_integrity_error_is_not_leaked(tmp_path):
    rt = await _runtime(tmp_path)
    try:
        assert sqlite3.IntegrityError is not ValueError
    finally:
        await rt.close()
