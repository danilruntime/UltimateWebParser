from datetime import UTC, datetime

import pytest
from odf.config import ConfigItem
from odf.number import Number
from odf.opendocument import load
from odf.style import Style, TableColumnProperties
from odf.table import DatabaseRange, Table, TableCell, TableColumn, TableRow
from odf.text import A, P

from uwp.ods import prune_reports, report_filename, slugify, write_report
from uwp.parsers.base import Column


def _sheet(doc, name):
    for table in doc.spreadsheet.getElementsByType(Table):
        if table.getAttribute("name") == name:
            return table
    raise AssertionError(f"sheet {name!r} not found")


def _cell_text(node) -> str:
    """Concatenate every text node below ``node`` (handles ``text:a`` wrappers)."""
    parts = []
    if getattr(node, "data", None):
        parts.append(node.data)
    for child in getattr(node, "childNodes", ()):
        parts.append(_cell_text(child))
    return "".join(parts)


def _rows(table):
    out = []
    for row in table.getElementsByType(TableRow):
        cells = []
        for cell in row.getElementsByType(TableCell):
            repeat = int(cell.getAttribute("numbercolumnsrepeated") or 1)
            text = "".join(_cell_text(p) for p in cell.getElementsByType(P))
            cells.extend([text] * repeat)
        out.append(cells)
    return out


def test_slugify_transliterates():
    assert slugify("Steam — скидки") == "steam_skidki"
    assert slugify("  Ünïcode / Test!! ") == "unicode_test"


def test_slugify_full_alphabet():
    assert slugify("АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ") == (
        "abvgdeezhziyklmnoprstufkhtschshshch_y_eyuya"
    )


def test_filename():
    when = datetime(2026, 11, 13, 1, 20, tzinfo=UTC)
    assert report_filename(
        "{parser_title}_{site_name}_{timestamp}", "Steam — скидки", "Steam US", when
    ) == "steam_skidki_steam_us_2026-11-13_01-20.ods"


def test_filename_appends_extension_and_keeps_literal_parts():
    when = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    assert report_filename("report-{timestamp}", "P", "S", when) == "report-2026-01-02_03-04.ods"
    assert report_filename("{site_name}.ods", "P", "S", when) == "s.ods"


def test_filename_cannot_escape_the_exports_directory():
    """The template is operator input; ``../`` must not write outside exports."""
    when = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)

    assert "/" not in report_filename("../{site_name}", "P", "S", when)
    assert "\\" not in report_filename("..\\{site_name}", "P", "S", when)
    assert report_filename("../../etc/passwd", "P", "S", when) == "etc_passwd.ods"
    assert report_filename("/abs/{site_name}", "P", "S", when) == "abs_s.ods"


def test_report_contents(tmp_path):
    cols = [Column("title", "Title", 30), Column("url", "URL", 40), Column("price", "Price", 10)]
    rows = [
        {"external_key": "1", "title": "A", "url": "https://x/1", "price": 1.5},
        {"external_key": "2", "title": "B", "url": "https://x/2", "price": 2.0},
    ]
    path = write_report(tmp_path / "t.ods", rows, cols, {"site": "S"}, new_keys={"2"})
    doc = load(str(path))
    data = _rows(_sheet(doc, "Данные"))
    assert data[0][:4] == ["Title", "URL", "Price", "Новое"]
    assert data[1][0] == "A" and data[1][3] == "нет"
    assert data[2][0] == "B" and data[2][3] == "да"
    meta = _rows(_sheet(doc, "Метаданные"))
    assert meta[0][:2] == ["site", "S"]


def test_report_hyperlinks_and_widths(tmp_path):
    cols = [Column("title", "Title", 30), Column("url", "URL", 40)]
    rows = [{"external_key": "1", "title": "A", "url": "https://x/1"}]
    path = write_report(tmp_path / "sub" / "t.ods", rows, cols, {})
    assert path.exists()
    doc = load(str(path))
    table = _sheet(doc, "Данные")
    columns = table.getElementsByType(TableColumn)
    widths = {
        s.getAttribute("name"): s.getElementsByType(TableColumnProperties)[0].getAttribute(
            "columnwidth"
        )
        for s in doc.automaticstyles.getElementsByType(Style)
        if s.getAttribute("family") == "table-column"
    }
    assert [widths[c.getAttribute("stylename")] for c in columns] == ["30pt", "40pt"]
    # Hyperlink: the URL cell carries an <text:a href="..."> element.
    links = doc.getElementsByType(A)
    assert any(a.getAttribute("href") == "https://x/1" for a in links)
    # No «Новое» column when new_keys is None.
    assert _rows(table)[0] == ["Title", "URL"]


def test_report_links_url_and_suffixed_url_keys(tmp_path):
    cols = [Column("url", "URL", 40), Column("image_url", "Image", 40), Column("note", "Note", 20)]
    rows = [
        {
            "external_key": "1",
            "url": "https://x/page",
            "image_url": "https://x/img.png",
            "note": "https://x/not-a-link",
        }
    ]
    path = write_report(tmp_path / "t.ods", rows, cols, {})
    doc = load(str(path))
    hrefs = [a.getAttribute("href") for a in doc.getElementsByType(A)]
    assert hrefs == ["https://x/page", "https://x/img.png"]


def test_report_drops_dangerous_hyperlink_schemes(tmp_path):
    """``url`` comes from scraped HTML: a javascript:/file: target is data, not a link."""
    cols = [Column("url", "URL", 40)]
    rows = [
        {"external_key": "1", "url": "javascript:alert(document.cookie)"},
        {"external_key": "2", "url": "file:///etc/passwd"},
        {"external_key": "3", "url": "JaVaScRiPt:alert(1)"},
        {"external_key": "4", "url": "https://x/ok"},
    ]
    path = write_report(tmp_path / "t.ods", rows, cols, {})

    doc = load(str(path))
    hrefs = [a.getAttribute("href") for a in doc.getElementsByType(A)]
    assert hrefs == ["https://x/ok"]
    # The cell keeps the value as text, so the admin still sees what was scraped.
    texts = [p for row in _rows(_sheet(doc, "Данные")) for p in row]
    assert "javascript:alert(document.cookie)" in texts
    assert "file:///etc/passwd" in texts


def test_report_skips_empty_url_and_highlights_changed(tmp_path):
    cols = [Column("title", "Title", 30), Column("url", "URL", 40)]
    rows = [
        {"external_key": "1", "title": "A", "url": ""},
        {"external_key": "2", "title": "B", "url": "https://x/2"},
    ]
    path = write_report(
        tmp_path / "t.ods", rows, cols, {"changed_keys": "2", "site": "S"}, new_keys=set()
    )
    doc = load(str(path))
    _sheet(doc, "Данные")
    links = doc.getElementsByType(A)
    assert [a.getAttribute("href") for a in links] == ["https://x/2"]
    # Changed row carries a cell style with a light-yellow background.
    styles = doc.styles.getElementsByType(Style)
    assert any(
        s.getAttribute("family") == "table-cell" and (s.getAttribute("name") or "").startswith("changed")
        for s in styles
    )
    meta = _rows(_sheet(doc, "Метаданные"))
    assert meta[0][:2] == ["site", "S"]


def test_report_autofilter_freeze_and_number_format(tmp_path):
    cols = [
        Column("title", "Title", 30),
        Column("url", "URL", 40),
        Column("price", "Price", 12, "0.00"),
    ]
    rows = [{"external_key": "1", "title": "A", "url": "https://x/1", "price": 2.0}]
    path = write_report(tmp_path / "t.ods", rows, cols, {})
    doc = load(str(path))

    ranges = doc.spreadsheet.getElementsByType(DatabaseRange)
    assert len(ranges) == 1
    assert ranges[0].getAttribute("name") == "Autofilter"
    assert ranges[0].getAttribute("displayfilterbuttons") == "true"
    assert ranges[0].getAttribute("targetrangeaddress") == "$Данные.A1:$Данные.C2"

    # database-ranges must follow every sheet in document order.
    tags = [child.tagName for child in doc.spreadsheet.childNodes]
    assert tags.index("table:database-ranges") > tags.index("table:table", 1)

    settings = doc.settings.getElementsByType(ConfigItem)
    frozen = {
        item.getAttribute("name"): item.firstChild.data
        for item in settings
        if item.getAttribute("name") in {"VerticalSplitMode", "VerticalSplitPosition"}
    }
    assert frozen == {"VerticalSplitMode": "2", "VerticalSplitPosition": "1"}

    # The "0.00" column gets a data style with two decimals.
    numbers = doc.styles.getElementsByType(Number)
    assert [n.getAttribute("decimalplaces") for n in numbers] == ["2"]
    data_styles = {
        s.getAttribute("name"): s.getAttribute("datastylename")
        for s in doc.styles.getElementsByType(Style)
        if s.getAttribute("datastylename")
    }
    assert data_styles["value3"] == "num3"


def test_prune_keeps_newest(tmp_path):
    for i in range(5):
        (tmp_path / f"f{i}.ods").write_bytes(b"x")
    assert prune_reports(tmp_path, keep=2) == 3
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f3.ods", "f4.ods"]


def test_prune_ignores_other_files_and_missing_dir(tmp_path):
    (tmp_path / "a.ods").write_bytes(b"x")
    (tmp_path / "b.txt").write_bytes(b"x")
    assert prune_reports(tmp_path, keep=0) == 1
    assert [p.name for p in tmp_path.iterdir()] == ["b.txt"]
    assert prune_reports(tmp_path / "missing", keep=5) == 0


@pytest.mark.parametrize("keep", [1, 3])
def test_prune_keep_larger_than_count(tmp_path, keep):
    (tmp_path / "a.ods").write_bytes(b"x")
    assert prune_reports(tmp_path, keep=keep) == 0
