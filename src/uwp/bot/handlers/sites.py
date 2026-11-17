"""Site list and site card callbacks."""

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from uwp.bot import keyboards, texts
from uwp.i18n import Lang
from uwp.timeutil import format_local, utcnow

log = logging.getLogger(__name__)


def build_router(settings, deps) -> Router:
    router = Router(name="sites")
    sites_service = deps.sites
    registry = deps.registry

    @router.callback_query(F.data == "sites:list")
    async def show_list(callback: CallbackQuery, lang: Lang) -> None:
        sites = await sites_service.list()
        if not sites:
            await callback.message.edit_text(
                texts.no_sites(lang), reply_markup=keyboards.sites_list_kb(sites, lang)
            )
            await callback.answer()
            return
        lines = []
        for site in sites:
            mark = "🟢" if site.enabled else "🔴"
            next_run = (
                format_local(site.next_run_at, settings.tz) if site.next_run_at else "—"
            )
            lines.append(
                f"{mark} <b>{texts.esc(site.name)}</b>"
                + texts.sites_list_suffix(lang, next_run)
            )
        await callback.message.edit_text(
            texts.sites_header(lang) + "\n\n" + "\n".join(lines),
            reply_markup=keyboards.sites_list_kb(sites, lang),
        )
        await callback.answer()

    @router.callback_query(F.data == "site:add")
    async def choose_parser(callback: CallbackQuery, lang: Lang) -> None:
        parsers = registry.all()
        if not parsers:
            await callback.answer(texts.no_parsers(lang), show_alert=True)
            return
        await callback.message.edit_text(
            texts.choose_parser(lang),
            reply_markup=keyboards.parsers_kb(parsers, lang),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("site:add:"))
    async def add_site(callback: CallbackQuery, lang: Lang) -> None:
        key = callback.data.split(":", 2)[2]
        try:
            site = await sites_service.create(key)
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await callback.message.edit_text(
            texts.site_added(lang, site.name),
            reply_markup=keyboards.site_card_kb(site, lang),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("site:toggle:"))
    async def toggle_site(callback: CallbackQuery, lang: Lang) -> None:
        site_id = int(callback.data.rsplit(":", 1)[1])
        site = await sites_service.get(site_id)
        if site is None:
            await callback.answer(texts.site_not_found(lang), show_alert=True)
            return
        site = await sites_service.set_enabled(site_id, not site.enabled, utcnow())
        text = texts.site_enabled(lang) if site.enabled else texts.site_disabled(lang)
        await callback.message.edit_text(
            text, reply_markup=keyboards.site_card_kb(site, lang)
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("site:run:"))
    async def run_site(callback: CallbackQuery, lang: Lang) -> None:
        site_id = int(callback.data.rsplit(":", 1)[1])
        try:
            job_id = await sites_service.run_now(site_id)
        except ValueError:
            await callback.answer(texts.site_not_found(lang), show_alert=True)
            return
        text = (
            texts.site_started(lang)
            if job_id is not None
            else texts.site_already_running(lang)
        )
        await callback.answer(text, show_alert=job_id is None)

    @router.callback_query(F.data.startswith("site:del:"))
    async def confirm_delete(callback: CallbackQuery, lang: Lang) -> None:
        site_id = int(callback.data.rsplit(":", 1)[1])
        site = await sites_service.get(site_id)
        if site is None:
            await callback.answer(texts.site_not_found(lang), show_alert=True)
            return
        await callback.message.edit_text(
            texts.confirm_delete(lang, site.name),
            reply_markup=keyboards.confirm_delete_kb(site, lang),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("site:delok:"))
    async def delete_site(callback: CallbackQuery, lang: Lang) -> None:
        site_id = int(callback.data.rsplit(":", 1)[1])
        await sites_service.delete(site_id)
        sites = await sites_service.list()
        await callback.message.edit_text(
            texts.site_deleted(lang),
            reply_markup=keyboards.sites_list_kb(sites, lang),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("site:login:"))
    async def login_from_site(callback: CallbackQuery, lang: Lang) -> None:
        # A login started from the site card must open the site's own page and
        # honour the site's proxy, not the neutral fallback URL.
        site_id = int(callback.data.rsplit(":", 1)[1])
        site = await sites_service.get(site_id)
        if site is None:
            await callback.answer(texts.site_not_found(lang), show_alert=True)
            return
        if site.profile_id is None:
            await callback.answer(texts.need_profile(lang), show_alert=True)
            return
        try:
            info = await deps.vnc.request(
                site.profile_id, site_id=site.id, started_by=callback.from_user.id
            )
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await callback.message.answer(
            texts.vnc_session(lang, info, settings.vnc_ttl_minutes),
            reply_markup=keyboards.vnc_session_kb(info.session_id, lang),
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^site:\d+$"))
    async def show_card(callback: CallbackQuery, lang: Lang) -> None:
        site_id = int(callback.data.split(":")[1])
        site = await sites_service.get(site_id)
        if site is None:
            await callback.answer(texts.site_not_found(lang), show_alert=True)
            return
        next_run = (
            format_local(site.next_run_at, settings.tz) if site.next_run_at else None
        )
        await callback.message.edit_text(
            texts.site_card(lang, site, next_run),
            reply_markup=keyboards.site_card_kb(site, lang),
            link_preview_options=keyboards.NO_PREVIEW,
        )
        await callback.answer()

    return router
