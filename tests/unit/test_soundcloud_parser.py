"""Offline tests for the SoundCloud trending parser.

The fixtures are real: ``soundcloud_playlist.html`` is server HTML captured with
``curl`` and trimmed to the hydration script, ``soundcloud_tracks.json`` is five
real track objects in the API's order, which deliberately differs from the
playlist's order.
"""
import asyncio
import json
import logging
from pathlib import Path

import pytest

import uwp.parsers.soundcloud_trending.parser as soundcloud
from uwp.domain import ErrorKind
from uwp.i18n import Lang
from uwp.parsers.base import ParseContext, ParseError
from uwp.parsers.registry import ParserRegistry
from uwp.parsers.soundcloud_trending.parser import (
    DISCOVER_URL,
    SIGNIN_URL,
    SoundcloudSettings,
    SoundcloudTrendingParser,
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


def test_parse_hydration_keeps_only_track_entries():
    """A playlist entry carries ``kind``; anything else has no ``/tracks`` body.

    Measured: all five live genre pages are 100 % ``kind: "track"``, but a
    ``playlist`` or ``user`` entry in the same list would be requested from
    ``/tracks?ids=``, never appear in the answer, and turn every run into a
    partial one.
    """
    html = _html().replace(
        '{"id": 2437199922, "kind": "track"',
        '{"id": 2437199922, "kind": "playlist"',
        1,
    )

    playlist = parse_hydration(html)

    assert playlist is not None
    assert 2437199922 not in playlist.ids
    assert len(playlist.ids) == 49


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


def test_parser_metadata_and_columns():
    parser = SoundcloudTrendingParser()

    assert parser.key == "soundcloud_trending"
    assert parser.url == DISCOVER_URL == "https://soundcloud.com/discover"
    # The VNC login must open the form; ``/signin`` is 200, ``/sign-in`` is 404.
    assert parser.auth_url == SIGNIN_URL == "https://soundcloud.com/signin"
    assert parser.login_url == "https://soundcloud.com/signin"
    assert parser.requires_login is False
    assert parser.settings_model is SoundcloudSettings
    assert [column.key for column in parser.columns] == [
        "title",
        "artist",
        "url",
        "artist_url",
        "genre",
        "duration",
        "published",
        "playback_count",
        "likes_count",
        "reposts_count",
        "comment_count",
    ]


def test_the_counters_are_volatile_and_the_rest_is_not():
    """A live track's play count moves between runs; ``changes`` must ignore it.

    Measured: the page's own HTML rounds the number to ``1.25M``, while the API
    returns ``1258443``; either way it drifts, so it is not news.
    """
    by_key = {column.key: column for column in SoundcloudTrendingParser.columns}

    for name in ("playback_count", "likes_count", "reposts_count", "comment_count"):
        assert by_key[name].volatile is True, name
    for name in (
        "title",
        "artist",
        "url",
        "artist_url",
        "genre",
        "duration",
        "published",
    ):
        assert by_key[name].volatile is False, name


def test_every_column_has_a_catalogue_key():
    """A column added without a key would show Russian in an English report."""
    assert [column.key for column in SoundcloudTrendingParser.columns] == [
        column.title_key.removeprefix("parser.soundcloud_trending.column.")
        for column in SoundcloudTrendingParser.columns
    ]


def test_every_column_title_resolves_in_both_languages():
    """``title_for`` calls ``t``, which raises on an unknown key."""
    for lang in (Lang.ru, Lang.en):
        for column in SoundcloudTrendingParser.columns:
            assert column.title_for(lang), (column.key, lang)


def test_the_parser_name_and_description_resolve_in_both_languages():
    parser = SoundcloudTrendingParser()

    for lang in (Lang.ru, Lang.en):
        assert parser.title_for(lang)
        assert parser.description_for(lang)
    assert parser.title_for(Lang.ru) != parser.title_for(Lang.en)


def test_every_settings_field_has_a_short_label_key():
    for name, field in SoundcloudSettings.model_fields.items():
        extra = field.json_schema_extra or {}
        assert extra.get("title_key") == f"parser.soundcloud_trending.field.{name}"
        assert field.description, f"{name} has no description"


def test_parser_is_discovered_with_the_example_still_present():
    registry = ParserRegistry.discover()

    assert registry.broken() == {}
    assert registry.get("steam_deals") is not None
    assert registry.get("soundcloud_trending") is not None


TRACKS_URL = "https://api-v2.soundcloud.com/tracks?ids=1%2C2"


def _playlist_html(ids, *, title="All Genres", client_id="CLIENT"):
    """The smallest page that carries a usable hydration block."""
    entries = [
        {"hydratable": "apiClient", "data": {"id": client_id, "isExpiring": True}},
        {
            "hydratable": "systemPlaylist",
            "data": {
                "title": title,
                "tracks": [{"id": track_id, "kind": "track"} for track_id in ids],
            },
        },
    ]
    return "<script>window.__sc_hydration = " + json.dumps(entries) + ";</script>"


def _track(track_id, **extra):
    track = {
        "id": track_id,
        "title": f"Track {track_id}",
        "permalink_url": f"https://soundcloud.com/a/t{track_id}",
        "genre": "House",
        "duration": 137331,
        "created_at": "2026-11-12T21:17:35Z",
        "playback_count": 10,
        "likes_count": 20,
        "reposts_count": 30,
        "comment_count": 40,
        "user": {"username": "A", "permalink_url": "https://soundcloud.com/a"},
    }
    track.update(extra)
    return track


class _FakeResponse:
    def __init__(self, url, payload):
        self.url = url
        self._payload = payload

    async def json(self):
        return self._payload


class _FakePage:
    """Plays back hydration HTML and fires ``response`` handlers like the browser."""

    def __init__(self, html, *, responses=(), evaluate=None):
        self.html = html
        self.responses = list(responses)
        self._evaluate = evaluate
        self.calls: list[str] = []
        self.goto_calls: list[tuple[str, str | None]] = []
        self.evaluate_calls: list[tuple[str, dict]] = []
        self.handlers: dict[str, object] = {}

    async def goto(self, url, wait_until=None):
        self.goto_calls.append((url, wait_until))
        for payload in self.responses:
            await self.emit(TRACKS_URL, payload)

    async def content(self):
        return self.html

    def on(self, event, handler):
        self.calls.append(f"on:{event}")
        self.handlers[event] = handler

    async def emit(self, url, payload):
        handler = self.handlers["response"]
        await handler(_FakeResponse(url, payload))

    async def evaluate(self, expression, arg=None):
        self.evaluate_calls.append((expression, arg))
        if self._evaluate is None:
            return []
        return self._evaluate(arg)


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """The collection window is wall-clock; a unit test must not spend 10 s in it.

    ``raising=False`` so a missing constant fails the test body with the real
    error instead of every test erroring in setup.
    """
    monkeypatch.setattr(soundcloud, "_COLLECT_TIMEOUT_S", 0.0, raising=False)


def _ctx(page, tmp_path):
    saved: list[tuple[str, bytes]] = []
    ctx = ParseContext(
        page=page,
        context=None,
        settings=SoundcloudSettings(),
        log=logging.getLogger("test.soundcloud"),
        artifacts_dir=tmp_path,
        site=None,
        profile=None,
        known_keys=lambda: set(),
        save_artifact=lambda name, data: saved.append((name, data)) or tmp_path / name,
    )
    return ctx, saved


async def test_run_takes_the_rows_from_the_pages_own_api_response(tmp_path):
    page = _FakePage(_playlist_html([1, 2]), responses=[[_track(1), _track(2)]])
    ctx, _ = _ctx(page, tmp_path)

    result = await SoundcloudTrendingParser().run(ctx)

    # Subscribed before ``goto``: the page's request can land before
    # ``domcontentloaded``, and a listener added later would miss it.
    assert page.calls == ["on:response"]
    assert page.goto_calls == [
        (
            "https://soundcloud.com/discover/sets/trending-by-genre:all-genres",
            "domcontentloaded",
        )
    ]
    assert [row["external_key"] for row in result.rows] == ["1", "2"]
    assert page.evaluate_calls == []
    assert result.meta == {
        "playlist": "All Genres",
        "url": "https://soundcloud.com/discover/sets/trending-by-genre:all-genres",
        "objects": 2,
    }


async def test_run_requests_the_missing_ids_from_the_same_endpoint(tmp_path):
    """No interception and a live ``client_id``: one fetch fills the gap."""
    page = _FakePage(
        _playlist_html([1, 2]),
        evaluate=lambda arg: [_track(arg["ids"][0]), _track(arg["ids"][1])],
    )
    ctx, _ = _ctx(page, tmp_path)

    result = await SoundcloudTrendingParser().run(ctx)

    assert len(page.evaluate_calls) == 1
    _expression, arg = page.evaluate_calls[0]
    assert arg == {"ids": [1, 2], "clientId": "CLIENT"}
    assert [row["external_key"] for row in result.rows] == ["1", "2"]


async def test_run_waits_for_a_response_that_lands_after_the_navigation(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(soundcloud, "_COLLECT_TIMEOUT_S", 2.0)
    monkeypatch.setattr(soundcloud, "_POLL_INTERVAL_S", 0.01)
    page = _FakePage(_playlist_html([1, 2]))
    ctx, _ = _ctx(page, tmp_path)

    async def late():
        await asyncio.sleep(0.05)
        await page.emit(TRACKS_URL, [_track(1), _track(2)])

    task = asyncio.create_task(late())
    result = await SoundcloudTrendingParser().run(ctx)
    await task

    assert [row["external_key"] for row in result.rows] == ["1", "2"]
    assert page.evaluate_calls == []


async def test_run_fails_blocked_and_saves_the_page_when_there_is_no_playlist(tmp_path):
    page = _FakePage("<html><body>captcha</body></html>")
    ctx, saved = _ctx(page, tmp_path)

    with pytest.raises(ParseError) as excinfo:
        await SoundcloudTrendingParser().run(ctx)

    assert excinfo.value.kind is ErrorKind.blocked
    assert excinfo.value.message == (
        "SoundCloud returned no playlist (geo block or markup change?)"
    )
    assert excinfo.value.partial_rows == []
    assert saved and saved[0][0] == "empty.html"
    assert b"captcha" in saved[0][1]


async def test_run_fails_blocked_when_neither_path_returned_an_object(tmp_path):
    page = _FakePage(_playlist_html([1, 2]), evaluate=lambda arg: [])
    ctx, saved = _ctx(page, tmp_path)

    with pytest.raises(ParseError) as excinfo:
        await SoundcloudTrendingParser().run(ctx)

    assert excinfo.value.kind is ErrorKind.blocked
    assert excinfo.value.message == (
        "SoundCloud returned no tracks (geo block, captcha or API change?)"
    )
    assert saved and saved[0][0] == "empty.html"


async def test_run_saves_a_partial_answer_as_partial_rows(tmp_path):
    """``partial_rows`` makes the runner store with ``complete=False``.

    Without it the tracks the API failed to return would be marked absent, and
    the next full run would report them all as new.
    """
    page = _FakePage(_playlist_html([1, 2, 3]), responses=[[_track(1), _track(3)]])
    ctx, saved = _ctx(page, tmp_path)

    with pytest.raises(ParseError) as excinfo:
        await SoundcloudTrendingParser().run(ctx)

    assert excinfo.value.kind is ErrorKind.blocked
    assert excinfo.value.message == "SoundCloud returned 2 of 3 tracks"
    assert [row["external_key"] for row in excinfo.value.partial_rows] == ["1", "3"]
    assert saved == []


async def test_run_without_a_client_id_only_reports_what_it_intercepted(tmp_path):
    """A missing ``apiClient`` is not a reason to fail: rows we have are exact."""
    page = _FakePage(_playlist_html([1, 2], client_id=""), responses=[[_track(1)]])
    ctx, _ = _ctx(page, tmp_path)

    with pytest.raises(ParseError) as excinfo:
        await SoundcloudTrendingParser().run(ctx)

    assert page.evaluate_calls == []
    assert excinfo.value.message == "SoundCloud returned 1 of 2 tracks"


async def test_the_collector_reads_a_batch_track_response():
    collector = soundcloud._TrackCollector()

    await collector.handle(_FakeResponse(TRACKS_URL, [_track(1), _track(2)]))

    assert sorted(collector.objects) == [1, 2]


async def test_the_collector_ignores_per_track_endpoints_and_error_bodies():
    """``/tracks/<id>/comments`` is not a track list and a 401 body is a dict."""
    collector = soundcloud._TrackCollector()

    await collector.handle(
        _FakeResponse(
            "https://api-v2.soundcloud.com/tracks/1/comments?client_id=x", [_track(1)]
        )
    )
    await collector.handle(
        _FakeResponse(TRACKS_URL, {"errors": [{"error_message": "401"}]})
    )

    assert collector.objects == {}


async def test_the_collector_keeps_the_first_object_for_an_id():
    collector = soundcloud._TrackCollector()

    await collector.handle(_FakeResponse(TRACKS_URL, [_track(1, title="first")]))
    await collector.handle(_FakeResponse(TRACKS_URL, [_track(1, title="second")]))

    assert collector.objects[1]["title"] == "first"
