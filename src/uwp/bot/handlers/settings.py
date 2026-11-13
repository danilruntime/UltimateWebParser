"""Site settings screens: interval, profile, proxy, export mode and parser params."""
import logging
import re
from typing import Any, Literal, get_args, get_origin

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from pydantic import ValidationError

from uwp.bot import keyboards, texts
from uwp.domain import ExportMode, ProfileKind, Site
from uwp.parsers.base import BaseParser

log = logging.getLogger(__name__)

MAX_INTERVAL_MINUTES = 10_080  # one week
_INTERVAL_RE = re.compile(r"^\s*(\d+)\s*(?:[-–\s]\s*(\d+)\s*)?$")


class IntervalInput(StatesGroup):
    waiting = State()


class ParamInput(StatesGroup):
    waiting = State()


def parse_interval(text: str) -> tuple[int, int]:
    """Parse ``45-90``, ``45 90`` or ``60`` into ``(min, max)`` minutes."""
    match = _INTERVAL_RE.match(text or "")
    if match is None:
        raise ValueError("интервал должен быть числом или диапазоном, например 45-90")
    low = int(match.group(1))
    high = int(match.group(2)) if match.group(2) is not None else low
    if low < 1 or high < 1:
        raise ValueError("интервал должен быть больше нуля")
    if low > high:
        raise ValueError("минимум не может быть больше максимума")
    if high > MAX_INTERVAL_MINUTES:
        raise ValueError(f"максимум — {MAX_INTERVAL_MINUTES} минут (одна неделя)")
    return low, high


def settings_kb(site: Site) -> InlineKeyboardMarkup:
    """One button per editable setting plus «back»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏱ Интервал", callback_data=f"set:interval:{site.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="👤 Профиль", callback_data=f"set:profile:{site.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🌍 Прокси", callback_data=f"set:proxy:{site.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 Экспорт", callback_data=f"set:export:{site.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔧 Параметры парсера", callback_data=f"set:params:{site.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.BACK, callback_data=f"set:back:{site.id}"
                )
            ],
        ]
    )


def params_kb(parser: BaseParser, site: Site) -> InlineKeyboardMarkup:
    """One button per settings field, showing the value it currently has."""
    rows: list[list[InlineKeyboardButton]] = []
    for name, field in parser.settings_model.model_fields.items():
        current = (site.parser_settings or {}).get(name, field.get_default(call_default_factory=True))
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{_field_title(field)}: {_display_value(current)}",
                    callback_data=f"param:{site.id}:{name}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text=texts.BACK, callback_data=f"set:{site.id}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def next_literal(field: Any, current: Any) -> Any:
    """Cycle a ``Literal`` field to its next allowed value."""
    values = list(get_args(field.annotation))
    if not values:
        raise ValueError("поле не является перечислением")
    try:
        index = values.index(current)
    except ValueError:
        return values[0]
    return values[(index + 1) % len(values)]


def next_export(mode: ExportMode | str) -> ExportMode:
    order = [ExportMode.always, ExportMode.changes, ExportMode.never]
    return order[(order.index(ExportMode(str(mode))) + 1) % len(order)]


def apply_param(parser: BaseParser, site: Site, name: str, raw: str) -> dict[str, Any]:
    """Validate one field and return the merged settings dict."""
    field = parser.settings_model.model_fields.get(name)
    if field is None:
        raise ValueError(f"неизвестный параметр: {name}")
    merged = dict(site.parser_settings or {})
    merged[name] = _convert(raw, field.annotation)
    try:
        validated = parser.settings_model(**merged)
    except ValidationError as exc:
        error = exc.errors()[0]
        location = ".".join(str(part) for part in error.get("loc", ())) or name
        hint = _bounds(field)
        detail = f" ({hint})" if hint else ""
        raise ValueError(f"недопустимое значение «{raw}» для {location}{detail}") from exc
    return validated.model_dump()


def _convert(raw: str, annotation: Any) -> Any:
    if get_origin(annotation) is Literal:
        values = get_args(annotation)
        if raw not in values:
            raise ValueError(f"допустимые значения: {', '.join(map(str, values))}")
        return raw
    if annotation is int:
        try:
            return int(raw)
        except ValueError:
            raise ValueError("введите целое число") from None
    if annotation is float:
        try:
            return float(raw)
        except ValueError:
            raise ValueError("введите число") from None
    return raw


def _bounds(field: Any) -> str:
    """A Russian hint about the allowed range of a numeric field."""
    parts = []
    for constraint in getattr(field, "metadata", ()) or ():
        for attr, template in (
            ("ge", "не меньше {}"),
            ("gt", "больше {}"),
            ("le", "не больше {}"),
            ("lt", "меньше {}"),
        ):
            value = getattr(constraint, attr, None)
            if value is not None:
                parts.append(template.format(value))
    return ", ".join(parts)


def _field_title(field: Any) -> str:
    """A short Russian label from the field description, without the hints."""
    text = field.description or field.title or ""
    for separator in (" (", ";", ":"):
        text = text.split(separator)[0]
    return text.strip() or "параметр"


def _display_value(value: Any) -> str:
    if isinstance(value, bool):
        return "да" if value else "нет"
    return str(value)


def build_router(settings, deps) -> Router:
    router = Router(name="settings")
    service = deps.sites

    @router.callback_query(F.data.regexp(r"^site:settings:\d+$"))
    async def open_settings(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        site_id = int(callback.data.rsplit(":", 1)[1])
        site = await service.get(site_id)
        if site is None:
            await callback.answer("Сайт не найден", show_alert=True)
            return
        await callback.message.edit_text(
            f"⚙️ <b>{texts.esc(site.name)}</b>\nВыберите, что изменить:",
            reply_markup=settings_kb(site),
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^set:back:\d+$"))
    async def back_to_card(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        site_id = int(callback.data.rsplit(":", 1)[1])
        site = await service.get(site_id)
        if site is None:
            await callback.answer("Сайт не найден", show_alert=True)
            return
        await callback.message.edit_text(
            texts.site_card(site, None),
            reply_markup=keyboards.site_card_kb(site),
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^set:interval:\d+$"))
    async def ask_interval(callback: CallbackQuery, state: FSMContext) -> None:
        site_id = int(callback.data.rsplit(":", 1)[1])
        await state.set_state(IntervalInput.waiting)
        await state.update_data(site_id=site_id)
        await callback.message.answer(texts.ASK_INTERVAL)
        await callback.answer()

    @router.message(IntervalInput.waiting)
    async def save_interval(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        try:
            low, high = parse_interval(message.text or "")
        except ValueError as exc:
            await message.answer(f"❌ {exc}\n{texts.ASK_INTERVAL}")
            return
        site = await service.update(
            data["site_id"], interval_min_min=low, interval_max_min=high
        )
        await state.clear()
        await message.answer(
            texts.INTERVAL_SAVED.format(low=low, high=high, name=site.name),
            reply_markup=settings_kb(site),
        )

    @router.callback_query(F.data.regexp(r"^set:export:\d+$"))
    async def cycle_export(callback: CallbackQuery) -> None:
        site_id = int(callback.data.rsplit(":", 1)[1])
        site = await service.get(site_id)
        if site is None:
            await callback.answer("Сайт не найден", show_alert=True)
            return
        site = await service.update(site_id, export_mode=str(next_export(site.export_mode)))
        await callback.message.edit_text(
            texts.EXPORT_SET.format(mode=texts.export_label(site.export_mode)),
            reply_markup=settings_kb(site),
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^set:profile:\d+$"))
    async def choose_profile(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        site_id = int(callback.data.rsplit(":", 1)[1])
        site = await service.get(site_id)
        if site is None:
            await callback.answer("Сайт не найден", show_alert=True)
            return
        parser = deps.registry.get(site.parser_key)
        profiles = await deps.profiles.list()
        rows = [
            [
                InlineKeyboardButton(
                    text=f"👤 {p.name}", callback_data=f"set:profile:{site_id}:{p.id}"
                )
            ]
            for p in profiles
            if p.kind == ProfileKind.persistent
        ]
        if parser is None or not parser.requires_login:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="🧼 Чистый профиль",
                        callback_data=f"set:profile:{site_id}:clean",
                    )
                ]
            )
        elif not rows:
            await callback.answer(
                "Парсеру нужен вход: создайте постоянный профиль в разделе «Профили».",
                show_alert=True,
            )
            return
        rows.append(
            [InlineKeyboardButton(text=texts.BACK, callback_data=f"site:settings:{site_id}")]
        )
        await callback.message.edit_text(
            texts.CHOOSE_PROFILE, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^set:profile:\d+:(clean|\d+)$"))
    async def save_profile(callback: CallbackQuery) -> None:
        parts = callback.data.split(":")
        site_id, choice = int(parts[2]), parts[3]
        if choice == "clean":
            site = await service.update(
                site_id, profile_mode=str(ProfileKind.clean), profile_id=None
            )
        else:
            site = await service.update(
                site_id, profile_mode=str(ProfileKind.persistent), profile_id=int(choice)
            )
        await callback.message.edit_text(
            texts.PROFILE_SET.format(label=texts.profile_label(site)),
            reply_markup=settings_kb(site),
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^set:proxy:\d+$"))
    async def choose_proxy(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        site_id = int(callback.data.rsplit(":", 1)[1])
        proxies = await deps.proxies.list()
        rows = [
            [
                InlineKeyboardButton(
                    text=f"🌍 {p.name}", callback_data=f"set:proxy:{site_id}:{p.id}"
                )
            ]
            for p in proxies
        ]
        rows.append(
            [InlineKeyboardButton(text="🚫 Без прокси", callback_data=f"set:proxy:{site_id}:none")]
        )
        rows.append(
            [InlineKeyboardButton(text=texts.BACK, callback_data=f"site:settings:{site_id}")]
        )
        await callback.message.edit_text(
            texts.CHOOSE_PROXY, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^set:proxy:\d+:(none|\d+)$"))
    async def save_proxy(callback: CallbackQuery) -> None:
        parts = callback.data.split(":")
        site_id, choice = int(parts[2]), parts[3]
        proxy_id = None if choice == "none" else int(choice)
        site = await service.update(site_id, proxy_id=proxy_id)
        await callback.message.edit_text(
            texts.PROXY_SET.format(name=choice if proxy_id else "нет"),
            reply_markup=settings_kb(site),
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^set:params:\d+$"))
    async def open_params(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        site_id = int(callback.data.rsplit(":", 1)[1])
        site = await service.get(site_id)
        parser = deps.registry.get(site.parser_key) if site else None
        if site is None or parser is None:
            await callback.answer("Парсер не найден", show_alert=True)
            return
        await callback.message.edit_text(
            texts.PARAMS_HEADER, reply_markup=params_kb(parser, site)
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^set:\d+$"))
    async def settings_root(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        site_id = int(callback.data.split(":")[1])
        site = await service.get(site_id)
        if site is None:
            await callback.answer("Сайт не найден", show_alert=True)
            return
        await callback.message.edit_text(
            f"⚙️ <b>{texts.esc(site.name)}</b>", reply_markup=settings_kb(site)
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^param:\d+:[a-z_]+$"))
    async def edit_param(callback: CallbackQuery, state: FSMContext) -> None:
        _, site_id_raw, name = callback.data.split(":")
        site_id = int(site_id_raw)
        site = await service.get(site_id)
        parser = deps.registry.get(site.parser_key) if site else None
        if site is None or parser is None:
            await callback.answer("Парсер не найден", show_alert=True)
            return
        field = parser.settings_model.model_fields.get(name)
        if field is None:
            await callback.answer("Параметр не найден", show_alert=True)
            return
        default = field.get_default(call_default_factory=True)
        current = (site.parser_settings or {}).get(name, default)
        if get_origin(field.annotation) is Literal:
            try:
                merged = apply_param(parser, site, name, str(next_literal(field, current)))
            except ValueError as exc:
                await callback.answer(str(exc), show_alert=True)
                return
            site = await service.update(site_id, parser_settings=merged)
            await callback.message.edit_text(
                texts.PARAMS_HEADER, reply_markup=params_kb(parser, site)
            )
            await callback.answer()
            return
        await state.set_state(ParamInput.waiting)
        await state.update_data(site_id=site_id, field=name)
        await callback.message.answer(
            texts.ASK_PARAM.format(title=_field_title(field))
        )
        await callback.answer()

    @router.message(ParamInput.waiting)
    async def save_param(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        site = await service.get(data["site_id"])
        parser = deps.registry.get(site.parser_key) if site else None
        if site is None or parser is None:
            await state.clear()
            await message.answer("Парсер не найден")
            return
        try:
            merged = apply_param(parser, site, data["field"], message.text or "")
        except ValueError as exc:
            await message.answer(f"❌ {exc}")
            return
        site = await service.update(site.id, parser_settings=merged)
        await state.clear()
        await message.answer(
            texts.PARAM_SAVED.format(name=data["field"]),
            reply_markup=params_kb(parser, site),
        )

    return router
