"""Bot assembly: dispatcher with routers, admin gate and polling entry point."""

import logging
from dataclasses import dataclass, field
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from uwp.bot.handlers import common, profiles, sites
from uwp.bot.handlers import settings as settings_handlers
from uwp.bot.middleware import AdminOnlyMiddleware
from uwp.config import Settings

log = logging.getLogger(__name__)


@dataclass
class BotDeps:
    """Everything the routers need; later tasks add their services here."""

    sites: Any
    registry: Any
    runner: Any = None
    events: Any = None
    profiles: Any = None
    proxies: Any = None
    vnc: Any = None
    runs: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


def build_dispatcher(
    settings: Settings, deps: BotDeps, admin_ids: set[int] | None = None
) -> Dispatcher:
    """Register the admin gate and every router, in a stable order.

    ``admin_ids`` defaults to the environment list; the runtime passes the union
    with the ``admins`` table so rows created by ``uwp init`` also work.
    """
    dp = Dispatcher()
    gate = AdminOnlyMiddleware(set(admin_ids or ()) or set(settings.admin_ids))
    # Outer middleware on the update itself: runs once before handler
    # resolution, so a non-admin gets exactly one refusal, and every update
    # type is gated (message, callback, edited message, inline query, ...).
    dp.update.outer_middleware(gate)

    dp.include_router(common.build_router(settings, deps))
    dp.include_router(sites.build_router(settings, deps))
    dp.include_router(settings_handlers.build_router(settings, deps))
    dp.include_router(profiles.build_router(settings, deps))
    dp.include_router(common.build_fallback_router())
    dp.errors.register(handle_update_error)
    return dp


async def handle_update_error(event: Any) -> bool:
    """Log expected Telegram refusals as one line instead of a traceback.

    A stale button (``query is too old``), a repeated press (``message is not
    modified``) and a user who blocked the bot are normal, not crashes. Return
    ``True`` to mark the update as handled.
    """
    from aiogram.exceptions import TelegramBadRequest

    exc = getattr(event, "exception", event)
    if isinstance(exc, TelegramBadRequest):
        text = str(exc).lower()
        if any(
            marker in text
            for marker in (
                "query is too old",
                "query id is invalid",
                "message is not modified",
                "message can't be deleted",
                "message to edit not found",
            )
        ):
            log.info("update skipped: %s", exc)
            return True
    # Anything else keeps aiogram's own traceback: it is a real bug.
    return False


async def prepare_polling(bot: Bot, settings: Settings) -> None:
    """Drop updates queued while the bot was down, when configured.

    ``start_polling`` has no ``drop_pending_updates`` of its own (it is a
    ``getUpdates``/``deleteWebhook`` parameter), so the webhook deletion is what
    discards the backlog. Nothing is called when the flag is off: then the
    backlog is delivered, which is what an operator who turned it off wants.
    """
    if settings.drop_pending_updates:
        await bot.delete_webhook(drop_pending_updates=True)


async def run_bot(settings: Settings, deps: BotDeps) -> None:
    """Long-poll Telegram until stopped."""
    bot = make_bot(settings)
    dp = build_dispatcher(settings, deps)
    try:
        await prepare_polling(bot, settings)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


def make_bot(settings: Settings) -> Bot:
    """Build the Bot API client, through ``UWP_TELEGRAM_PROXY`` when set.

    This is the only place that knows about the Telegram transport, so a
    second call site cannot silently drop the proxy and start timing out.
    """
    from aiogram.client.session.aiohttp import AiohttpSession

    if not settings.bot_token:
        raise RuntimeError("UWP_BOT_TOKEN is not set")
    session = None
    if settings.telegram_proxy:
        # SOCKS/HTTP proxy for Bot API calls only; scraping keeps its own
        # per-site proxies. aiogram builds the connector from the URL, and
        # SOCKS additionally requires the ``aiohttp-socks`` package.
        session = AiohttpSession(proxy=settings.telegram_proxy)
    return Bot(
        settings.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
