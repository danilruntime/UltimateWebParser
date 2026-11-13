"""Handlers shared by every screen: /start, /help, /id, /sites and the menu."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from uwp.bot import keyboards, texts
from uwp.timeutil import format_local


def build_router(settings, sites_service) -> Router:
    router = Router(name="common")

    @router.message(Command("start"))
    async def start_message(message: Message) -> None:
        await message.answer(texts.MENU, reply_markup=keyboards.menu_kb())

    @router.message(Command("help"))
    async def help_message(message: Message) -> None:
        await message.answer(texts.HELP)

    @router.message(Command("id"))
    async def show_id(message: Message) -> None:
        await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")

    @router.message(Command("sites"))
    async def list_sites(message: Message) -> None:
        await send_sites(message, sites_service, settings)

    @router.callback_query(F.data == "menu:main")
    async def back_to_menu(callback: CallbackQuery) -> None:
        await callback.message.edit_text(texts.MENU, reply_markup=keyboards.menu_kb())
        await callback.answer()

    return router


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
        lines.append(f"{mark} <b>{site.name}</b>")
        if site.next_run_at is not None:
            lines.append(f"   следующий: {format_local(site.next_run_at, settings.tz)}")
    await message.answer(
        texts.SITES_HEADER + "\n\n" + "\n".join(lines),
        reply_markup=markup,
    )
