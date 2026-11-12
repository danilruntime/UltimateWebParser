"""UTC time helpers: storage format and local display."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DB_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
DISPLAY_FORMAT = "%d.%m.%Y %H:%M"


def utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def to_db(dt: datetime) -> str:
    """Serialize a datetime to the SQLite storage format ``YYYY-MM-DDTHH:MM:SSZ``."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(DB_FORMAT)


def from_db(s: str) -> datetime:
    """Parse a stored ``YYYY-MM-DDTHH:MM:SSZ`` string into an aware UTC datetime."""
    return datetime.strptime(s, DB_FORMAT).replace(tzinfo=timezone.utc)


def format_local(dt: datetime, tz: str) -> str:
    """Format a datetime in the given IANA timezone as ``DD.MM.YYYY HH:MM``."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(tz)).strftime(DISPLAY_FORMAT)
