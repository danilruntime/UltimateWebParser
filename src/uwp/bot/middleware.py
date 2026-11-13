"""Admin gate: only Telegram ids from ``admin_ids`` may use the bot."""
import logging

from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import CallbackQuery, TelegramObject

from uwp.bot import texts

log = logging.getLogger(__name__)


class AdminOnlyMiddleware(BaseMiddleware):
    """Refuse every message and callback from a non-admin.

    The refusal is sent once and the handler chain is stopped with
    ``SkipHandler``, so no other middleware or handler runs.
    """

    def __init__(self, admin_ids: set[int]) -> None:
        self.admin_ids = admin_ids

    async def __call__(self, handler, event: TelegramObject, data: dict) -> object:
        user = getattr(event, "from_user", None)
        if user is None or user.id not in self.admin_ids:
            # Callbacks show a modal alert; messages just get a reply. Duck-typing
            # keeps this testable without constructing real Telegram objects.
            if isinstance(event, CallbackQuery):
                await event.answer(texts.ACCESS_DENIED, show_alert=True)
            elif hasattr(event, "answer"):
                await event.answer(texts.ACCESS_DENIED)
            else:
                log.warning("refusing %s from an unknown sender", type(event).__name__)
            raise SkipHandler
        data["is_admin"] = True
        return await handler(event, data)
