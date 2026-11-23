"""Runtime composition: wire settings, database, repos, services and processes.

The bot and the worker share one SQLite database. In ``combined`` mode they run
in the same process (the default); the split mode runs only the half a process
needs.
"""
import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from uwp.artifacts import prune_artifacts
from uwp.bot.app import BotDeps, build_dispatcher, make_bot, prepare_polling
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
from uwp.i18n import Lang
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
# Housekeeping (dead profiles, old session rows, screenshots) is cheap but not
# free; once an hour is enough for data that ages in days.
CLEANUP_SECONDS = 3600


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
        # Rows left ``running``/``stopping`` by a dead process would keep their
        # port and profile stuck forever. ``requested`` is excluded on purpose:
        # in split mode the bot books it and this worker launches it on the
        # next tick, so it may be alive. A failing sweep must not take the
        # worker down with it: in combined mode nothing would notice.
        try:
            for profile_id in await self.vnc.sessions.sweep_orphans():
                # Conditional on purpose: never free a profile a run has leased.
                await self.vnc.profiles.release_login(profile_id)
        except Exception:
            log.exception("VNC orphan sweep failed; starting the worker anyway")
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
        await self._cleanup(now)

    async def _cleanup(self, now: Any = None) -> None:
        """Housekeeping: expired clean profiles, VNC history, run artifacts.

        Every step is best-effort: a full disk or a permission error in one of
        them must not stop the VNC tick that follows in the same loop.
        """
        now = now or utcnow()
        try:
            removed = await self.worker.runner.profiles.cleanup_clean(now)
            if removed:
                log.info("removed %d expired clean profile(s)", removed)
        except Exception:
            log.exception("cleanup of expired clean profiles failed")
        try:
            # A stopped login row keeps its port reserved in taken_ports only
            # while unfinished; the row itself is history after a week.
            purged = await self.vnc.sessions.purge_finished(
                now - timedelta(days=self.settings.vnc_history_days)
            )
            if purged:
                log.info("purged %d finished VNC session(s)", purged)
        except Exception:
            log.exception("purging finished VNC sessions failed")
        try:
            pruned = prune_artifacts(
                self.settings.artifacts_dir, self.settings.artifacts_retention
            )
            if pruned:
                log.info("pruned %d artifact director(ies)", pruned)
        except Exception:
            log.exception("pruning run artifacts failed")

    async def _maintain(self, stop: asyncio.Event) -> None:
        last_cleanup: float | None = None
        while not stop.is_set():
            try:
                await self.vnc.tick(utcnow())
            except Exception:
                log.exception("VNC maintenance tick failed")
            if (
                last_cleanup is None
                or asyncio.get_running_loop().time() - last_cleanup >= CLEANUP_SECONDS
            ):
                last_cleanup = asyncio.get_running_loop().time()
                await self._cleanup(utcnow())
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
    try:
        await db.connect()
        # Freshness is "no schema yet", so it can only be read before the
        # migration. Two processes can both answer "yes" here (that is what makes
        # the first `up` fast); ``seed`` itself is what serialises them, under a
        # write transaction, so the loser sees the winner's row instead of dying
        # on "UNIQUE constraint failed: sites.slug".
        fresh = await is_fresh(db)
        await db.migrate()
        # Seed only a brand-new database: a site the admin deleted must stay gone
        # across restarts, so re-seeding on every start would be a bug.
        if fresh:
            await seed(db, settings)
        return await _wire_runtime(settings, db, bot, eager_vnc=eager_vnc)
    except BaseException:
        # ``aiosqlite`` starts a non-daemon thread per connection, and a start
        # that fails after `connect()` never reaches `Runtime.close()`: the
        # process printed the refusal and then hung forever (measured on the
        # downgrade guard, `timeout 15` → 124). A container alive but stuck is
        # never restarted, so the failure has to close the connection to exit.
        await db.close()
        raise


async def _wire_runtime(
    settings: Settings, db: Database, bot: object, *, eager_vnc: bool
) -> Runtime:
    """Build every collaborator over an open, migrated database."""
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
    site_service = SiteService(
        sites, jobs, scheduler, registry, lang=Lang(settings.default_language)
    )
    proxy_service = ProxyService(proxies, settings)
    # The worker must hand the runner a *decrypted* password: the row holds a
    # Fernet token when UWP_SECRET_KEY is set, and Chromium cannot authenticate
    # with ciphertext.
    worker = Worker(
        settings, jobs, sites, profiles, proxy_service, runner, scheduler, WORKER_ID, bus
    )
    vnc_manager = VncManager(
        settings, profiles, sessions, bus=bus, proxies=proxy_service
    )
    vnc_service = VncService(
        vnc_manager,
        profiles,
        sites,
        settings,
        sessions,
        parser_auth_urls={p.key: p.login_url for p in registry.all()},
        eager_launch=eager_vnc,
    )

    deps = BotDeps(
        sites=site_service,
        registry=registry,
        events=bus,
        profiles=ProfilesService(profiles_manager, profiles, settings),
        proxies=proxy_service,
        vnc=vnc_service,
        runs=RunsService(runs, sites, jobs),
        admins=admins,
    )

    # Allowlist = environment ids ∪ rows in ``admins``. Notifications go only
    # to those who did not mute themselves, which makes ``admins.notify`` real.
    stored = {
        admin_id: notify for admin_id, notify, _language in await admins.with_settings()
    }
    admin_ids = set(settings.admin_ids) | set(stored)
    recipients = sorted(
        admin_id for admin_id in admin_ids if stored.get(admin_id, True)
    )

    return Runtime(
        settings=settings,
        db=db,
        worker=worker,
        deps=deps,
        delivery=EventDelivery(
            bot,
            bus,
            settings,
            recipients=recipients,
            language_for=admins.language,
        ),
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
        await prepare_polling(runtime.bot, settings)
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
        await prepare_polling(runtime.bot, settings)
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
