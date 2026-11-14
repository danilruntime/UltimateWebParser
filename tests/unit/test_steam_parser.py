"""Offline tests for the Steam deals parser (fixture captured from the live store)."""
import logging
from pathlib import Path

import pytest

from uwp.domain import ErrorKind
from uwp.parsers.base import ParseContext, ParseError
from uwp.parsers.registry import ParserRegistry
from uwp.parsers.steam_deals.parser import (
    SteamDealsParser,
    SteamSettings,
    _parse_price,
    build_search_url,
    parse_search_page,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "steam_search.html"


def _fixture_html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_build_search_url():
    url = build_search_url(SteamSettings(region="de", language="german", max_pages=3), start=50)
    assert url.startswith("https://store.steampowered.com/search/?")
    assert "specials=1" in url and "cc=de" in url and "l=german" in url
    assert "start=50" in url and "filter=topsellers" in url


def test_build_search_url_uses_filter():
    url = build_search_url(SteamSettings(filter="newreleases"), start=0)
    assert "filter=newreleases" in url and "start=0" in url


def test_parse_fixture_rows():
    rows = parse_search_page(_fixture_html(), SteamSettings())
    assert len(rows) >= 40
    first = rows[0]
    assert first["app_id"] == "227300"
    assert first["title"] == "Euro Truck Simulator 2"
    assert first["url"] == "https://store.steampowered.com/app/227300/"
    assert first["discount_percent"] == 75
    assert first["price_final"] == 4.99 and first["price_original"] == 19.99
    assert first["currency"] == "USD"
    assert first["review_summary"] == "Overwhelmingly Positive"
    assert first["review_percent"] == 97 and first["review_count"] == 147599
    assert first["release_date"] == "Oct 12, 2012"
    assert first["platforms"] == "Windows, macOS, Linux"
    assert first["image_url"].startswith("https://shared.akamai.steamstatic.com/")


def test_every_row_carries_an_external_key_for_dedup():
    rows = parse_search_page(_fixture_html(), SteamSettings())
    assert all(row["external_key"] == row["app_id"] for row in rows)
    assert len({row["external_key"] for row in rows}) == len(rows)


def test_filters_by_min_discount_and_max_price():
    html = _fixture_html()
    rows = parse_search_page(html, SteamSettings(min_discount_percent=80, max_price=6))
    assert rows and all(r["discount_percent"] >= 80 and r["price_final"] <= 6 for r in rows)
    assert {r["app_id"] for r in rows} == {"397540"}


def test_rows_without_discount_are_skipped():
    rows = parse_search_page(_fixture_html(), SteamSettings())
    assert all(r["discount_percent"] > 0 for r in rows)


def test_row_without_reviews_has_empty_review_fields():
    rows = parse_search_page(_fixture_html(), SteamSettings())
    row = next(r for r in rows if r["app_id"] == "2272360")
    assert row["review_summary"] == ""
    assert row["review_percent"] is None
    assert row["review_count"] is None


def test_sort_by_discount_and_price():
    html = _fixture_html()
    by_discount = parse_search_page(html, SteamSettings(sort="discount"))
    assert [r["discount_percent"] for r in by_discount] == sorted(
        (r["discount_percent"] for r in by_discount), reverse=True
    )
    by_price = parse_search_page(html, SteamSettings(sort="price"))
    assert [r["price_final"] for r in by_price] == sorted(r["price_final"] for r in by_price)


def test_parse_undiscounted_row_is_skipped():
    html = """
    <a class="search_result_row" data-ds-appid="1" href="https://store.steampowered.com/app/1/X/">
      <span class="title">Full price game</span>
      <div class="discount_block"><div class="discount_final_price">$9.99</div></div>
    </a>
    """
    assert parse_search_page(html, SteamSettings()) == []


def test_parse_euro_price_and_comma_decimal():
    html = """
    <a class="search_result_row" data-ds-appid="2,3"
       href="https://store.steampowered.com/app/2/Y/?snr=1">
      <span class="title">Euro game</span>
      <div class="discount_block" data-discount="50">
        <div class="discount_pct">-50%</div>
        <div class="discount_original_price">9,99€</div>
        <div class="discount_final_price">4,99€</div>
      </div>
    </a>
    """
    rows = parse_search_page(html, SteamSettings())
    assert len(rows) == 1
    row = rows[0]
    assert row["app_id"] == "2"
    assert row["url"] == "https://store.steampowered.com/app/2/"
    assert row["price_original"] == 9.99 and row["price_final"] == 4.99
    assert row["currency"] == "EUR"
    assert row["discount_percent"] == 50


def test_parse_price_handles_thousands_separators():
    """``1.234,56 €`` is 1234.56, not 1.23456: the rightmost separator is decimal."""
    cases = {
        "1.234,56 €": 1234.56,
        "1,234.56 €": 1234.56,
        "1 234,56 ₽": 1234.56,
        # Steam's French/Russian pages use a narrow or thin no-break space.
        "1\u202f234,56 €": 1234.56,
        "1\u2009234,56 €": 1234.56,
        "12,345,678.90": 12345678.90,
        "1.234": 1234.0,
        "1,234": 1234.0,
        "4,99€": 4.99,
        "$19.99": 19.99,
        "19,99 руб.": 19.99,
        "2 499.00": 2499.0,
        # A trailing separator is not a decimal point: these are 19.99 and 4.99.
        "19,99.-": 19.99,
        "4,99.": 4.99,
    }

    for text, expected in cases.items():
        assert _parse_price(text)[0] == expected, text


def test_parse_price_reads_brazilian_and_canadian_dollars_as_their_own_currency():
    assert _parse_price("R$ 19,99") == (19.99, "BRL")
    assert _parse_price("CDN$ 19,99") == (19.99, "CAD")


def test_parser_metadata_and_columns():
    parser = SteamDealsParser()
    assert parser.key == "steam_deals"
    assert parser.title == "Steam — скидки"
    assert parser.url == "https://store.steampowered.com/search/"
    assert parser.requires_login is False
    assert parser.settings_model is SteamSettings
    assert [c.key for c in parser.columns] == [
        "app_id",
        "title",
        "url",
        "discount_percent",
        "price_original",
        "price_final",
        "currency",
        "review_summary",
        "review_percent",
        "review_count",
        "release_date",
        "platforms",
        "image_url",
    ]


def test_settings_have_russian_descriptions():
    for name, field in SteamSettings.model_fields.items():
        assert field.description, f"{name} has no description"


def test_parser_is_discovered():
    reg = ParserRegistry.discover()
    assert reg.broken() == {}
    assert reg.get("steam_deals") is not None

class _FakePage:
    def __init__(self, pages: list[str]) -> None:
        self.pages = pages
        self.goto_calls: list[tuple[str, str]] = []

    async def goto(self, url: str, wait_until: str | None = None) -> None:
        self.goto_calls.append((url, wait_until))

    async def content(self) -> str:
        return self.pages.pop(0)


def _ctx(page, tmp_path: Path, settings: SteamSettings) -> tuple[ParseContext, list]:
    saved: list[tuple[str, bytes]] = []
    ctx = ParseContext(
        page=page,
        context=None,
        settings=settings,
        log=logging.getLogger("test.steam"),
        artifacts_dir=tmp_path,
        site=None,
        profile=None,
        known_keys=lambda: set(),
        save_artifact=lambda name, data: saved.append((name, data)) or tmp_path / name,
    )
    return ctx, saved


async def test_run_collects_pages_and_stops_on_empty(tmp_path: Path):
    html = _fixture_html()
    page = _FakePage([html, html, ""])
    ctx, _ = _ctx(page, tmp_path, SteamSettings(max_pages=5))
    result = await SteamDealsParser().run(ctx)

    assert len(page.goto_calls) == 3
    assert all(wait_until == "domcontentloaded" for _, wait_until in page.goto_calls)
    assert "start=0" in page.goto_calls[0][0]
    assert "start=50" in page.goto_calls[1][0]
    assert "start=100" in page.goto_calls[2][0]
    assert len(result.rows) == 100
    assert result.meta["pages"] == 2
    assert result.meta["url"] == page.goto_calls[0][0]


async def test_run_respects_max_pages(tmp_path: Path):
    page = _FakePage([_fixture_html()] * 3)
    ctx, _ = _ctx(page, tmp_path, SteamSettings(max_pages=2))
    result = await SteamDealsParser().run(ctx)
    assert len(page.goto_calls) == 2
    assert result.meta["pages"] == 2


async def test_run_raises_blocked_and_saves_artifact_on_empty_first_page(tmp_path: Path):
    page = _FakePage(["<html><body>captcha</body></html>"])
    ctx, saved = _ctx(page, tmp_path, SteamSettings())
    with pytest.raises(ParseError) as excinfo:
        await SteamDealsParser().run(ctx)
    assert excinfo.value.kind is ErrorKind.blocked
    assert excinfo.value.message == "Steam returned no results (captcha or region block?)"
    assert saved and saved[0][0] == "empty.html"
    assert b"captcha" in saved[0][1]
