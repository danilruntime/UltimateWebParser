"""SoundCloud trending parser: one trending-by-genre playlist as report rows.

The page renders ten of its fifty rows and its DOM has rounded counters and no
dates, so the data comes from the page's own JSON: the server-rendered
``__sc_hydration`` block gives the playlist title and the track order, and the
``api-v2`` answer the page requests for those ids gives exact values. A run that
sees neither fails ``blocked`` instead of writing approximate numbers (see
``docs/superpowers/specs/2026-11-22-soundcloud-parser-design.md``).
"""
import json
import re
from dataclasses import dataclass
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from uwp.parsers.base import BaseParser, Column, ParseContext, ParseResult

DISCOVER_URL = "https://soundcloud.com/discover"
SIGNIN_URL = "https://soundcloud.com/signin"
TRENDING_URL = "https://soundcloud.com/discover/sets/trending-by-genre:{genre}"

_HYDRATION_RE = re.compile(
    r"window\.__sc_hydration\s*=\s*(\[.*?\]);\s*</script>", re.DOTALL
)


class SoundcloudSettings(BaseModel):
    """User-facing settings: one genre per run."""

    genre: Literal["all-genres", "pop", "hip-hop", "electronic", "trap"] = Field(
        "all-genres",
        # A ``Literal`` because the bot cycles the value with one button, and
        # only the slugs the discover page links to work: ``hip-hop-rap`` is 404.
        description="Жанр раздела «в тренде»: all-genres, pop, hip-hop, electronic, trap",
        json_schema_extra={"title_key": "parser.soundcloud_trending.field.genre"},
    )


@dataclass
class Playlist:
    """What the hydration block says: the title, the id order, the API client."""

    title: str
    ids: list[int]
    client_id: str | None = None


def build_playlist_url(settings: SoundcloudSettings) -> str:
    """The trending page of the chosen genre."""
    return TRENDING_URL.format(genre=settings.genre)


def parse_hydration(html: str) -> Playlist | None:
    """Read ``__sc_hydration`` from server HTML; ``None`` when the block is unusable.

    Only the title, the track ids and the API client are read. The ids are the
    order the playlist is rendered in: the ``/tracks`` answer arrives in a
    different order, and following it would shuffle the report and make
    ``export_mode=changes`` see a change on every run.
    """
    match = _HYDRATION_RE.search(html)
    if match is None:
        return None
    try:
        entries = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(entries, list):
        return None

    playlist = next(
        (
            entry.get("data")
            for entry in entries
            if isinstance(entry, dict) and entry.get("hydratable") == "systemPlaylist"
        ),
        None,
    )
    if not isinstance(playlist, dict):
        return None
    tracks = playlist.get("tracks")
    if not isinstance(tracks, list):
        return None
    ids = [
        track["id"]
        for track in tracks
        if isinstance(track, dict) and isinstance(track.get("id"), int)
    ]

    client = next(
        (
            entry.get("data")
            for entry in entries
            if isinstance(entry, dict) and entry.get("hydratable") == "apiClient"
        ),
        None,
    )
    client_id = client.get("id") if isinstance(client, dict) else None
    return Playlist(
        title=str(playlist.get("title") or ""),
        ids=ids,
        client_id=client_id if isinstance(client_id, str) and client_id else None,
    )


def format_duration(ms: int | None) -> str:
    """Milliseconds to ``m:ss``; a missing length stays empty, not ``0:00``."""
    if not isinstance(ms, int) or ms <= 0:
        return ""
    seconds = ms // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


def published_date(value: str | None) -> str:
    """``2026-11-12T21:17:35Z`` → ``2026-11-12``; the day is the site's UTC day.

    ``ods`` has no date format, and converting to the report's timezone would
    move a late-evening upload to the previous day.
    """
    if not isinstance(value, str) or len(value) < 10:
        return ""
    return value[:10]


def track_rows(ids: list[int], objects: dict[int, dict]) -> list[dict]:
    """One report row per id, in the playlist's order; unknown ids are skipped."""
    rows = []
    for track_id in ids:
        track = objects.get(track_id)
        if track is None:
            continue
        user = track.get("user") if isinstance(track.get("user"), dict) else {}
        rows.append(
            {
                "external_key": str(track_id),
                "title": track.get("title") or "",
                "artist": user.get("username") or "",
                "url": track.get("permalink_url") or "",
                "artist_url": user.get("permalink_url") or "",
                "genre": track.get("genre") or "",
                "duration": format_duration(track.get("duration")),
                "published": published_date(track.get("created_at")),
                # A counter stays ``None`` when the API omits it: a zero would
                # read as "no plays yet" and would itself look like a change as
                # soon as the real number appeared.
                "playback_count": track.get("playback_count"),
                "likes_count": track.get("likes_count"),
                "reposts_count": track.get("reposts_count"),
                "comment_count": track.get("comment_count"),
            }
        )
    return rows


class SoundcloudTrendingParser(BaseParser):
    """Collects one trending-by-genre playlist: title, artist, exact counters."""

    key = "soundcloud_trending"
    title = "SoundCloud — тренды"
    description = (
        "Треки из раздела «в тренде» SoundCloud: исполнитель, жанр, "
        "длительность, счётчики и дата публикации."
    )
    title_key = "parser.soundcloud_trending.title"
    description_key = "parser.soundcloud_trending.description"
    # ``url`` is the section the report names; the genre lives in the settings,
    # the same way Steam's ``url`` is the search page and not one query string.
    url = DISCOVER_URL
    auth_url = SIGNIN_URL
    requires_login = False
    settings_model = SoundcloudSettings
    columns: ClassVar[list[Column]] = [
        Column(
            "title", "Название", 45, title_key="parser.soundcloud_trending.column.title"
        ),
        Column(
            "artist",
            "Исполнитель",
            26,
            title_key="parser.soundcloud_trending.column.artist",
        ),
        Column("url", "Ссылка", 55, title_key="parser.soundcloud_trending.column.url"),
        Column(
            "artist_url",
            "Исполнитель: ссылка",
            45,
            title_key="parser.soundcloud_trending.column.artist_url",
        ),
        Column("genre", "Жанр", 18, title_key="parser.soundcloud_trending.column.genre"),
        Column(
            "duration",
            "Длительность",
            12,
            title_key="parser.soundcloud_trending.column.duration",
        ),
        Column(
            "published",
            "Дата публикации",
            14,
            title_key="parser.soundcloud_trending.column.published",
        ),
        Column(
            "playback_count",
            "Прослушивания",
            14,
            volatile=True,
            title_key="parser.soundcloud_trending.column.playback_count",
        ),
        Column(
            "likes_count",
            "Лайки",
            10,
            volatile=True,
            title_key="parser.soundcloud_trending.column.likes_count",
        ),
        Column(
            "reposts_count",
            "Репосты",
            10,
            volatile=True,
            title_key="parser.soundcloud_trending.column.reposts_count",
        ),
        Column(
            "comment_count",
            "Комментарии",
            12,
            volatile=True,
            title_key="parser.soundcloud_trending.column.comment_count",
        ),
    ]

    async def run(self, ctx: ParseContext) -> ParseResult:  # pragma: no cover
        raise NotImplementedError
