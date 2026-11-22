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


@pytest.mark.skipif(
    not os.environ.get("UWP_LIVE"), reason="set UWP_LIVE=1 to hit SoundCloud"
)
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
