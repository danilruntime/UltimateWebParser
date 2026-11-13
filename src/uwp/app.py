"""Runtime composition: wire settings, database, repos, services and processes.

The bot and the worker share one SQLite database. In ``combined`` mode they run
in the same process (the default); the split mode runs only the half a process
needs.
"""
import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any

from uwp.bot.app import BotDeps, build_dispatcher, make_bot
from uwp.bot.delivery import EventDelivery
from uwp.config import Settings
from uwp.db.database import Database
from uwp.db.repo.admins import AdminsRepo
from uwp.db.repo.events import EventsRepo
from uwp.db.repo.items import ItemsRepo
from uwp.db.repo.jobs import JobsRepo
from uwp.db.repo.profiles import ProfilesRepo
from uwp.db.repo.proxies import ProxiesRepo
from uwp.db.repo.runs import RunsRepo
from uwp.db.repo.sites import SitesRepo
from uwp.db.repo.vnc import VncRepo
from uwp.events import EventBus
from uwp.parsers.registry import ParserRegistry
from uwp.profiles import ProfileManager
from uwp.runner import Runner
from uwp.scheduler import Scheduler
from uwp.seed import is_fresh, seed
from uwp.services.profiles import ProfilesService
from uwp.services.proxies import ProxyService
from uwp.services.runs import RunsService
from uwp.services.sites import SiteService
from uwp.services.vnc import VncService
from uwp.timeutil import utcnow
from uwp.vnc import VncManager
from uwp.worker import Worker

log = logging.getLogger(__name__)

WORKER_ID = "worker-1"
# Short enough that a login requested in the bot's process (split mode) turns
# into a live browser while the admin is still reading the message.
MAINTENANCE_SECONDS = 5


class NullBot:
    """Stand-in for a process that has no Telegram bot of its own."""

    async def send_message(self, *args, **kwargs) -> bool:
        return True

    async def send_document(self, *args, **kwargs) -> bool:
        return True


@dataclass
class Runtime:
    """Everything one process needs, already wired."""

    settings: Settings
    db: Database
    worker: Worker
    deps: BotDeps
    delivery: EventDelivery
    registry: ParserRegistry
    vnc: VncManager
    bot: Any = None
    # Telegram ids allowed to use the bot, and those who also get notifications.
    admin_ids: set[int] = field(default_factory=set)
    recipients: list[int] = field(default_factory=list)
    closeables: list[Any] = field(default_factory=list)

    async def close(self) -> None:
        for close in reversed(self.closeables):
            with contextlib.suppress(Exception):
                await close()
        if self.bot is not None and hasattr(self.bot, "session"):
            with contextlib.suppress(Exception):
                await self.bot.session.close()
        await self.db.close()

    async def run_worker(self, stop: asyncio.Event) -> None:
        """Worker loop plus the VNC TTL sweep the worker process owns."""
        maintenance = asyncio.create_task(self._maintain(stop))
        try:
            await self.worker.run_forever(stop)
        finally:
            maintenance.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await maintenance

    async def run_once(self) -> None:
        """One worker iteration, for ``uwp run --once`` and smoke tests."""
        now = utcnow()
        await self.worker.recover(now)
        await self.worker.scheduler.tick(now)
        await self.worker.process_one(now)
        await self.vnc.tick(now)

    async def _maintain(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.vnc.tick(utcnow())
            except Exception:
                log.exception("VNC maintenance tick failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=MAINTENANCE_SECONDS)
            except TimeoutError:
                pass


async def build_runtime(
    settings: Settings, *, with_bot: bool = True, eager_vnc: bool = True
) -> Runtime:
    """Open the database, migrate it and construct every collaborator."""
    # Fail before touching the database: a missing token must not leave an open
    # connection (and its worker thread) behind.
    bot = make_bot(settings) if with_bot else NullBot()
    db = Database(settings.db_path)
    await db.connect()
    fresh = await is_fresh(db)
    await db.migrate()
    # Seed only a brand-new database: a site the admin deleted must stay gone
    # across restarts, so re-seeding on every start would be a bug.
    if fresh:
        await seed(db, settings)

    admins = AdminsRepo(db)
    jobs = JobsRepo(db)
    sites = SitesRepo(db)
    profiles = ProfilesRepo(db)
    proxies = ProxiesRepo(db)
    runs = RunsRepo(db)
    items = ItemsRepo(db)
    sessions = VncRepo(db)
    events = EventsRepo(db)

    registry = ParserRegistry.discover()
    profiles_manager = ProfileManager(settings, profiles)
    scheduler = Scheduler(sites, jobs)
    runner = Runner(settings, profiles_manager, registry, runs, items)
    bus = EventBus(events, admins, settings)
    worker = Worker(
        settings, jobs, sites, profiles, proxies, runner, scheduler, WORKER_ID, bus
    )

    site_service = SiteService(sites, jobs, scheduler, registry)
    vnc_manager = VncManager(settings, profiles, sessions, bus=bus)
    vnc_service = VncService(
        vnc_manager,
        profiles,
        sites,
        settings,
        sessions,
        parser_urls={p.key: p.url for p in registry.all()},
        eager_launch=eager_vnc,
    )

    deps = BotDeps(
        sites=site_service,
        registry=registry,
        events=bus,
        profiles=ProfilesService(profiles_manager, profiles, settings),
        proxies=ProxyService(proxies, settings),
        vnc=vnc_service,
        runs=RunsService(runs, sites, jobs),
    )

    # Allowlist = environment ids ∪ rows in ``admins``. Notifications go only
    # to those who did not mute themselves, which makes ``admins.notify`` real.
    stored = dict(await admins.with_notify())
    admin_ids = set(settings.admin_ids) | set(stored)
    recipients = sorted(
        admin_id for admin_id in admin_ids if stored.get(admin_id, True)
    )

    return Runtime(
        settings=settings,
        db=db,
        worker=worker,
        deps=deps,
        delivery=EventDelivery(bot, bus, settings, recipients=recipients),
        registry=registry,
        vnc=vnc_manager,
        bot=bot,
        admin_ids=admin_ids,
        recipients=recipients,
    )


async def run_combined(settings: Settings) -> None:
    """Run the worker, the event delivery and the bot in one process."""
    runtime = await build_runtime(settings)
    stop = asyncio.Event()
    dispatcher = build_dispatcher(settings, runtime.deps, admin_ids=runtime.admin_ids)
    delivery_task = asyncio.create_task(runtime.delivery.run(stop))
    worker_task = asyncio.create_task(runtime.run_worker(stop))
    try:
        await dispatcher.start_polling(runtime.bot)
    finally:
        stop.set()
        await _stop_tasks(worker_task, delivery_task)
        await runtime.close()


async def run_worker_only(settings: Settings) -> None:
    """Worker process of the split deployment; it never talks to Telegram.

    Delivery belongs to the bot process alone: a worker that owned it would
    claim every pending event and mark it delivered without sending anything,
    so the split deployment would lose every report.
    """
    runtime = await build_runtime(settings, with_bot=False)
    stop = asyncio.Event()
    try:
        await runtime.run_worker(stop)
    finally:
        stop.set()
        await runtime.close()


async def run_bot_only(settings: Settings) -> None:
    """Bot process of the split deployment; the worker keeps running separately.

    The bot has no GUI stack, so a login request only books a session row and
    the worker launches the browser on its next tick.
    """
    runtime = await build_runtime(settings, eager_vnc=False)
    stop = asyncio.Event()
    dispatcher = build_dispatcher(settings, runtime.deps, admin_ids=runtime.admin_ids)
    delivery_task = asyncio.create_task(runtime.delivery.run(stop))
    try:
        await dispatcher.start_polling(runtime.bot)
    finally:
        stop.set()
        await _stop_tasks(delivery_task)
        await runtime.close()


async def run_once(settings: Settings) -> None:
    """One worker cycle without polling Telegram."""
    runtime = await build_runtime(settings, with_bot=False)
    try:
        await runtime.run_once()
    finally:
        await runtime.close()


async def _stop_tasks(*tasks: asyncio.Task) -> None:
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
