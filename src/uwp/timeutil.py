"""UTC time helpers: storage format and local display."""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from uwp.i18n import DEFAULT_LANG, Lang, normalize

DB_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
DISPLAY_FORMAT = "%d.%m.%Y %H:%M"
# ``13.11.2026`` reads as an ambiguous month/day to an English reader; the
# report already switched to ISO (finding 13), and the chat must agree with it.
DISPLAY_FORMAT_EN = "%Y-%m-%d %H:%M"


def utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def to_db(dt: datetime) -> str:
    """Serialize a datetime to the SQLite storage format ``YYYY-MM-DDTHH:MM:SSZ``.

    Naive datetimes are assumed to already be UTC.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime(DB_FORMAT)


def from_db(s: str) -> datetime:
    """Parse a stored ``YYYY-MM-DDTHH:MM:SSZ`` string into an aware UTC datetime."""
    return datetime.strptime(s, DB_FORMAT).replace(tzinfo=UTC)


def display_format(lang: Lang | str = DEFAULT_LANG) -> str:
    """The date pattern a reader of ``lang`` expects."""
    return DISPLAY_FORMAT_EN if normalize(lang) is Lang.en else DISPLAY_FORMAT


def format_local(dt: datetime, tz: str, lang: Lang | str = DEFAULT_LANG) -> str:
    """Format a datetime in the given IANA timezone for a reader of ``lang``.

    Naive datetimes are assumed to already be UTC. Storage is unaffected: this
    is the display half only.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ZoneInfo(tz)).strftime(display_format(lang))
