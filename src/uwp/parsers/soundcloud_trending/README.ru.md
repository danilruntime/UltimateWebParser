# Парсер трендов SoundCloud (`soundcloud_trending`)

**English version:** [`README.md`](README.md).

Собирает один плейлист «в тренде по жанрам» со страницы
`https://soundcloud.com/discover/sets/trending-by-genre:<genre>`. Один прогон —
один жанр, не больше 50 треков. Работает на чистом профиле:
`requires_login = False`.

## Как устроен

- `auth_url` указывает на `https://soundcloud.com/signin`: вход через VNC
  открывает форму, а `url` остаётся `https://soundcloud.com/discover` — тем
  разделом, который называет отчёт.
- `build_playlist_url(settings)` подставляет жанр в
  `https://soundcloud.com/discover/sets/trending-by-genre:{genre}`.
- `run(ctx)` подписывается на `page.on("response", …)` **до** `goto`, затем
  загружает страницу с `wait_until="domcontentloaded"` и читает
  `page.content()`. Обработчик складывает объекты треков из запроса самой
  страницы к `api-v2.soundcloud.com/tracks?ids=…`; подписка обязана появиться до
  навигации, иначе ответ, пришедший рано, потерян.
- `parse_hydration(html)` — чистая функция: читает серверный блок
  `window.__sc_hydration`, берёт название и упорядоченные id из
  `systemPlaylist`, а `client_id` — из `apiClient`. Сама страница рисует лишь
  десять строк, но блок есть в HTML, который получает `curl`, поэтому ни скролл,
  ни `page.evaluate` для чтения не нужны.
- Строки собираются в порядке id из hydration (`track_rows`). Ответ `/tracks`
  приходит в другом порядке; следование ему перетасовало бы отчёт, а
  `export_mode=changes` счёл бы перетасовку изменением.
- Если покрыты не все id, парсер ждёт до 10 с короткими шагами, а затем один раз
  запрашивает тот же эндпоинт через `page.evaluate(fetch(…))` с `client_id` из
  hydration. Отсутствие `client_id` — не ошибка: прогон теряет только те треки,
  которых не было в перехвате.
- Совсем ничего (нет блока плейлиста или ни одного объекта трека ни одним путём)
  — это `ParseError(kind=blocked, message="SoundCloud returned no tracks (geo
  block, captcha or API change?)")`, страница сохраняется артефактом
  `empty.html`. Частичный ответ — `ParseError(kind=blocked, partial_rows=rows,
  message="SoundCloud returned N of M tracks")`: runner сохраняет эти строки с
  `complete=False`, поэтому недостающие треки не считаются пропавшими.
- `external_key` — id трека строкой.

## Настройки

| Поле | Тип | По умолчанию | Смысл |
| --- | --- | --- | --- |
| `genre` | `all-genres` / `pop` / `hip-hop` / `electronic` / `trap` | `all-genres` | Какую страницу трендов открыть |

Значения — те слаги, на которые ссылается `/discover`. `hip-hop-rap` даёт 404, а
`hip-hop` — 200, поэтому выдуманный слаг не принимается. Бот листает значение
одной кнопкой — из-за этого поле `Literal`, а не свободный текст.

## Колонки

| Ключ | Заголовок | Источник | `volatile` |
| --- | --- | --- | --- |
| `title` | Название | `title` | нет |
| `artist` | Исполнитель | `user.username` | нет |
| `url` | Ссылка | `permalink_url` | нет |
| `artist_url` | Исполнитель: ссылка | `user.permalink_url` | нет |
| `genre` | Жанр | `genre` | нет |
| `duration` | Длительность | `duration` (мс → `м:сс`) | нет |
| `published` | Дата публикации | `created_at` (день UTC) | нет |
| `playback_count` | Прослушивания | `playback_count` | **да** |
| `likes_count` | Лайки | `likes_count` | **да** |
| `reposts_count` | Репосты | `reposts_count` | **да** |
| `comment_count` | Комментарии | `comment_count` | **да** |

Пустое значение остаётся пустым, а отсутствующий счётчик — `None`, не `0`:
«нет данных» и «ноль прослушиваний» — разные факты, и счётчик, начавшийся с нуля,
выглядел бы изменением в момент, когда появилось настоящее число.

## Почему нет запасного DOM-пути

В отрисованных строках числа округлены (`1.25M`, `411K`), даты публикации нет, а
автор иногда отличается от API. Строки из DOM попали бы в базу и сравнивались бы
с точными значениями следующего прогона: прогон, ушедший в запасной путь,
затёр бы хорошие данные, а когда API вернулся бы, один раз соврал бы «изменилось».
`items.upsert` перезаписывает payload даже при частичном прогоне, поэтому
«приблизительно» — не безопасное состояние. Прогон, который не смог получить
JSON, падает `blocked`.

## Смена жанра

`external_key` — id трека, поэтому смена `genre` у существующего сайта заменяет
весь набор строк: старые треки становятся пропавшими, новые приходят как новые.
Это ожидаемо. Чтобы смотреть два жанра рядом, добавьте два сайта, по одному на
жанр.

## Тесты

`.venv/bin/python -m pytest tests/unit/test_soundcloud_parser.py -v`

Чтобы обновить фикстуры, сначала снимите страницу:

```bash
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
curl -A "$UA" "https://soundcloud.com/discover/sets/trending-by-genre:all-genres" \
  -o /tmp/soundcloud.html
```

Остальная часть обновления (урезать блок hydration и забрать пять id) — это
Python-скрипт из задачи 1
[`../../../../docs/superpowers/plans/2026-11-22-soundcloud-trending-parser.md`](../../../../docs/superpowers/plans/2026-11-22-soundcloud-trending-parser.md).
Фикстура — источник истины для юнит-тестов: если SoundCloud сменит разметку или
плейлист изменится, обновляйте ожидания вместе с ней.
