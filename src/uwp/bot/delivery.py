"""Background delivery of queued events to the admins' Telegram chats."""
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

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

_KNOWN = {RUN_FINISHED, RUN_FAILED, SITE_PROBLEM, VNC_READY, VNC_STOPPED, VNC_FAILED}


class EventDelivery:
    """Polls the event queue and turns each event into one message per admin.

    A failed send is logged and left pending, so the next tick retries it: a
    Telegram outage must never lose a report.
    """

    def __init__(self, bot: Any, bus: EventBus, settings: Settings) -> None:
        self.bot = bot
        self.bus = bus
        self.settings = settings
        self._last_purge: datetime | None = None

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
        if self._last_purge is None or (now - self._last_purge).total_seconds() >= PURGE_EVERY_SECONDS:
            await self.bus.purge(now)
            self._last_purge = now
        for admin_id in self.settings.admin_ids:
            for event in await self.bus.pending(admin_id, now):
                try:
                    await self._deliver(admin_id, event)
                except Exception:
                    log.exception("delivering event %s to %s failed", event.id, admin_id)
                    continue
                await self.bus.mark_delivered(event.id, admin_id)

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
        for admin_id in self.settings.admin_ids:
            await self.bus.mark_delivered(event.id, admin_id)

    async def _send_report(self, admin_id: int, event: Event) -> None:
        path = (event.payload or {}).get("report_path")
        caption = _caption_for(event)
        if path and Path(path).is_file():
            await self.bot.send_document(
                admin_id, FSInputFile(path), caption=caption
            )
            return
        log.warning("run %s has no report file (%s)", event.run_id, path)
        await self.bot.send_message(admin_id, caption)

    async def _send_failure(self, admin_id: int, event: Event) -> None:
        """Text plus the run's screenshot, with a «Повторить» button when possible."""
        payload = event.payload or {}
        markup = None
        if payload.get("site_id") is not None:
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🔁 Повторить",
                            callback_data=f"run:retry:{payload['site_id']}",
                        )
                    ]
                ]
            )
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
                await self.bot.send_message(admin_id, _text_for(event), reply_markup=markup)
                return
            except Exception:
                log.exception("sending the screenshot of event %s failed", event.id)
        await self.bot.send_message(admin_id, _text_for(event), reply_markup=markup)


def _caption_for(event: Event) -> str:
    payload = event.payload or {}
    site = payload.get("site") or payload.get("site_name") or "сайт"
    if event.kind == RUN_FINISHED:
        rows = payload.get("rows", 0)
        new = payload.get("new", 0)
        return f"📊 {site}\nСтрок: {rows}, новых: {new}"
    if event.kind == RUN_FAILED:
        reason = payload.get("text") or payload.get("error") or "неизвестная ошибка"
        return f"❌ {site}: {reason}"
    return f"{site}"


def _text_for(event: Event) -> str:
    payload = event.payload or {}
    site = payload.get("site") or payload.get("site_name")
    if event.kind == RUN_FAILED:
        reason = payload.get("text") or payload.get("error") or "неизвестная ошибка"
        return f"❌ Прогон {'«' + site + '» ' if site else ''}не удался: {reason}"
    if event.kind == SITE_PROBLEM:
        return f"⚠️ Проблема с сайтом{' «' + site + '»' if site else ''}: " + str(
            payload.get("text") or payload.get("error") or "—"
        )
    if event.kind == VNC_READY:
        return (
            "🖥 Вход на сайт готов\n"
            f"Ссылка: {payload.get('url', '—')}\n"
            f"Пароль: {payload.get('password', '—')}"
        )
    if event.kind == VNC_STOPPED:
        return "🖥 Сессия входа завершена."
    if event.kind == VNC_FAILED:
        return "🖥 Не удалось запустить сессию входа: " + str(payload.get("error") or "—")
    return f"Уведомление: {event.kind}"
