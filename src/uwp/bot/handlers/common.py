"""Handlers shared by every screen: /start, /help, /id, /sites, /language."""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from uwp.bot import keyboards, texts
from uwp.bot.handlers import profiles
from uwp.i18n import Lang, normalize
from uwp.timeutil import format_local

log = logging.getLogger(__name__)


def build_router(settings, deps) -> Router:
    router = Router(name="common")
    sites_service = deps.sites
    profiles_service = deps.profiles
    proxies_service = deps.proxies
    runs = deps.runs

    @router.message(Command("start"))
    async def start_message(message: Message, lang: Lang) -> None:
        await message.answer(
            texts.menu(lang), reply_markup=keyboards.reply_menu_kb(lang)
        )

    @router.message(Command("help"))
    async def help_message(message: Message, lang: Lang) -> None:
        await message.answer(texts.help(lang))

    @router.message(Command("id"))
    async def show_id(message: Message, lang: Lang) -> None:
        await message.answer(texts.your_id(lang, message.from_user.id))

    @router.message(Command("sites"))
    async def list_sites(message: Message, lang: Lang) -> None:
        await send_sites(message, sites_service, settings, lang)

    @router.message(Command("language"))
    async def command_language(message: Message, lang: Lang) -> None:
        await message.answer(
            texts.language_screen(lang), reply_markup=keyboards.language_kb(lang)
        )

    @router.message(Command("runs"))
    async def latest_runs(message: Message, lang: Lang) -> None:
        items = await runs.latest(limit=10)
        if not items:
            await message.answer(texts.no_runs(lang))
            return
        names = {site.id: site.name for site in await sites_service.list()}
        lines = [texts.runs_header(lang)]
        for run in items:
            name = names.get(run.site_id) or texts.run_site_name(lang, run.site_id)
            lines.append(
                texts.run_line(
                    lang,
                    texts.status_mark(run.status),
                    format_local(run.started_at, settings.tz, lang),
                    name,
                    texts.status_label(lang, run.status),
                    run.rows_count,
                )
            )
        await message.answer("\n".join(lines))

    @router.callback_query(F.data == "menu:main")
    async def back_to_menu(callback: CallbackQuery, lang: Lang) -> None:
        await callback.message.edit_text(
            texts.menu(lang), reply_markup=keyboards.menu_kb(lang)
        )
        await callback.answer()

    # These match regardless of FSM state on purpose. A tap during a wizard must
    # abandon the wizard: with a StateFilter(None) it would fall through to the
    # wizard's text handler and create a profile named after the menu button.
    # Every label is matched in both languages: a user who switched language
    # still has the old reply keyboard under the chat input.
    @router.message(F.text.in_(set(keyboards.BTN_SITES.values())))
    async def menu_sites(message: Message, state: FSMContext, lang: Lang) -> None:
        await state.clear()
        await send_sites(message, sites_service, settings, lang)

    @router.message(F.text.in_(set(keyboards.BTN_PROFILES.values())))
    async def menu_profiles(message: Message, state: FSMContext, lang: Lang) -> None:
        await state.clear()
        items = await profiles_service.list()
        text = texts.profiles_header(lang) if items else texts.no_profiles(lang)
        await message.answer(text, reply_markup=profiles.profiles_kb(items, lang))

    @router.message(F.text.in_(set(keyboards.BTN_PROXIES.values())))
    async def menu_proxies(message: Message, state: FSMContext, lang: Lang) -> None:
        await state.clear()
        items = await proxies_service.list()
        text = texts.proxies_header(lang) if items else texts.no_proxies(lang)
        await message.answer(text, reply_markup=profiles.proxies_kb(items, lang))

    @router.message(F.text.in_(set(keyboards.BTN_HELP.values())))
    async def menu_help(message: Message, state: FSMContext, lang: Lang) -> None:
        await state.clear()
        await message.answer(texts.help(lang))

    @router.message(F.text.in_(set(keyboards.BTN_LANGUAGE.values())))
    async def menu_language(message: Message, state: FSMContext, lang: Lang) -> None:
        await state.clear()
        await message.answer(
            texts.language_screen(lang), reply_markup=keyboards.language_kb(lang)
        )

    @router.callback_query(F.data == "lang:screen")
    async def show_language(callback: CallbackQuery, lang: Lang) -> None:
        await callback.message.edit_text(
            texts.language_screen(lang), reply_markup=keyboards.language_kb(lang)
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("lang:set:"))
    async def set_language(callback: CallbackQuery, lang: Lang) -> None:
        code = normalize(callback.data.rsplit(":", 1)[1])
        if code is None:
            # The callback data is ours, but a hand-crafted one must not write
            # an untranslatable value into the column.
            await callback.answer(texts.language_unknown(lang), show_alert=True)
            return
        admins = deps.admins
        if admins is None:
            await callback.answer(texts.language_unavailable(lang), show_alert=True)
            return
        try:
            await admins.set_language(callback.from_user.id, code.value)
        except Exception:
            # A locked database must not look like a dead button: the admin
            # taps, nothing happens, and the screen still shows the old tongue.
            log.exception(
                "cannot save the interface language of %s", callback.from_user.id
            )
            await callback.answer(
                texts.language_save_failed(lang), show_alert=True
            )
            return
        # Re-render in the *new* language, then resend the reply keyboard: the
        # old one stays under the chat input until a new keyboard replaces it,
        # and its labels are the filter the menu handlers match on.
        await callback.message.edit_text(
            texts.language_screen(code), reply_markup=keyboards.language_kb(code)
        )
        await callback.message.answer(
            texts.language_saved(code),
            reply_markup=keyboards.reply_menu_kb(code),
        )
        await callback.answer()

    return router


def build_fallback_router() -> Router:
    """The catch-all must be registered after every FSM input handler.

    Otherwise it would eat the wizard's text (a profile name, an interval) and
    the admin would see the unknown-command hint instead of a saved setting.
    """
    router = Router(name="fallback")

    @router.message(F.text)
    async def unknown(message: Message, lang: Lang) -> None:
        await message.answer(texts.unknown_command(lang))

    return router


async def send_sites(message: Message, sites_service, settings, lang: Lang) -> None:
    """Render the site list, either as an edit or as a new message."""
    sites = await sites_service.list()
    markup = keyboards.sites_list_kb(sites, lang)
    if not sites:
        await message.answer(texts.no_sites(lang), reply_markup=markup)
        return
    lines = []
    for site in sites:
        mark = "🟢" if site.enabled else "🔴"
        lines.append(f"{mark} <b>{texts.esc(site.name)}</b>")
        if site.next_run_at is not None:
            lines.append(
                texts.sites_next_line(
                    lang, format_local(site.next_run_at, settings.tz, lang)
                )
            )
    await message.answer(
        texts.sites_header(lang) + "\n\n" + "\n".join(lines),
        reply_markup=markup,
    )
