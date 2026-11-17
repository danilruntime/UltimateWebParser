"""Tests for report naming, metadata and the ``export_mode`` decision."""
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest
from odf.opendocument import load
from odf.table import Table, TableCell, TableRow
from odf.text import P

from uwp.config import load_settings
from uwp.domain import ExportMode, Profile, ProfileKind, Proxy, ProxyScheme, Site
from uwp.i18n import Lang
from uwp.ods import data_sheet, meta_sheet
from uwp.parsers.base import BaseParser, Column, _EmptySettings
from uwp.reports import export_report, report_meta, should_export

WHEN = datetime(2026, 11, 13, 12, 30, tzinfo=UTC)
ROWS = [
    {"external_key": "1", "title": "A", "url": "https://x/1"},
    {"external_key": "2", "title": "B", "url": "https://x/2"},
]


class FakeParser(BaseParser):
    key, title, description, url = "fake", "Fake parser", "", "https://example.com/list"
    settings_model = _EmptySettings
    columns: ClassVar[list[Column]] = [
        Column("title", "Title"),
        Column("url", "URL"),
    ]

    async def run(self, ctx):  # pragma: no cover - never called here
        raise NotImplementedError


class KeyedParser(FakeParser):
    """A parser wired to the catalogue: its title and headers have keys."""

    key = "keyed"
    title_key = "parser.steam_deals.title"
    columns: ClassVar[list[Column]] = [
        Column("title", "Title", title_key="parser.steam_deals.column.title"),
        Column("url", "URL", title_key="parser.steam_deals.column.url"),
    ]


@pytest.fixture
def settings(tmp_path):
    return load_settings({"UWP_DATA_DIR": str(tmp_path), "UWP_TZ": "Europe/Moscow"})


def _sheet(doc, name):
    for table in doc.spreadsheet.getElementsByType(Table):
        if table.getAttribute("name") == name:
            return table
    raise AssertionError(f"sheet {name!r} not found")


def _text(cell) -> str:
    """The visible text of a cell (``odfpy`` keeps it in ``text:p`` children)."""
    return "".join(str(p) for p in cell.getElementsByType(P))


def _site(**fields) -> Site:
    return Site(
        id=1,
        slug="steam_skidki",
        name="Steam — скидки",
        parser_key="fake",
        interval_min_min=1,
        interval_max_min=2,
        **fields,
    )


@pytest.mark.parametrize(
    ("mode", "new", "changed", "expected"),
    [
        (ExportMode.always, set(), set(), True),
        (ExportMode.always, {"1"}, set(), True),
        (ExportMode.changes, set(), set(), False),
        (ExportMode.changes, {"1"}, set(), True),
        (ExportMode.changes, set(), {"1"}, True),
        (ExportMode.never, {"1"}, {"2"}, False),
    ],
)
def test_export_mode_decision(mode, new, changed, expected):
    assert should_export(mode, new, changed) is expected


def test_report_lands_in_a_per_site_directory(settings):
    path = export_report(
        settings,
        site=_site(),
        parser=FakeParser(),
        rows=ROWS,
        profile=None,
        proxy=None,
        when=WHEN,
        duration_ms=1500,
        new_keys={"1"},
        changed_keys=set(),
    )

    assert path is not None and path.is_file()
    assert path.parent == settings.exports_dir / "steam_skidki"
    # Name template: ``{parser_title}_{site_name}_{timestamp}.ods``.
    assert path.name == "fake_parser_steam_skidki_2026-11-13_15-30.ods"


def test_an_english_report_translates_sheets_headers_and_meta(tmp_path):
    """The report language is a deployment setting, not the admin's chat language."""
    settings = load_settings(
        {
            "UWP_DATA_DIR": str(tmp_path),
            "UWP_TZ": "Europe/Moscow",
            "UWP_DEFAULT_LANGUAGE": "en",
        }
    )
    path = export_report(
        settings,
        site=_site(),
        parser=KeyedParser(),
        rows=ROWS,
        profile=None,
        proxy=None,
        when=WHEN,
        duration_ms=1500,
        new_keys={"1"},
        changed_keys=set(),
    )

    assert path is not None
    # The file name carries the localised parser title, so an English
    # deployment gets ``steam_deals`` rather than the transliterated Russian.
    assert path.name == "steam_deals_steam_skidki_2026-11-13_15-30.ods"
    doc = load(str(path))
    data = _sheet(doc, data_sheet(Lang.en))
    header = [
        _text(cell)
        for cell in data.getElementsByType(TableRow)[0].getElementsByType(TableCell)
    ]
    assert "Link" in header and "New" in header
    meta = _sheet(doc, meta_sheet(Lang.en))
    rows = meta.getElementsByType(TableRow)
    meta_labels = [_text(row.getElementsByType(TableCell)[0]) for row in rows]
    assert "Site" in meta_labels
    # The time in an English report is ISO-shaped, not the Russian dotted one.
    values = {
        _text(row.getElementsByType(TableCell)[0]): _text(
            row.getElementsByType(TableCell)[1]
        )
        for row in rows
    }
    assert values["Time"] == "2026-11-13 15:30"
    assert "Ср" not in values["Time"]


def test_report_language_overrides_the_default(tmp_path):
    settings = load_settings(
        {
            "UWP_DATA_DIR": str(tmp_path),
            "UWP_TZ": "Europe/Moscow",
            "UWP_DEFAULT_LANGUAGE": "ru",
            "UWP_REPORT_LANGUAGE": "en",
        }
    )
    path = export_report(
        settings,
        site=_site(),
        parser=FakeParser(),
        rows=ROWS,
        profile=None,
        proxy=None,
        when=WHEN,
        duration_ms=1,
        new_keys=set(),
        changed_keys=set(),
    )

    assert path is not None
    doc = load(str(path))
    assert _sheet(doc, data_sheet(Lang.en))


def test_always_mode_still_marks_new_rows(settings):
    path = export_report(
        settings,
        site=_site(export_mode=ExportMode.always),
        parser=FakeParser(),
        rows=ROWS,
        profile=None,
        proxy=None,
        when=WHEN,
        duration_ms=1,
        new_keys={"2"},
        changed_keys=set(),
    )

    assert path is not None
    doc = load(str(path))
    data = _sheet(doc, data_sheet(Lang.ru))
    rows = data.getElementsByType(TableRow)
    header = [_text(cell) for cell in rows[0].getElementsByType(TableCell)]
    # «Новое» describes the data, not the mode: an ``always`` site that has a
    # new game today must say so in today's full snapshot.
    assert header[-1] == "Новое"
    flags = [_text(row.getElementsByType(TableCell)[-1]) for row in rows[1:]]
    assert flags == ["нет", "да"]


def test_never_mode_writes_nothing(settings):
    path = export_report(
        settings,
        site=_site(export_mode=ExportMode.never),
        parser=FakeParser(),
        rows=ROWS,
        profile=None,
        proxy=None,
        when=WHEN,
        duration_ms=1,
        new_keys=set(),
        changed_keys=set(),
    )
    assert path is None
    assert not list(settings.exports_dir.rglob("*.ods"))


def test_two_runs_in_the_same_minute_do_not_overwrite(settings):
    def export() -> Path:
        return export_report(
            settings,
            site=_site(),
            parser=FakeParser(),
            rows=ROWS,
            profile=None,
            proxy=None,
            when=WHEN,
            duration_ms=1,
            new_keys=set(),
            changed_keys=set(),
        )

    first, second = export(), export()

    assert first != second
    assert first.is_file() and second.is_file()


def test_retention_prunes_old_reports(settings):
    settings = settings.model_copy(update={"exports_retention": 2})
    for index in range(4):
        export_report(
            settings,
            site=_site(),
            parser=FakeParser(),
            rows=[{"external_key": str(index), "title": "T", "url": ""}],
            profile=None,
            proxy=None,
            when=WHEN,
            duration_ms=1,
            new_keys={str(index)},
            changed_keys=set(),
        )

    assert len(list((settings.exports_dir / "steam_skidki").glob("*.ods"))) == 2


def test_meta_has_no_proxy_password_and_shows_the_local_time(settings):
    proxy = Proxy(
        name="DE-1",
        scheme=ProxyScheme.http,
        host="10.0.0.1",
        port=8080,
        username="user",
        password="s3cret",
        country="Германия",
    )
    profile = Profile(
        name="Steam-RU",
        kind=ProfileKind.persistent,
        seed=1,
        platform="Linux",
        brand="Chrome",
        brand_version="150",
        timezone="Europe/Moscow",
        locale="ru-RU",
        user_data_dir="/data/profiles/1",
    )
    meta = report_meta(
        site=_site(),
        parser=FakeParser(),
        profile=profile,
        proxy=proxy,
        when=WHEN,
        tz=settings.tz,
        duration_ms=2500,
        rows=ROWS,
        new_keys={"1", "2"},
        changed_keys={"1"},
        vanished=3,
    )

    assert "s3cret" not in " ".join(meta.values())
    assert "user" not in meta["Прокси"]
    assert meta["Прокси"] == "DE-1 (http, Германия)"
    assert meta["Профиль"] == "Steam-RU (постоянный)"
    assert meta["Время"] == "13.11.2026 15:30"
    assert meta["Новых"] == "2" and meta["Изменённых"] == "1" and meta["Исчезло"] == "3"
    assert meta["changed_keys"] == "1"
    assert meta["Длительность"] == "2.5 с"
    assert meta["URL"] == "https://example.com/list"


def test_changes_mode_marks_new_rows(settings):
    path = export_report(
        settings,
        site=_site(export_mode=ExportMode.changes),
        parser=FakeParser(),
        rows=ROWS,
        profile=None,
        proxy=None,
        when=WHEN,
        duration_ms=1,
        new_keys={"2"},
        changed_keys={"1"},
    )

    assert path is not None
    doc = load(str(path))
    data = _sheet(doc, data_sheet(Lang.ru))
    rows = data.getElementsByType(TableRow)
    header = [_text(cell) for cell in rows[0].getElementsByType(TableCell)]
    assert header[-1] == "Новое"
    flags = [_text(row.getElementsByType(TableCell)[-1]) for row in rows[1:]]
    assert flags == ["нет", "да"]
