"""Admin gate: only Telegram ids from ``admin_ids`` may use the bot."""
import logging

from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import CallbackQuery, TelegramObject

from uwp.bot import texts

log = logging.getLogger(__name__)


class AdminOnlyMiddleware(BaseMiddleware):
    """Refuse every update from a non-admin.

    Registered on ``dp.update``, so it sees every kind of update, not only
    messages and callback queries: a future handler for an edited message,
    inline query or channel post must not be reachable by a stranger.

    The refusal is sent once and then ``UNHANDLED`` is returned instead of
    calling the handler. At this level ``SkipHandler`` would escape
    ``Dispatcher.feed_update`` (there is no ``trigger`` loop to catch it) and be
    logged as an exception by the polling loop on every message from a stranger.
    """

    def __init__(self, admin_ids: set[int]) -> None:
        self.admin_ids = admin_ids

    async def __call__(self, handler, event: TelegramObject, data: dict) -> object:
        target = self._inner_event(event)
        user = getattr(target, "from_user", None)
        if user is None or user.id not in self.admin_ids:
            # Callbacks show a modal alert; messages just get a reply. Duck-typing
            # keeps this testable without constructing real Telegram objects.
            try:
                if isinstance(target, CallbackQuery):
                    await target.answer(texts.ACCESS_DENIED, show_alert=True)
                elif hasattr(target, "answer"):
                    await target.answer(texts.ACCESS_DENIED)
                else:
                    log.warning(
                        "refusing %s from an unknown sender", type(target).__name__
                    )
            except Exception:  # a failed refusal must still refuse
                log.warning("cannot send the access-denied reply", exc_info=True)
            return UNHANDLED
        data["is_admin"] = True
        return await handler(event, data)

    @staticmethod
    def _inner_event(event: TelegramObject) -> TelegramObject:
        """Unwrap an ``Update`` so the sender (and ``answer``) can be found.

        At update level the object has no ``from_user`` of its own; the payload
        carries it. ``event`` is the raw nested object for anything else.
        """
        inner = getattr(event, "event", None)
        return inner if inner is not None else event
