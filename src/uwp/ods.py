"""LibreOffice Calc (``.ods``) report writing: naming, layout and export retention.

Files are produced directly with ``odfpy``; LibreOffice itself is never invoked.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from odf.number import Number, NumberStyle
from odf.opendocument import OpenDocumentSpreadsheet
from odf.style import (
    Style,
    TableCellProperties,
    TableColumnProperties,
    TextProperties,
)
from odf.table import (
    DatabaseRange,
    DatabaseRanges,
    Table,
    TableCell,
    TableColumn,
    TableColumns,
    TableRow,
)
from odf.text import A, P

from uwp.parsers.base import Column

DATA_SHEET = "Данные"
META_SHEET = "Метаданные"
NEW_COLUMN_TITLE = "Новое"
TIMESTAMP_FORMAT = "%Y-%m-%d_%H-%M"

# ``ъ`` and ``ь`` map to ``_`` so they survive as a separator instead of
# gluing neighbouring letters together (``съезд`` → ``s_ezd``).
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "_", "ы": "y", "ь": "_", "э": "e", "ю": "yu", "я": "ya",
}

_CHANGED_BACKGROUND = "#fff2cc"
_LINK_COLOR = "#0563c1"


def slugify(text: str) -> str:
    """Lowercase, transliterate Cyrillic and reduce to ``[a-z0-9_]``."""
    lowered = text.lower()
    transliterated = "".join(_TRANSLIT.get(ch, ch) for ch in lowered)
    # Strip accents (``Ü`` → ``u``) before dropping everything non-ASCII.
    decomposed = unicodedata.normalize("NFKD", transliterated)
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_")


def report_filename(template: str, parser_title: str, site_name: str, when: datetime) -> str:
    """Render the report file name; ``when`` is formatted in its own timezone.

    Only the substituted values are slugified; the literal parts of the
    template are kept as given — except for path separators. The template is
    operator input stored in the database, so ``../`` in it must not be able to
    write outside ``data/exports``.
    """
    name = template.format(
        parser_title=slugify(parser_title),
        site_name=slugify(site_name),
        timestamp=when.strftime(TIMESTAMP_FORMAT),
    )
    name = re.sub(r"[/\\]+", "_", name).lstrip("_.").strip(" ")
    if not name:
        name = f"report_{when.strftime(TIMESTAMP_FORMAT)}"
    if not name.endswith(".ods"):
        name += ".ods"
    return name


def _is_link_key(key: str) -> bool:
    """``url`` and any ``*_url`` column holds a hyperlink target."""
    return key == "url" or key.endswith("_url")


# Only web links become clickable cells. Column values come from scraped HTML,
# so ``javascript:``/``data:``/``file:`` must stay inert text: a report is opened
# by the admin in LibreOffice, whose hyperlink handler is another attack surface.
_LINK_SCHEMES = frozenset({"http", "https"})


def safe_href(value: str) -> str | None:
    """Return ``value`` when it is an http(s) URL, else ``None``.

    Checked case-insensitively and with leading control characters/whitespace
    stripped, since browsers ignore those before the scheme.
    """
    cleaned = value.strip().lstrip("\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\r")
    scheme, sep, _rest = cleaned.partition(":")
    if not sep or scheme.lower() not in _LINK_SCHEMES:
        return None
    return cleaned



def _changed_keys(meta: dict[str, Any]) -> set[str]:
    raw = meta.get("changed_keys") or ""
    return {part.strip() for part in str(raw).split(",") if part.strip()}


def _cell_style(
    doc: OpenDocumentSpreadsheet,
    name: str,
    *,
    bold: bool = False,
    link: bool = False,
    background: str | None = None,
    data_style: str | None = None,
) -> Style:
    """Create a table-cell style in ``office:styles`` and return it."""
    style = Style(name=name, family="table-cell")
    if data_style is not None:
        style.setAttribute("datastylename", data_style)
    if bold or link:
        props: dict[str, str] = {}
        if bold:
            props["fontweight"] = "bold"
        if link:
            props["color"] = _LINK_COLOR
            props["textunderlinestyle"] = "solid"
            props["textunderlinecolor"] = _LINK_COLOR
        style.addElement(TextProperties(**props))
    if background:
        style.addElement(TableCellProperties(backgroundcolor=background))
    doc.styles.addElement(style)
    return style


def _number_style(doc: OpenDocumentSpreadsheet, name: str, decimals: int) -> str:
    """Register a data style rendering numbers with ``decimals`` digits."""
    style = NumberStyle(name=name)
    style.addElement(
        Number(decimalplaces=decimals, minintegerdigits=1, grouping="false")
    )
    doc.styles.addElement(style)
    return name


def _column_width_points(characters: int) -> int:
    """Convert ``Column.width`` (a character count) into an ODF length in points.

    ``Column.width`` is a count of characters, as in Excel and openpyxl: parsers
    write 12 for an app id, 40 for a title. ODF wants an absolute length, and
    writing the number as points made a 12-character column 4.2 mm wide —
    "147599" then rendered as ``##`` in every delivered report.

    Measured in LibreOffice: the optimal width of a column holding text is
    5.1 pt per character for prose and 6.9 pt for long numbers (which are set
    in the same body font but are all digits). 7 pt per character plus a small
    pad therefore fits the worst case instead of clipping it.
    """
    return max(characters, 1) * 7 + 4


def _column_style(doc: OpenDocumentSpreadsheet, name: str, width: int) -> Style:
    style = Style(name=name, family="table-column")
    style.addElement(
        TableColumnProperties(columnwidth=f"{_column_width_points(width)}pt")
    )
    doc.automaticstyles.addElement(style)
    return style


def _fmt_decimals(fmt: str | None) -> int | None:
    """``"0.00"`` → ``2``; ``None`` or a format without decimals → ``None``."""
    if not fmt or "." not in fmt:
        return None
    tail = fmt.split(".", 1)[1]
    decimals = len(tail) - len(tail.lstrip("0#"))
    return decimals


def _text_cell(
    doc: OpenDocumentSpreadsheet,
    text: str,
    style: Style | None = None,
    href: str | None = None,
) -> TableCell:
    """Build a string cell; ``href`` wraps the text in a ``text:a`` hyperlink."""
    cell = TableCell(valuetype="string")
    if style is not None:
        cell.setAttribute("stylename", style)
    paragraph = P()
    if href:
        link = A(href=href)
        link.addText(text)
        paragraph.addElement(link)
    else:
        paragraph.addText(text)
    cell.addElement(paragraph)
    return cell


def _value_cell(
    doc: OpenDocumentSpreadsheet,
    value: Any,
    style: Style | None = None,
) -> TableCell:
    """Build a cell for ``value``: numbers stay numeric, everything else is text."""
    if isinstance(value, bool) or value is None:
        return _text_cell(doc, "" if value is None else str(value), style)
    if isinstance(value, int | float):
        cell = TableCell(valuetype="float", value=value)
        if style is not None:
            cell.setAttribute("stylename", style)
        cell.addElement(P(text=str(value)))
        return cell
    return _text_cell(doc, str(value), style)


def _add_autofilter(doc: OpenDocumentSpreadsheet, sheet: str, last_col: int, last_row: int) -> None:
    """Register a ``table:database-range`` so Calc offers autofilter on the data.

    Must be called after every sheet has been attached: the ODF content model
    requires all ``table:table`` elements to precede ``table:database-ranges``.
    """
    ranges = DatabaseRanges()
    ranges.addElement(
        DatabaseRange(
            name="Autofilter",
            targetrangeaddress=f"${sheet}.A1:${sheet}.{_column_letter(last_col)}{last_row}",
            containsheader="true",
            displayfilterbuttons="true",
        )
    )
    doc.spreadsheet.addElement(ranges)


def _column_letter(index: int) -> str:
    """1-based column index → spreadsheet letters (1 → ``A``, 27 → ``AA``)."""
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _column_value_styles(
    doc: OpenDocumentSpreadsheet,
    columns: list[Column],
    changed_style: Style,
) -> list[tuple[Style | None, Style | None]]:
    """Per-column ``(normal, changed)`` cell styles, with number formats applied."""
    result: list[tuple[Style | None, Style | None]] = []
    for index, col in enumerate(columns, start=1):
        decimals = _fmt_decimals(col.fmt)
        if decimals is None:
            result.append((None, changed_style))
            continue
        data_style = _number_style(doc, f"num{index}", decimals)
        result.append(
            (
                _cell_style(doc, f"value{index}", data_style=data_style),
                _cell_style(
                    doc,
                    f"value{index}-changed",
                    background=_CHANGED_BACKGROUND,
                    data_style=data_style,
                ),
            )
        )
    return result


def write_report(
    path: Path,
    rows: list[dict],
    columns: list[Column],
    meta: dict[str, str],
    new_keys: set[str] | None = None,
) -> Path:
    """Write ``rows`` to an ``.ods`` file and return its path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    doc = OpenDocumentSpreadsheet()

    header_style = _cell_style(doc, "header", bold=True)
    link_style = _cell_style(doc, "link", link=True)
    changed_style = _cell_style(doc, "changed", background=_CHANGED_BACKGROUND)
    changed_link_style = _cell_style(doc, "changed-link", link=True, background=_CHANGED_BACKGROUND)

    headers = [col.title for col in columns]
    if new_keys is not None:
        headers.append(NEW_COLUMN_TITLE)

    table = Table(name=DATA_SHEET)
    table_columns = TableColumns()
    for index, col in enumerate(columns, start=1):
        table_columns.addElement(
            TableColumn(stylename=_column_style(doc, f"col{index}", col.width))
        )
    if new_keys is not None:
        table_columns.addElement(
            TableColumn(stylename=_column_style(doc, f"col{len(columns) + 1}", 8))
        )
    table.addElement(table_columns)

    header_row = TableRow()
    for title in headers:
        header_row.addElement(_text_cell(doc, title, header_style))
    table.addElement(header_row)

    changed = _changed_keys(meta)
    value_styles = _column_value_styles(doc, columns, changed_style)
    for row in rows:
        is_changed = str(row.get("external_key")) in changed
        table_row = TableRow()
        for index, col in enumerate(columns):
            value = row.get(col.key)
            href = None
            if _is_link_key(col.key) and isinstance(value, str) and value:
                href = safe_href(value)
            if href:
                style = changed_link_style if is_changed else link_style
                table_row.addElement(_text_cell(doc, str(value), style, href=href))
            else:
                normal_style, changed_column_style = value_styles[index]
                table_row.addElement(
                    _value_cell(
                        doc,
                        value,
                        changed_column_style if is_changed else normal_style,
                    )
                )
        if new_keys is not None:
            flag = "да" if str(row.get("external_key")) in new_keys else "нет"
            table_row.addElement(_text_cell(doc, flag, changed_style if is_changed else None))
        table.addElement(table_row)

    doc.spreadsheet.addElement(table)

    meta_table = Table(name=META_SHEET)
    for key, value in meta.items():
        if key == "changed_keys":
            continue
        meta_row = TableRow()
        meta_row.addElement(_text_cell(doc, str(key)))
        meta_row.addElement(_text_cell(doc, str(value)))
        meta_table.addElement(meta_row)
    doc.spreadsheet.addElement(meta_table)

    # All sheets must be attached before the database range is added.
    _add_autofilter(doc, DATA_SHEET, len(headers), len(rows) + 1)

    doc.save(str(path))
    return path


def prune_reports(directory: Path, keep: int) -> int:
    """Delete the oldest ``*.ods`` files beyond ``keep``; return how many were removed."""
    directory = Path(directory)
    if not directory.is_dir():
        return 0
    files = sorted(
        (p for p in directory.glob("*.ods") if p.is_file()),
        key=lambda p: (p.stat().st_mtime, p.name),
    )
    excess = files[: max(len(files) - keep, 0)]
    for file in excess:
        file.unlink()
    return len(excess)
