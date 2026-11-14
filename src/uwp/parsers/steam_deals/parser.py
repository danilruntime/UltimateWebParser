"""Steam store search parser: discounted games from ``store.steampowered.com/search/``."""
import re
from typing import ClassVar, Literal
from urllib.parse import urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, Field

from uwp.domain import ErrorKind
from uwp.parsers.base import BaseParser, Column, ParseContext, ParseError, ParseResult

SEARCH_URL = "https://store.steampowered.com/search/"
PAGE_SIZE = 50

_PLATFORMS = {"win": "Windows", "mac": "macOS", "linux": "Linux"}
# Longest symbol first: ``R$``/``CDN$`` would otherwise match the bare ``$``.
_CURRENCY_SYMBOLS = (
    ("CDN$", "CAD"),
    ("R$", "BRL"),
    ("$", "USD"),
    ("€", "EUR"),
    ("£", "GBP"),
    ("₽", "RUB"),
    ("¥", "JPY"),
    ("₴", "UAH"),
)
_CURRENCY_CODES = {"USD", "EUR", "GBP", "RUB", "UAH", "JPY", "KZT", "BRL", "CAD", "TRY", "PLN"}

_REVIEW_RE = re.compile(r"(\d+)%\s+of\s+the\s+([\d,]+)\s+user\s+reviews", re.IGNORECASE)
_NUMBER_RE = re.compile(r"-?\d[\d,.\s]*")


class SteamSettings(BaseModel):
    """User-facing settings for the Steam deals parser."""

    region: str = Field("us", description="Регион магазина Steam (код страны: us, ru, de, …)")
    language: str = Field("english", description="Язык страницы магазина (english, russian, …)")
    filter: Literal["topsellers", "newreleases", "specials"] = Field(
        "topsellers", description="Подборка Steam: topsellers, newreleases или specials"
    )
    max_pages: int = Field(
        2, ge=1, le=10, description="Сколько страниц результатов просматривать (1–10)"
    )
    max_price: float = Field(0, ge=0, description="Максимальная итоговая цена; 0 — без ограничения")
    min_discount_percent: int = Field(
        0, ge=0, le=100, description="Минимальная скидка в процентах (0 — любая)"
    )
    sort: Literal["relevance", "discount", "price"] = Field(
        "relevance",
        description="Сортировка: relevance — как в Steam, discount — по скидке, price — по цене",
    )


def build_search_url(settings: SteamSettings, start: int) -> str:
    """Build the search URL for one results page."""
    query = urlencode(
        {
            "filter": settings.filter,
            "specials": 1,
            "cc": settings.region,
            "l": settings.language,
            "start": start,
        }
    )
    return f"{SEARCH_URL}?{query}"


def _text(node: Tag | None) -> str:
    return node.get_text(strip=True) if node is not None else ""


def _parse_price(text: str) -> tuple[float | None, str]:
    """Parse ``$19.99`` / ``4,99€`` / ``1.234,56€`` into a float and an ISO currency code.

    Steam formats the same amount as ``1,234.56`` or ``1.234,56`` depending on
    the store region, and sometimes uses a space as the thousands separator.
    When both separators are present the rightmost one is the decimal point;
    with a single separator it is decimal only if 1-2 digits follow it.
    """
    text = text.strip()
    if not text:
        return None, ""
    currency = ""
    for symbol, code in _CURRENCY_SYMBOLS:
        if symbol in text:
            currency = code
            break
    if not currency:
        for token in re.findall(r"[A-Z]{3}", text):
            if token in _CURRENCY_CODES:
                currency = token
                break
    match = _NUMBER_RE.search(text)
    if match is None:
        return None, currency
    number = re.sub(r"\s", "", match.group(0))
    # Steam writes ``19,99.-`` and ``4,99.``; the trailing dot is decoration and
    # must not be read as a decimal point (that would make 1999 and 499).
    number = number.rstrip(".,")
    decimal = _decimal_separator(number)
    if decimal is not None:
        whole, _, fraction = number.rpartition(decimal)
        number = f"{whole.replace('.', '').replace(',', '')}.{fraction}"
    else:
        number = number.replace(".", "").replace(",", "")
    try:
        return float(number), currency
    except ValueError:
        return None, currency


def _decimal_separator(number: str) -> str | None:
    """Which of `.` and `,` splits the integer part from the fraction, if any."""
    has_dot = "." in number
    has_comma = "," in number
    if has_dot and has_comma:
        return "." if number.rindex(".") > number.rindex(",") else ","
    separator = "." if has_dot else ("," if has_comma else None)
    if separator is None:
        return None
    # ``4,99`` is a decimal comma; ``1,234`` is a thousands comma.
    tail = number.rpartition(separator)[2]
    return separator if len(tail) in (1, 2) else None


def _parse_discount(node: Tag | None) -> int:
    match = re.search(r"(\d+)", _text(node))
    return int(match.group(1)) if match else 0


def _parse_review(row: Tag) -> tuple[str, int | None, int | None]:
    summary_node = row.select_one(".search_review_summary")
    if summary_node is None:
        return "", None, None
    tooltip = summary_node.get("data-tooltip-html") or ""
    summary = tooltip.split("<br>")[0].strip()
    match = _REVIEW_RE.search(tooltip)
    if match is None:
        return summary, None, None
    return summary, int(match.group(1)), int(match.group(2).replace(",", ""))


def _parse_platforms(row: Tag) -> str:
    names = []
    for node in row.select(".search_platforms .platform_img"):
        for css_class in node.get("class", []):
            if css_class in _PLATFORMS and _PLATFORMS[css_class] not in names:
                names.append(_PLATFORMS[css_class])
    return ", ".join(names)


def _parse_row(row: Tag) -> dict | None:
    app_id = (row.get("data-ds-appid") or "").split(",")[0].strip()
    discount = _parse_discount(row.select_one(".discount_pct"))
    if not app_id or discount <= 0:
        return None

    href = row.get("href") or ""
    parts = urlsplit(href)
    url = urlunsplit((parts.scheme, parts.netloc, f"/app/{app_id}/", "", ""))

    price_original, currency = _parse_price(_text(row.select_one(".discount_original_price")))
    price_final, final_currency = _parse_price(_text(row.select_one(".discount_final_price")))
    if price_final is None:
        cents = row.select_one("[data-price-final]")
        if cents is not None and (cents.get("data-price-final") or "").isdigit():
            price_final = int(cents["data-price-final"]) / 100
    currency = currency or final_currency

    summary, percent, count = _parse_review(row)
    image = row.select_one("img")
    return {
        # Per the parser contract every row carries the dedup key; the Steam
        # store's identity for a game is its app id.
        "external_key": app_id,
        "app_id": app_id,
        "title": _text(row.select_one(".title")),
        "url": url,
        "discount_percent": discount,
        "price_original": price_original,
        "price_final": price_final,
        "currency": currency,
        "review_summary": summary,
        "review_percent": percent,
        "review_count": count,
        "release_date": _text(row.select_one(".search_released")),
        "platforms": _parse_platforms(row),
        "image_url": (image.get("src") or "") if image is not None else "",
    }


def parse_search_page(html: str, settings: SteamSettings) -> list[dict]:
    """Parse one search results page into rows; pure, no browser or network access."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for node in soup.select("a.search_result_row"):
        row = _parse_row(node)
        if row is None:
            continue
        if row["discount_percent"] < settings.min_discount_percent:
            continue
        if settings.max_price > 0 and (
            row["price_final"] is None or row["price_final"] > settings.max_price
        ):
            continue
        rows.append(row)

    if settings.sort == "discount":
        rows.sort(key=lambda r: r["discount_percent"], reverse=True)
    elif settings.sort == "price":
        rows.sort(key=lambda r: (r["price_final"] is None, r["price_final"] or 0.0))
    return rows


class SteamDealsParser(BaseParser):
    """Collects discounted Steam games across a few search result pages."""

    key = "steam_deals"
    title = "Steam — скидки"
    description = "Скидки в магазине Steam: цены, размер скидки, отзывы и платформы."
    url = SEARCH_URL
    requires_login = False
    settings_model = SteamSettings
    columns: ClassVar[list[Column]] = [
        Column("app_id", "App ID", 12),
        Column("title", "Название", 40),
        Column("url", "Ссылка", 45),
        Column("discount_percent", "Скидка, %", 10),
        Column("price_original", "Цена без скидки", 14, "0.00"),
        Column("price_final", "Цена со скидкой", 14, "0.00"),
        Column("currency", "Валюта", 8),
        Column("review_summary", "Отзывы", 24),
        Column("review_percent", "Положительных, %", 12),
        Column("review_count", "Отзывов, шт", 12),
        Column("release_date", "Дата выхода", 14),
        Column("platforms", "Платформы", 24),
        Column("image_url", "Обложка", 45),
    ]

    async def run(self, ctx: ParseContext) -> ParseResult:
        settings: SteamSettings = ctx.settings  # type: ignore[assignment]
        rows: list[dict] = []
        pages = 0
        first_url = ""
        for page_index in range(settings.max_pages):
            url = build_search_url(settings, start=page_index * PAGE_SIZE)
            if not first_url:
                first_url = url
            ctx.log.info("Steam: loading page %s (%s)", page_index + 1, url)
            await ctx.page.goto(url, wait_until="domcontentloaded")
            html = await ctx.page.content()
            page_rows = parse_search_page(html, settings)
            if not page_rows:
                if page_index == 0:
                    ctx.save_artifact("empty.html", html.encode())
                    raise ParseError(
                        kind=ErrorKind.blocked,
                        message="Steam returned no results (captcha or region block?)",
                    )
                break
            rows.extend(page_rows)
            pages += 1
        return ParseResult(rows=rows, meta={"pages": pages, "url": first_url})
