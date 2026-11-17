"""Report strings: sheet names and headers. Filled in by the report localisation task."""

from uwp.i18n import Lang

CATALOG: dict[Lang, dict[str, str]] = {
    Lang.ru: {
        "report.data_sheet": "Данные",
        "report.meta_sheet": "Метаданные",
        "report.new_column": "Новое",
    },
    Lang.en: {
        "report.data_sheet": "Data",
        "report.meta_sheet": "Metadata",
        "report.new_column": "New",
    },
}
