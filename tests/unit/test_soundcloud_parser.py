"""Offline tests for the SoundCloud trending parser.

The fixtures are real: ``soundcloud_playlist.html`` is server HTML captured with
``curl`` and trimmed to the hydration script, ``soundcloud_tracks.json`` is five
real track objects in the API's order, which deliberately differs from the
playlist's order.
"""
import json
from pathlib import Path

import pytest

from uwp.parsers.soundcloud_trending.parser import (
    SoundcloudSettings,
    build_playlist_url,
    format_duration,
    parse_hydration,
    published_date,
    track_rows,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"
PLAYLIST = FIXTURES / "soundcloud_playlist.html"
TRACKS = FIXTURES / "soundcloud_tracks.json"


def _html() -> str:
    return PLAYLIST.read_text(encoding="utf-8")


def _objects() -> list[dict]:
    return json.loads(TRACKS.read_text(encoding="utf-8"))


def test_parse_hydration_reads_the_title_the_id_order_and_the_client():
    playlist = parse_hydration(_html())

    assert playlist is not None
    assert playlist.title == "All Genres"
    assert len(playlist.ids) == 50
    assert playlist.ids[:2] == [2393623920, 2437199922]
    assert playlist.client_id is not None and playlist.client_id.startswith("QhVE")


def test_parse_hydration_is_none_when_the_block_is_missing_or_broken():
    """A captcha page has no block; a truncated script must not crash the run."""
    assert parse_hydration("<html><body>captcha</body></html>") is None
    assert parse_hydration("<script>window.__sc_hydration = [oops];</script>") is None


def test_track_rows_follow_the_playlist_order_not_the_api_order():
    """The API shuffles its answer; following it would reshuffle the report.

    Measured on the live page: hydration order and the ``/tracks`` payload order
    differ, and the report's ``changes`` mode treats order as a change.
    """
    playlist = parse_hydration(_html())
    assert playlist is not None
    objects = {track["id"]: track for track in _objects()}
    api_order = [track["id"] for track in _objects()]
    hydration_order = [track_id for track_id in playlist.ids if track_id in objects]
    assert api_order != hydration_order

    rows = track_rows(playlist.ids, objects)

    assert [row["external_key"] for row in rows] == [
        str(track_id) for track_id in hydration_order
    ]


def test_a_track_row_carries_the_exact_api_values():
    playlist = parse_hydration(_html())
    assert playlist is not None
    objects = {track["id"]: track for track in _objects()}

    first = track_rows(playlist.ids, objects)[0]

    assert first["external_key"] == "2393623920"
    assert first["title"] == "ural mountains / уральские горы 🌄"
    assert first["artist"] == "janecocaine #certified"
    assert first["url"] == "https://soundcloud.com/shawtylies2me/uralmountains"
    assert first["artist_url"] == "https://soundcloud.com/shawtylies2me"
    assert first["genre"] == "House"
    assert first["duration"] == "1:53"
    assert first["published"] == "2026-09-03"
    # Rounded in the DOM as ``1.25M``; exact only in the API response.
    assert first["playback_count"] == 1258443
    assert first["likes_count"] == 37644
    assert first["reposts_count"] == 437
    assert first["comment_count"] == 408


def test_track_rows_skip_ids_the_api_did_not_return():
    row = {
        "id": 1,
        "title": "one",
        "permalink_url": "https://soundcloud.com/a/one",
        "user": {"username": "A", "permalink_url": "https://soundcloud.com/a"},
    }

    assert [r["external_key"] for r in track_rows([1, 2], {1: row})] == ["1"]


def test_missing_values_stay_empty_and_counters_stay_none():
    """Zero would read as "no plays yet" and then look like a change."""
    track = {
        "id": 7,
        "title": "no data",
        "permalink_url": "",
        "genre": None,
        "duration": None,
        "created_at": None,
        "playback_count": None,
        "likes_count": None,
        "reposts_count": None,
        "comment_count": None,
        "user": {},
    }

    row = track_rows([7], {7: track})[0]

    assert row["genre"] == "" and row["artist"] == "" and row["artist_url"] == ""
    assert row["duration"] == "" and row["published"] == ""
    assert row["playback_count"] is None
    assert row["likes_count"] is None


@pytest.mark.parametrize(
    ("ms", "text"),
    [
        (137331, "2:17"),
        (113861, "1:53"),
        (60000, "1:00"),
        (59000, "0:59"),
        (0, ""),
        (None, ""),
    ],
)
def test_format_duration(ms, text):
    assert format_duration(ms) == text


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("2026-11-12T21:17:35Z", "2026-11-12"),
        ("2026-11-12", "2026-11-12"),
        (None, ""),
        ("", ""),
    ],
)
def test_published_date_keeps_the_utc_day(value, text):
    assert published_date(value) == text


@pytest.mark.parametrize(
    ("genre", "url"),
    [
        (
            "all-genres",
            "https://soundcloud.com/discover/sets/trending-by-genre:all-genres",
        ),
        ("pop", "https://soundcloud.com/discover/sets/trending-by-genre:pop"),
        ("hip-hop", "https://soundcloud.com/discover/sets/trending-by-genre:hip-hop"),
        (
            "electronic",
            "https://soundcloud.com/discover/sets/trending-by-genre:electronic",
        ),
        ("trap", "https://soundcloud.com/discover/sets/trending-by-genre:trap"),
    ],
)
def test_build_playlist_url(genre, url):
    assert build_playlist_url(SoundcloudSettings(genre=genre)) == url


def test_the_default_genre_is_all_genres():
    assert SoundcloudSettings().genre == "all-genres"
