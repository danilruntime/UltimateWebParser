"""Background delivery of queued events to the admins' Telegram chats."""

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from uwp.bot.texts import esc as _esc
from uwp.config import Settings
from uwp.domain import Event
from uwp.events import (
    RUN_FAILED,
    RUN_FINISHED,
    SITE_PROBLEM,
    VNC_FAILED,
    VNC_READY,
    VNC_STOPPED,
    EventBus,
)
from uwp.timeutil import utcnow

log = logging.getLogger(__name__)

POLL_SECONDS = 5
PURGE_EVERY_SECONDS = 3600
# Telegram's hard limit for one message, measured after entity parsing.
MAX_MESSAGE_LENGTH = 4096
# Same limit for a media caption; a photo with a longer caption is refused.
CAPTION_LENGTH = 1024
# A failed send waits longer each time: 10 s, 20 s, 40 s … up to a minute.
# Without this a transient outage retries one event every 5 s for the whole TTL,
# which is ~50 000 requests for a single report.
RETRY_BASE_SECONDS = 10
RETRY_MAX_SECONDS = 60

_KNOWN = {RUN_FINISHED, RUN_FAILED, SITE_PROBLEM, VNC_READY, VNC_STOPPED, VNC_FAILED}


class EventDelivery:
    """Polls the event queue and turns each event into one message per admin.

    A failed send is logged and left pending, so the next tick retries it: a
    Telegram outage must never lose a report.
    """

    def __init__(
        self,
        bot: Any,
        bus: EventBus,
        settings: Settings,
        recipients: list[int] | None = None,
    ) -> None:
        self.bot = bot
        self.bus = bus
        self.settings = settings
        # The runtime resolves this as "environment admins ∪ unmuted DB admins".
        self.recipients = list(
            recipients if recipients is not None else settings.admin_ids
        )
        self._last_purge: datetime | None = None
        # (admin_id, event_id) -> (failures so far, earliest next attempt). In
        # memory on purpose: after a restart one immediate attempt is right.
        self._retry: dict[tuple[int, int], tuple[int, datetime]] = {}

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.tick()
            except Exception:
                log.exception("event delivery iteration failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)
            except TimeoutError:
                pass

    async def tick(self, now: datetime | None = None) -> None:
        """Deliver everything pending; also purge expired events hourly."""
        now = now or utcnow()
        if (
            self._last_purge is None
            or (now - self._last_purge).total_seconds() >= PURGE_EVERY_SECONDS
        ):
            await self.bus.purge(now)
            await self._prune_retries()
            self._last_purge = now
        for admin_id in self.recipients:
            for event in await self.bus.pending(admin_id, now):
                key = (admin_id, event.id if event.id is not None else -1)
                if not self._is_due(key, now):
                    continue
                try:
                    await self._deliver(admin_id, event)
                except Exception as exc:
                    if _is_permanent(exc):
                        # Telegram itself refused this chat: "chat not found",
                        # the bot was blocked, the user is deactivated. Retrying
                        # cannot help, and a 3-day TTL of retries turns one stale
                        # admin id into a log flood.
                        log.warning(
                            "dropping event %s for %s: %s", event.id, admin_id, exc
                        )
                        self._retry.pop(key, None)
                        await self.bus.mark_delivered(event.id, admin_id)
                        continue
                    log.exception(
                        "delivering event %s to %s failed", event.id, admin_id
                    )
                    self._schedule_retry(key, now)
                    continue
                self._retry.pop(key, None)
                await self.bus.mark_delivered(event.id, admin_id)

    async def _prune_retries(self) -> None:
        """Forget keys of events that no longer exist.

        Purged events would otherwise leave one entry per (admin, event) for the
        life of the process: only a delivered or permanently-failed event pops
        its own key, and a purged one never gets either.
        """
        if not self._retry:
            return
        live = await self.bus.live_ids()
        for key in [key for key in self._retry if key[1] not in live]:
            del self._retry[key]

    def _is_due(self, key: tuple[int, int], now: datetime) -> bool:
        """Has the backoff of this (admin, event) pair run out?"""
        retry = self._retry.get(key)
        return retry is None or now >= retry[1]

    def _schedule_retry(self, key: tuple[int, int], now: datetime) -> None:
        failures = self._retry.get(key, (0, now))[0] + 1
        delay = min(RETRY_BASE_SECONDS * 2 ** (failures - 1), RETRY_MAX_SECONDS)
        self._retry[key] = (failures, now + timedelta(seconds=delay))

    async def _deliver(self, admin_id: int, event: Event) -> None:
        if event.kind not in _KNOWN:
            log.warning("dropping event %s of unknown kind %r", event.id, event.kind)
            await self._drop(event)
            return
        if event.kind == RUN_FINISHED:
            await self._send_report(admin_id, event)
            return
        if event.kind == RUN_FAILED:
            await self._send_failure(admin_id, event)
            return
        await self.bot.send_message(admin_id, _text_for(event))

    async def _drop(self, event: Event) -> None:
        for admin_id in self.recipients:
            await self.bus.mark_delivered(event.id, admin_id)

    async def _send_report(self, admin_id: int, event: Event) -> None:
        payload = event.payload or {}
        path = payload.get("report_path")
        caption = _caption_for(event)
        if path and Path(path).is_file():
            await self.bot.send_document(admin_id, FSInputFile(path), caption=caption)
            return
        if payload.get("report_status") == "failed":
            # The run was fine but the export was not: say so, do not pretend.
            reason = _esc(payload.get("report_error") or "неизвестная ошибка")
            await self.bot.send_message(
                admin_id, _limit(f"{caption}\n⚠️ Отчёт не создан: {reason}")
            )
            return
        log.warning("run %s has no report file (%s)", event.run_id, path)
        await self.bot.send_message(admin_id, caption)

    async def _send_failure(self, admin_id: int, event: Event) -> None:
        """Text plus the run's screenshot, with action buttons when possible."""
        payload = event.payload or {}
        buttons = []
        if payload.get("site_id") is not None:
            buttons.append(
                InlineKeyboardButton(
                    text="🔁 Повторить", callback_data=f"run:retry:{payload['site_id']}"
                )
            )
        # A login wall is fixed in the profile, not by retrying blindly.
        if payload.get("error_kind") == "login_required" and payload.get("profile_id"):
            buttons.insert(
                0,
                InlineKeyboardButton(
                    text="🖥 Войти в профиль",
                    callback_data=f"profile:login:{payload['profile_id']}",
                ),
            )
        markup = InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None
        screenshot = payload.get("screenshot")
        if screenshot and Path(screenshot).is_file():
            try:
                await self.bot.send_photo(
                    admin_id,
                    FSInputFile(screenshot),
                    caption=_caption_for(event),
                    reply_markup=markup,
                )
                # The caption is short, so the full error follows as text.
                await self.bot.send_message(
                    admin_id, _text_for(event), reply_markup=markup
                )
                return
            except Exception:
                log.exception("sending the screenshot of event %s failed", event.id)
        await self.bot.send_message(admin_id, _text_for(event), reply_markup=markup)


def _is_permanent(exc: Exception) -> bool:
    """Is this Telegram error impossible to fix by retrying the same chat?

    A network blip or a 5xx must be retried; a chat the bot cannot write to
    must not. ``retry_after`` is a flood control limit, not a bad chat.
    """
    from aiogram.exceptions import (
        TelegramBadRequest,
        TelegramForbiddenError,
        TelegramNotFound,
    )

    if isinstance(exc, TelegramBadRequest):
        message = str(exc).lower()
        return any(
            marker in message
            for marker in (
                "chat not found",
                "chat_id is empty",
                "user is deactivated",
                "bot was blocked by the user",
                "bot can't initiate conversation",
            )
        )
    return isinstance(exc, (TelegramForbiddenError, TelegramNotFound))


def _caption_for(event: Event) -> str:
    payload = event.payload or {}
    site = payload.get("site") or payload.get("site_name") or "сайт"
    if event.kind == RUN_FINISHED:
        rows = payload.get("rows", 0)
        new = payload.get("new", 0)
        head = "⚠️ Прогон частичный" if payload.get("status") == "partial" else "📊"
        text = f"{head} {_esc(site)}\nСтрок: {rows}, новых: {new}"
        if payload.get("notes"):
            text += f"\n{_esc(payload['notes'])}"
        if payload.get("report_status") == "failed":
            text += "\n⚠️ Отчёт не создан"
        return _limit(text, CAPTION_LENGTH)
    if event.kind == RUN_FAILED:
        reason = _esc(payload.get("text") or payload.get("error") or "неизвестная ошибка")
        return _limit(f"❌ {_esc(site)}: {reason}")
    return f"{_esc(site)}"


def _limit(text: str, limit: int = MAX_MESSAGE_LENGTH) -> str:
    """Keep a message inside Telegram's hard limit.

    ``text`` is measured after entity parsing, so escaping makes an already
    long error longer. Truncating is better than a message Telegram refuses —
    but the cut must not land inside an entity: ``&lt;`` sliced to ``&l`` is
    rejected exactly like the raw tag was.
    """
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    head, sep, tail = cut.rpartition("&")
    if sep and ";" not in tail:
        cut = head
    return cut + "…"


def _text_for(event: Event) -> str:
    payload = event.payload or {}
    site = payload.get("site") or payload.get("site_name")
    if event.kind == RUN_FAILED:
        reason = _esc(payload.get("text") or payload.get("error") or "неизвестная ошибка")
        return _limit(f"❌ Прогон {_esc('«' + site + '» ') if site else ''}не удался: {reason}")
    if event.kind == SITE_PROBLEM:
        return _limit(
            f"⚠️ Проблема с сайтом{_esc(' «' + site + '»') if site else ''}: "
            + _esc(payload.get("text") or payload.get("error") or "—")
        )
    if event.kind == VNC_READY:
        return _limit(
            "🖥 Вход на сайт готов\n"
            f"Ссылка: {_esc(payload.get('url', '—'))}\n"
            f"Пароль: {_esc(payload.get('password', '—'))}"
        )
    if event.kind == VNC_STOPPED:
        return "🖥 Сессия входа завершена."
    if event.kind == VNC_FAILED:
        return _limit(
            "🖥 Не удалось запустить сессию входа: "
            + _esc(payload.get("error") or "—")
        )
    return f"Уведомление: {event.kind}"
