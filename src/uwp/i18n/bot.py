"""Bot strings. Filled in by the bot localisation task; the seed key proves the wiring."""

from uwp.i18n import Lang

CATALOG: dict[Lang, dict[str, str]] = {
    Lang.ru: {
        "bot.error.not_found": "{name} не найден",
    },
    Lang.en: {
        "bot.error.not_found": "{name} not found",
    },
}
