from datetime import UTC, datetime

from uwp.timeutil import format_local, from_db, to_db, utcnow


def test_roundtrip():
    dt = datetime(2026, 11, 13, 1, 20, 5, tzinfo=UTC)
    assert to_db(dt) == "2026-11-13T01:20:05Z"
    assert from_db(to_db(dt)) == dt


def test_format_local_moscow():
    dt = datetime(2026, 11, 12, 22, 20, tzinfo=UTC)
    assert format_local(dt, "Europe/Moscow") == "13.11.2026 01:20"


def test_utcnow_is_aware_utc():
    now = utcnow()
    assert now.tzinfo is UTC


def test_to_db_converts_aware_datetime_to_utc():
    dt = datetime(2026, 11, 13, 4, 20, 5, tzinfo=UTC)
    assert to_db(dt) == "2026-11-13T04:20:05Z"


def test_from_db_returns_utc():
    dt = from_db("2026-11-13T01:20:05Z")
    assert dt.tzinfo is UTC


def test_the_display_format_follows_the_reader_language():
    """A dotted date is the Russian convention; English reads year first.

    The chat prints ``format_local`` for run history and the next run, so
    finding 13's rule (an English report gets an English date) applies here too.
    Storage format is untouched: only the rendering differs.
    """
    dt = datetime(2026, 11, 12, 22, 20, tzinfo=UTC)

    assert format_local(dt, "Europe/Moscow", "ru") == "13.11.2026 01:20"
    assert format_local(dt, "Europe/Moscow", "en") == "2026-11-13 01:20"


def test_the_display_format_defaults_to_russian():
    """Without a language, the old dotted rendering must stay."""
    dt = datetime(2026, 11, 12, 22, 20, tzinfo=UTC)

    assert format_local(dt, "Europe/Moscow") == "13.11.2026 01:20"
