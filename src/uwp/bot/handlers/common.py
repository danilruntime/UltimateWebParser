"""Handlers shared by every screen: /start, /help, /id, /sites and the menu."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from uwp.bot import keyboards, texts
from uwp.bot.handlers import profiles
from uwp.timeutil import format_local


def build_router(settings, deps) -> Router:
    router = Router(name="common")
    sites_service = deps.sites
    profiles_service = deps.profiles
    proxies_service = deps.proxies
    runs = deps.runs

    @router.message(Command("start"))
    async def start_message(message: Message) -> None:
        await message.answer(texts.MENU, reply_markup=keyboards.reply_menu_kb())

    @router.message(Command("help"))
    async def help_message(message: Message) -> None:
        await message.answer(texts.HELP)

    @router.message(Command("id"))
    async def show_id(message: Message) -> None:
        await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")

    @router.message(Command("sites"))
    async def list_sites(message: Message) -> None:
        await send_sites(message, sites_service, settings)

    @router.message(Command("runs"))
    async def latest_runs(message: Message) -> None:
        items = await runs.latest(limit=10)
        if not items:
            await message.answer(texts.NO_RUNS)
            return
        names = {site.id: site.name for site in await sites_service.list()}
        lines = [texts.RUNS_HEADER]
        for run in items:
            name = names.get(run.site_id, f"сайт {run.site_id}")
            lines.append(
                f"{_mark(run.status)} {format_local(run.started_at, settings.tz)} — "
                f"{texts.esc(name)}: {texts.status_label(run.status)}, "
                f"{run.rows_count} строк"
            )
        await message.answer("\n".join(lines))

    @router.callback_query(F.data == "menu:main")
    async def back_to_menu(callback: CallbackQuery) -> None:
        await callback.message.edit_text(texts.MENU, reply_markup=keyboards.menu_kb())
        await callback.answer()

    # These match regardless of FSM state on purpose. A tap during a wizard must
    # abandon the wizard: with a StateFilter(None) it would fall through to the
    # wizard's text handler and create a profile named «🌐 Сайты».
    @router.message(F.text == keyboards.BTN_SITES)
    async def menu_sites(message: Message, state: FSMContext) -> None:
        await state.clear()
        await send_sites(message, sites_service, settings)

    @router.message(F.text == keyboards.BTN_PROFILES)
    async def menu_profiles(message: Message, state: FSMContext) -> None:
        await state.clear()
        items = await profiles_service.list()
        text = texts.PROFILES_HEADER if items else texts.NO_PROFILES
        await message.answer(text, reply_markup=profiles.profiles_kb(items))

    @router.message(F.text == keyboards.BTN_PROXIES)
    async def menu_proxies(message: Message, state: FSMContext) -> None:
        await state.clear()
        items = await proxies_service.list()
        text = texts.PROXIES_HEADER if items else texts.NO_PROXIES
        await message.answer(text, reply_markup=profiles.proxies_kb(items))

    @router.message(F.text == keyboards.BTN_HELP)
    async def menu_help(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(texts.HELP)

    return router


def build_fallback_router() -> Router:
    """The catch-all must be registered after every FSM input handler.

    Otherwise it would eat the wizard's text (a profile name, an interval) and
    the admin would see «Не понял команду» instead of a saved setting.
    """
    router = Router(name="fallback")

    @router.message(F.text)
    async def unknown(message: Message) -> None:
        await message.answer(texts.UNKNOWN_COMMAND)

    return router


def _mark(status) -> str:
    return {
        "ok": "✅",
        "partial": "⚠️",
        "failed": "❌",
        "canceled": "⏹",
    }.get(str(status), "•")


async def send_sites(message: Message, sites_service, settings) -> None:
    """Render the site list, either as an edit or as a new message."""
    sites = await sites_service.list()
    markup = keyboards.sites_list_kb(sites)
    if not sites:
        await message.answer(texts.NO_SITES, reply_markup=markup)
        return
    lines = []
    for site in sites:
        mark = "🟢" if site.enabled else "🔴"
        lines.append(f"{mark} <b>{texts.esc(site.name)}</b>")
        if site.next_run_at is not None:
            lines.append(f"   следующий: {format_local(site.next_run_at, settings.tz)}")
    await message.answer(
        texts.SITES_HEADER + "\n\n" + "\n".join(lines),
        reply_markup=markup,
    )
