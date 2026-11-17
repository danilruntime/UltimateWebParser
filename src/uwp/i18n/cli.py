"""CLI strings. Filled in by the CLI localisation task; the seed key proves the wiring."""

from uwp.i18n import Lang

CATALOG: dict[Lang, dict[str, str]] = {
    Lang.ru: {
        "cli.ok": "Готово.",
    },
    Lang.en: {
        "cli.ok": "Done.",
    },
}
