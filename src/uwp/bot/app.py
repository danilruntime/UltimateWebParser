"""Bot assembly: dispatcher with routers, admin gate and polling entry point."""
import logging
from dataclasses import dataclass, field
from typing import Any

from aiogram import Bot, Dispatcher

from uwp.bot.handlers import common, sites
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
    extra: dict[str, Any] = field(default_factory=dict)


def build_dispatcher(settings: Settings, deps: BotDeps) -> Dispatcher:
    """Register the admin gate and every router, in a stable order."""
    dp = Dispatcher()
    gate = AdminOnlyMiddleware(set(settings.admin_ids))
    dp.message.middleware(gate)
    dp.callback_query.middleware(gate)

    dp.include_router(common.build_router(settings, deps.sites))
    dp.include_router(sites.build_router(settings, deps.sites, deps.registry))
    return dp


async def run_bot(settings: Settings, deps: BotDeps) -> None:
    """Long-poll Telegram until stopped."""
    if not settings.bot_token:
        raise RuntimeError("UWP_BOT_TOKEN is not set")
    bot = Bot(settings.bot_token)
    dp = build_dispatcher(settings, deps)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
