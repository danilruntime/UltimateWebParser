"""Tests for the profile service and the bot keyboards around it."""
import pytest

from uwp.bot.handlers.profiles import profile_card_kb, profiles_kb, proxies_kb
from uwp.bot.keyboards import vnc_session_kb
from uwp.config import load_settings
from uwp.db.database import Database
from uwp.db.repo.profiles import ProfilesRepo
from uwp.profiles import ProfileManager
from uwp.services.profiles import ProfilesService


@pytest.fixture
async def env(tmp_path):
    db = await Database.in_memory()
    await db.migrate()
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path)})
    repo = ProfilesRepo(db)
    yield repo, ProfilesService(ProfileManager(settings, repo), repo, settings), tmp_path
    await db.close()


async def test_create_requires_a_name(env):
    _repo, svc, _tmp = env

    with pytest.raises(ValueError):
        await svc.create("   ")


async def test_list_hides_clean_profiles(env):
    _repo, svc, _tmp = env
    await svc.create("Persistent")
    await svc.manager.create_clean(run_id=1)

    items = await svc.list()

    assert [p.name for p in items] == ["Persistent"]


async def test_create_and_delete(env):
    _repo, svc, _tmp = env
    profile = await svc.create("Phone")
    assert profile.kind == "persistent"

    await svc.delete(profile.id)

    assert await svc.list() == []


async def test_profiles_kb_has_new_and_back(env):
    _repo, svc, _tmp = env
    await svc.create("A")

    flat = [b.callback_data for row in profiles_kb(await svc.list()).inline_keyboard for b in row]

    assert flat[-2:] == ["profile:new", "menu:main"]
    assert flat[0].startswith("profile:")


async def test_profile_card_offers_login_and_delete(env):
    _repo, svc, _tmp = env
    profile = await svc.create("A")

    flat = [b.callback_data for row in profile_card_kb(profile).inline_keyboard for b in row]

    assert f"profile:login:{profile.id}" in flat
    assert f"profile:del:{profile.id}" in flat
    assert "profiles:list" in flat


async def test_release_after_login_does_not_steal_a_leased_profile(env):
    """A stale «Готово» card must not free a profile a run has just leased."""
    _repo, svc, _tmp = env
    profile = await svc.create("A")
    assert await svc.manager.lease(profile.id, ttl_seconds=600)

    await svc.release(profile.id)

    assert (await svc.get(profile.id)).status == "locked"


async def test_release_after_login_frees_a_profile_waiting_for_login(env):
    _repo, svc, _tmp = env
    profile = await svc.create("A")
    await _repo.update(profile.id, status="login")

    await svc.release(profile.id)

    assert (await svc.get(profile.id)).status == "free"


async def test_proxies_kb_and_vnc_kb():
    flat = [b.callback_data for row in proxies_kb([]).inline_keyboard for b in row]
    assert flat == ["proxy:new", "menu:main"]

    flat = [b.callback_data for row in vnc_session_kb(9).inline_keyboard for b in row]
    assert flat == ["vnc:done:9", "vnc:extend:9"]


def test_reply_menu_kb_is_a_two_by_two_keyboard():
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

    from uwp.bot import keyboards

    kb = keyboards.reply_menu_kb()

    assert isinstance(kb, ReplyKeyboardMarkup)
    texts = [b.text for row in kb.keyboard for b in row]
    assert texts == [
        keyboards.BTN_SITES,
        keyboards.BTN_PROFILES,
        keyboards.BTN_PROXIES,
        keyboards.BTN_HELP,
    ]
    assert all(isinstance(b, KeyboardButton) for row in kb.keyboard for b in row)
    assert kb.resize_keyboard is True
