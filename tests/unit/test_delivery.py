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
        self.document_attempts = 0
        # Per-chat failures, for tests that need one admin to succeed and one
        # to fail: ``fail_documents`` fails every chat.
        self.fail_for: set[int] = set()

    async def send_document(self, chat_id, document, caption=None, **kw):
        self.document_attempts += 1
        if self.fail_documents or chat_id in self.fail_for:
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
    t0 = utcnow()

    await delivery.tick(t0)
    assert len(await bus.pending(111, utcnow())) == 1

    bot.fail_documents = False
    await delivery.tick(t0 + timedelta(seconds=10))
    assert await bus.pending(111, utcnow()) == []


async def test_a_failed_send_is_not_retried_on_the_next_5s_tick(env):
    """A Telegram outage must not turn one event into 50 000 requests."""
    settings, bus, tmp_path = env
    report = tmp_path / "report.ods"
    report.write_bytes(b"data")
    bot = FakeBot(fail_documents=True)
    delivery = EventDelivery(bot, bus, settings)
    await bus.emit("run_finished", payload={"report_path": str(report), "site": "S"})
    t0 = utcnow()

    await delivery.tick(t0)
    await delivery.tick(t0 + timedelta(seconds=5))

    assert bot.document_attempts == 2  # two admins, one attempt each
    assert len(await bus.pending(111, utcnow())) == 1


async def test_the_retry_backoff_grows_with_each_failure(env):
    settings, bus, tmp_path = env
    report = tmp_path / "report.ods"
    report.write_bytes(b"data")
    bot = FakeBot(fail_documents=True)
    delivery = EventDelivery(bot, bus, settings, recipients=[111])
    await bus.emit("run_finished", payload={"report_path": str(report), "site": "S"})
    t0 = utcnow()

    await delivery.tick(t0)  # failure 1: next attempt at +10 s
    await delivery.tick(t0 + timedelta(seconds=10))  # failure 2: at +20 s
    await delivery.tick(t0 + timedelta(seconds=30))  # failure 3: at +40 s
    attempts = bot.document_attempts

    await delivery.tick(t0 + timedelta(seconds=60))  # 30 s after the third: too early
    assert bot.document_attempts == attempts

    await delivery.tick(t0 + timedelta(seconds=70))  # 40 s after the third: due
    assert bot.document_attempts > attempts


async def test_the_retry_backoff_stops_at_one_minute(env):
    settings, bus, tmp_path = env
    report = tmp_path / "report.ods"
    report.write_bytes(b"data")
    bot = FakeBot(fail_documents=True)
    delivery = EventDelivery(bot, bus, settings, recipients=[111])
    await bus.emit("run_finished", payload={"report_path": str(report), "site": "S"})
    t0 = utcnow()

    # Delays are 10/20/40 then the 60 s cap; attempts happen at 0, 10, 30, 70, 130.
    # Without the cap the fifth wait would be 80 s and fire at +150.
    for offset in (0, 10, 30, 70, 130):
        await delivery.tick(t0 + timedelta(seconds=offset))
    attempts = bot.document_attempts

    await delivery.tick(t0 + timedelta(seconds=189))  # 59 s after the fifth
    assert bot.document_attempts == attempts

    await delivery.tick(t0 + timedelta(seconds=190))  # 60 s: capped, not 80
    assert bot.document_attempts > attempts


async def test_a_successful_send_leaves_no_backoff_key(env):
    """A delivered event must not keep a counter: the dict must not grow forever."""
    settings, bus, tmp_path = env
    report = tmp_path / "report.ods"
    report.write_bytes(b"data")
    bot = FakeBot()
    bot.fail_for = {111}
    delivery = EventDelivery(bot, bus, settings, recipients=[111])
    await bus.emit("run_finished", payload={"report_path": str(report), "site": "S"})
    t0 = utcnow()

    await delivery.tick(t0)  # fails once for 111: a key appears
    assert delivery._retry

    bot.fail_for = set()
    await delivery.tick(t0 + timedelta(seconds=10))  # succeeds

    assert delivery._retry == {}


async def test_purging_forgets_retry_state_of_gone_events(env):
    """Otherwise one entry per purged (admin, event) lives for the whole process."""
    settings, bus, tmp_path = env
    report = tmp_path / "report.ods"
    report.write_bytes(b"data")
    bot = FakeBot()
    bot.fail_for = {111}
    delivery = EventDelivery(bot, bus, settings, recipients=[111])
    await bus.emit("run_finished", payload={"report_path": str(report), "site": "S"})
    t0 = utcnow()

    await delivery.tick(t0)
    assert delivery._retry
    stale = set(delivery._retry)

    # An hour later the TTL has taken the event and the prune runs.
    await delivery.tick(t0 + timedelta(days=settings.event_ttl_days + 1))

    assert stale.isdisjoint(delivery._retry)


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


async def test_html_in_an_error_is_escaped_not_a_parse_failure(env):
    """Measured: Playwright's call log contains ``<launching>``.

    The bot sends with ``parse_mode=HTML``, so Telegram rejected the whole
    ``run_failed`` event with "can't parse entities: Unsupported start tag
    launching" and the admin never learned why the run failed.
    """
    settings, bus, _tmp_path = env
    bot = FakeBot()
    delivery = EventDelivery(bot, bus, settings)
    error = (
        "BrowserType.launch_persistent_context: Failed to create a ProcessSingleton\n"
        "Call log:\n"
        "  - <launching> /opt/uwp/browser/chrome --fingerprint=1\n"
        "  - [pid=938][err] <process did exit: exitCode=21>"
    )
    await bus.emit("run_failed", site_id=None, payload={"text": error})

    await delivery.tick()

    text = bot.messages[0][1]
    assert "&lt;launching&gt;" in text
    assert "<launching>" not in text.replace("&lt;launching&gt;", "")


async def test_a_long_error_is_truncated_to_one_message(env):
    """Telegram rejects a message over 4096 characters; the run error is longer."""
    settings, bus, _tmp_path = env
    bot = FakeBot()
    delivery = EventDelivery(bot, bus, settings)
    await bus.emit("run_failed", site_id=None, payload={"text": "x" * 10_000})

    await delivery.tick()

    assert bot.messages, "a long error must still reach the admin"
    assert len(bot.messages[0][1]) <= 4096


async def test_truncation_never_splits_an_html_entity(env):
    """``&lt;`` cut in half is "can't parse entities" again — the same silence."""
    settings, bus, _tmp_path = env
    bot = FakeBot()
    delivery = EventDelivery(bot, bus, settings)
    await bus.emit("run_failed", site_id=None, payload={"text": "<" * 10_000})

    await delivery.tick()

    text = bot.messages[0][1]
    assert len(text) <= 4096
    assert text.endswith("…")
    body = text[: -1]
    assert body.count("&") == body.count(";"), "every escaped entity must be closed"


async def test_a_report_failure_reason_is_escaped(env):
    """``report_error`` is an exception string too, and can carry markup."""
    settings, bus, _tmp_path = env
    bot = FakeBot()
    delivery = EventDelivery(bot, bus, settings)
    await bus.emit(
        "run_finished",
        payload={"report_status": "failed", "report_error": "<ValueError: bad <tag>>"},
    )

    await delivery.tick()

    assert bot.messages
    assert "<tag>" not in bot.messages[0][1]


async def test_a_network_error_is_still_retried(env):
    settings, bus, tmp_path = env
    report = tmp_path / "report.ods"
    report.write_bytes(b"data")
    bot = FakeBot(fail_documents=True)
    delivery = EventDelivery(bot, bus, settings)
    await bus.emit("run_finished", payload={"report_path": str(report), "site": "S"})

    await delivery.tick()

    assert len(await bus.pending(111, utcnow())) == 1


# --- per-recipient language ------------------------------------------------


async def test_each_admin_gets_the_message_in_their_own_language(env):
    """Two admins, one queue: the same event must not pick one language for both.

    Measured: a Russian admin and an English admin shared the ``admins.language``
    check, and whichever the loop reached first decided the wording.
    """
    settings, bus, _tmp_path = env
    bot = FakeBot()
    spoken = {111: "ru", 222: "en"}

    async def language_for(admin_id):
        return spoken[admin_id]

    delivery = EventDelivery(bot, bus, settings, language_for=language_for)
    await bus.emit("vnc_stopped", payload={})

    await delivery.tick()

    by_admin = {admin_id: text for admin_id, text, *_ in bot.messages}
    assert by_admin[111] == "🖥 Сессия входа завершена."
    assert by_admin[222] == "🖥 Login session finished."


async def test_an_unset_choice_falls_back_to_the_deployment_language(env):
    settings, bus, _tmp_path = env
    bot = FakeBot()

    async def language_for(admin_id):
        return None

    delivery = EventDelivery(bot, bus, settings, language_for=language_for)
    await bus.emit("vnc_stopped", payload={})

    await delivery.tick()

    assert all(text == "🖥 Сессия входа завершена." for _, text, *_ in bot.messages)


async def test_a_switched_language_applies_without_a_restart(env):
    """The resolver is read on every event, not remembered at construction."""
    settings, bus, _tmp_path = env
    bot = FakeBot()
    spoken = {111: "ru"}

    async def language_for(admin_id):
        return spoken.get(admin_id)

    delivery = EventDelivery(
        bot, bus, settings, recipients=[111], language_for=language_for
    )
    await bus.emit("vnc_stopped", payload={})
    await delivery.tick()

    spoken[111] = "en"
    await bus.emit("vnc_stopped", payload={})
    await delivery.tick()

    assert [text for _, text, *_ in bot.messages] == [
        "🖥 Сессия входа завершена.",
        "🖥 Login session finished.",
    ]


async def test_a_failing_language_lookup_still_delivers(env):
    """A locked database must not silence a report: fall back and send it."""
    settings, bus, _tmp_path = env
    bot = FakeBot()

    async def language_for(admin_id):
        raise RuntimeError("database is locked")

    delivery = EventDelivery(bot, bus, settings, language_for=language_for)
    await bus.emit("vnc_stopped", payload={})

    await delivery.tick()

    assert len(bot.messages) == 2
    assert all(text == "🖥 Сессия входа завершена." for _, text, *_ in bot.messages)


async def test_the_failure_buttons_are_localised(env):
    settings, bus, _tmp_path = env
    bot = FakeBot()
    spoken = {111: "en", 222: "ru"}

    async def language_for(admin_id):
        return spoken[admin_id]

    delivery = EventDelivery(bot, bus, settings, language_for=language_for)
    await bus.emit(
        "run_failed",
        payload={"text": "boom", "site_id": 5, "site": "Steam"},
    )

    await delivery.tick()

    buttons = {
        admin_id: [b.text for b in markup.inline_keyboard[0]]
        for admin_id, _text, markup in bot.messages
    }
    assert buttons[111] == ["🔁 Retry"]
    assert buttons[222] == ["🔁 Повторить"]
