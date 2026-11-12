"""UTC time helpers: storage format and local display."""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

DB_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
DISPLAY_FORMAT = "%d.%m.%Y %H:%M"


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


def format_local(dt: datetime, tz: str) -> str:
    """Format a datetime in the given IANA timezone as ``DD.MM.YYYY HH:MM``.

    Naive datetimes are assumed to already be UTC.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ZoneInfo(tz)).strftime(DISPLAY_FORMAT)
