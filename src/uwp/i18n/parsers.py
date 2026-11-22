"""Strings a parser shows a human: its own name, column headers, field labels.

A parser is a plugin and may live outside this repository. Keys are therefore
namespaced by the parser's stable ``key`` (``parser.steam_deals.*``), never by a
module path; a parser that declares no keys keeps its own ``title``,
``Column.title`` and ``Field.description`` (see :mod:`uwp.parsers.base`), so an
installed third-party parser keeps producing readable screens and reports.
"""

from uwp.i18n import Lang

CATALOG: dict[Lang, dict[str, str]] = {
    Lang.ru: {
        "parser.steam_deals.title": "Steam — скидки",
        "parser.steam_deals.description": (
            "Скидки в магазине Steam: цены, размер скидки, отзывы и платформы."
        ),
        "parser.steam_deals.column.app_id": "App ID",
        "parser.steam_deals.column.title": "Название",
        "parser.steam_deals.column.url": "Ссылка",
        "parser.steam_deals.column.discount_percent": "Скидка, %",
        "parser.steam_deals.column.price_original": "Цена без скидки",
        "parser.steam_deals.column.price_final": "Цена со скидкой",
        "parser.steam_deals.column.currency": "Валюта",
        "parser.steam_deals.column.review_summary": "Отзывы",
        "parser.steam_deals.column.review_percent": "Положительных, %",
        "parser.steam_deals.column.review_count": "Отзывов, шт",
        "parser.steam_deals.column.release_date": "Дата выхода",
        "parser.steam_deals.column.platforms": "Платформы",
        "parser.steam_deals.column.image_url": "Обложка",
        # Field labels are short: the settings keyboard puts ": value" right
        # after them, and a long description wraps a phone button onto two lines.
        "parser.steam_deals.field.region": "Регион магазина",
        "parser.steam_deals.field.language": "Язык страницы",
        "parser.steam_deals.field.filter": "Подборка",
        "parser.steam_deals.field.max_pages": "Страниц",
        "parser.steam_deals.field.max_price": "Макс. цена",
        "parser.steam_deals.field.min_discount_percent": "Мин. скидка, %",
        "parser.steam_deals.field.sort": "Сортировка",
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
    },
    Lang.en: {
        "parser.steam_deals.title": "Steam — deals",
        "parser.steam_deals.description": (
            "Discounted games in the Steam store: prices, discount size, "
            "reviews and platforms."
        ),
        "parser.steam_deals.column.app_id": "App ID",
        "parser.steam_deals.column.title": "Title",
        "parser.steam_deals.column.url": "Link",
        "parser.steam_deals.column.discount_percent": "Discount, %",
        "parser.steam_deals.column.price_original": "Original price",
        "parser.steam_deals.column.price_final": "Final price",
        "parser.steam_deals.column.currency": "Currency",
        "parser.steam_deals.column.review_summary": "Reviews",
        "parser.steam_deals.column.review_percent": "Positive, %",
        "parser.steam_deals.column.review_count": "Review count",
        "parser.steam_deals.column.release_date": "Release date",
        "parser.steam_deals.column.platforms": "Platforms",
        "parser.steam_deals.column.image_url": "Cover",
        "parser.steam_deals.field.region": "Store region",
        "parser.steam_deals.field.language": "Page language",
        "parser.steam_deals.field.filter": "Selection",
        "parser.steam_deals.field.max_pages": "Pages",
        "parser.steam_deals.field.max_price": "Max price",
        "parser.steam_deals.field.min_discount_percent": "Min discount, %",
        "parser.steam_deals.field.sort": "Sort",
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
    },
}
