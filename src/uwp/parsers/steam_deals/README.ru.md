# Парсер скидок Steam (`steam_deals`)

**English version:** [`README.md`](README.md).

Собирает игры со скидкой со страницы поиска магазина Steam
(`https://store.steampowered.com/search/?specials=1`). Работает на чистом
профиле: `requires_login = False`.

## Как устроен

- `build_search_url(settings, start)` собирает URL одной страницы результатов
  (`filter`, `specials=1`, `cc=<region>`, `l=<language>`, `start=`).
- `run(ctx)` идёт по страницам `start = 0, 50, 100, …` до `max_pages`, загружая
  каждую через `page.goto(url, wait_until="domcontentloaded")` и
  `page.content()`. Останавливается досрочно, если страница не дала строк.
- `parse_search_page(html, settings)` — чистая функция (без браузера и сети):
  разбирает карточки `a.search_result_row`, пропускает строки без скидки,
  применяет фильтры по скидке и цене и сортирует. Юнит-тесты гоняют её на
  `tests/fixtures/steam_search.html` — реальной странице, снятой `curl`.
- Если первая страница не дала ни одной строки (капча, блок региона, смена
  разметки), сырой HTML сохраняется артефактом `empty.html`, а прогон падает с
  `ParseError(kind=blocked, message="Steam returned no results (captcha or region block?)")`.
- `external_key` для дедупликации — id приложения в Steam (`app_id`).

## Настройки (`SteamSettings`)

| Поле | Тип | По умолчанию | Смысл |
| --- | --- | --- | --- |
| `region` | str | `us` | Код страны магазина (`us`, `ru`, `de`, …) → `cc=` |
| `language` | str | `english` | Язык страницы магазина (`english`, `russian`, …) → `l=` |
| `filter` | `topsellers` / `newreleases` / `specials` | `topsellers` | Фильтр поиска Steam |
| `max_pages` | int 1–10 | `2` | Сколько страниц результатов листать (по 50 игр) |
| `max_price` | float ≥ 0 | `0` | Максимальная итоговая цена; `0` = без ограничения |
| `min_discount_percent` | int 0–100 | `0` | Минимальная скидка в процентах |
| `sort` | `relevance` / `discount` / `price` | `relevance` | `relevance` сохраняет порядок Steam, `discount` сортирует по убыванию скидки, `price` — по возрастанию итоговой цены |

## Колонки

| Ключ | Заголовок | Примечания |
| --- | --- | --- |
| `app_id` | App ID | Первый id из `data-ds-appid` (до запятой) |
| `title` | Название | `.title` |
| `url` | Ссылка | Канонический `https://store.steampowered.com/app/<app_id>/` (query отброшен) |
| `discount_percent` | Скидка, % | `-75%` → `75` |
| `price_original` | Цена без скидки | Float, разобранный из `$19.99` / `4,99€` |
| `price_final` | Цена со скидкой | Float; при отсутствии берётся `data-price-final` в центах |
| `currency` | Валюта | ISO-код по символу цены (`$` → USD, `€` → EUR, …) |
| `review_summary` | Отзывы | Первая строка `data-tooltip-html` (например, `Overwhelmingly Positive`); пусто, если отзывов нет |
| `review_percent` | Положительных, % | `volatile`; из `97% of the 147,599 user reviews`; `None`, если нет |
| `review_count` | Отзывов, шт | `volatile`; из того же тултипа; `None`, если нет |
| `release_date` | Дата выхода | `.search_released` как его показывает Steam |
| `platforms` | Платформы | `win mac linux` → `Windows, macOS, Linux` |
| `image_url` | Обложка | `img src` капсулы |

`volatile`-колонки попадают в отчёт, но исключены из подсчёта изменений:
счётчики отзывов в Steam двигаются каждые несколько минут, и если считать их
изменениями, режим `export_mode=changes` присылал файл при каждом прогоне
(измерено: 43 отчёта из 63 не имели новых строк и отличались только этими двумя
колонками). `review_summary` намеренно остаётся отслеживаемой — «Very Positive»
→ «Mixed» это новость.

Заголовки в таблице выше — то, что каталог (`uwp.i18n.parsers`, ключи
`parser.steam_deals.*`) отдаёт по-русски; в английском отчёте те же колонки
называются `Title`, `Link` и так далее. Парсер без ключей работает как есть —
его `title`, `description` и `Column.title` показываются как написаны, — а
парсер, которому нужны оба языка, указывает ключи на каталог:

`Column("title", "Название", 40, title_key="parser.steam_deals.column.title")`

Литерал остаётся русским, потому что это запасной вариант автора (`Column.title`,
`src/uwp/parsers/base.py:32-39`): он показывается, когда `title_key` не
разрешился, а парсер писался для русского оператора.

## Тесты

`.venv/bin/python -m pytest tests/unit/test_steam_parser.py -v`

Обновить фикстуру:

```bash
curl -A "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36" \
  "https://store.steampowered.com/search/?filter=topsellers&specials=1&cc=us&l=english" \
  -o tests/fixtures/steam_search.html
```

Фикстура — источник истины для юнит-тестов: если Steam меняет разметку или
меняются сами скидки, обновляйте ожидаемые значения вместе с ней.
