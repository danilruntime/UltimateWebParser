# Steam deals parser (`steam_deals`)

**Русская версия:** [`README.ru.md`](README.ru.md).

Scrapes discounted games from the Steam store search page
(`https://store.steampowered.com/search/?specials=1`). Works on a clean
profile: `requires_login = False`.

## How it works

- `build_search_url(settings, start)` builds one results page URL
  (`filter`, `specials=1`, `cc=<region>`, `l=<language>`, `start=`).
- `run(ctx)` walks pages `start = 0, 50, 100, …` up to `max_pages`, loading each
  with `page.goto(url, wait_until="domcontentloaded")` and `page.content()`.
  It stops early when a page yields no rows.
- `parse_search_page(html, settings)` is pure (no browser, no network): it parses
  `a.search_result_row` cards, skips rows without a discount, applies the
  discount/price filters and sorts. Unit tests run it against
  `tests/fixtures/steam_search.html`, a real page captured with `curl`.
- If the first page yields zero rows (captcha, region block, markup change) the
  raw HTML is saved as the `empty.html` artifact and the run fails with
  `ParseError(kind=blocked, message="Steam returned no results (captcha or region block?)")`.
- `external_key` for dedup is the Steam app id (`app_id`).

## Settings (`SteamSettings`)

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `region` | str | `us` | Store country code (`us`, `ru`, `de`, …) → `cc=` |
| `language` | str | `english` | Store page language (`english`, `russian`, …) → `l=` |
| `filter` | `topsellers` / `newreleases` / `specials` | `topsellers` | Steam search filter |
| `max_pages` | int 1–10 | `2` | How many result pages to walk (50 games each) |
| `max_price` | float ≥ 0 | `0` | Max final price; `0` = no limit |
| `min_discount_percent` | int 0–100 | `0` | Minimum discount percent |
| `sort` | `relevance` / `discount` / `price` | `relevance` | `relevance` keeps Steam order, `discount` sorts by discount desc, `price` by final price asc |

## Columns

| Key | Title | Notes |
| --- | --- | --- |
| `app_id` | App ID | First id of `data-ds-appid` (before any comma) |
| `title` | Title | `.title` |
| `url` | Link | Canonical `https://store.steampowered.com/app/<app_id>/` (query stripped) |
| `discount_percent` | Discount, % | `-75%` → `75` |
| `price_original` | Original price | Float parsed from `$19.99` / `4,99€` |
| `price_final` | Final price | Float; falls back to `data-price-final` cents |
| `currency` | Currency | ISO code from the price symbol (`$` → USD, `€` → EUR, …) |
| `review_summary` | Reviews | First line of `data-tooltip-html` (e.g. `Overwhelmingly Positive`); empty when no reviews |
| `review_percent` | Positive, % | `volatile`; from `97% of the 147,599 user reviews`; `None` when absent |
| `review_count` | Review count | `volatile`; same tooltip; `None` when absent |
| `release_date` | Release date | `.search_released` as shown by Steam |
| `platforms` | Platforms | `win mac linux` → `Windows, macOS, Linux` |
| `image_url` | Cover | Capsule `img src` |

`volatile` columns are written to the report but excluded from change
detection: Steam's review counters move every few minutes, so treating them as
changes made `export_mode=changes` deliver a file on every run (measured: 43 of
63 reports had zero new rows and differed only in these two columns).
`review_summary` stays tracked on purpose — «Very Positive» → «Mixed» is news.

The headers above are what the catalogue (`uwp.i18n.parsers`, keys
`parser.steam_deals.*`) renders in English; a Russian report prints the same
columns in Russian. A parser without keys still works — its own `title`,
`description` and `Column.title` are shown as written — while one that wants
both languages points the keys at the catalogue:

`Column("title", "Название", 40, title_key="parser.steam_deals.column.title")`

## Tests

`.venv/bin/python -m pytest tests/unit/test_steam_parser.py -v`

To refresh the fixture:

```bash
curl -A "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36" \
  "https://store.steampowered.com/search/?filter=topsellers&specials=1&cc=us&l=english" \
  -o tests/fixtures/steam_search.html
```

The fixture is the source of truth for the unit tests: if Steam changes its
markup or the deals change, update the expected values together with it.
