"""Inline keyboards used by the bot."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from uwp.bot import texts


def sites_list_kb(sites) -> InlineKeyboardMarkup:
    """One row per site plus the «add» button."""
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'🟢' if site.enabled else '🔴'} {site.name}",
                callback_data=f"site:{site.id}",
            )
        ]
        for site in sites
        if site.id is not None
    ]
    rows.append([InlineKeyboardButton(text=texts.ADD_SITE, callback_data="site:add")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def site_card_kb(site) -> InlineKeyboardMarkup:
    """Toggle, run now, settings, login, history, delete and back."""
    toggle = texts.TOGGLE_OFF if site.enabled else texts.TOGGLE_ON
    rows = [
        [InlineKeyboardButton(text=toggle, callback_data=f"site:toggle:{site.id}")],
        [
            InlineKeyboardButton(
                text=texts.RUN_NOW, callback_data=f"site:run:{site.id}"
            ),
            InlineKeyboardButton(
                text=texts.SETTINGS, callback_data=f"site:settings:{site.id}"
            ),
        ],
    ]
    if site.profile_id is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🖥 Войти в профиль", callback_data=f"site:login:{site.id}"
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text="📜 История", callback_data=f"runs:{site.id}"),
            InlineKeyboardButton(
                text=texts.DELETE, callback_data=f"site:del:{site.id}"
            ),
        ]
    )
    rows.append([InlineKeyboardButton(text=texts.BACK, callback_data="sites:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def vnc_session_kb(session_id: int) -> InlineKeyboardMarkup:
    """«Done» and «+30 min» for an open login session."""
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


def confirm_delete_kb(site) -> InlineKeyboardMarkup:
    """Destructive actions ask once more before they run."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Да, удалить", callback_data=f"site:delok:{site.id}"
                )
            ],
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"site:{site.id}")],
        ]
    )


def parsers_kb(parsers) -> InlineKeyboardMarkup:
    """Choose which parser a new site should use."""
    rows = [
        [
            InlineKeyboardButton(
                text=parser.title, callback_data=f"site:add:{parser.key}"
            )
        ]
        for parser in parsers
    ]
    rows.append([InlineKeyboardButton(text=texts.BACK, callback_data="sites:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def menu_kb() -> InlineKeyboardMarkup:
    """The main menu: everything an admin may open."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🌐 Сайты", callback_data="sites:list")],
            [InlineKeyboardButton(text="👤 Профили", callback_data="profiles:list")],
            [InlineKeyboardButton(text="🌍 Прокси", callback_data="proxies:list")],
        ]
    )
