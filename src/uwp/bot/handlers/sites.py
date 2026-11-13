"""Site list and site card callbacks."""
import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from uwp.bot import keyboards, texts
from uwp.timeutil import format_local, utcnow

log = logging.getLogger(__name__)


def build_router(settings, sites_service, registry) -> Router:
    router = Router(name="sites")

    @router.callback_query(F.data == "sites:list")
    async def show_list(callback: CallbackQuery) -> None:
        sites = await sites_service.list()
        if not sites:
            await callback.message.edit_text(
                texts.NO_SITES, reply_markup=keyboards.sites_list_kb(sites)
            )
            await callback.answer()
            return
        lines = []
        for site in sites:
            mark = "🟢" if site.enabled else "🔴"
            next_run = (
                format_local(site.next_run_at, settings.tz) if site.next_run_at else "—"
            )
            lines.append(f"{mark} <b>{site.name}</b> — след. {next_run}")
        await callback.message.edit_text(
            texts.SITES_HEADER + "\n\n" + "\n".join(lines),
            reply_markup=keyboards.sites_list_kb(sites),
        )
        await callback.answer()

    @router.callback_query(F.data == "site:add")
    async def choose_parser(callback: CallbackQuery) -> None:
        parsers = registry.all()
        if not parsers:
            await callback.answer("Нет доступных парсеров", show_alert=True)
            return
        await callback.message.edit_text(
            "Выберите парсер:", reply_markup=keyboards.parsers_kb(parsers)
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("site:add:"))
    async def add_site(callback: CallbackQuery) -> None:
        key = callback.data.split(":", 2)[2]
        try:
            site = await sites_service.create(key)
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await callback.message.edit_text(
            texts.SITE_ADDED.format(name=site.name),
            reply_markup=keyboards.site_card_kb(site),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("site:toggle:"))
    async def toggle_site(callback: CallbackQuery) -> None:
        site_id = int(callback.data.rsplit(":", 1)[1])
        site = await sites_service.get(site_id)
        if site is None:
            await callback.answer("Сайт не найден", show_alert=True)
            return
        site = await sites_service.set_enabled(site_id, not site.enabled, utcnow())
        text = texts.SITE_ENABLED if site.enabled else texts.SITE_DISABLED
        await callback.message.edit_text(text, reply_markup=keyboards.site_card_kb(site))
        await callback.answer()

    @router.callback_query(F.data.startswith("site:run:"))
    async def run_site(callback: CallbackQuery) -> None:
        site_id = int(callback.data.rsplit(":", 1)[1])
        job_id = await sites_service.run_now(site_id)
        text = texts.SITE_STARTED if job_id is not None else texts.SITE_ALREADY_RUNNING
        await callback.answer(text, show_alert=job_id is None)

    @router.callback_query(F.data.regexp(r"^site:\d+$"))
    async def show_card(callback: CallbackQuery) -> None:
        site_id = int(callback.data.split(":")[1])
        site = await sites_service.get(site_id)
        if site is None:
            await callback.answer("Сайт не найден", show_alert=True)
            return
        next_run = format_local(site.next_run_at, settings.tz) if site.next_run_at else None
        await callback.message.edit_text(
            texts.site_card(site, next_run), reply_markup=keyboards.site_card_kb(site)
        )
        await callback.answer()

    return router
