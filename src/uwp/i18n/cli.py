"""CLI strings. Filled in by the CLI localisation task; one seed key proves wiring."""

from uwp.i18n import Lang

CATALOG: dict[Lang, dict[str, str]] = {
    Lang.ru: {
        "cli.ok": "Готово.",
    },
    Lang.en: {
        "cli.ok": "Done.",
    },
}
