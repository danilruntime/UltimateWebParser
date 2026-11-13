"""Opt-in live test: the real Steam search page still yields deals.

Run with ``UWP_LIVE=1 pytest -m live``. It is skipped by default so the normal
suite never needs the internet.
"""
import os
import urllib.request

import pytest

from uwp.parsers.steam_deals.parser import (
    SteamSettings,
    build_search_url,
    parse_search_page,
)

pytestmark = pytest.mark.live

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)


@pytest.mark.skipif(not os.environ.get("UWP_LIVE"), reason="set UWP_LIVE=1 to hit Steam")
def test_real_steam_page_has_deals():
    settings = SteamSettings(max_pages=1)
    request = urllib.request.Request(
        build_search_url(settings, 0), headers={"User-Agent": USER_AGENT}
    )

    html = urllib.request.urlopen(request, timeout=30).read().decode("utf-8", "ignore")
    rows = parse_search_page(html, settings)

    assert len(rows) >= 10
    discounted = [row for row in rows if row.get("discount_percent", 0) > 0]
    assert discounted, "the deals page returned no discounted rows"
    assert all(row.get("title") for row in rows)
