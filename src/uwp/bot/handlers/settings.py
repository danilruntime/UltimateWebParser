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
from uwp.i18n import DEFAULT_LANG, Lang
from uwp.parsers.base import BaseParser

log = logging.getLogger(__name__)

MAX_INTERVAL_MINUTES = 10_080  # one week
_INTERVAL_RE = re.compile(r"^\s*(\d+)\s*(?:[-–\s]\s*(\d+)\s*)?$")


class IntervalInput(StatesGroup):
    waiting = State()


class ParamInput(StatesGroup):
    waiting = State()


def parse_interval(text: str, lang: Lang = DEFAULT_LANG) -> tuple[int, int]:
    """Parse ``45-90``, ``45 90`` or ``60`` into ``(min, max)`` minutes."""
    match = _INTERVAL_RE.match(text or "")
    if match is None:
        raise ValueError(texts.interval_number(lang))
    low = int(match.group(1))
    high = int(match.group(2)) if match.group(2) is not None else low
    if low < 1 or high < 1:
        raise ValueError(texts.interval_positive(lang))
    if low > high:
        raise ValueError(texts.interval_order(lang))
    if high > MAX_INTERVAL_MINUTES:
        raise ValueError(texts.interval_max(lang, MAX_INTERVAL_MINUTES))
    return low, high


def settings_kb(site: Site, lang: Lang = DEFAULT_LANG) -> InlineKeyboardMarkup:
    """One button per editable setting plus «back»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.interval_button(lang),
                    callback_data=f"set:interval:{site.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.profile_button(lang),
                    callback_data=f"set:profile:{site.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.proxy_button(lang),
                    callback_data=f"set:proxy:{site.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.export_button(lang),
                    callback_data=f"set:export:{site.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.params_button(lang),
                    callback_data=f"set:params:{site.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.back(lang), callback_data=f"set:back:{site.id}"
                )
            ],
        ]
    )


def params_kb(
    parser: BaseParser, site: Site, lang: Lang = DEFAULT_LANG
) -> InlineKeyboardMarkup:
    """One button per settings field, showing the value it currently has."""
    rows: list[list[InlineKeyboardButton]] = []
    for name, field in parser.settings_model.model_fields.items():
        current = (site.parser_settings or {}).get(
            name, field.get_default(call_default_factory=True)
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{_field_title(field, lang)}: "
                        f"{_display_value(current, lang)}"
                    ),
                    callback_data=f"param:{site.id}:{name}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=texts.back(lang), callback_data=f"set:{site.id}"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def next_literal(field: Any, current: Any, lang: Lang = DEFAULT_LANG) -> Any:
    """Cycle a ``Literal`` field to its next allowed value."""
    values = list(get_args(field.annotation))
    if not values:
        raise ValueError(texts.param_not_enum(lang))
    try:
        index = values.index(current)
    except ValueError:
        return values[0]
    return values[(index + 1) % len(values)]


def next_export(mode: ExportMode | str) -> ExportMode:
    order = [ExportMode.always, ExportMode.changes, ExportMode.never]
    return order[(order.index(ExportMode(str(mode))) + 1) % len(order)]


def apply_param(
    parser: BaseParser,
    site: Site,
    name: str,
    raw: str,
    lang: Lang = DEFAULT_LANG,
) -> dict[str, Any]:
    """Validate one field and return the merged settings dict."""
    field = parser.settings_model.model_fields.get(name)
    if field is None:
        raise ValueError(texts.param_unknown(lang, name))
    merged = dict(site.parser_settings or {})
    merged[name] = _convert(raw, field.annotation, lang)
    try:
        validated = parser.settings_model(**merged)
    except ValidationError as exc:
        error = exc.errors()[0]
        location = ".".join(str(part) for part in error.get("loc", ())) or name
        hint = _bounds(field, lang)
        detail = f" ({hint})" if hint else ""
        raise ValueError(
            texts.param_invalid(lang, raw, location, detail)
        ) from exc
    return validated.model_dump()


def _convert(raw: str, annotation: Any, lang: Lang) -> Any:
    if get_origin(annotation) is Literal:
        values = get_args(annotation)
        if raw not in values:
            raise ValueError(
                texts.param_allowed(lang, ", ".join(map(str, values)))
            )
        return raw
    if annotation is int:
        try:
            return int(raw)
        except ValueError:
            raise ValueError(texts.param_int(lang)) from None
    if annotation is float:
        try:
            return float(raw)
        except ValueError:
            raise ValueError(texts.param_float(lang)) from None
    return raw


def _bounds(field: Any, lang: Lang) -> str:
    """A localised hint about the allowed range of a numeric field."""
    parts = []
    for constraint in getattr(field, "metadata", ()) or ():
        for attr in ("ge", "gt", "le", "lt"):
            value = getattr(constraint, attr, None)
            if value is not None:
                parts.append(texts.bound(lang, attr, value))
    return ", ".join(parts)


def _field_title(field: Any, lang: Lang) -> str:
    """A short label from the field description, without the hints."""
    text = field.description or field.title or ""
    for separator in (" (", ";", ":"):
        text = text.split(separator)[0]
    return text.strip() or texts.param_fallback(lang)


def _display_value(value: Any, lang: Lang) -> str:
    if isinstance(value, bool):
        return texts.yes(lang) if value else texts.no(lang)
    return str(value)


def build_router(settings, deps) -> Router:
    router = Router(name="settings")
    service = deps.sites

    @router.callback_query(F.data.regexp(r"^site:settings:\d+$"))
    async def open_settings(
        callback: CallbackQuery, state: FSMContext, lang: Lang
    ) -> None:
        await state.clear()
        site_id = int(callback.data.rsplit(":", 1)[1])
        site = await service.get(site_id)
        if site is None:
            await callback.answer(texts.site_not_found(lang), show_alert=True)
            return
        await callback.message.edit_text(
            texts.settings_title(lang, site.name)
            + "\n"
            + texts.settings_choose(lang),
            reply_markup=settings_kb(site, lang),
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^set:back:\d+$"))
    async def back_to_card(
        callback: CallbackQuery, state: FSMContext, lang: Lang
    ) -> None:
        await state.clear()
        site_id = int(callback.data.rsplit(":", 1)[1])
        site = await service.get(site_id)
        if site is None:
            await callback.answer(texts.site_not_found(lang), show_alert=True)
            return
        await callback.message.edit_text(
            texts.site_card(lang, site, None),
            reply_markup=keyboards.site_card_kb(site, lang),
            link_preview_options=keyboards.NO_PREVIEW,
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^set:interval:\d+$"))
    async def ask_interval(
        callback: CallbackQuery, state: FSMContext, lang: Lang
    ) -> None:
        site_id = int(callback.data.rsplit(":", 1)[1])
        await state.set_state(IntervalInput.waiting)
        await state.update_data(site_id=site_id)
        await callback.message.answer(texts.ask_interval(lang))
        await callback.answer()

    @router.message(IntervalInput.waiting)
    async def save_interval(message: Message, state: FSMContext, lang: Lang) -> None:
        data = await state.get_data()
        try:
            low, high = parse_interval(message.text or "", lang)
        except ValueError as exc:
            await message.answer(
                f"❌ {texts.esc(exc)}\n{texts.ask_interval(lang)}"
            )
            return
        site = await service.update(
            data["site_id"], interval_min_min=low, interval_max_min=high
        )
        await state.clear()
        await message.answer(
            texts.interval_saved(lang, site.name, low, high),
            reply_markup=settings_kb(site, lang),
        )

    @router.callback_query(F.data.regexp(r"^set:export:\d+$"))
    async def cycle_export(callback: CallbackQuery, lang: Lang) -> None:
        site_id = int(callback.data.rsplit(":", 1)[1])
        site = await service.get(site_id)
        if site is None:
            await callback.answer(texts.site_not_found(lang), show_alert=True)
            return
        site = await service.update(
            site_id, export_mode=str(next_export(site.export_mode))
        )
        await callback.message.edit_text(
            texts.export_set(lang, texts.export_label(lang, site.export_mode)),
            reply_markup=settings_kb(site, lang),
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^set:profile:\d+$"))
    async def choose_profile(
        callback: CallbackQuery, state: FSMContext, lang: Lang
    ) -> None:
        await state.clear()
        site_id = int(callback.data.rsplit(":", 1)[1])
        site = await service.get(site_id)
        if site is None:
            await callback.answer(texts.site_not_found(lang), show_alert=True)
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
                        text=texts.clean_profile_button(lang),
                        callback_data=f"set:profile:{site_id}:clean",
                    )
                ]
            )
        elif not rows:
            await callback.answer(texts.need_login_profile(lang), show_alert=True)
            return
        rows.append(
            [
                InlineKeyboardButton(
                    text=texts.back(lang), callback_data=f"site:settings:{site_id}"
                )
            ]
        )
        await callback.message.edit_text(
            texts.choose_profile(lang),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^set:profile:\d+:(clean|\d+)$"))
    async def save_profile(callback: CallbackQuery, lang: Lang) -> None:
        parts = callback.data.split(":")
        site_id, choice = int(parts[2]), parts[3]
        if choice == "clean":
            site = await service.update(
                site_id, profile_mode=str(ProfileKind.clean), profile_id=None
            )
        else:
            site = await service.update(
                site_id,
                profile_mode=str(ProfileKind.persistent),
                profile_id=int(choice),
            )
        await callback.message.edit_text(
            texts.profile_set(lang, texts.profile_label(lang, site)),
            reply_markup=settings_kb(site, lang),
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^set:proxy:\d+$"))
    async def choose_proxy(
        callback: CallbackQuery, state: FSMContext, lang: Lang
    ) -> None:
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
            [
                InlineKeyboardButton(
                    text=texts.no_proxy(lang),
                    callback_data=f"set:proxy:{site_id}:none",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=texts.back(lang), callback_data=f"site:settings:{site_id}"
                )
            ]
        )
        await callback.message.edit_text(
            texts.choose_proxy(lang),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^set:proxy:\d+:(none|\d+)$"))
    async def save_proxy(callback: CallbackQuery, lang: Lang) -> None:
        parts = callback.data.split(":")
        site_id, choice = int(parts[2]), parts[3]
        proxy_id = None if choice == "none" else int(choice)
        site = await service.update(site_id, proxy_id=proxy_id)
        await callback.message.edit_text(
            texts.proxy_set(
                lang, choice if proxy_id else texts.none_word(lang)
            ),
            reply_markup=settings_kb(site, lang),
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^set:params:\d+$"))
    async def open_params(
        callback: CallbackQuery, state: FSMContext, lang: Lang
    ) -> None:
        await state.clear()
        site_id = int(callback.data.rsplit(":", 1)[1])
        site = await service.get(site_id)
        parser = deps.registry.get(site.parser_key) if site else None
        if site is None or parser is None:
            await callback.answer(texts.parser_not_found(lang), show_alert=True)
            return
        await callback.message.edit_text(
            texts.params_header(lang), reply_markup=params_kb(parser, site, lang)
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^set:\d+$"))
    async def settings_root(
        callback: CallbackQuery, state: FSMContext, lang: Lang
    ) -> None:
        await state.clear()
        site_id = int(callback.data.split(":")[1])
        site = await service.get(site_id)
        if site is None:
            await callback.answer(texts.site_not_found(lang), show_alert=True)
            return
        await callback.message.edit_text(
            texts.settings_title(lang, site.name),
            reply_markup=settings_kb(site, lang),
        )
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^param:\d+:[a-z_]+$"))
    async def edit_param(
        callback: CallbackQuery, state: FSMContext, lang: Lang
    ) -> None:
        _, site_id_raw, name = callback.data.split(":")
        site_id = int(site_id_raw)
        site = await service.get(site_id)
        parser = deps.registry.get(site.parser_key) if site else None
        if site is None or parser is None:
            await callback.answer(texts.parser_not_found(lang), show_alert=True)
            return
        field = parser.settings_model.model_fields.get(name)
        if field is None:
            await callback.answer(texts.param_not_found(lang), show_alert=True)
            return
        default = field.get_default(call_default_factory=True)
        current = (site.parser_settings or {}).get(name, default)
        if get_origin(field.annotation) is Literal:
            try:
                merged = apply_param(
                    parser, site, name, str(next_literal(field, current, lang)), lang
                )
            except ValueError as exc:
                await callback.answer(str(exc), show_alert=True)
                return
            site = await service.update(site_id, parser_settings=merged)
            await callback.message.edit_text(
                texts.params_header(lang), reply_markup=params_kb(parser, site, lang)
            )
            await callback.answer()
            return
        await state.set_state(ParamInput.waiting)
        await state.update_data(site_id=site_id, field=name)
        await callback.message.answer(
            texts.ask_param(lang, _field_title(field, lang))
        )
        await callback.answer()

    @router.message(ParamInput.waiting)
    async def save_param(message: Message, state: FSMContext, lang: Lang) -> None:
        data = await state.get_data()
        site = await service.get(data["site_id"])
        parser = deps.registry.get(site.parser_key) if site else None
        if site is None or parser is None:
            await state.clear()
            await message.answer(texts.parser_not_found(lang))
            return
        try:
            merged = apply_param(
                parser, site, data["field"], message.text or "", lang
            )
        except ValueError as exc:
            await message.answer(f"❌ {texts.esc(exc)}")
            return
        site = await service.update(site.id, parser_settings=merged)
        await state.clear()
        await message.answer(
            texts.param_saved(lang, data["field"]),
            reply_markup=params_kb(parser, site, lang),
        )

    return router
