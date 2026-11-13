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
    # Outer middleware runs once per update, before handler resolution, so a
    # non-admin gets exactly one refusal even when several handlers match.
    dp.message.outer_middleware(gate)
    dp.callback_query.outer_middleware(gate)

    dp.include_router(common.build_router(settings, deps))
    dp.include_router(sites.build_router(settings, deps))
    dp.include_router(settings_handlers.build_router(settings, deps))
    dp.include_router(profiles.build_router(settings, deps))
    dp.include_router(common.build_fallback_router())
    return dp


async def run_bot(settings: Settings, deps: BotDeps) -> None:
    """Long-poll Telegram until stopped."""
    if not settings.bot_token:
        raise RuntimeError("UWP_BOT_TOKEN is not set")
    bot = Bot(
        settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher(settings, deps)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
