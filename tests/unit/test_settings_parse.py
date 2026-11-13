"""Tests for the site settings screens: interval parsing and keyboards."""
import pytest

from uwp.bot.handlers.settings import (
    apply_param,
    next_literal,
    params_kb,
    parse_interval,
    settings_kb,
)
from uwp.domain import ExportMode, Site
from uwp.parsers.steam_deals.parser import SteamDealsParser


def test_parse_range():
    assert parse_interval("45-90") == (45, 90)
    assert parse_interval("45 90") == (45, 90)
    assert parse_interval("60") == (60, 60)
    assert parse_interval(" 45 - 90 ") == (45, 90)


@pytest.mark.parametrize("bad", ["90-60", "0-10", "-5", "abc", "1-20000", "", "10-"])
def test_reject_bad(bad):
    with pytest.raises(ValueError):
        parse_interval(bad)


def _site(**kw):
    return Site(
        id=3,
        slug="s",
        name="S",
        parser_key="steam_deals",
        interval_min_min=45,
        interval_max_min=90,
        **kw,
    )


def test_settings_kb_has_every_target():
    flat = [
        button.callback_data
        for row in settings_kb(_site()).inline_keyboard
        for button in row
    ]

    assert flat == [
        "set:interval:3",
        "set:profile:3",
        "set:proxy:3",
        "set:export:3",
        "set:params:3",
        "set:back:3",
    ]


def test_params_kb_shows_current_values_and_field_callbacks():
    parser = SteamDealsParser()
    rows = params_kb(parser, _site()).inline_keyboard
    buttons = [button for row in rows for button in row]
    callbacks = [button.callback_data for button in buttons if button.callback_data]

    assert "param:3:filter" in callbacks
    assert "param:3:max_pages" in callbacks
    # The current value is visible on the button, not only after a tap.
    filter_button = next(b for b in buttons if b.callback_data == "param:3:filter")
    assert "topsellers" in filter_button.text
    assert any(b.text.startswith("⬅️") for b in buttons)


def test_next_literal_cycles_values():
    from uwp.parsers.steam_deals.parser import SteamSettings

    assert next_literal(SteamSettings.model_fields["filter"], "topsellers") == "newreleases"
    assert next_literal(SteamSettings.model_fields["filter"], "specials") == "topsellers"


def test_apply_param_validates_and_merges():
    parser = SteamDealsParser()
    site = _site(parser_settings={"filter": "specials", "max_pages": 2})

    merged = apply_param(parser, site, "max_pages", "4")

    assert merged["max_pages"] == 4
    assert merged["filter"] == "specials"


def test_apply_param_rejects_out_of_range():
    parser = SteamDealsParser()
    site = _site(parser_settings={})

    with pytest.raises(ValueError):
        apply_param(parser, site, "max_pages", "99")


def test_export_cycles_always_changes_never():
    from uwp.bot.handlers.settings import next_export

    assert next_export(ExportMode.always) == ExportMode.changes
    assert next_export(ExportMode.changes) == ExportMode.never
    assert next_export(ExportMode.never) == ExportMode.always
