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

from uwp.bot import texts
from uwp.bot.handlers.settings import ProfileNameInput
from uwp.domain import ProfileStatus
from uwp.services.vnc import session_text
from uwp.timeutil import format_local

log = logging.getLogger(__name__)


class ProxyInput(StatesGroup):
    waiting = State()


def profiles_kb(profiles) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"👤 {profile.name}", callback_data=f"profile:{profile.id}"
            )
        ]
        for profile in profiles
    ]
    rows.append(
        [InlineKeyboardButton(text="➕ Новый профиль", callback_data="profile:new")]
    )
    rows.append([InlineKeyboardButton(text=texts.BACK, callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def profile_card_kb(profile) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🖥 Войти", callback_data=f"profile:login:{profile.id}")]]
    if profile.status == ProfileStatus.login:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Вход выполнен", callback_data=f"profile:done:{profile.id}"
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"profile:del:{profile.id}")]
    )
    rows.append([InlineKeyboardButton(text=texts.BACK, callback_data="profiles:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def proxies_kb(proxies) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"🌍 {proxy.name}", callback_data=f"proxy:{proxy.id}")]
        for proxy in proxies
    ]
    rows.append([InlineKeyboardButton(text="➕ Новый прокси", callback_data="proxy:new")])
    rows.append([InlineKeyboardButton(text=texts.BACK, callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def vnc_kb(session_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Готово", callback_data=f"vnc:done:{session_id}"
                ),
                InlineKeyboardButton(
                    text="⏱ +30 мин", callback_data=f"vnc:extend:{session_id}"
                ),
            ]
        ]
    )


def build_router(settings, deps) -> Router:
    router = Router(name="profiles")
    profiles = deps.profiles
    proxies = deps.proxies
    vnc = deps.vnc
    runs = deps.runs

    @router.callback_query(F.data == "profiles:list")
    async def list_profiles(callback: CallbackQuery) -> None:
        items = await profiles.list()
        text = texts.PROFILES_HEADER if items else texts.NO_PROFILES
        await callback.message.edit_text(text, reply_markup=profiles_kb(items))
        await callback.answer()

    @router.callback_query(F.data == "profile:new")
    async def ask_name(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(ProfileNameInput.waiting)
        await callback.message.answer(texts.ASK_PROFILE_NAME)
        await callback.answer()

    @router.message(ProfileNameInput.waiting)
    async def create_profile(message: Message, state: FSMContext) -> None:
        try:
            profile = await profiles.create(message.text or "")
        except ValueError as exc:
            await message.answer(f"❌ {exc}")
            return
        await state.clear()
        await message.answer(
            texts.PROFILE_CREATED.format(name=profile.name),
            reply_markup=profile_card_kb(profile),
        )

    @router.callback_query(F.data.regexp(r"^profile:\d+$"))
    async def show_profile(callback: CallbackQuery) -> None:
        profile = await profiles.get(int(callback.data.split(":")[1]))
        if profile is None:
            await callback.answer("Профиль не найден", show_alert=True)
            return
        await callback.message.edit_text(
            texts.profile_card(profile), reply_markup=profile_card_kb(profile)
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^profile:del:\d+$"))
    async def delete_profile(callback: CallbackQuery) -> None:
        profile_id = int(callback.data.rsplit(":", 1)[1])
        try:
            await profiles.delete(profile_id)
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        items = await profiles.list()
        await callback.message.edit_text(
            texts.NO_PROFILES if not items else texts.PROFILES_HEADER,
            reply_markup=profiles_kb(items),
        )
        await callback.answer(texts.PROFILE_DELETED)

    @router.callback_query(F.data.regexp(r"^profile:login:\d+$"))
    async def start_login(callback: CallbackQuery) -> None:
        profile_id = int(callback.data.rsplit(":", 1)[1])
        try:
            info = await vnc.request(profile_id, started_by=callback.from_user.id)
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await callback.message.answer(
            session_text(info, settings.vnc_ttl_minutes), reply_markup=vnc_kb(info.session_id)
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^profile:done:\d+$"))
    async def finish_login(callback: CallbackQuery) -> None:
        profile_id = int(callback.data.rsplit(":", 1)[1])
        profile = await profiles.get(profile_id)
        if profile is not None:
            await profiles.release(profile_id)
            profile = await profiles.get(profile_id)
        await callback.message.edit_text(
            texts.PROFILE_DONE.format(name=profile.name if profile else ""),
            reply_markup=profile_card_kb(profile) if profile else None,
        )
        await callback.answer()

    @router.callback_query(F.data == "proxies:list")
    async def list_proxies(callback: CallbackQuery) -> None:
        items = await proxies.list()
        text = texts.PROXIES_HEADER if items else texts.NO_PROXIES
        await callback.message.edit_text(text, reply_markup=proxies_kb(items))
        await callback.answer()

    @router.callback_query(F.data == "proxy:new")
    async def ask_proxy(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(ProxyInput.waiting)
        await callback.message.answer(texts.ASK_PROXY)
        await callback.answer()

    @router.message(ProxyInput.waiting)
    async def create_proxy(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        name, _, url = text.partition(" ")
        if not url:
            name, url = "", text
        try:
            await proxies.add(name, url)
        except ValueError as exc:
            await message.answer(f"❌ {exc}\n{texts.ASK_PROXY}")
            return
        await state.clear()
        items = await proxies.list()
        await message.answer(texts.PROXY_ADDED, reply_markup=proxies_kb(items))

    @router.callback_query(F.data.regexp(r"^proxy:\d+$"))
    async def show_proxy(callback: CallbackQuery) -> None:
        proxy = await proxies.get(int(callback.data.split(":")[1]))
        if proxy is None:
            await callback.answer("Прокси не найден", show_alert=True)
            return
        rows = [
            [InlineKeyboardButton(text="🔌 Проверить", callback_data=f"proxy:test:{proxy.id}")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"proxy:del:{proxy.id}")],
            [InlineKeyboardButton(text=texts.BACK, callback_data="proxies:list")],
        ]
        await callback.message.edit_text(
            texts.proxy_card(proxy), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^proxy:test:\d+$"))
    async def test_proxy(callback: CallbackQuery) -> None:
        ok, message = await proxies.test(int(callback.data.rsplit(":", 1)[1]))
        await callback.answer(("✅ " if ok else "❌ ") + message, show_alert=True)

    @router.callback_query(F.data.regexp(r"^proxy:del:\d+$"))
    async def delete_proxy(callback: CallbackQuery) -> None:
        await proxies.delete(int(callback.data.rsplit(":", 1)[1]))
        items = await proxies.list()
        await callback.message.edit_text(
            texts.PROXIES_HEADER if items else texts.NO_PROXIES,
            reply_markup=proxies_kb(items),
        )
        await callback.answer(texts.PROXY_DELETED)

    @router.callback_query(F.data.regexp(r"^vnc:done:\d+$"))
    async def vnc_done(callback: CallbackQuery) -> None:
        await vnc.mark_done(int(callback.data.rsplit(":", 1)[1]))
        await callback.message.edit_text("🖥 Сессия входа завершена.")
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^vnc:extend:\d+$"))
    async def vnc_extend(callback: CallbackQuery) -> None:
        session = await vnc.extend(int(callback.data.rsplit(":", 1)[1]))
        remaining = await vnc.remaining_minutes(session)
        await callback.answer(f"⏱ Продлено, осталось {remaining} мин.", show_alert=True)

    @router.callback_query(F.data.regexp(r"^runs:\d+$"))
    async def run_history(callback: CallbackQuery) -> None:
        site_id = int(callback.data.rsplit(":", 1)[1])
        items = await runs.recent(site_id)
        if not items:
            await callback.message.edit_text(texts.NO_RUNS)
            await callback.answer()
            return
        lines = [texts.RUNS_HEADER]
        for run in items:
            lines.append(
                f"{_status_mark(run.status)} {format_local(run.started_at, settings.tz)} — "
                f"{run.rows_count} строк, новых {run.new_count}"
            )
        rows = [
            [InlineKeyboardButton(text="🔁 Повторить", callback_data=f"run:retry:{site_id}")],
            [InlineKeyboardButton(text=texts.BACK, callback_data=f"site:settings:{site_id}")],
        ]
        await callback.message.edit_text(
            "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^run:retry:\d+$"))
    async def retry_run(callback: CallbackQuery) -> None:
        job_id = await runs.retry(int(callback.data.rsplit(":", 1)[1]))
        await callback.answer(
            texts.SITE_STARTED if job_id is not None else texts.SITE_ALREADY_RUNNING,
            show_alert=job_id is None,
        )

    return router


def _status_mark(status) -> str:
    return {
        "ok": "✅",
        "partial": "⚠️",
        "failed": "❌",
        "running": "⏳",
        "canceled": "🚫",
    }.get(str(status), "•")

