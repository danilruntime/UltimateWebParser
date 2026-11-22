# Парсер SoundCloud — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** второй парсер рядом со `steam_deals`: один прогон сайта SoundCloud отдаёт `.ods` со строками одного плейлиста «trending by genre» — точные счётчики, дата публикации и порядок страницы.

**Architecture:** серверный HTML содержит `window.__sc_hydration` с `systemPlaylist` (название и упорядоченные id) и `apiClient` (`client_id`); полные объекты треков приходят ответом `api-v2.soundcloud.com/tracks?ids=…`, который страница делает сама. Парсер подписывается на ответы **до** `goto`, читает hydration из `page.content()`, ждёт покрытия id и при нехватке один раз дёргает тот же эндпоинт через `page.evaluate(fetch)`. DOM не используется вовсе: в нём округлённые счётчики, нет даты и другой автор — а `items.upsert` перезаписывает payload даже при `complete=False`, так что неточные строки отравили бы базу.

**Tech Stack:** Python 3.12, playwright (async), pydantic, pytest (asyncio auto), ruff, odfpy.

**Spec:** `docs/superpowers/specs/2026-11-22-soundcloud-parser-design.md` (утверждена 2026-11-22).

## Global Constraints

- Ключ парсера — `soundcloud_trending`; папка `src/uwp/parsers/soundcloud_trending/`.
- `url = https://soundcloud.com/discover`, `auth_url = https://soundcloud.com/signin` (замерено: 200; `/sign-in` — 404), `requires_login = False`.
- `TRENDING_URL = "https://soundcloud.com/discover/sets/trending-by-genre:{genre}"`; жанры — ровно `all-genres`, `pop`, `hip-hop`, `electronic`, `trap` (слаг `hip-hop-rap` — 404).
- Порядок строк — порядок id из hydration, **не** порядок ответа `/tracks` (замерено: отличается).
- Счётчики (`playback_count`, `likes_count`, `reposts_count`, `comment_count`) — `volatile=True`: в отчёте есть, в сравнении изменений нет.
- Пустое значение — `None` для счётчиков и `""` для текста, а не `0`: «нет данных» и «ноль прослушиваний» — разные вещи.
- Частичный ответ — `ParseError(kind=ErrorKind.blocked, partial_rows=rows)`, **не** `ParseResult`: runner сохранит найденные строки с `complete=False`, и недостающие треки не станут `absent`.
- Запасного DOM-пути нет. Если hydration нет или объектов нет ни одного — `blocked` + артефакт `empty.html`.
- Новых зависимостей, миграций и настроек «сколько строк» нет. Сайт SoundCloud в `seed.py` не добавляется.
- Русский для всего, что читает человек (заголовки отчёта, подписи настроек, README); английский для идентификаторов, комментариев и docstring. Сообщения `ParseError` — английские: это строка для лога.
- README парсера — на двух языках, обе версии в одном коммите (`tests/unit/test_readme_translations.py`).
- Тесты сначала. Проверки: `.venv/bin/python -m pytest <файл> -q`, `.venv/bin/python -m ruff check src tests`. `ruff format` не запускать.
- `docs/images/vnc-desktop.png` изменён до этой задачи: в коммиты парсера его не добавлять.

## Review Focus

- Подписка `page.on("response", …)` стоит **до** `goto`: иначе ответ `/tracks`, пришедший до `domcontentloaded`, потерян и прогон уходит в `fetch`-фолбэк на пустом месте (Task 3).
- `parse_hydration` ищет блок в серверном HTML, а не через `page.evaluate`: блок есть в ответе `curl` (88 КБ), то есть читается без отрисовки и без ожидания (Task 1).
- `track_rows` идёт по `ids` из hydration: ответ API перемешан, и следование ему перетасовало бы отчёт, а `changes` счёл бы это изменением (Task 1).
- Частичный прогон — `partial_rows`, а не «тихая потеря»: `items.upsert` перезаписывает payload всегда, поэтому неполный список без `complete=False` пометил бы недостающие треки `absent` (Task 3).
- Счётчики `volatile`: иначе живой трек меняет число прослушиваний между прогонами, и `export_mode=changes` шлёт файл каждый раз (Task 2).
- `auth_url` — `/signin`, `url` — `/discover`: отчёт называет раздел прогона, а VNC открывает форму входа (Task 2).
- `published` — текст `YYYY-MM-DD` в UTC: `ods` не умеет формат даты, а сдвиг на сутки в колонке «дата публикации» был бы сюрпризом (Task 1).

---

### Task 1: Фикстуры и чистые функции

**Files:**
- Create: `tests/fixtures/soundcloud_playlist.html`
- Create: `tests/fixtures/soundcloud_tracks.json`
- Create: `tests/unit/test_soundcloud_parser.py`
- Create: `tests/live/test_soundcloud_live.py`
- Create: `src/uwp/parsers/soundcloud_trending/__init__.py`
- Create: `src/uwp/parsers/soundcloud_trending/parser.py`

**Interfaces:**
- Consumes: nothing from this repository beyond `uwp.parsers.base`.
- Produces: `SoundcloudSettings` (поле `genre: Literal[...] = "all-genres"`), `build_playlist_url(settings) -> str`, `parse_hydration(html) -> Playlist | None`, `Playlist(title: str, ids: list[int], client_id: str | None)`, `format_duration(ms) -> str`, `published_date(value) -> str`, `track_rows(ids, objects) -> list[dict]`, константы `DISCOVER_URL`, `SIGNIN_URL`, `TRENDING_URL`.

- [ ] **Step 1: Собрать фикстуры из живой страницы**

`tests/fixtures/soundcloud_playlist.html` — реальный серверный HTML, урезанный до `<script>` с hydration-блоком (записи `apiClient` и `systemPlaylist`, данные не тронуты). `tests/fixtures/soundcloud_tracks.json` — пять реальных объектов треков, урезанных до полей, которые читает парсер, **в порядке ответа API** (он отличается от порядка плейлиста — этого требует тест порядка).

Если фикстур нет под рукой, пересобрать так (то же самое попадёт в README парсера):

```bash
cd /path/to/UltimateWebParser
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
curl -A "$UA" "https://soundcloud.com/discover/sets/trending-by-genre:all-genres" \
  -o /tmp/soundcloud.html

.venv/bin/python - <<'PY'
import json
import pathlib
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
PAGE = "https://soundcloud.com/discover/sets/trending-by-genre:all-genres"
html = pathlib.Path("/tmp/soundcloud.html").read_text(encoding="utf-8")

start = html.find("[", html.find("window.__sc_hydration"))
entries, _ = json.JSONDecoder().raw_decode(html[start:])
keep = [e for e in entries if e.get("hydratable") in ("apiClient", "systemPlaylist")]
keep.sort(key=lambda e: e["hydratable"] == "systemPlaylist")  # apiClient first, as on the page

page = (
    "<!DOCTYPE html>\n"
    f"<!-- Captured from {PAGE}\n"
    "     with curl; only the hydration <script> and the entries the parser reads are kept. -->\n"
    "<html>\n<head><title>All Genres</title></head>\n<body>\n"
    "<script>window.__sc_hydration = " + json.dumps(keep, ensure_ascii=False) + ";</script>\n"
    "</body>\n</html>\n"
)
pathlib.Path("tests/fixtures/soundcloud_playlist.html").write_text(page, encoding="utf-8")

playlist = next(e for e in keep if e["hydratable"] == "systemPlaylist")["data"]
client = next(e for e in keep if e["hydratable"] == "apiClient")["data"]["id"]
ids = [track["id"] for track in playlist["tracks"]]
# Five ids spread over the playlist; the API returns them in its own order.
wanted = [ids[0], ids[3], ids[4], ids[10], ids[30]]
url = ("https://api-v2.soundcloud.com/tracks?ids="
       + urllib.parse.quote(",".join(map(str, wanted)))
       + "&client_id=" + client)
request = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": PAGE})
tracks = json.loads(urllib.request.urlopen(request, timeout=30).read())

fields = ("id", "title", "permalink_url", "genre", "duration", "created_at",
          "playback_count", "likes_count", "reposts_count", "comment_count")
trimmed = []
for track in tracks:                      # API order on purpose
    row = {name: track.get(name) for name in fields}
    row["user"] = {name: (track.get("user") or {}).get(name)
                   for name in ("username", "permalink_url")}
    trimmed.append(row)
pathlib.Path("tests/fixtures/soundcloud_tracks.json").write_text(
    json.dumps(trimmed, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print("playlist ids:", len(ids), "| tracks:", len(trimmed))
PY
```

Ожидаемо: `playlist ids: 50 | tracks: 5`. Значения фикстуры (они же ожидания тестов):

| id | title | artist | genre | duration | published | plays |
| --- | --- | --- | --- | --- | --- | --- |
| 2393623920 | `ural mountains / уральские горы 🌄` | `janecocaine #certified` | `House` | `1:53` | `2026-09-03` | 1258443 |
| 2436299202 | `VAY` | `Mersad` | `Hip-hop & Rap` | `2:40` | `2026-11-11` | 271351 |
| 2437061721 | `Money from heaven music video in desc` | `GoshaRarri ☤` | `""` (в API `null`) | `2:29` | `2026-11-12` | 278510 |
| 2424242760 | `Weeka - Международная (Шампанское)` | `Weeka` | `Trap` | `2:00` | `2026-10-22` | 618711 |
| 2437063257 | `Перье (feat. SQWOZ BAB)` | `GONE.Fludd` | `""` | `2:53` | `2026-11-12` | 87628 |

Порядок плейлиста для этих пяти: `2393623920, 2436299202, 2437061721, 2424242760, 2437063257`; порядок ответа API: `2393623920, 2436299202, 2424242760, 2437061721, 2437063257`. Третий и четвёртый меняются местами — тест порядка не вакуумный.

- [ ] **Step 2: Написать падающие тесты чистых функций**

`tests/unit/test_soundcloud_parser.py`:

```python
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
        ("all-genres", "https://soundcloud.com/discover/sets/trending-by-genre:all-genres"),
        ("pop", "https://soundcloud.com/discover/sets/trending-by-genre:pop"),
        ("hip-hop", "https://soundcloud.com/discover/sets/trending-by-genre:hip-hop"),
        ("electronic", "https://soundcloud.com/discover/sets/trending-by-genre:electronic"),
        ("trap", "https://soundcloud.com/discover/sets/trending-by-genre:trap"),
    ],
)
def test_build_playlist_url(genre, url):
    assert build_playlist_url(SoundcloudSettings(genre=genre)) == url


def test_the_default_genre_is_all_genres():
    assert SoundcloudSettings().genre == "all-genres"
```

`tests/live/test_soundcloud_live.py` (опциональный, пропускается без `UWP_LIVE`):

```python
"""Opt-in live test: the real SoundCloud page still yields exact track rows.

Run with ``UWP_LIVE=1 pytest -m live``. It is skipped by default so the normal
suite never needs the internet.
"""
import json
import os
import urllib.parse
import urllib.request

import pytest

from uwp.parsers.soundcloud_trending.parser import (
    SoundcloudSettings,
    build_playlist_url,
    parse_hydration,
    track_rows,
)

pytestmark = pytest.mark.live

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


@pytest.mark.skipif(not os.environ.get("UWP_LIVE"), reason="set UWP_LIVE=1 to hit SoundCloud")
def test_the_real_playlist_yields_exact_rows():
    url = build_playlist_url(SoundcloudSettings())
    playlist = parse_hydration(_get(url).decode("utf-8", "ignore"))
    assert playlist is not None and playlist.ids
    assert playlist.client_id

    api = "https://api-v2.soundcloud.com/tracks?ids={}&client_id={}".format(
        urllib.parse.quote(",".join(map(str, playlist.ids))), playlist.client_id
    )
    objects = {track["id"]: track for track in json.loads(_get(api))}
    rows = track_rows(playlist.ids, objects)

    # The site does not promise exactly 50: the ``hip-hop`` page returned 49.
    assert 1 <= len(rows) <= 50
    assert all(row["duration"] for row in rows)
    assert all(row["published"] for row in rows)
```

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `.venv/bin/python -m pytest tests/unit/test_soundcloud_parser.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'uwp.parsers.soundcloud_trending'`.

- [ ] **Step 4: Создать пакет и чистые функции**

`src/uwp/parsers/soundcloud_trending/__init__.py`:

```python
"""SoundCloud trending parser package."""
```

`src/uwp/parsers/soundcloud_trending/parser.py` — на этом шаге только константы, настройки, `Playlist` и чистые функции (класс парсера появится в Task 2, `run()` — в Task 3):

```python
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
from typing import Literal

from pydantic import BaseModel, Field

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
```

- [ ] **Step 5: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/unit/test_soundcloud_parser.py -q`
Expected: PASS для тестов чистых функций; тесты класса парсера ещё не написаны.

Run: `.venv/bin/python -m ruff check src tests`
Expected: `All checks passed!` (the test file on this step imports only the pure functions it uses; `SoundcloudTrendingParser` joins the import list in Task 2).

- [ ] **Step 6: Коммит**

```bash
git add src/uwp/parsers/soundcloud_trending/__init__.py \
        src/uwp/parsers/soundcloud_trending/parser.py \
        tests/unit/test_soundcloud_parser.py \
        tests/live/test_soundcloud_live.py \
        tests/fixtures/soundcloud_playlist.html \
        tests/fixtures/soundcloud_tracks.json
git commit -m "feat(parsers): read a SoundCloud playlist from hydration and exact API values"
```

---

### Task 2: Класс парсера, колонки и каталог

**Files:**
- Modify: `src/uwp/parsers/soundcloud_trending/parser.py`
- Modify: `src/uwp/i18n/parsers.py`
- Test: `tests/unit/test_soundcloud_parser.py`

**Interfaces:**
- Consumes: `SoundcloudSettings`, `parse_hydration`, `track_rows` (Task 1); `BaseParser`, `Column` (`uwp.parsers.base`).
- Produces: `SoundcloudTrendingParser` с `key == "soundcloud_trending"`, `url == DISCOVER_URL`, `auth_url == SIGNIN_URL`, `requires_login is False`, 11 колонок; все ключи `parser.soundcloud_trending.*` в `ru` и `en`.

- [ ] **Step 1: Написать падающие тесты метаданных**

Добавить в `tests/unit/test_soundcloud_parser.py`; в шапку файла добавить `from uwp.i18n import Lang`, `from uwp.parsers.registry import ParserRegistry` и `SoundcloudTrendingParser` в импорт из `uwp.parsers.soundcloud_trending.parser`:

```python
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
    for name in ("title", "artist", "url", "artist_url", "genre", "duration", "published"):
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


def test_parser_is_discovered():
    registry = ParserRegistry.discover()

    assert registry.broken() == {}
    assert registry.get("steam_deals") is not None
    assert registry.get("soundcloud_trending") is not None
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/bin/python -m pytest tests/unit/test_soundcloud_parser.py -q`
Expected: FAIL — `ImportError: cannot import name 'SoundcloudTrendingParser'`.

- [ ] **Step 3: Добавить класс в `parser.py`**

Импорты в шапке файла:

```python
from typing import ClassVar, Literal

from uwp.domain import ErrorKind
from uwp.parsers.base import BaseParser, Column, ParseContext, ParseError, ParseResult
```

`ParseContext`, `ParseError`, `ParseResult` и `ErrorKind` нужны заглушке `run()` ниже (её заменит Task 3), поэтому импортируются сразу — иначе ruff сообщит о неиспользуемых импортах.

Ниже `track_rows`:

```python
class SoundcloudTrendingParser(BaseParser):
    """Collects one trending-by-genre playlist: title, artist, exact counters."""

    key = "soundcloud_trending"
    title = "SoundCloud — тренды"
    description = "Треки из раздела «в тренде» SoundCloud: исполнитель, жанр, счётчики и дата."
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
        Column(
            "url", "Ссылка", 55, title_key="parser.soundcloud_trending.column.url"
        ),
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
```

`run()` на этом шаге не нужен для тестов метаданных, но `BaseParser` абстрактный: без метода инстанс не создать. Временно добавьте заглушку, которую заменит Task 3:

```python
    async def run(self, ctx: ParseContext) -> ParseResult:  # pragma: no cover
        raise NotImplementedError
```

- [ ] **Step 4: Добавить ключи каталога**

`src/uwp/i18n/parsers.py`, в блок `Lang.ru` после ключей Steam:

```python
        "parser.soundcloud_trending.title": "SoundCloud — тренды",
        "parser.soundcloud_trending.description": (
            "Треки из раздела «в тренде» SoundCloud: исполнитель, жанр, "
            "длительность, счётчики и дата публикации."
        ),
        "parser.soundcloud_trending.column.title": "Название",
        "parser.soundcloud_trending.column.artist": "Исполнитель",
        "parser.soundcloud_trending.column.url": "Ссылка",
        "parser.soundcloud_trending.column.artist_url": "Исполнитель: ссылка",
        "parser.soundcloud_trending.column.genre": "Жанр",
        "parser.soundcloud_trending.column.duration": "Длительность",
        "parser.soundcloud_trending.column.published": "Дата публикации",
        "parser.soundcloud_trending.column.playback_count": "Прослушивания",
        "parser.soundcloud_trending.column.likes_count": "Лайки",
        "parser.soundcloud_trending.column.reposts_count": "Репосты",
        "parser.soundcloud_trending.column.comment_count": "Комментарии",
        "parser.soundcloud_trending.field.genre": "Жанр трендов",
```

и в блок `Lang.en`:

```python
        "parser.soundcloud_trending.title": "SoundCloud — trending",
        "parser.soundcloud_trending.description": (
            "Trending SoundCloud tracks: artist, genre, duration, counters "
            "and the publication date."
        ),
        "parser.soundcloud_trending.column.title": "Title",
        "parser.soundcloud_trending.column.artist": "Artist",
        "parser.soundcloud_trending.column.url": "Link",
        "parser.soundcloud_trending.column.artist_url": "Artist: link",
        "parser.soundcloud_trending.column.genre": "Genre",
        "parser.soundcloud_trending.column.duration": "Length",
        "parser.soundcloud_trending.column.published": "Published",
        "parser.soundcloud_trending.column.playback_count": "Plays",
        "parser.soundcloud_trending.column.likes_count": "Likes",
        "parser.soundcloud_trending.column.reposts_count": "Reposts",
        "parser.soundcloud_trending.column.comment_count": "Comments",
        "parser.soundcloud_trending.field.genre": "Trending genre",
```

- [ ] **Step 5: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/unit/test_soundcloud_parser.py tests/unit/test_i18n.py tests/unit/test_bot_menu.py -q`
Expected: PASS. `test_bot_menu` подтверждает, что пикер парсеров по-прежнему переводится.

- [ ] **Step 6: Коммит**

```bash
git add src/uwp/parsers/soundcloud_trending/parser.py src/uwp/i18n/parsers.py \
        tests/unit/test_soundcloud_parser.py
git commit -m "feat(parsers): declare the SoundCloud report columns and their labels"
```

---

### Task 3: `run()` — перехват, ожидание, `fetch`-фолбэк и частичный прогон

**Files:**
- Modify: `src/uwp/parsers/soundcloud_trending/parser.py`
- Test: `tests/unit/test_soundcloud_parser.py`

**Interfaces:**
- Consumes: `parse_hydration`, `track_rows`, `build_playlist_url` (Task 1), `SoundcloudTrendingParser` (Task 2).
- Produces: `run(ctx)` — подписка на `response` до `goto`, `goto(..., wait_until="domcontentloaded")`, ожидание до `_COLLECT_TIMEOUT_S` (10 с), один `page.evaluate`-фолбэк, `ParseResult(rows, meta={"playlist", "url", "objects"})`, `ParseError(blocked)`/`ParseError(blocked, partial_rows=…)`. Модульные константы `_COLLECT_TIMEOUT_S`, `_POLL_INTERVAL_S`, `_FETCH_TRACKS` — тесты их подменяют.

- [ ] **Step 1: Написать падающие тесты прогона**

Добавить в `tests/unit/test_soundcloud_parser.py` (импорты: `asyncio`, `logging`, `pytest`, `ParseContext`, `ParseError`, `ErrorKind`, плюс `import uwp.parsers.soundcloud_trending.parser as soundcloud`):

```python
TRACKS_URL = "https://api-v2.soundcloud.com/tracks?ids=1%2C2"


def _playlist_html(ids, *, title="All Genres", client_id="CLIENT"):
    """The smallest page that carries a usable hydration block."""
    entries = [
        {"hydratable": "apiClient", "data": {"id": client_id, "isExpiring": True}},
        {
            "hydratable": "systemPlaylist",
            "data": {"title": title, "tracks": [{"id": i, "kind": "track"} for i in ids]},
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
    """The collection window is wall-clock; a unit test must not spend 10 s in it."""
    monkeypatch.setattr(soundcloud, "_COLLECT_TIMEOUT_S", 0.0)


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
        ("https://soundcloud.com/discover/sets/trending-by-genre:all-genres", "domcontentloaded")
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


async def test_run_waits_for_a_response_that_lands_after_the_navigation(tmp_path, monkeypatch):
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
    assert excinfo.value.message == "SoundCloud returned no playlist (geo block or markup change?)"
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


def test_the_collector_reads_a_batch_track_response():
    collector = soundcloud._TrackCollector()

    asyncio.run(collector.handle(_FakeResponse(TRACKS_URL, [_track(1), _track(2)])))

    assert sorted(collector.objects) == [1, 2]


def test_the_collector_ignores_per_track_endpoints_and_error_bodies():
    """``/tracks/<id>/comments`` is not a track list and a 401 body is a dict."""
    collector = soundcloud._TrackCollector()

    asyncio.run(collector.handle(_FakeResponse(
        "https://api-v2.soundcloud.com/tracks/1/comments?client_id=x", [_track(1)])))
    asyncio.run(collector.handle(_FakeResponse(TRACKS_URL, {"errors": [{"error_message": "401"}]})))

    assert collector.objects == {}


def test_the_collector_keeps_the_first_object_for_an_id():
    collector = soundcloud._TrackCollector()

    asyncio.run(collector.handle(_FakeResponse(TRACKS_URL, [_track(1, title="first")])))
    asyncio.run(collector.handle(_FakeResponse(TRACKS_URL, [_track(1, title="second")])))

    assert collector.objects[1]["title"] == "first"
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/bin/python -m pytest tests/unit/test_soundcloud_parser.py -q`
Expected: FAIL — `run()` бросает `NotImplementedError`, `_TrackCollector` не существует.

- [ ] **Step 3: Переписать `parser.py` с прогоном**

Импорты в шапке:

```python
import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from uwp.domain import ErrorKind
from uwp.parsers.base import BaseParser, Column, ParseContext, ParseError, ParseResult
```

Константы после `TRENDING_URL`:

```python
# The page's own batch request usually lands within a second; the window only
# covers a slow response before the single ``fetch`` fallback below.
_COLLECT_TIMEOUT_S = 10.0
_POLL_INTERVAL_S = 0.25

_API_TRACKS = "api-v2.soundcloud.com/tracks?"

_NO_PLAYLIST = "SoundCloud returned no playlist (geo block or markup change?)"
_NO_TRACKS = "SoundCloud returned no tracks (geo block, captcha or API change?)"

# The page's own call, repeated from the page: same endpoint, same session, so
# a rotated ``client_id`` is the only thing that can make it fail.
_FETCH_TRACKS = """
async ({ids, clientId}) => {
  const url = "https://api-v2.soundcloud.com/tracks?ids="
    + ids.join("%2C") + "&client_id=" + encodeURIComponent(clientId);
  const response = await fetch(url, {credentials: "include"});
  if (!response.ok) return [];
  return await response.json();
}
"""
```

Коллектор и фолбэк после `track_rows`:

```python
class _TrackCollector:
    """Collects full track objects from the page's own ``api-v2`` responses."""

    def __init__(self) -> None:
        self.objects: dict[int, dict] = {}

    async def handle(self, response) -> None:
        # ``/tracks?ids=`` is the batch call; ``/tracks/<id>/comments`` and the
        # other per-track endpoints are not track lists.
        if _API_TRACKS not in response.url:
            return
        try:
            payload = await response.json()
        except Exception:  # a body the page already dropped is not fatal
            return
        self.remember(payload)

    def remember(self, payload) -> None:
        if not isinstance(payload, list):
            return
        for track in payload:
            if isinstance(track, dict) and isinstance(track.get("id"), int):
                self.objects.setdefault(track["id"], track)


def _missing(collector: _TrackCollector, ids: list[int]) -> list[int]:
    return [track_id for track_id in ids if track_id not in collector.objects]


async def _collect_missing(
    collector: _TrackCollector, playlist: Playlist, page
) -> None:
    """Wait for the page's request, then ask the same endpoint once for the rest."""
    deadline = time.monotonic() + _COLLECT_TIMEOUT_S
    while time.monotonic() < deadline and _missing(collector, playlist.ids):
        await asyncio.sleep(_POLL_INTERVAL_S)
    missing = _missing(collector, playlist.ids)
    if not missing or not playlist.client_id:
        return
    try:
        payload = await page.evaluate(
            _FETCH_TRACKS, {"ids": missing, "clientId": playlist.client_id}
        )
    except Exception:  # the fetch is a second chance, never a requirement
        return
    collector.remember(payload)
```

`run()` вместо заглушки:

```python
    async def run(self, ctx: ParseContext) -> ParseResult:
        settings: SoundcloudSettings = ctx.settings  # type: ignore[assignment]
        url = build_playlist_url(settings)
        collector = _TrackCollector()
        # Subscribed before ``goto``: the page's own ``/tracks`` request can
        # finish before ``domcontentloaded`` and a listener added afterwards
        # would miss it. The listener dies with the page; Playwright re-wraps a
        # handler on ``remove_listener``, so there is nothing to unsubscribe.
        ctx.page.on("response", collector.handle)
        await ctx.page.goto(url, wait_until="domcontentloaded")
        html = await ctx.page.content()

        playlist = parse_hydration(html)
        if playlist is None or not playlist.ids:
            ctx.save_artifact("empty.html", html.encode())
            raise ParseError(kind=ErrorKind.blocked, message=_NO_PLAYLIST)

        await _collect_missing(collector, playlist, ctx.page)
        rows = track_rows(playlist.ids, collector.objects)
        if not rows:
            ctx.save_artifact("empty.html", html.encode())
            raise ParseError(kind=ErrorKind.blocked, message=_NO_TRACKS)

        if len(rows) < len(playlist.ids):
            ctx.log.warning(
                "SoundCloud returned %s of %s tracks; missing: %s",
                len(rows),
                len(playlist.ids),
                ", ".join(str(track_id) for track_id in _missing(collector, playlist.ids)),
            )
            # ``partial_rows`` makes the runner save with ``complete=False``, so
            # the tracks that did not arrive are not marked absent and the next
            # full run does not report them all as new.
            raise ParseError(
                kind=ErrorKind.blocked,
                partial_rows=rows,
                message=f"SoundCloud returned {len(rows)} of {len(playlist.ids)} tracks",
            )

        return ParseResult(
            rows=rows,
            meta={
                "playlist": playlist.title,
                "url": url,
                "objects": len(collector.objects),
            },
        )
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/unit/test_soundcloud_parser.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/uwp/parsers/soundcloud_trending/parser.py tests/unit/test_soundcloud_parser.py
git commit -m "feat(parsers): collect SoundCloud tracks from the page's own api-v2 responses"
```

---

### Task 4: README парсера на двух языках

**Files:**
- Create: `src/uwp/parsers/soundcloud_trending/README.md`
- Create: `src/uwp/parsers/soundcloud_trending/README.ru.md`
- Modify: `tests/unit/test_readme_translations.py`

**Interfaces:**
- Consumes: everything from Tasks 1–3.
- Produces: пара README с одинаковым числом и порядком заголовков, с взаимными ссылками; пара добавлена в `PAIRS`.

- [ ] **Step 1: Добавить пару в тест переводов**

`tests/unit/test_readme_translations.py`, в `PAIRS` после строки Steam:

```python
    (
        "src/uwp/parsers/soundcloud_trending/README.md",
        "src/uwp/parsers/soundcloud_trending/README.ru.md",
    ),
```

Run: `.venv/bin/python -m pytest tests/unit/test_readme_translations.py -q`
Expected: FAIL — файлов ещё нет.

- [ ] **Step 2: Написать `README.md`**

```markdown
# SoundCloud trending parser (`soundcloud_trending`)

**Русская версия:** [`README.ru.md`](README.ru.md).

Scrapes one «trending by genre» playlist from
`https://soundcloud.com/discover/sets/trending-by-genre:<genre>`. One run is one
genre, at most 50 tracks. Works on a clean profile: `requires_login = False`.

## How it works

- `auth_url` points at `https://soundcloud.com/signin`: a VNC login opens the
  form, while `url` stays `https://soundcloud.com/discover`, the section the
  report names.
- `build_playlist_url(settings)` fills the genre into
  `https://soundcloud.com/discover/sets/trending-by-genre:{genre}`.
- `run(ctx)` subscribes `page.on("response", …)` **before** `goto`, then loads
  the page with `wait_until="domcontentloaded"` and reads `page.content()`. The
  listener keeps the track objects from the page's own
  `api-v2.soundcloud.com/tracks?ids=…` request; the listener must exist before
  the navigation, or a response that lands early is lost.
- `parse_hydration(html)` is pure: it reads the server-rendered
  `window.__sc_hydration` block, takes the title and the ordered track ids from
  `systemPlaylist` and the `client_id` from `apiClient`. The page itself renders
  only ten rows, but the block is in the HTML `curl` receives, so no scrolling
  and no `page.evaluate` are needed.
- Rows are built in the hydration id order (`track_rows`). The `/tracks` answer
  comes in a different order; following it would shuffle the report, and
  `export_mode=changes` would read a shuffle as a change.
- If the ids are not all covered, the parser waits up to 10 s in short steps,
  then asks the same endpoint once through `page.evaluate(fetch(…))` with the
  hydration `client_id`. A missing `client_id` is not an error: the run only
  loses the tracks the interception did not see.
- Nothing at all (no playlist block, or no track object from either path) is
  `ParseError(kind=blocked, message="SoundCloud returned no tracks (geo block,
  captcha or API change?)")`, with the page saved as `empty.html`. A partial
  answer is `ParseError(kind=blocked, partial_rows=rows,
  message="SoundCloud returned N of M tracks")`: the runner stores those rows
  with `complete=False`, so the missing tracks are not counted as vanished.
- `external_key` is the track id as a string.

## Settings

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `genre` | `all-genres` / `pop` / `hip-hop` / `electronic` / `trap` | `all-genres` | Which trending page to open |

The values are the slugs `/discover` links to. `hip-hop-rap` is a 404 while
`hip-hop` is a 200, so a made-up slug is not accepted. The bot cycles the value
with one button, which is why the field is a `Literal` and not free text.

## Columns

| Key | Title | Source | `volatile` |
| --- | --- | --- | --- |
| `title` | Title | `title` | no |
| `artist` | Artist | `user.username` | no |
| `url` | Link | `permalink_url` | no |
| `artist_url` | Artist: link | `user.permalink_url` | no |
| `genre` | Genre | `genre` | no |
| `duration` | Length | `duration` (ms → `m:ss`) | no |
| `published` | Published | `created_at` (UTC day) | no |
| `playback_count` | Plays | `playback_count` | **yes** |
| `likes_count` | Likes | `likes_count` | **yes** |
| `reposts_count` | Reposts | `reposts_count` | **yes** |
| `comment_count` | Comments | `comment_count` | **yes** |

A missing value stays empty and a missing counter stays `None`, never `0`: "no
data" and "zero plays" are different facts, and a counter that starts at zero
would look like a change the moment the real number arrived.

## Why there is no DOM fallback

The rendered rows carry rounded counters (`1.25M`, `411K`), no publication date,
and a different author than the API (`janecocaine, prodwhiteball` for a track
whose API `user.username` is `ICEGERGERT`). Rows from the DOM would be stored in
the database and compared against the exact values of the next run: a run that
falls back would overwrite good data and then fire a false "changed" burst when
the API returned. `items.upsert` overwrites the payload even for a partial run,
so "approximate" is not a safe state. A run that cannot reach the JSON fails as
`blocked` instead.

## Switching the genre

`external_key` is the track id, so changing `genre` on an existing site replaces
the whole row set: the old tracks become absent and the new ones are new. That is
expected. To watch two genres side by side, add two sites, one per genre.

## Tests

`.venv/bin/python -m pytest tests/unit/test_soundcloud_parser.py -v`

To refresh the fixtures (the page, then five track objects in the API's order):

```bash
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
curl -A "$UA" "https://soundcloud.com/discover/sets/trending-by-genre:all-genres" \
  -o /tmp/soundcloud.html
```

The rest of the refresh (trimming the hydration block and fetching five ids)
is the Python snippet in
[`../../../../docs/superpowers/plans/2026-11-22-soundcloud-trending-parser.md`](../../../../docs/superpowers/plans/2026-11-22-soundcloud-trending-parser.md),
Task 1. The fixture is the source of truth for the unit tests: if SoundCloud
changes its markup or the playlist changes, update the expected values together
with it.
```

- [ ] **Step 3: Написать `README.ru.md`**

Тот же текст по-русски, **то же число и порядок заголовков** (`## Как устроен`,
`## Настройки`, `## Колонки`, `## Почему нет запасного DOM-пути`,
`## Смена жанра`, `## Тесты`), та же ссылка на `README.md` в шапке, те же
таблицы и тот же блок кода. Ключевые формулировки:

- «Один прогон — один жанр, до 50 треков. Работает на чистом профиле:
  `requires_login = False`.»
- «`auth_url` указывает на `https://soundcloud.com/signin`…»
- «Подписка `page.on("response", …)` стоит **до** `goto`…»
- «Порядок строк — порядок id из hydration…»
- «Счётчики помечены `volatile`: в отчёте они есть, изменением не считаются.»
- «Запасного DOM-пути нет: в DOM числа округлены, даты нет, автор другой…»

- [ ] **Step 4: Прогнать проверку переводов**

Run: `.venv/bin/python -m pytest tests/unit/test_readme_translations.py -q`
Expected: PASS (число секций, порядок уровней, кросс-ссылки, парность ```).

- [ ] **Step 5: Коммит**

```bash
git add src/uwp/parsers/soundcloud_trending/README.md \
        src/uwp/parsers/soundcloud_trending/README.ru.md \
        tests/unit/test_readme_translations.py
git commit -m "docs(parsers): explain the SoundCloud parser and its fixtures"
```

---

### Task 5: Документация репозитория

**Files:**
- Modify: `docs/superpowers/specs/2026-11-22-soundcloud-parser-design.md` (статус)
- Modify: `docs/README.md`, `docs/README.ru.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: спека со статусом «утверждена 2026-11-22»; обе таблицы (`Design specs`, `Implementation plans`) со строками о новой паре; абзац о двух парсерах в «Writing your own parser»; `AGENTS.md` знает про второй парсер.

- [ ] **Step 1: Отметить спеку утверждённой**

`docs/superpowers/specs/2026-11-22-soundcloud-parser-design.md`, строка 4:

```markdown
Статус: утверждена 2026-11-22
```

- [ ] **Step 2: `docs/README.md`**

В `### Writing your own parser`, после абзаца про `url`/`auth_url`:

```markdown
The repository ships two parsers: `steam_deals` reads a DOM with
`BeautifulSoup`, and `soundcloud_trending` shows the other common shape — a page
that already fetches its own JSON, so the parser intercepts the response and
reads exact values instead of the rounded text in the DOM.
```

В таблице `## Design specs` после строки `2026-11-21-parser-auth-url-design.md`:

```markdown
| [`superpowers/specs/2026-11-22-soundcloud-parser-design.md`](superpowers/specs/2026-11-22-soundcloud-parser-design.md) | a second parser: exact counters from the page's own API, and why there is no DOM fallback |
```

В таблице `## Implementation plans` после строки `2026-11-21-parser-auth-url.md`:

```markdown
| [`superpowers/plans/2026-11-22-soundcloud-trending-parser.md`](superpowers/plans/2026-11-22-soundcloud-trending-parser.md) | the SoundCloud parser: hydration, response interception, partial runs |
```

- [ ] **Step 3: `docs/README.ru.md`** — те же три вставки по-русски, в тех же местах:

```markdown
В репозитории два парсера: `steam_deals` разбирает DOM через `BeautifulSoup`, а
`soundcloud_trending` показывает другую частую форму — страница сама забирает
свой JSON, поэтому парсер перехватывает ответ и берёт точные значения вместо
округлённого текста в DOM.
```

```markdown
| [`superpowers/specs/2026-11-22-soundcloud-parser-design.md`](superpowers/specs/2026-11-22-soundcloud-parser-design.md) | второй парсер: точные счётчики из API самой страницы и почему запасного DOM-пути нет |
```

```markdown
| [`superpowers/plans/2026-11-22-soundcloud-trending-parser.md`](superpowers/plans/2026-11-22-soundcloud-trending-parser.md) | парсер SoundCloud: hydration, перехват ответов, частичные прогоны |
```

- [ ] **Step 4: `AGENTS.md`**

В таблице «Where to look first», строка про парсер:

```markdown
| Add a parser | `parsers/steam_deals/` (DOM) or `parsers/soundcloud_trending/` (intercepted API), with the README in that folder |
```

В `## Layout` описание папки `parsers/` не меняется (там перечислены только `base.py` и `registry.py`).

- [ ] **Step 5: Прогнать проверки документации**

Run: `.venv/bin/python -m pytest tests/unit/test_readme_translations.py tests/unit/test_i18n.py -q`
Expected: PASS.

- [ ] **Step 6: Коммит**

```bash
git add docs/README.md docs/README.ru.md AGENTS.md \
        docs/superpowers/specs/2026-11-22-soundcloud-parser-design.md
git commit -m "docs: approve and link the SoundCloud parser design and plan"
```

---

### Task 6: Полная проверка и живой замер

**Files:** none (проверка).

- [ ] **Step 1: Весь набор тестов**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, `0 failed`; прежние 3 пропуска допустимы (плюс новый live-тест пропущен).

- [ ] **Step 2: Линтер**

Run: `.venv/bin/python -m ruff check src tests`
Expected: `All checks passed!`

- [ ] **Step 3: Живой тест (по желанию, но именно он ловит смену страницы)**

Run: `UWP_LIVE=1 .venv/bin/python -m pytest tests/live/test_soundcloud_live.py -q`
Expected: PASS — от 1 до 50 строк, у всех непустые `duration` и `published`.

- [ ] **Step 4: Прогон в контейнере на двух жанрах**

```bash
docker compose -f compose.split.yaml up -d --build
docker compose -f compose.split.yaml exec worker uwp site add soundcloud_trending --name "SoundCloud — all genres"
docker compose -f compose.split.yaml exec worker uwp site run soundcloud-all-genres
docker compose -f compose.split.yaml exec worker uwp site add soundcloud_trending --name "SoundCloud — electronic"
docker compose -f compose.split.yaml exec worker uwp site run soundcloud-electronic
```

Проверить в чате: отчёт на 1–50 строк, «Прослушивания» — точное число (не `1.25M`),
«Дата публикации» заполнена, «Длительность» вида `2:17`; в `data/artifacts/` нет
`empty.html` у удачных прогонов. Если жанр пришлось переключить у существующего
сайта — убедиться, что старые строки ушли в `absent`, а новые пришли как «Новые»
(это ожидаемо и описано в README парсера).

- [ ] **Step 5: Коммит, если шаги 1–4 потребовали правок**

Отдельный коммит с измеренным симптомом в теле, например
`fix(parsers): read SoundCloud ids in the playlist order` — не смешивать с
документацией и не добавлять `docs/images/vnc-desktop.png`.
