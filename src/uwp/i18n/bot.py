"""Every word the Telegram bot shows, in Russian and English.

The catalogue is flat and namespaced by area (``bot.sites.*``, ``bot.settings.*``,
...), so a handler reads the same key in both languages and a missing translation
is a ``KeyError`` at the call site instead of a Russian word in an English chat.

``tests/unit/test_i18n.py`` pins key and placeholder parity; the bot's own suite
pins that the English half holds no Cyrillic. Emoji are part of the value on
purpose: Telegram renders them next to the label, and a translator must see the
whole string to keep the leading glyph in place.
"""

from uwp.i18n import Lang

CATALOG: dict[Lang, dict[str, str]] = {
    Lang.ru: {
        # --- shared words -------------------------------------------------
        "bot.error.not_found": "{name} не найден",
        "bot.error.access_denied": "⛔ Доступ только для администратора.",
        "bot.error.site_not_found": "Сайт не найден",
        "bot.error.profile_not_found": "Профиль не найден",
        "bot.error.proxy_not_found": "Прокси не найден",
        "bot.error.parser_not_found": "Парсер не найден",
        "bot.error.param_not_found": "Параметр не найден",
        "bot.error.no_parsers": "Нет доступных парсеров",
        "bot.common.back": "⬅️ Назад",
        "bot.common.cancel": "⬅️ Отмена",
        "bot.common.delete": "🗑 Удалить",
        "bot.common.run_now": "▶️ Запустить сейчас",
        "bot.common.settings": "⚙️ Настройки",
        "bot.common.toggle_on": "🟢 Включить",
        "bot.common.toggle_off": "🔴 Выключить",
        "bot.common.add_site": "➕ Добавить сайт",
        "bot.common.retry": "🔁 Повторить",
        "bot.common.history": "📜 История",
        "bot.common.new_profile": "➕ Новый профиль",
        "bot.common.new_proxy": "➕ Новый прокси",
        "bot.common.no_proxy": "🚫 Без прокси",
        "bot.common.none": "нет",
        "bot.common.check": "🔌 Проверить",
        "bot.common.login": "🖥 Войти",
        "bot.common.login_done": "✅ Вход выполнен",
        "bot.common.yes": "да",
        "bot.common.no": "нет",
        "bot.common.param_fallback": "параметр",
        # --- main menu ----------------------------------------------------
        "bot.menu.title": "Главное меню:",
        "bot.menu.button.sites": "🌐 Сайты",
        "bot.menu.button.profiles": "👤 Профили",
        "bot.menu.button.proxies": "🌍 Прокси",
        "bot.menu.button.help": "❓ Помощь",
        "bot.menu.your_id": "Ваш Telegram ID: <code>{id}</code>",
        "bot.menu.help": (
            "Я парсер сайтов под управлением администратора.\n\n"
            "/sites — список сайтов\n"
            "/runs — последние прогоны\n"
            "/help — эта справка\n"
            "/language — язык интерфейса\n"
            "/id — ваш Telegram ID"
        ),
        "bot.menu.unknown_command": (
            "Не понял команду. /help — список команд."
        ),
        # --- site list and card -------------------------------------------
        "bot.sites.header": "🌐 Сайты",
        "bot.sites.added": "Сайт добавлен: {name}",
        "bot.sites.started": "▶️ Запуск поставлен в очередь.",
        "bot.sites.already_running": "⏳ Прогон уже идёт или стоит в очереди.",
        "bot.sites.enabled": "🟢 Сайт включён, первый прогон запустится сразу.",
        "bot.sites.disabled": "🔴 Сайт выключен, очередь очищена.",
        "bot.sites.deleted": "🗑 Сайт удалён.",
        "bot.sites.confirm_delete": (
            "Удалить сайт «{name}»? Очередь прогонов и история будут стёрты."
        ),
        "bot.sites.confirm_delete_yes": "🗑 Да, удалить",
        "bot.sites.empty": "Пока нет ни одного сайта. Нажмите «➕ Добавить сайт».",
        "bot.sites.next_line": "   следующий: {when}",
        "bot.sites.list_suffix": " — след. {when}",
        "bot.sites.choose_parser": "Выберите парсер:",
        "bot.sites.need_profile": (
            "Сначала выберите постоянный профиль в настройках сайта."
        ),
        "bot.sites.card.parser": "Парсер: <code>{parser}</code>",
        "bot.sites.card.state": "Состояние: {state}",
        "bot.sites.card.state_on": "🟢 включён",
        "bot.sites.card.state_off": "🔴 выключен",
        "bot.sites.card.interval": "Интервал: каждые {low}–{high} мин",
        "bot.sites.card.profile": "Профиль: {label}",
        "bot.sites.card.export": "Экспорт: {mode}",
        "bot.sites.card.next_run": "Следующий прогон: {when}",
        "bot.sites.card.last_run": "Последний прогон: {status}",
        "bot.sites.profile.persistent": "постоянный (id {id})",
        "bot.sites.profile.persistent_plain": "постоянный",
        "bot.sites.profile.clean": "чистый (случайный отпечаток)",
        # --- settings -----------------------------------------------------
        "bot.settings.title": "⚙️ <b>{name}</b>",
        "bot.settings.choose": "Выберите, что изменить:",
        "bot.settings.interval_button": "⏱ Интервал",
        "bot.settings.profile_button": "👤 Профиль",
        "bot.settings.proxy_button": "🌍 Прокси",
        "bot.settings.export_button": "📄 Экспорт",
        "bot.settings.params_button": "🔧 Параметры парсера",
        "bot.settings.ask_interval": (
            "Введите интервал в минутах: одно число (например 60) "
            "или диапазон (например 45-90)."
        ),
        "bot.settings.interval_saved": (
            "✅ Интервал сайта «{name}»: каждые {low}–{high} мин."
        ),
        "bot.settings.choose_profile": "Выберите профиль:",
        "bot.settings.profile_set": "✅ Профиль: {label}",
        "bot.settings.choose_proxy": "Выберите прокси:",
        "bot.settings.proxy_set": "✅ Прокси: {name}",
        "bot.settings.params_header": (
            "🔧 Параметры парсера. Нажмите, чтобы изменить:"
        ),
        "bot.settings.ask_param": "Введите новое значение параметра «{title}»:",
        "bot.settings.param_saved": "✅ Параметр «{name}» сохранён.",
        "bot.settings.export_set": "✅ Экспорт: {mode}",
        "bot.settings.interval_number": (
            "интервал должен быть числом или диапазоном, например 45-90"
        ),
        "bot.settings.interval_positive": "интервал должен быть больше нуля",
        "bot.settings.interval_order": "минимум не может быть больше максимума",
        "bot.settings.interval_max": (
            "максимум — {max} минут (одна неделя)"
        ),
        "bot.settings.param_not_enum": "поле не является перечислением",
        "bot.settings.param_unknown": "неизвестный параметр: {name}",
        "bot.settings.param_invalid": (
            "недопустимое значение «{raw}» для {location}{detail}"
        ),
        "bot.settings.param_allowed": "допустимые значения: {values}",
        "bot.settings.param_int": "введите целое число",
        "bot.settings.param_float": "введите число",
        "bot.settings.bound_ge": "не меньше {value}",
        "bot.settings.bound_gt": "больше {value}",
        "bot.settings.bound_le": "не больше {value}",
        "bot.settings.bound_lt": "меньше {value}",
        "bot.settings.need_login_profile": (
            "Парсеру нужен вход: создайте постоянный профиль в разделе "
            "«Профили»."
        ),
        "bot.settings.clean_profile_button": "🧼 Чистый профиль",
        # --- profiles -----------------------------------------------------
        "bot.profiles.header": "👤 Профили для входа",
        "bot.profiles.empty": (
            "Пока нет постоянных профилей. Нажмите «➕ Новый профиль»."
        ),
        "bot.profiles.ask_name": "Введите имя нового профиля:",
        "bot.profiles.created": "✅ Профиль «{name}» создан.",
        "bot.profiles.deleted": "🗑 Профиль удалён.",
        "bot.profiles.done": "✅ Вход в профиль «{name}» завершён.",
        "bot.profiles.card.state": "Состояние: {status}",
        "bot.profiles.card.fingerprint": (
            "Отпечаток: {platform}, {brand} {version}"
        ),
        "bot.profiles.card.timezone": "Часовой пояс: {timezone}, язык: {locale}",
        "bot.profile.status.free": "свободен",
        "bot.profile.status.locked": "используется",
        "bot.profile.status.login": "ожидает входа",
        "bot.profile.status.expired": "истёк",
        # --- proxies ------------------------------------------------------
        "bot.proxies.header": "🌍 Прокси",
        "bot.proxies.empty": "Пока нет прокси. Нажмите «➕ Новый прокси».",
        "bot.proxies.ask": (
            "Отправьте прокси в формате «имя http://user:pass@host:port» "
            "(имя необязательно)."
        ),
        "bot.proxies.added": "✅ Прокси добавлен.",
        "bot.proxies.deleted": "🗑 Прокси удалён.",
        "bot.proxies.check_ok": "✅ {text}",
        "bot.proxies.check_failed": "❌ {text}",
        "bot.proxies.card.user": "пользователь {name}",
        "bot.proxies.card.country": "Страна: {country}",
        # --- run history --------------------------------------------------
        "bot.runs.header": "📜 Последние прогоны:",
        "bot.runs.empty": "Прогонов ещё не было.",
        "bot.runs.line": (
            "{mark} {when} — {name}: {status}, {rows} строк"
        ),
        "bot.runs.site": "сайт {id}",
        "bot.runs.history_line": (
            "{mark} {when} — {status}, {rows} строк, новых {new}"
        ),
        # --- VNC login ----------------------------------------------------
        "bot.vnc.session": (
            "🖥 <b>Вход на сайт</b>\n\n"
            "Ссылка: {url}\n"
            "Пароль: <code>{password}</code>\n"
            "Осталось: {remaining} мин.\n\n"
            "Откройте ссылку, войдите на сайт и нажмите «Готово»."
        ),
        "bot.vnc.login_button": "🖥 Войти в профиль",
        "bot.vnc.done_button": "✅ Готово",
        "bot.vnc.extend_button": "⏱ +30 мин",
        "bot.vnc.finished": "🖥 Сессия входа завершена.",
        "bot.vnc.extended": "⏱ Продлено, осталось {remaining} мин.",
        # --- interface language -------------------------------------------
        "bot.language.button": "🌐 Язык",
        "bot.language.screen": "🌐 <b>Язык интерфейса</b>\nВыберите язык.",
        "bot.language.ru": "🇷🇺 Русский",
        "bot.language.en": "🇬🇧 English",
        "bot.language.saved": "✅ Язык интерфейса: русский.",
        "bot.language.unknown": "Неизвестный язык.",
        "bot.language.unavailable": (
            "Не удалось сохранить язык: хранилище недоступно."
        ),
        "bot.language.save_failed": (
            "Не удалось сохранить язык: база не ответила. Попробуйте ещё раз."
        ),
        # --- notifications -------------------------------------------------
        "bot.delivery.caption_report": (
            "{head} {site}\nСтрок: {rows}, новых: {new}"
        ),
        "bot.delivery.caption_failed": "❌ {site}: {reason}",
        "bot.delivery.partial_head": "⚠️ Прогон частичный",
        "bot.delivery.report_head": "📊",
        "bot.delivery.report_missing": "⚠️ Отчёт не создан",
        "bot.delivery.report_missing_reason": "⚠️ Отчёт не создан: {reason}",
        "bot.delivery.site_placeholder": "сайт",
        "bot.delivery.unknown_error": "неизвестная ошибка",
        "bot.delivery.run_failed": "❌ Прогон не удался: {reason}",
        "bot.delivery.run_failed_site": "❌ Прогон «{site}» не удался: {reason}",
        "bot.delivery.site_problem": "⚠️ Проблема с сайтом: {text}",
        "bot.delivery.site_problem_repeated": "{failures} провала подряд",
        "bot.delivery.site_problem_named": (
            "⚠️ Проблема с сайтом «{site}»: {text}"
        ),
        "bot.delivery.vnc_ready": (
            "🖥 Вход на сайт готов\n"
            "Ссылка: {url}\n"
            "Пароль: {password}"
        ),
        "bot.delivery.vnc_stopped": "🖥 Сессия входа завершена.",
        "bot.delivery.vnc_failed": (
            "🖥 Не удалось запустить сессию входа: {error}"
        ),
        "bot.delivery.unknown_event": "Уведомление: {kind}",
    },
    Lang.en: {
        # --- shared words -------------------------------------------------
        "bot.error.not_found": "{name} not found",
        "bot.error.access_denied": "⛔ Administrators only.",
        "bot.error.site_not_found": "Site not found",
        "bot.error.profile_not_found": "Profile not found",
        "bot.error.proxy_not_found": "Proxy not found",
        "bot.error.parser_not_found": "Parser not found",
        "bot.error.param_not_found": "Setting not found",
        "bot.error.no_parsers": "No parsers available",
        "bot.common.back": "⬅️ Back",
        "bot.common.cancel": "⬅️ Cancel",
        "bot.common.delete": "🗑 Delete",
        "bot.common.run_now": "▶️ Run now",
        "bot.common.settings": "⚙️ Settings",
        "bot.common.toggle_on": "🟢 Enable",
        "bot.common.toggle_off": "🔴 Disable",
        "bot.common.add_site": "➕ Add site",
        "bot.common.retry": "🔁 Retry",
        "bot.common.history": "📜 History",
        "bot.common.new_profile": "➕ New profile",
        "bot.common.new_proxy": "➕ New proxy",
        "bot.common.no_proxy": "🚫 No proxy",
        "bot.common.none": "none",
        "bot.common.check": "🔌 Test",
        "bot.common.login": "🖥 Log in",
        "bot.common.login_done": "✅ Logged in",
        "bot.common.yes": "yes",
        "bot.common.no": "no",
        "bot.common.param_fallback": "setting",
        # --- main menu ----------------------------------------------------
        "bot.menu.title": "Main menu:",
        "bot.menu.button.sites": "🌐 Sites",
        "bot.menu.button.profiles": "👤 Profiles",
        "bot.menu.button.proxies": "🌍 Proxies",
        "bot.menu.button.help": "❓ Help",
        "bot.menu.your_id": "Your Telegram ID: <code>{id}</code>",
        "bot.menu.help": (
            "I am a site scraper run by an administrator.\n\n"
            "/sites — the site list\n"
            "/runs — recent runs\n"
            "/help — this help\n"
            "/language — interface language\n"
            "/id — your Telegram ID"
        ),
        "bot.menu.unknown_command": (
            "I did not understand that. /help lists the commands."
        ),
        # --- site list and card -------------------------------------------
        "bot.sites.header": "🌐 Sites",
        "bot.sites.added": "Site added: {name}",
        "bot.sites.started": "▶️ The run is queued.",
        "bot.sites.already_running": "⏳ A run is already running or queued.",
        "bot.sites.enabled": "🟢 The site is on; the first run starts at once.",
        "bot.sites.disabled": "🔴 The site is off and its queue is cleared.",
        "bot.sites.deleted": "🗑 Site deleted.",
        "bot.sites.confirm_delete": (
            "Delete the site “{name}”? Its run queue and history will be "
            "erased."
        ),
        "bot.sites.confirm_delete_yes": "🗑 Yes, delete",
        "bot.sites.empty": "No sites yet. Press “➕ Add site”.",
        "bot.sites.next_line": "   next: {when}",
        "bot.sites.list_suffix": " — next {when}",
        "bot.sites.choose_parser": "Choose a parser:",
        "bot.sites.need_profile": (
            "Choose a persistent profile in the site settings first."
        ),
        "bot.sites.card.parser": "Parser: <code>{parser}</code>",
        "bot.sites.card.state": "State: {state}",
        "bot.sites.card.state_on": "🟢 enabled",
        "bot.sites.card.state_off": "🔴 disabled",
        "bot.sites.card.interval": "Interval: every {low}–{high} min",
        "bot.sites.card.profile": "Profile: {label}",
        "bot.sites.card.export": "Export: {mode}",
        "bot.sites.card.next_run": "Next run: {when}",
        "bot.sites.card.last_run": "Last run: {status}",
        "bot.sites.profile.persistent": "persistent (id {id})",
        "bot.sites.profile.persistent_plain": "persistent",
        "bot.sites.profile.clean": "clean (random fingerprint)",
        # --- settings -----------------------------------------------------
        "bot.settings.title": "⚙️ <b>{name}</b>",
        "bot.settings.choose": "Choose what to change:",
        "bot.settings.interval_button": "⏱ Interval",
        "bot.settings.profile_button": "👤 Profile",
        "bot.settings.proxy_button": "🌍 Proxy",
        "bot.settings.export_button": "📄 Export",
        "bot.settings.params_button": "🔧 Parser settings",
        "bot.settings.ask_interval": (
            "Send the interval in minutes: one number (for example 60) or a "
            "range (for example 45-90)."
        ),
        "bot.settings.interval_saved": (
            "✅ The interval of “{name}”: every {low}–{high} min."
        ),
        "bot.settings.choose_profile": "Choose a profile:",
        "bot.settings.profile_set": "✅ Profile: {label}",
        "bot.settings.choose_proxy": "Choose a proxy:",
        "bot.settings.proxy_set": "✅ Proxy: {name}",
        "bot.settings.params_header": (
            "🔧 Parser settings. Press one to change it:"
        ),
        "bot.settings.ask_param": "Send a new value for “{title}”:",
        "bot.settings.param_saved": "✅ Setting “{name}” saved.",
        "bot.settings.export_set": "✅ Export: {mode}",
        "bot.settings.interval_number": (
            "the interval must be a number or a range, for example 45-90"
        ),
        "bot.settings.interval_positive": "the interval must be above zero",
        "bot.settings.interval_order": (
            "the minimum cannot be greater than the maximum"
        ),
        "bot.settings.interval_max": "the maximum is {max} minutes (one week)",
        "bot.settings.param_not_enum": "the field is not a choice list",
        "bot.settings.param_unknown": "unknown setting: {name}",
        "bot.settings.param_invalid": (
            "“{raw}” is not a valid value for {location}{detail}"
        ),
        "bot.settings.param_allowed": "allowed values: {values}",
        "bot.settings.param_int": "send a whole number",
        "bot.settings.param_float": "send a number",
        "bot.settings.bound_ge": "at least {value}",
        "bot.settings.bound_gt": "greater than {value}",
        "bot.settings.bound_le": "at most {value}",
        "bot.settings.bound_lt": "less than {value}",
        "bot.settings.need_login_profile": (
            "The parser needs a login: create a persistent profile under "
            "“Profiles”."
        ),
        "bot.settings.clean_profile_button": "🧼 Clean profile",
        # --- profiles -----------------------------------------------------
        "bot.profiles.header": "👤 Login profiles",
        "bot.profiles.empty": "No persistent profiles yet. Press “➕ New profile”.",
        "bot.profiles.ask_name": "Send the name of the new profile:",
        "bot.profiles.created": "✅ Profile “{name}” created.",
        "bot.profiles.deleted": "🗑 Profile deleted.",
        "bot.profiles.done": "✅ Login to “{name}” finished.",
        "bot.profiles.card.state": "State: {status}",
        "bot.profiles.card.fingerprint": "Fingerprint: {platform}, {brand} {version}",
        "bot.profiles.card.timezone": "Time zone: {timezone}, locale: {locale}",
        "bot.profile.status.free": "free",
        "bot.profile.status.locked": "in use",
        "bot.profile.status.login": "waiting for login",
        "bot.profile.status.expired": "expired",
        # --- proxies ------------------------------------------------------
        "bot.proxies.header": "🌍 Proxies",
        "bot.proxies.empty": "No proxies yet. Press “➕ New proxy”.",
        "bot.proxies.ask": (
            "Send the proxy as “name http://user:pass@host:port” (the name "
            "is optional)."
        ),
        "bot.proxies.added": "✅ Proxy added.",
        "bot.proxies.deleted": "🗑 Proxy deleted.",
        "bot.proxies.check_ok": "✅ {text}",
        "bot.proxies.check_failed": "❌ {text}",
        "bot.proxies.card.user": "user {name}",
        "bot.proxies.card.country": "Country: {country}",
        # --- run history --------------------------------------------------
        "bot.runs.header": "📜 Recent runs:",
        "bot.runs.empty": "There have been no runs yet.",
        "bot.runs.line": "{mark} {when} — {name}: {status}, {rows} rows",
        "bot.runs.site": "site {id}",
        "bot.runs.history_line": (
            "{mark} {when} — {status}, {rows} rows, {new} new"
        ),
        # --- VNC login ----------------------------------------------------
        "bot.vnc.session": (
            "🖥 <b>Log in to the site</b>\n\n"
            "Link: {url}\n"
            "Password: <code>{password}</code>\n"
            "Remaining: {remaining} min.\n\n"
            "Open the link, log in to the site and press “Done”."
        ),
        "bot.vnc.login_button": "🖥 Log in to profile",
        "bot.vnc.done_button": "✅ Done",
        "bot.vnc.extend_button": "⏱ +30 min",
        "bot.vnc.finished": "🖥 Login session finished.",
        "bot.vnc.extended": "⏱ Extended, {remaining} min left.",
        # --- interface language -------------------------------------------
        "bot.language.button": "🌐 Language",
        "bot.language.screen": "🌐 <b>Interface language</b>\nChoose a language.",
        "bot.language.ru": "🇷🇺 Русский",
        "bot.language.en": "🇬🇧 English",
        "bot.language.saved": "✅ Interface language: English.",
        "bot.language.unknown": "Unknown language.",
        "bot.language.unavailable": (
            "Could not save the language: the storage is unavailable."
        ),
        "bot.language.save_failed": (
            "Could not save the language: the database did not answer. "
            "Try again."
        ),
        # --- notifications -------------------------------------------------
        "bot.delivery.caption_report": (
            "{head} {site}\nRows: {rows}, new: {new}"
        ),
        "bot.delivery.caption_failed": "❌ {site}: {reason}",
        "bot.delivery.partial_head": "⚠️ Partial run",
        "bot.delivery.report_head": "📊",
        "bot.delivery.report_missing": "⚠️ Report not created",
        "bot.delivery.report_missing_reason": "⚠️ Report not created: {reason}",
        "bot.delivery.site_placeholder": "site",
        "bot.delivery.unknown_error": "unknown error",
        "bot.delivery.run_failed": "❌ The run failed: {reason}",
        "bot.delivery.run_failed_site": "❌ The run of “{site}” failed: {reason}",
        "bot.delivery.site_problem": "⚠️ Problem with the site: {text}",
        "bot.delivery.site_problem_repeated": "{failures} failures in a row",
        "bot.delivery.site_problem_named": (
            "⚠️ Problem with the site “{site}”: {text}"
        ),
        "bot.delivery.vnc_ready": (
            "🖥 The site login is ready\n"
            "Link: {url}\n"
            "Password: {password}"
        ),
        "bot.delivery.vnc_stopped": "🖥 Login session finished.",
        "bot.delivery.vnc_failed": (
            "🖥 Could not start the login session: {error}"
        ),
        "bot.delivery.unknown_event": "Notification: {kind}",
    },
}
