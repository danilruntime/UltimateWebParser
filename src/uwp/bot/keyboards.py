"""Inline keyboards used by the bot, in the admin's language.

Every builder takes ``lang`` last; the labels come from :mod:`uwp.bot.texts`, so
a keyboard can never be rendered in a language the screen above it is not in.
"""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LinkPreviewOptions,
    ReplyKeyboardMarkup,
)

from uwp.bot import texts
from uwp.i18n import Lang, t

# A card is a stack of plain lines; Telegram would otherwise unfold a preview of
# the parser URL or a site name that looks like a link and push the buttons off.
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

# Reply-keyboard labels are also *filters*: the handler has to recognise the
# button the user pressed. All four labels exist in both languages, so a user
# who switches language without pressing /start keeps a working keyboard (the
# old one stays under the chat input until the new one is sent). Dicts rather
# than functions because the filters need every label up front, not one.
BTN_SITES = {lang: t(lang, "bot.menu.button.sites") for lang in Lang}
BTN_PROFILES = {lang: t(lang, "bot.menu.button.profiles") for lang in Lang}
BTN_PROXIES = {lang: t(lang, "bot.menu.button.proxies") for lang in Lang}
BTN_HELP = {lang: t(lang, "bot.menu.button.help") for lang in Lang}
BTN_LANGUAGE = {lang: t(lang, "bot.language.button") for lang in Lang}


def sites_list_kb(sites, lang) -> InlineKeyboardMarkup:
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
    rows.append(
        [InlineKeyboardButton(text=texts.add_site(lang), callback_data="site:add")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def site_card_kb(site, lang) -> InlineKeyboardMarkup:
    """Toggle, run now, settings, login, history, delete and back."""
    toggle = texts.toggle_off(lang) if site.enabled else texts.toggle_on(lang)
    rows = [
        [InlineKeyboardButton(text=toggle, callback_data=f"site:toggle:{site.id}")],
        [
            InlineKeyboardButton(
                text=texts.run_now(lang), callback_data=f"site:run:{site.id}"
            ),
            InlineKeyboardButton(
                text=texts.settings(lang), callback_data=f"site:settings:{site.id}"
            ),
        ],
    ]
    if site.profile_id is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text=texts.vnc_login_button(lang),
                    callback_data=f"site:login:{site.id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=texts.history(lang), callback_data=f"runs:{site.id}"
            ),
            InlineKeyboardButton(
                text=texts.delete(lang), callback_data=f"site:del:{site.id}"
            ),
        ]
    )
    rows.append(
        [InlineKeyboardButton(text=texts.back(lang), callback_data="sites:list")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def vnc_session_kb(session_id: int, lang) -> InlineKeyboardMarkup:
    """«Done» and «+30 min» for an open login session."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.vnc_done_button(lang),
                    callback_data=f"vnc:done:{session_id}",
                ),
                InlineKeyboardButton(
                    text=texts.vnc_extend_button(lang),
                    callback_data=f"vnc:extend:{session_id}",
                ),
            ]
        ]
    )


def confirm_delete_kb(site, lang) -> InlineKeyboardMarkup:
    """Destructive actions ask once more before they run.

    The affirmative must not reuse the card's opener label: two identical
    buttons on two screens make the second tap feel like the first did nothing.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.confirm_delete_yes(lang),
                    callback_data=f"site:delok:{site.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.cancel(lang), callback_data=f"site:{site.id}"
                )
            ],
        ]
    )


def parsers_kb(parsers, lang) -> InlineKeyboardMarkup:
    """Choose which parser a new site should use."""
    rows = [
        [
            InlineKeyboardButton(
                text=parser.title_for(lang), callback_data=f"site:add:{parser.key}"
            )
        ]
        for parser in parsers
    ]
    rows.append(
        [InlineKeyboardButton(text=texts.back(lang), callback_data="sites:list")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def menu_kb(lang) -> InlineKeyboardMarkup:
    """The main menu: everything an admin may open."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BTN_SITES[lang], callback_data="sites:list"
                )
            ],
            [
                InlineKeyboardButton(
                    text=BTN_PROFILES[lang], callback_data="profiles:list"
                )
            ],
            [
                InlineKeyboardButton(
                    text=BTN_PROXIES[lang], callback_data="proxies:list"
                )
            ],
            [
                InlineKeyboardButton(
                    text=BTN_LANGUAGE[lang], callback_data="lang:screen"
                )
            ],
        ]
    )


def language_kb(lang) -> InlineKeyboardMarkup:
    """The two languages; the current one carries a tick so the screen answers
    "which one am I on?" without an extra line of text."""
    rows = []
    for code in (Lang.ru, Lang.en):
        label = texts.language_option(lang, code)
        if code == lang:
            label = f"{label} ✓"
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"lang:set:{code}")]
        )
    rows.append(
        [InlineKeyboardButton(text=texts.back(lang), callback_data="menu:main")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reply_menu_kb(lang) -> ReplyKeyboardMarkup:
    """The permanent keyboard under the chat input.

    ``resize_keyboard`` keeps it three rows tall on a phone instead of letting
    Telegram stretch each button to full width.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=BTN_SITES[lang]),
                KeyboardButton(text=BTN_PROFILES[lang]),
            ],
            [
                KeyboardButton(text=BTN_PROXIES[lang]),
                KeyboardButton(text=BTN_HELP[lang]),
            ],
            [KeyboardButton(text=BTN_LANGUAGE[lang])],
        ],
        resize_keyboard=True,
    )
