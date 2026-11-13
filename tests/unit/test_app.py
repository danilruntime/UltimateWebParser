"""Tests for the runtime wiring shared by the bot and the worker."""
import pytest

from uwp.app import build_runtime
from uwp.config import load_settings


@pytest.fixture
async def runtime(tmp_path):
    rt = await build_runtime(
        load_settings({"UWP_DATA_DIR": str(tmp_path)}), with_bot=False
    )
    yield rt
    await rt.close()


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
