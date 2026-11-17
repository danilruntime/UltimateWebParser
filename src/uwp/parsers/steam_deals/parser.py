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
# Ordered longest-symbol-first so ``CDN$`` wins over ``$`` and ``R$`` over ``R``.
# Captured from live store pages in 48 regions; the region cannot be used as the
# key because the store renders whatever wallet the session holds (``cz`` served
# ``€``, ``tr`` served ``$``), so the symbol on the page is the only honest source.
_CURRENCY_SYMBOLS = (
    ("CDN$", "CAD"),
    ("Mex$", "MXN"),
    ("COL$", "COP"),
    ("CLP$", "CLP"),
    ("HK$", "HKD"),
    ("NT$", "TWD"),
    ("NZ$", "NZD"),
    ("A$", "AUD"),
    ("C$", "CAD"),
    ("S$", "SGD"),
    ("R$", "BRL"),
    ("Rp", "IDR"),
    ("RM", "MYR"),
    ("S/.", "PEN"),
    ("CHF", "CHF"),
    ("AED", "AED"),
    ("SR", "SAR"),
    ("руб", "RUB"),
    ("zł", "PLN"),
    ("kr", "NOK"),
    ("$", "USD"),
    ("€", "EUR"),
    ("£", "GBP"),
    ("₽", "RUB"),
    ("¥", "JPY"),
    ("₴", "UAH"),
    ("₸", "KZT"),
    ("₪", "ILS"),
    ("₱", "PHP"),
    ("฿", "THB"),
    ("₫", "VND"),
    ("₹", "INR"),
    # ``R`` last: it is also a prefix of ``R$`` and a suffix of nothing else.
    ("R", "ZAR"),
)
_CURRENCY_CODES = {
    "USD", "EUR", "GBP", "RUB", "UAH", "JPY", "KZT", "BRL", "CAD", "TRY", "PLN",
    "AUD", "CHF", "INR", "IDR", "MYR", "NOK", "PHP", "THB", "VND", "TWD", "HKD",
    "SGD", "NZD", "MXN", "CLP", "COP", "PEN", "ZAR", "SAR", "AED", "ILS", "CNY",
    "SEK", "DKK",
}
# A few glyphs denote different currencies in different stores, so the symbol
# alone cannot decide. The region is consulted *only* for these.
_AMBIGUOUS_SYMBOLS: dict[str, dict[str, str]] = {
    "¥": {"jp": "JPY", "cn": "CNY", "tw": "TWD"},
    "kr": {"no": "NOK", "se": "SEK", "dk": "DKK"},
}
_AMBIGUOUS_DEFAULT = {"¥": "JPY", "kr": "NOK"}

# Structural split, not a sentence: the store localises the review line, and an
# English-only regex left both fields ``None`` on every non-English page.
_REVIEW_BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_PERCENT_RE = re.compile(r"(\d[\d\s.,]*)\s*%")
_REVIEW_NUMBER_RE = re.compile(r"\d[\d\s.,]*\d|\d")
_NUMBER_RE = re.compile(r"-?\d[\d,.\s]*")


class SteamSettings(BaseModel):
    """User-facing settings for the Steam deals parser."""

    region: str = Field(
        "us",
        description="Регион магазина Steam (код страны: us, ru, de, …)",
        json_schema_extra={"title_key": "parser.steam_deals.field.region"},
    )
    language: str = Field(
        "english",
        description="Язык страницы магазина (english, russian, …)",
        json_schema_extra={"title_key": "parser.steam_deals.field.language"},
    )
    filter: Literal["topsellers", "newreleases", "specials"] = Field(
        "topsellers",
        description="Подборка Steam: topsellers, newreleases или specials",
        json_schema_extra={"title_key": "parser.steam_deals.field.filter"},
    )
    max_pages: int = Field(
        2,
        ge=1,
        le=10,
        description="Сколько страниц результатов просматривать (1–10)",
        json_schema_extra={"title_key": "parser.steam_deals.field.max_pages"},
    )
    max_price: float = Field(
        0,
        ge=0,
        description="Максимальная итоговая цена; 0 — без ограничения",
        json_schema_extra={"title_key": "parser.steam_deals.field.max_price"},
    )
    min_discount_percent: int = Field(
        0,
        ge=0,
        le=100,
        description="Минимальная скидка в процентах (0 — любая)",
        json_schema_extra={
            "title_key": "parser.steam_deals.field.min_discount_percent"
        },
    )
    sort: Literal["relevance", "discount", "price"] = Field(
        "relevance",
        description="Сортировка: relevance — как в Steam, discount — по скидке, price — по цене",
        json_schema_extra={"title_key": "parser.steam_deals.field.sort"},
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


def _parse_price(text: str, region: str | None = None) -> tuple[float | None, str]:
    """Parse ``$19.99`` / ``4,99€`` / ``1.234,56€`` into a float and an ISO currency code.

    Steam formats the same amount as ``1,234.56`` or ``1.234,56`` depending on
    the store region, and sometimes uses a space as the thousands separator.
    When both separators are present the rightmost one is the decimal point;
    with a single separator it is decimal only if 1-2 digits follow it.

    ``region`` is consulted only for glyphs that mean different currencies in
    different stores (``¥`` is JPY in Japan and CNY in China); every other
    symbol decides on its own, because the store renders whatever wallet the
    session holds regardless of ``cc``.
    """
    text = text.strip()
    if not text:
        return None, ""
    currency = ""
    # Only a prefix or a suffix counts: a symbol buried inside the digits would
    # be a decoding artefact, not a currency marker.
    head = text.lstrip("0123456789 -")
    tail = text.rstrip("0123456789 .-")
    for symbol, code in _CURRENCY_SYMBOLS:
        if not (head.startswith(symbol) or tail.endswith(symbol)):
            continue
        ambiguous = _AMBIGUOUS_SYMBOLS.get(symbol)
        if ambiguous is not None:
            currency = ambiguous.get(
                (region or "").lower(), _AMBIGUOUS_DEFAULT[symbol]
            )
        else:
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
    """Split the tooltip structurally: the store localises the sentence.

    ``97% of the 147,599 user reviews``, ``96% из 139,634 обзоров``,
    ``97 % der 29,219 Nutzerrezensionen`` and ``52,966 篇用户评测中有 96%`` all
    have the same shape: a summary, then exactly one percentage and one count,
    in either order. The percentage is whichever number carries ``%``; the
    count is the largest remaining number.
    """
    summary_node = row.select_one(".search_review_summary")
    if summary_node is None:
        return "", None, None
    tooltip = summary_node.get("data-tooltip-html") or ""
    parts = _REVIEW_BREAK_RE.split(tooltip)
    summary = parts[0].strip()
    # The figures live in the second segment; later segments are prose notes
    # ("review bombing excluded", a year, and so on) and their digits must not
    # be mistaken for the count.
    rest = _TAG_RE.sub(" ", parts[1] if len(parts) > 1 else "")
    percent = None
    consumed: list[int] = []
    match = _PERCENT_RE.search(rest)
    if match is not None:
        digits = re.sub(r"[,.\s]", "", match.group(1))
        if digits.isdigit() and 0 <= int(digits) <= 100:
            percent = int(digits)
            consumed = list(range(match.start(), match.end()))
    counts = []
    for number in _REVIEW_NUMBER_RE.finditer(rest):
        if number.start() in consumed:
            continue
        # Thousands separators only: ``139,634`` / ``29.219`` / ``1 234``.
        digits = re.sub(r"[,.]", "", re.sub(r"\s", "", number.group(0)))
        if digits.isdigit():
            counts.append(int(digits))
    count = max(counts) if counts else None
    return summary, percent, count


def _parse_platforms(row: Tag) -> str:
    names = []
    for node in row.select(".search_platforms .platform_img"):
        for css_class in node.get("class", []):
            if css_class in _PLATFORMS and _PLATFORMS[css_class] not in names:
                names.append(_PLATFORMS[css_class])
    return ", ".join(names)


def _parse_row(row: Tag, region: str | None = None) -> dict | None:
    app_id = (row.get("data-ds-appid") or "").split(",")[0].strip()
    discount = _parse_discount(row.select_one(".discount_pct"))
    if not app_id or discount <= 0:
        return None

    href = row.get("href") or ""
    parts = urlsplit(href)
    url = urlunsplit((parts.scheme, parts.netloc, f"/app/{app_id}/", "", ""))

    price_original, currency = _parse_price(
        _text(row.select_one(".discount_original_price")), region=region
    )
    price_final, final_currency = _parse_price(
        _text(row.select_one(".discount_final_price")), region=region
    )
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
        row = _parse_row(node, region=settings.region)
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
    title_key = "parser.steam_deals.title"
    description_key = "parser.steam_deals.description"
    url = SEARCH_URL
    requires_login = False
    settings_model = SteamSettings
    # ``title`` stays the Russian fallback; ``title_key`` is what a screen or a
    # report renders in the deployment's language.
    columns: ClassVar[list[Column]] = [
        Column("app_id", "App ID", 12, title_key="parser.steam_deals.column.app_id"),
        Column("title", "Название", 40, title_key="parser.steam_deals.column.title"),
        Column("url", "Ссылка", 45, title_key="parser.steam_deals.column.url"),
        Column(
            "discount_percent",
            "Скидка, %",
            10,
            title_key="parser.steam_deals.column.discount_percent",
        ),
        Column(
            "price_original",
            "Цена без скидки",
            14,
            "0.00",
            title_key="parser.steam_deals.column.price_original",
        ),
        Column(
            "price_final",
            "Цена со скидкой",
            14,
            "0.00",
            title_key="parser.steam_deals.column.price_final",
        ),
        Column("currency", "Валюта", 8, title_key="parser.steam_deals.column.currency"),
        Column(
            "review_summary",
            "Отзывы",
            24,
            title_key="parser.steam_deals.column.review_summary",
        ),
        Column(
            "review_percent",
            "Положительных, %",
            12,
            volatile=True,
            title_key="parser.steam_deals.column.review_percent",
        ),
        Column(
            "review_count",
            "Отзывов, шт",
            12,
            volatile=True,
            title_key="parser.steam_deals.column.review_count",
        ),
        Column(
            "release_date",
            "Дата выхода",
            14,
            title_key="parser.steam_deals.column.release_date",
        ),
        Column(
            "platforms",
            "Платформы",
            24,
            title_key="parser.steam_deals.column.platforms",
        ),
        Column(
            "image_url",
            "Обложка",
            45,
            title_key="parser.steam_deals.column.image_url",
        ),
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
