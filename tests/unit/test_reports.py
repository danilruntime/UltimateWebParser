"""Tests for report naming, metadata and the ``export_mode`` decision."""
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest

from uwp.config import load_settings
from uwp.domain import ExportMode, Profile, ProfileKind, Proxy, ProxyScheme, Site
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


@pytest.fixture
def settings(tmp_path):
    return load_settings({"UWP_DATA_DIR": str(tmp_path), "UWP_TZ": "Europe/Moscow"})


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
    from odf.opendocument import load
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    doc = load(str(path))
    data = next(
        t for t in doc.spreadsheet.getElementsByType(Table) if t.getAttribute("name") == "Данные"
    )
    rows = data.getElementsByType(TableRow)
    header = [
        "".join(str(p) for p in c.getElementsByType(P))
        for c in rows[0].getElementsByType(TableCell)
    ]
    # «Новое» describes the data, not the mode: an ``always`` site that has a
    # new game today must say so in today's full snapshot.
    assert header[-1] == "Новое"
    flags = [
        "".join(str(p) for p in row.getElementsByType(TableCell)[-1].getElementsByType(P))
        for row in rows[1:]
    ]
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
    from odf.opendocument import load
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    doc = load(str(path))
    data = next(t for t in doc.spreadsheet.getElementsByType(Table) if t.getAttribute("name") == "Данные")
    rows = data.getElementsByType(TableRow)
    header = ["".join(str(p) for p in c.getElementsByType(P)) for c in rows[0].getElementsByType(TableCell)]
    assert header[-1] == "Новое"
    flags = [
        "".join(str(p) for p in row.getElementsByType(TableCell)[-1].getElementsByType(P))
        for row in rows[1:]
    ]
    assert flags == ["нет", "да"]
