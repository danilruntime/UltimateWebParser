"""Profiles, proxies, VNC login and run history screens."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from uwp.bot import keyboards, texts
from uwp.domain import ProfileStatus
from uwp.i18n import DEFAULT_LANG, Lang
from uwp.timeutil import format_local

log = logging.getLogger(__name__)


class ProxyInput(StatesGroup):
    waiting = State()


class ProfileNameInput(StatesGroup):
    waiting = State()


def profiles_kb(profiles, lang: Lang = DEFAULT_LANG) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"👤 {profile.name}", callback_data=f"profile:{profile.id}"
            )
        ]
        for profile in profiles
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=texts.new_profile(lang), callback_data="profile:new"
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=texts.back(lang), callback_data="menu:main"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def profile_card_kb(profile, lang: Lang = DEFAULT_LANG) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=texts.login(lang), callback_data=f"profile:login:{profile.id}"
            )
        ]
    ]
    if profile.status == ProfileStatus.login:
        rows.append(
            [
                InlineKeyboardButton(
                    text=texts.login_done(lang),
                    callback_data=f"profile:done:{profile.id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=texts.delete(lang), callback_data=f"profile:del:{profile.id}"
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=texts.back(lang), callback_data="profiles:list"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def proxies_kb(proxies, lang: Lang = DEFAULT_LANG) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"🌍 {proxy.name}", callback_data=f"proxy:{proxy.id}"
            )
        ]
        for proxy in proxies
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=texts.new_proxy(lang), callback_data="proxy:new"
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=texts.back(lang), callback_data="menu:main"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_router(settings, deps) -> Router:
    router = Router(name="profiles")
    profiles = deps.profiles
    proxies = deps.proxies
    vnc = deps.vnc
    runs = deps.runs

    @router.callback_query(F.data == "profiles:list")
    async def list_profiles(callback: CallbackQuery, lang: Lang) -> None:
        items = await profiles.list()
        text = (
            texts.profiles_header(lang) if items else texts.no_profiles(lang)
        )
        await callback.message.edit_text(
            text, reply_markup=profiles_kb(items, lang)
        )
        await callback.answer()

    @router.callback_query(F.data == "profile:new")
    async def ask_name(callback: CallbackQuery, state: FSMContext, lang: Lang) -> None:
        await state.set_state(ProfileNameInput.waiting)
        await callback.message.answer(texts.ask_profile_name(lang))
        await callback.answer()

    @router.message(ProfileNameInput.waiting)
    async def create_profile(message: Message, state: FSMContext, lang: Lang) -> None:
        try:
            profile = await profiles.create(message.text or "")
        except ValueError as exc:
            await message.answer(f"❌ {texts.esc(exc)}")
            return
        await state.clear()
        await message.answer(
            texts.profile_created(lang, profile.name),
            reply_markup=profile_card_kb(profile, lang),
        )

    @router.callback_query(F.data.regexp(r"^profile:\d+$"))
    async def show_profile(callback: CallbackQuery, lang: Lang) -> None:
        profile = await profiles.get(int(callback.data.split(":")[1]))
        if profile is None:
            await callback.answer(texts.profile_not_found(lang), show_alert=True)
            return
        await callback.message.edit_text(
            texts.profile_card(lang, profile),
            reply_markup=profile_card_kb(profile, lang),
            link_preview_options=keyboards.NO_PREVIEW,
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^profile:del:\d+$"))
    async def delete_profile(callback: CallbackQuery, lang: Lang) -> None:
        profile_id = int(callback.data.rsplit(":", 1)[1])
        try:
            await profiles.delete(profile_id)
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        items = await profiles.list()
        await callback.message.edit_text(
            texts.no_profiles(lang) if not items else texts.profiles_header(lang),
            reply_markup=profiles_kb(items, lang),
        )
        await callback.answer(texts.profile_deleted(lang))

    @router.callback_query(F.data.regexp(r"^profile:login:\d+$"))
    async def start_login(callback: CallbackQuery, lang: Lang) -> None:
        profile_id = int(callback.data.rsplit(":", 1)[1])
        try:
            info = await vnc.request(profile_id, started_by=callback.from_user.id)
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await callback.message.answer(
            texts.vnc_session(lang, info, settings.vnc_ttl_minutes),
            reply_markup=keyboards.vnc_session_kb(info.session_id, lang),
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^profile:done:\d+$"))
    async def finish_login(callback: CallbackQuery, lang: Lang) -> None:
        profile_id = int(callback.data.rsplit(":", 1)[1])
        # Freeing the profile here would let the next run open a second
        # Chromium on the same user-data-dir while the VNC browser is alive.
        # Stopping the session is the only correct way to finish a login.
        session = await vnc.active_session(profile_id)
        if session is not None:
            await vnc.mark_done(session.id)
        else:
            await profiles.release(profile_id)
        profile = await profiles.get(profile_id)
        await callback.message.edit_text(
            texts.profile_done(lang, profile.name if profile else ""),
            reply_markup=profile_card_kb(profile, lang) if profile else None,
        )
        await callback.answer()

    @router.callback_query(F.data == "proxies:list")
    async def list_proxies(callback: CallbackQuery, lang: Lang) -> None:
        items = await proxies.list()
        text = texts.proxies_header(lang) if items else texts.no_proxies(lang)
        await callback.message.edit_text(text, reply_markup=proxies_kb(items, lang))
        await callback.answer()

    @router.callback_query(F.data == "proxy:new")
    async def ask_proxy(callback: CallbackQuery, state: FSMContext, lang: Lang) -> None:
        await state.set_state(ProxyInput.waiting)
        await callback.message.answer(texts.ask_proxy(lang))
        await callback.answer()

    @router.message(ProxyInput.waiting)
    async def create_proxy(message: Message, state: FSMContext, lang: Lang) -> None:
        text = (message.text or "").strip()
        name, _, url = text.partition(" ")
        if not url:
            name, url = "", text
        try:
            await proxies.add(name, url)
        except ValueError as exc:
            await message.answer(
                f"❌ {texts.esc(exc)}\n{texts.ask_proxy(lang)}"
            )
            return
        await state.clear()
        items = await proxies.list()
        await message.answer(
            texts.proxy_added(lang), reply_markup=proxies_kb(items, lang)
        )

    @router.callback_query(F.data.regexp(r"^proxy:\d+$"))
    async def show_proxy(callback: CallbackQuery, lang: Lang) -> None:
        proxy = await proxies.get(int(callback.data.split(":")[1]))
        if proxy is None:
            await callback.answer(texts.proxy_not_found(lang), show_alert=True)
            return
        rows = [
            [
                InlineKeyboardButton(
                    text=texts.check(lang), callback_data=f"proxy:test:{proxy.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.delete(lang), callback_data=f"proxy:del:{proxy.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.back(lang), callback_data="proxies:list"
                )
            ],
        ]
        await callback.message.edit_text(
            texts.proxy_card(lang, proxy),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            link_preview_options=keyboards.NO_PREVIEW,
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^proxy:test:\d+$"))
    async def test_proxy(callback: CallbackQuery, lang: Lang) -> None:
        check = await proxies.test(int(callback.data.rsplit(":", 1)[1]))
        await callback.answer(texts.proxy_check(lang, check), show_alert=True)

    @router.callback_query(F.data.regexp(r"^proxy:del:\d+$"))
    async def delete_proxy(callback: CallbackQuery, lang: Lang) -> None:
        await proxies.delete(int(callback.data.rsplit(":", 1)[1]))
        items = await proxies.list()
        await callback.message.edit_text(
            texts.proxies_header(lang) if items else texts.no_proxies(lang),
            reply_markup=proxies_kb(items, lang),
        )
        await callback.answer(texts.proxy_deleted(lang))

    @router.callback_query(F.data.regexp(r"^vnc:done:\d+$"))
    async def vnc_done(callback: CallbackQuery, lang: Lang) -> None:
        await vnc.mark_done(int(callback.data.rsplit(":", 1)[1]))
        await callback.message.edit_text(texts.vnc_finished(lang))
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^vnc:extend:\d+$"))
    async def vnc_extend(callback: CallbackQuery, lang: Lang) -> None:
        session = await vnc.extend(int(callback.data.rsplit(":", 1)[1]))
        remaining = await vnc.remaining_minutes(session)
        await callback.answer(
            texts.vnc_extended(lang, remaining), show_alert=True
        )

    @router.callback_query(F.data.regexp(r"^runs:\d+$"))
    async def run_history(callback: CallbackQuery, lang: Lang) -> None:
        site_id = int(callback.data.rsplit(":", 1)[1])
        items = await runs.recent(site_id)
        if not items:
            await callback.message.edit_text(texts.no_runs(lang))
            await callback.answer()
            return
        lines = [texts.runs_header(lang)]
        for run in items:
            lines.append(
                texts.run_history_line(
                    lang,
                    texts.status_mark(run.status),
                    format_local(run.started_at, settings.tz),
                    texts.status_label(lang, run.status),
                    run.rows_count,
                    run.new_count,
                )
            )
        rows = [
            [
                InlineKeyboardButton(
                    text=texts.retry(lang), callback_data=f"run:retry:{site_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.back(lang), callback_data=f"site:settings:{site_id}"
                )
            ],
        ]
        await callback.message.edit_text(
            "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^run:retry:\d+$"))
    async def retry_run(callback: CallbackQuery, lang: Lang) -> None:
        try:
            job_id = await runs.retry(int(callback.data.rsplit(":", 1)[1]))
        except ValueError:
            await callback.answer(texts.site_not_found(lang), show_alert=True)
            return
        await callback.answer(
            texts.site_started(lang)
            if job_id is not None
            else texts.site_already_running(lang),
            show_alert=job_id is None,
        )

    return router
