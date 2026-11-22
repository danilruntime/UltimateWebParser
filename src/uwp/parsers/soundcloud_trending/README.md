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
and sometimes a different author than the API. Rows from the DOM would be stored
in the database and compared against the exact values of the next run: a run
that fell back would overwrite good data and then fire a false "changed" burst
when the API returned. `items.upsert` overwrites the payload even for a partial
run, so "approximate" is not a safe state. A run that cannot reach the JSON
fails as `blocked` instead.

## Switching the genre

`external_key` is the track id, so changing `genre` on an existing site replaces
the whole row set: the old tracks become absent and the new ones are new. That is
expected. To watch two genres side by side, add two sites, one per genre.

## Tests

`.venv/bin/python -m pytest tests/unit/test_soundcloud_parser.py -v`

To refresh the fixtures, first capture the page:

```bash
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
curl -A "$UA" "https://soundcloud.com/discover/sets/trending-by-genre:all-genres" \
  -o /tmp/soundcloud.html
```

The rest of the refresh (trimming the hydration block and fetching five ids) is
the Python snippet in Task 1 of
[`../../../../docs/superpowers/plans/2026-11-22-soundcloud-trending-parser.md`](../../../../docs/superpowers/plans/2026-11-22-soundcloud-trending-parser.md).
The fixture is the source of truth for the unit tests: if SoundCloud changes its
markup or the playlist changes, update the expected values together with it.
