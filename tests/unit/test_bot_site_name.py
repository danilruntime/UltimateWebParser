"""The bot asks for a site name when adding one and can rename a site."""

from uwp.bot import keyboards, texts
from uwp.i18n import Lang


def test_the_add_prompt_offers_the_parser_name_and_a_way_back():
    kb = keyboards.site_name_prompt_kb("steam_deals", "Steam — deals", Lang.en)

    flat = [b for row in kb.inline_keyboard for b in row]
    assert [b.callback_data for b in flat] == [
        "site:adddefault:steam_deals",
        "sites:list",
    ]
    assert "Steam — deals" in flat[0].text
    assert "Cancel" in flat[1].text


def test_the_add_prompt_is_translated():
    assert "назвать" in texts.ask_site_name(Lang.ru, "Steam — скидки").lower()
    assert "called" in texts.ask_site_name(Lang.en, "Steam — deals").lower()
    assert "название" in texts.site_name_saved(Lang.ru, "Моё").lower()
    assert "name" in texts.site_name_saved(Lang.en, "Mine").lower()
