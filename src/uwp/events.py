"""Durable event queue: admins get every notification exactly once."""
import logging
from datetime import datetime, timedelta

from uwp.config import Settings
from uwp.db.repo.admins import AdminsRepo
from uwp.db.repo.events import EventsRepo
from uwp.domain import Event

log = logging.getLogger(__name__)

# Notification kinds understood by the bot.
RUN_FINISHED = "run_finished"
RUN_FAILED = "run_failed"
SITE_PROBLEM = "site_problem"
VNC_READY = "vnc_ready"
VNC_STOPPED = "vnc_stopped"
VNC_FAILED = "vnc_failed"


class EventBus:
    """Thin facade over ``EventsRepo`` that applies the TTL from settings.

    Delivery is per admin: an event stays pending for an admin until they are
    marked as delivered, so a restart never loses a notification.
    """

    def __init__(self, events: EventsRepo, admins: AdminsRepo, settings: Settings) -> None:
        self.events = events
        self.admins = admins
        self.settings = settings

    async def emit(
        self,
        kind: str,
        site_id: int | None = None,
        run_id: int | None = None,
        payload: dict | None = None,
    ) -> int:
        """Persist an event and return its id."""
        return await self.events.emit(kind, site_id, run_id, payload or {})

    async def pending(self, admin_id: int, now: datetime) -> list[Event]:
        """Events not yet delivered to ``admin_id`` and inside the TTL."""
        return await self.events.pending(admin_id, now, self.settings.event_ttl_days)

    async def mark_delivered(self, event_id: int, admin_id: int) -> None:
        await self.events.mark_delivered(event_id, admin_id)

    async def purge(self, now: datetime) -> int:
        """Drop events older than the TTL; returns the number removed."""
        cutoff = now - timedelta(days=self.settings.event_ttl_days)
        removed = await self.events.purge(cutoff)
        if removed:
            log.info("purged %s expired events", removed)
        return removed

    async def live_ids(self) -> set[int]:
        """Ids of the events still stored; used to prune in-memory bookkeeping."""
        rows = await self.events.list_ids()
        return set(rows)
