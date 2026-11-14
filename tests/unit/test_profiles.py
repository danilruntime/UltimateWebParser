"""Tests for profile identities, browser flags and the profile manager."""
import random
from datetime import timedelta
from pathlib import Path

import pytest

from uwp.config import load_settings
from uwp.db.database import Database
from uwp.db.repo.profiles import ProfilesRepo
from uwp.domain import Profile, ProfileKind, ProfileStatus
from uwp.profiles import (
    MACHINE_PROFILES,
    ProfileManager,
    browser_flags,
    identity_from_profile,
    identity_from_seed,
    random_identity,
)
from uwp.timeutil import utcnow

TIMEZONES = {
    "America/New_York",
    "Europe/London",
    "America/Los_Angeles",
    "Europe/Berlin",
    "Europe/Moscow",
    "Asia/Tokyo",
}


@pytest.fixture
async def db():
    d = await Database.in_memory()
    await d.migrate()
    yield d
    await d.close()


@pytest.fixture
def settings(tmp_path):
    return load_settings({"UWP_DATA_DIR": str(tmp_path)})


@pytest.fixture
def mgr(settings, db):
    return ProfileManager(settings, ProfilesRepo(db))


def test_identity_is_coherent():
    for i in range(50):
        ident = random_identity(random.Random(i))
        assert ident.platform in {"windows", "macos", "linux"}
        assert 1 <= ident.seed <= 2**31 - 1
        assert ident.locale.split("-")[0] in ident.accept_lang
        assert ident.timezone in TIMEZONES


def test_machine_profiles_cover_required_timezones():
    assert {p["timezone"] for p in MACHINE_PROFILES} >= TIMEZONES
    for profile in MACHINE_PROFILES:
        assert profile["platform"] in {"windows", "macos", "linux"}
        assert profile["brand"] in {"Chrome", "Edge", "Opera", "Vivaldi"}
        assert profile["locale"].split("-")[0] in profile["accept_lang"]
        assert profile["weight"] > 0
        assert len(profile["viewport"]) == 2


def test_identity_country_filter():
    ident = random_identity(random.Random(1), country="DE")
    assert ident.timezone == "Europe/Berlin"


def test_identity_country_filter_falls_back_to_full_list():
    ident = random_identity(random.Random(1), country="ZZ")
    assert ident.timezone in TIMEZONES


def test_identity_is_deterministic_for_a_seed():
    assert random_identity(random.Random(3)) == random_identity(random.Random(3))


def test_identity_from_seed_keeps_seed_and_is_stable():
    for i in range(20):
        original = random_identity(random.Random(i))
        rebuilt = identity_from_seed(original.seed)
        assert rebuilt.seed == original.seed
        assert rebuilt == identity_from_seed(original.seed)
        assert rebuilt.timezone in TIMEZONES
        assert rebuilt.locale.split("-")[0] in rebuilt.accept_lang


def test_identity_from_profile_matches_what_was_stored(tmp_path):
    """A profile made for a country must run as that country, not as a rebuild from seed."""
    for country in (None, "DE", "RU"):
        for i in range(50):
            identity = random_identity(random.Random(i), country=country)
            profile = Profile(
                name=f"p-{country}-{i}",
                kind=ProfileKind.persistent,
                seed=identity.seed,
                platform=identity.platform,
                brand=identity.brand,
                brand_version=identity.brand_version,
                timezone=identity.timezone,
                locale=identity.locale,
                user_data_dir=str(tmp_path / f"{country}-{i}"),
            )

            assert identity_from_profile(profile) == identity


def test_identity_from_profile_recovers_accept_lang_and_viewport():
    """The row keeps five machine fields; the other two come from the machine table."""
    profile = Profile(
        name="windows-ru",
        kind=ProfileKind.persistent,
        seed=123,
        platform="windows",
        brand="Chrome",
        brand_version="150",
        timezone="Europe/Moscow",
        locale="ru-RU",
        user_data_dir="/tmp/x",
    )

    identity = identity_from_profile(profile)

    assert identity.accept_lang == "ru-RU,ru"
    assert identity.viewport == (1920, 1080)
    assert identity.seed == 123


def test_identity_from_profile_falls_back_to_seed_for_unknown_machine(tmp_path):
    """A row that matches no machine profile still launches coherently."""
    profile = Profile(
        name="odd",
        kind=ProfileKind.persistent,
        seed=random_identity(random.Random(4)).seed,
        platform="windows",
        brand="Opera",
        brand_version="99",
        timezone="Mars/Olympus",
        locale="xx-XX",
        user_data_dir=str(tmp_path),
    )

    identity = identity_from_profile(profile)

    assert identity == identity_from_seed(profile.seed)


def test_browser_flags_contain_fingerprint_and_proxy(tmp_path):
    ident = random_identity(random.Random(7))
    flags = browser_flags(ident, tmp_path, "http://127.0.0.1:5555", headless=True)
    assert f"--fingerprint={ident.seed}" in flags
    assert f"--fingerprint-platform={ident.platform}" in flags
    assert f"--user-data-dir={tmp_path}" in flags
    assert "--proxy-server=http://127.0.0.1:5555" in flags
    assert "--headless=new" in flags and "--disable-non-proxied-udp" in flags


def test_browser_flags_exact_order(tmp_path):
    ident = random_identity(random.Random(7))
    flags = browser_flags(ident, tmp_path, "http://127.0.0.1:5555", headless=True, no_sandbox=True)
    assert flags == [
        f"--fingerprint={ident.seed}",
        f"--fingerprint-platform={ident.platform}",
        f"--fingerprint-brand={ident.brand}",
        f"--fingerprint-brand-version={ident.brand_version}",
        f"--timezone={ident.timezone}",
        f"--lang={ident.locale}",
        f"--accept-lang={ident.accept_lang}",
        f"--user-data-dir={tmp_path}",
        "--disable-non-proxied-udp",
        "--proxy-server=http://127.0.0.1:5555",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
        "--headless=new",
        "--no-sandbox",
    ]


def test_browser_flags_omit_optional_flags(tmp_path):
    ident = random_identity(random.Random(7))
    flags = browser_flags(ident, tmp_path, None, headless=False)
    assert not any(f.startswith("--proxy-server=") for f in flags)
    assert "--headless=new" not in flags
    assert "--no-sandbox" not in flags


async def test_create_persistent(mgr, settings):
    p = await mgr.create_persistent("Main account")
    assert p.id is not None
    assert p.kind == ProfileKind.persistent
    assert p.status == ProfileStatus.free
    assert p.expires_at is None
    assert Path(p.user_data_dir).is_dir()
    assert Path(p.user_data_dir).parent == settings.profiles_dir
    assert Path(p.user_data_dir).name != "tmp"
    stored = await mgr.repo.get(p.id)
    assert stored is not None and stored.seed == p.seed


async def test_create_persistent_keeps_given_identity(mgr):
    ident = random_identity(random.Random(5))
    p = await mgr.create_persistent("Fixed", identity=ident)
    assert (p.seed, p.platform, p.brand, p.timezone) == (
        ident.seed,
        ident.platform,
        ident.brand,
        ident.timezone,
    )


async def test_clean_profile_folder_removed_after_cleanup(mgr):
    p = await mgr.create_clean(run_id=42)
    assert Path(p.user_data_dir).is_dir()
    await mgr.release(p.id)
    removed = await mgr.cleanup_clean(now=utcnow() + timedelta(days=2))
    assert removed == 1 and not Path(p.user_data_dir).exists()
    assert await mgr.repo.get(p.id) is None


async def test_create_clean_is_locked_one_shot(mgr, settings):
    p = await mgr.create_clean(run_id=7)
    assert p.kind == ProfileKind.clean
    assert p.status == ProfileStatus.locked
    assert Path(p.user_data_dir).parent == settings.profiles_dir / "tmp"
    assert Path(p.user_data_dir).name.startswith("7-")
    assert p.expires_at is not None
    assert timedelta(hours=23) < p.expires_at - utcnow() <= timedelta(hours=24)


async def test_create_clean_twice_for_same_run_is_allowed(mgr):
    first = await mgr.create_clean(run_id=42)
    second = await mgr.create_clean(run_id=42)
    assert first.id != second.id
    assert first.name != second.name
    assert first.user_data_dir != second.user_data_dir
    assert Path(first.user_data_dir).is_dir() and Path(second.user_data_dir).is_dir()


async def test_create_clean_removes_folder_when_insert_fails(mgr, settings, monkeypatch):
    async def boom(profile):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(mgr.repo, "create", boom)
    with pytest.raises(RuntimeError):
        await mgr.create_clean(run_id=43)
    assert list((settings.profiles_dir / "tmp").iterdir()) == []


async def test_create_persistent_removes_folder_when_insert_fails(mgr, settings, monkeypatch):
    async def boom(profile):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(mgr.repo, "create", boom)
    with pytest.raises(RuntimeError):
        await mgr.create_persistent("P")
    assert list(settings.profiles_dir.iterdir()) == []


async def test_release_clean_marks_expired_and_keeps_folder(mgr):
    p = await mgr.create_clean(run_id=8)
    await mgr.release(p.id)
    stored = await mgr.repo.get(p.id)
    assert stored is not None
    assert stored.status == ProfileStatus.expired
    assert Path(p.user_data_dir).is_dir()


async def test_release_persistent_marks_free(mgr):
    p = await mgr.create_persistent("P")
    assert await mgr.lease(p.id, ttl_seconds=60)
    await mgr.release(p.id)
    stored = await mgr.repo.get(p.id)
    assert stored is not None
    assert stored.status == ProfileStatus.free
    assert stored.lease_until is None


async def test_cleanup_keeps_unexpired_clean_profiles(mgr):
    p = await mgr.create_clean(run_id=9)
    await mgr.release(p.id)
    assert await mgr.cleanup_clean(now=utcnow()) == 0
    assert Path(p.user_data_dir).is_dir()
    assert await mgr.repo.get(p.id) is not None


async def test_lease_sets_lease_until(mgr):
    p = await mgr.create_persistent("P")
    before = utcnow()
    assert await mgr.lease(p.id, ttl_seconds=120) is True
    stored = await mgr.repo.get(p.id)
    assert stored is not None
    assert stored.status == ProfileStatus.locked
    assert stored.lease_until is not None
    assert before + timedelta(seconds=119) <= stored.lease_until <= utcnow() + timedelta(seconds=120)
    assert await mgr.lease(p.id, ttl_seconds=120) is False


async def test_delete_removes_persistent_profile(mgr):
    p = await mgr.create_persistent("P")
    await mgr.delete(p.id)
    assert await mgr.repo.get(p.id) is None
    assert not Path(p.user_data_dir).exists()


async def test_delete_refuses_locked_persistent_profile(mgr):
    p = await mgr.create_persistent("P")
    assert await mgr.lease(p.id, ttl_seconds=60)
    with pytest.raises(ValueError):
        await mgr.delete(p.id)
    assert Path(p.user_data_dir).is_dir()
    assert await mgr.repo.get(p.id) is not None


async def test_delete_missing_profile_is_noop(mgr):
    await mgr.delete(999)
