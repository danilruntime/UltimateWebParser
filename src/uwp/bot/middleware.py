"""Admin gate and interface language, in that order, on every update."""

import logging
from collections.abc import Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import CallbackQuery, TelegramObject

from uwp.i18n import DEFAULT_LANG, Lang, normalize, t

log = logging.getLogger(__name__)


def sender_of(event: TelegramObject) -> TelegramObject | None:
    """The object that carries ``from_user``, unwrapping an ``Update``.

    At update level the wrapper has no sender of its own; the payload does. Both
    middlewares need the same unwrapping, so it lives in one place.
    """
    inner = getattr(event, "event", None)
    return inner if inner is not None else event


def telegram_user(event: TelegramObject):
    """The Telegram ``User`` behind an update, or ``None`` for anonymous ones."""
    target = sender_of(event)
    return None if target is None else getattr(target, "from_user", None)


class AdminOnlyMiddleware(BaseMiddleware):
    """Refuse every update from a non-admin.

    Registered on ``dp.update``, so it sees every kind of update, not only
    messages and callback queries: a future handler for an edited message,
    inline query or channel post must not be reachable by a stranger.

    The refusal is sent once and then ``UNHANDLED`` is returned instead of
    calling the handler. At this level ``SkipHandler`` would escape
    ``Dispatcher.feed_update`` (there is no ``trigger`` loop to catch it) and be
    logged as an exception by the polling loop on every message from a stranger.

    ``default`` localises that refusal: a stranger's Telegram client still
    carries a ``language_code``, so it is used when we translate it, and the
    deployment default otherwise. Without it every stranger got Russian.
    """

    def __init__(self, admin_ids: set[int], default: Lang = DEFAULT_LANG) -> None:
        self.admin_ids = admin_ids
        self.default = Lang(default)

    async def __call__(self, handler, event: TelegramObject, data: dict) -> object:
        target = sender_of(event)
        user = getattr(target, "from_user", None) if target is not None else None
        if user is None or user.id not in self.admin_ids:
            # Callbacks show a modal alert; messages just get a reply. Duck-typing
            # keeps this testable without constructing real Telegram objects.
            refusal = t(self._refusal_lang(user), "bot.error.access_denied")
            try:
                if isinstance(target, CallbackQuery):
                    await target.answer(refusal, show_alert=True)
                elif hasattr(target, "answer"):
                    await target.answer(refusal)
                else:
                    log.warning(
                        "refusing %s from an unknown sender", type(target).__name__
                    )
            except Exception:  # a failed refusal must still refuse
                log.warning("cannot send the access-denied reply", exc_info=True)
            return UNHANDLED
        data["is_admin"] = True
        return await handler(event, data)

    def _refusal_lang(self, user) -> Lang:
        code = None if user is None else getattr(user, "language_code", None)
        return normalize(code) or self.default


class LanguageMiddleware(BaseMiddleware):
    """Put ``data["lang"]`` in front of every handler.

    Resolution order is deliberate: the admin's stored choice wins over the
    Telegram client's locale, which wins over the deployment default. The
    stored value is read on each update (one indexed SELECT), so switching the
    language takes effect on the next tap without a restart.

    Registered *after* the gate: a stranger is refused before any database read
    and gets one message in the refusal language, not a resolvable admin's.
    """

    def __init__(
        self,
        resolve: Callable[[int], Awaitable[str | None]],
        default: Lang = DEFAULT_LANG,
    ) -> None:
        self.resolve = resolve
        self.default = Lang(default)

    async def __call__(self, handler, event: TelegramObject, data: dict) -> object:
        user = telegram_user(event)
        data["lang"] = await self._language(user)
        return await handler(event, data)

    async def _language(self, user) -> Lang:
        if user is None:
            return self.default
        try:
            stored = await self.resolve(user.id)
        except Exception:
            # A broken row must not eat the update: fall back like an unset
            # choice and let the handler answer in some language. It is logged
            # at ERROR, not WARNING — a locked or dropped table is an outage,
            # and an operator greps for errors, not for a line among the noise.
            log.exception(
                "cannot read the stored language of admin %s; "
                "falling back to the client locale",
                user.id,
            )
            stored = None
        return (
            normalize(stored)
            or normalize(getattr(user, "language_code", None))
            or self.default
        )
