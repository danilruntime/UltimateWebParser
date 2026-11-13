"""Tests for the event delivery loop that pushes notifications to admins."""
import asyncio
from datetime import timedelta

import pytest

from uwp.bot.delivery import EventDelivery
from uwp.config import load_settings
from uwp.db.database import Database
from uwp.db.repo.admins import AdminsRepo
from uwp.db.repo.events import EventsRepo
from uwp.events import EventBus
from uwp.timeutil import utcnow


class FakeBot:
    def __init__(self, fail_documents: bool = False):
        self.documents = []
        self.messages = []
        self.photos = []
        self.fail_documents = fail_documents

    async def send_document(self, chat_id, document, caption=None, **kw):
        if self.fail_documents:
            raise RuntimeError("telegram is down")
        self.documents.append((chat_id, str(document.path), caption))
        return True

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        self.messages.append((chat_id, text, reply_markup))
        return True

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None, **kw):
        self.photos.append((chat_id, str(photo.path), caption, reply_markup))
        return True


@pytest.fixture
async def env(tmp_path):
    db = await Database.in_memory()
    await db.migrate()
    settings = load_settings(
        {"UWP_DATA_DIR": str(tmp_path), "UWP_ADMIN_IDS": "111,222"}
    )
    bus = EventBus(EventsRepo(db), AdminsRepo(db), settings)
    yield settings, bus, tmp_path
    await db.close()


async def test_delivers_a_report_once(env):
    settings, bus, tmp_path = env
    report = tmp_path / "report.ods"
    report.write_bytes(b"data")
    bot = FakeBot()
    delivery = EventDelivery(bot, bus, settings)
    await bus.emit("run_finished", payload={"report_path": str(report), "site": "Steam"})

    await delivery.tick()

    assert [d[:2] for d in bot.documents] == [
        (111, str(report)),
        (222, str(report)),
    ]
    assert "Steam" in bot.documents[0][2]
    assert await bus.pending(111, utcnow()) == []


async def test_a_failed_send_is_retried_next_tick(env):
    settings, bus, tmp_path = env
    report = tmp_path / "report.ods"
    report.write_bytes(b"data")
    bot = FakeBot(fail_documents=True)
    delivery = EventDelivery(bot, bus, settings)
    await bus.emit("run_finished", payload={"report_path": str(report), "site": "S"})

    await delivery.tick()
    assert len(await bus.pending(111, utcnow())) == 1

    bot.fail_documents = False
    await delivery.tick()
    assert await bus.pending(111, utcnow()) == []


async def test_text_events_are_sent_as_messages(env):
    settings, bus, _tmp_path = env
    bot = FakeBot()
    delivery = EventDelivery(bot, bus, settings)
    await bus.emit("vnc_ready", payload={"url": "http://x:6000/vnc.html", "password": "pw"})
    await bus.emit("site_problem", payload={"text": "сайт отдаёт 403"})

    await delivery.tick()

    joined = "\n".join(text for _, text, *_ in bot.messages)
    assert "http://x:6000/vnc.html" in joined
    assert "403" in joined


async def test_unknown_kind_is_dropped_not_retried(env):
    settings, bus, _tmp_path = env
    bot = FakeBot()
    delivery = EventDelivery(bot, bus, settings)
    event_id = await bus.emit("something_new", payload={})

    await delivery.tick()

    # Marked delivered for everyone: never retried, later removed by the purge.
    assert await bus.pending(111, utcnow()) == []
    assert await bus.pending(222, utcnow()) == []
    assert event_id > 0


async def test_run_failed_carries_a_retry_button(env):
    settings, bus, _tmp_path = env
    bot = FakeBot()
    delivery = EventDelivery(bot, bus, settings)
    await bus.emit("run_failed", site_id=None, payload={"text": "boom"})

    await delivery.tick()

    assert "boom" in bot.messages[0][1]


async def test_failure_sends_the_screenshot_and_a_retry_button(env):
    settings, bus, tmp_path = env
    shot = tmp_path / "screenshot.png"
    shot.write_bytes(b"png")
    bot = FakeBot()
    delivery = EventDelivery(bot, bus, settings)
    await bus.emit(
        "run_failed",
        payload={"text": "капча", "screenshot": str(shot), "site": "Steam", "site_id": 5},
    )

    await delivery.tick()

    assert bot.photos and bot.photos[0][1] == str(shot)
    caption, markup = bot.photos[0][2], bot.photos[0][3]
    assert "Steam" in caption
    assert markup.inline_keyboard[0][0].callback_data == "run:retry:5"
    # The full error follows as text, since a caption is too short for it.
    assert any("капча" in text for _, text, *_ in bot.messages)


async def test_a_broken_screenshot_falls_back_to_text(env):
    settings, bus, tmp_path = env
    shot = tmp_path / "screenshot.png"
    shot.write_bytes(b"png")
    bot = FakeBot()
    delivery = EventDelivery(bot, bus, settings)
    await bus.emit("run_failed", payload={"text": "boom", "screenshot": str(shot)})

    async def broken_photo(*_a, **_kw):
        raise RuntimeError("photo rejected")

    bot.send_photo = broken_photo

    await delivery.tick()

    assert any("boom" in text for _, text, *_ in bot.messages)
    assert await bus.pending(111, utcnow()) == []


async def test_run_forever_stops(env):
    settings, bus, _tmp_path = env
    bot = FakeBot()
    delivery = EventDelivery(bot, bus, settings)
    stop = asyncio.Event()

    task = asyncio.create_task(delivery.run(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=5)


async def test_expired_events_are_purged(env):
    settings, bus, _tmp_path = env
    bot = FakeBot()
    delivery = EventDelivery(bot, bus, settings)
    await bus.emit("site_problem", payload={"text": "old"})

    await delivery.tick(now=utcnow() + timedelta(days=5))

    assert await bus.pending(111, utcnow() + timedelta(days=5)) == []


async def test_a_failed_export_is_reported_not_silenced(env):
    """The run was fine, the file was not: the admin must hear why."""
    settings, bus, _tmp_path = env
    bot = FakeBot()
    delivery = EventDelivery(bot, bus, settings)
    await bus.emit(
        "run_finished",
        payload={
            "site": "Steam",
            "rows": 10,
            "new": 2,
            "report_status": "failed",
            "report_error": "OSError: no space left on device",
        },
    )

    await delivery.tick()

    joined = "\n".join(text for _, text, *_ in bot.messages)
    assert "Отчёт не создан" in joined
    assert "no space left" in joined
    assert bot.documents == []


async def test_a_partial_run_says_so_and_lists_the_notes(env):
    settings, bus, tmp_path = env
    report = tmp_path / "report.ods"
    report.write_bytes(b"data")
    bot = FakeBot()
    delivery = EventDelivery(bot, bus, settings)
    await bus.emit(
        "run_finished",
        payload={
            "site": "Steam",
            "rows": 7,
            "new": 1,
            "status": "partial",
            "notes": "страница 2 не открылась",
            "report_path": str(report),
        },
    )

    await delivery.tick()

    caption = bot.documents[0][2]
    assert "частичный" in caption
    assert "страница 2 не открылась" in caption


async def test_only_recipients_get_the_report(env):
    """A muted admin keeps bot access but must not be pinged."""
    settings, bus, tmp_path = env
    report = tmp_path / "report.ods"
    report.write_bytes(b"data")
    bot = FakeBot()
    delivery = EventDelivery(bot, bus, settings, recipients=[111])
    await bus.emit("run_finished", payload={"report_path": str(report), "site": "S"})

    await delivery.tick()

    assert [d[0] for d in bot.documents] == [111]


async def test_a_login_wall_offers_the_profile_button(env):
    settings, bus, _tmp_path = env
    bot = FakeBot()
    delivery = EventDelivery(bot, bus, settings)
    await bus.emit(
        "run_failed",
        payload={
            "text": "нужен вход",
            "error_kind": "login_required",
            "profile_id": 7,
            "site": "Steam",
            "site_id": 3,
        },
    )

    await delivery.tick()

    buttons = bot.messages[0][2].inline_keyboard[0]
    assert [b.callback_data for b in buttons] == ["profile:login:7", "run:retry:3"]


async def test_a_chat_not_found_is_dropped_not_retried_forever(env):
    """A stale admin id used to flood the log every 5 s for the whole event TTL."""
    from aiogram.exceptions import TelegramBadRequest

    settings, bus, tmp_path = env
    report = tmp_path / "report.ods"
    report.write_bytes(b"data")
    bot = FakeBot()

    async def bad_chat(chat_id, document, caption=None, **kw):
        raise TelegramBadRequest(
            method=None, message="Telegram server says - Bad Request: chat not found"
        )

    bot.send_document = bad_chat
    delivery = EventDelivery(bot, bus, settings)
    await bus.emit("run_finished", payload={"report_path": str(report), "site": "S"})

    await delivery.tick()

    assert await bus.pending(111, utcnow()) == []
    assert await bus.pending(222, utcnow()) == []


async def test_a_network_error_is_still_retried(env):
    settings, bus, tmp_path = env
    report = tmp_path / "report.ods"
    report.write_bytes(b"data")
    bot = FakeBot(fail_documents=True)
    delivery = EventDelivery(bot, bus, settings)
    await bus.emit("run_finished", payload={"report_path": str(report), "site": "S"})

    await delivery.tick()

    assert len(await bus.pending(111, utcnow())) == 1
