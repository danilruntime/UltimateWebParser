"""CLI strings: every sentence ``uwp ...`` prints, plus its ``--help`` text.

One deployment, one operator: the language is ``settings.default_language`` and
there is no ``--lang`` flag. ``argparse`` builds its help before any command
runs, so ``build_parser`` takes the language as an argument instead of reading
the environment itself.
"""

from uwp.i18n import Lang

CATALOG: dict[Lang, dict[str, str]] = {
    Lang.ru: {
        # Seed key from Task 1: ``test_i18n.py`` asserts it as the proof that
        # the CLI domain is merged. Kept even though no command prints it.
        "cli.ok": "Готово.",
        # --- init ---------------------------------------------------------
        "cli.init.site_created": "Создан сайт «{name}» (выключен).",
        "cli.init.skipped": "Сайты уже есть, сид пропущен.",
        "cli.init.db_path": "База данных: {path}",
        # --- doctor -------------------------------------------------------
        "cli.doctor.dir": "каталог {path}",
        "cli.doctor.browser": "браузер fingerprint-chromium",
        "cli.doctor.browser_no_version": "не запускается: {error}",
        "cli.doctor.browser_returncode": "--version вернул {code}",
        "cli.doctor.tool_missing": "не найден (нужен только для VNC)",
        "cli.doctor.playwright": "драйвер playwright",
        "cli.doctor.driver_missing": "не найден: {error}",
        "cli.doctor.driver_broken": "не запускается: {error}",
        "cli.doctor.token_missing": "не задан",
        "cli.doctor.admins_missing": "пусто",
        "cli.doctor.proxy_label": "прокси Telegram ({url})",
        "cli.doctor.proxy_none": "прокси Telegram",
        "cli.doctor.proxy_missing": (
            "не задан (UWP_TELEGRAM_PROXY): нужен там, где api.telegram.org недоступен"
        ),
        "cli.doctor.secret_missing": "не задан: пароли прокси хранятся открыто",
        "cli.doctor.secret_short": (
            "всего {length} символов: нужен длинный случайный ключ (32+ символа)"
        ),
        "cli.doctor.db": "база данных",
        "cli.doctor.all_ok": "Всё в порядке.",
        # --- telegram proxy probe ----------------------------------------
        "cli.proxy.bad_scheme": (
            "неподдерживаемая схема «{scheme}» (нужно http, https или socks5)"
        ),
        "cli.proxy.no_host_port": "в адресе прокси нет хоста или порта",
        "cli.proxy.unreachable": "прокси {host}:{port} недоступен: {error}",
        "cli.proxy.socks_missing": "для socks-прокси нужен пакет aiohttp-socks",
        "cli.proxy.http_status": "api.telegram.org вернул {status}",
        "cli.proxy.request_failed": "запрос через прокси не прошёл: {error}",
        "cli.proxy.timeout": "прокси не ответил за {seconds:.0f} с",
        "cli.proxy.loopback_hint": (
            "; внутри контейнера 127.0.0.1 — это сам контейнер, "
            "запустите его с network_mode: host или укажите адрес хоста"
        ),
        # --- browser install ---------------------------------------------
        "cli.browser.downloading": "Скачиваю {url}",
        "cli.browser.sha_mismatch": "❌ sha256 не совпал: {digest}",
        "cli.browser.sha_ok": "sha256 совпал, распаковываю.",
        "cli.browser.installed": "Браузер установлен: {path}",
        "cli.browser.export_hint": "Добавьте в окружение UWP_BROWSER_PATH={path}",
        "cli.browser.unsafe_path": "небезопасный путь в архиве: {path}",
        # --- parsers -----------------------------------------------------
        "cli.parsers.login_required": " (нужен вход)",
        # --- site --------------------------------------------------------
        "cli.site.empty": "Сайтов нет. Запустите `uwp init`.",
        "cli.site.col_slug": "slug",
        "cli.site.col_state": "состояние",
        "cli.site.col_interval": "интервал",
        "cli.site.col_next": "следующий",
        "cli.site.col_parser": "парсер",
        "cli.site.list_header": (
            "{slug:<20} {state:<10} {interval:<12} {next:<18} {parser}"
        ),
        "cli.site.state_on": "включён",
        "cli.site.state_off": "выключен",
        "cli.site.created": "Создан сайт {slug} ({name}), пока выключен.",
        "cli.site.not_found": "❌ Сайт «{slug}» не найден.",
        "cli.site.toggled": "Сайт «{name}» {state}.",
        "cli.site.updated": "Сайт «{name}» обновлён.",
        "cli.site.nothing_to_change": "Нечего менять: укажите --interval и/или --name.",
        "cli.site.bad_interval": "❌ Интервал: {error}",
        "cli.site.queued": "Прогон поставлен в очередь (job {job}).",
        "cli.site.already_running": "Прогон уже идёт или стоит в очереди.",
        # --- profile -----------------------------------------------------
        "cli.profile.empty": "Постоянных профилей нет.",
        "cli.profile.created": "Создан профиль {id} «{name}».",
        "cli.profile.deleted": "Профиль {id} удалён.",
        "cli.profile.not_found": "❌ Профиль {id} не найден.",
        "cli.profile.vnc_link": "Ссылка: {url}",
        "cli.profile.vnc_password": "Пароль: {password}",
        "cli.profile.vnc_hint": "Нажмите Ctrl+C, чтобы завершить сессию.",
        "cli.profile.session_finished": "Сессия завершена.",
        # --- proxy -------------------------------------------------------
        "cli.proxy.empty": "Прокси нет.",
        "cli.proxy.added": "Добавлен прокси {id} «{name}».",
        "cli.proxy.check_ok": "✅ {text}",
        "cli.proxy.check_failed": "❌ {text}",
        # --- vnc ---------------------------------------------------------
        "cli.vnc.empty": "Активных сессий нет.",
        "cli.vnc.line": "{id:<4} профиль {profile} порт {port}",
        "cli.vnc.not_found": "❌ Сессия {id} не найдена.",
        "cli.vnc.already_stopped": "Сессия {id} уже завершена.",
        "cli.vnc.stop_queued": (
            "Сессия {id} помечена на остановку; процесс остановит воркер."
        ),
        # --- export / run / db -------------------------------------------
        "cli.export.not_found": "❌ Прогон или сайт «{target}» не найден.",
        "cli.export.no_report": "❌ У прогона нет файла отчёта.",
        "cli.export.written": "Отчёт: {path}",
        "cli.run.once_done": "Один цикл воркера выполнен.",
        "cli.db.upgraded": "Схема обновлена: {path}",
        # --- argparse help -----------------------------------------------
        "cli.help.init": "создать базу, админов и тестовый сайт",
        "cli.help.doctor": "проверить окружение",
        "cli.help.doctor_quiet": "молчать при успехе",
        "cli.help.doctor_offline": (
            "не ходить в сеть (так его зовёт healthcheck контейнера)"
        ),
        "cli.help.doctor_worker": "процесс без Telegram: UWP_BOT_TOKEN не требуется",
        "cli.help.browser": "управление браузером",
        "cli.help.browser_install": "скачать fingerprint-chromium",
        "cli.help.browser_version": "версия (по умолчанию из настроек)",
        "cli.help.browser_url": "URL архива; проверяется sha256",
        "cli.help.browser_dest": "каталог установки",
        "cli.help.parsers": "парсеры",
        "cli.help.parsers_list": "список парсеров",
        "cli.help.site": "сайты",
        "cli.help.site_list": "список сайтов",
        "cli.help.site_add": "добавить сайт",
        "cli.help.site_enable": "включить сайт",
        "cli.help.site_disable": "выключить сайт",
        "cli.help.site_run": "запустить сейчас",
        "cli.help.site_set": "изменить настройки",
        "cli.help.profile": "профили",
        "cli.help.profile_list": "список профилей",
        "cli.help.profile_create": "создать профиль",
        "cli.help.profile_login": "войти через VNC",
        "cli.help.profile_delete": "удалить профиль",
        "cli.help.proxy": "прокси",
        "cli.help.proxy_list": "список прокси",
        "cli.help.proxy_add": "добавить прокси",
        "cli.help.proxy_test": "проверить прокси",
        "cli.help.vnc": "сессии входа",
        "cli.help.vnc_list": "активные сессии",
        "cli.help.vnc_stop": "остановить сессию",
        "cli.help.export": "сохранить отчёт прогона",
        "cli.help.export_target": "id прогона или slug сайта",
        "cli.help.run": "запустить бота и воркер вместе",
        "cli.help.run_once": "один цикл воркера",
        "cli.help.bot": "только бот",
        "cli.help.worker": "только воркер",
        "cli.help.db": "база данных",
        "cli.help.db_upgrade": "применить миграции",
    },
    Lang.en: {
        "cli.ok": "Done.",
        "cli.init.site_created": "Created the site “{name}” (disabled).",
        "cli.init.skipped": "Sites already exist; seeding skipped.",
        "cli.init.db_path": "Database: {path}",
        "cli.doctor.dir": "directory {path}",
        "cli.doctor.browser": "fingerprint-chromium browser",
        "cli.doctor.browser_no_version": "does not start: {error}",
        "cli.doctor.browser_returncode": "--version exited with {code}",
        "cli.doctor.tool_missing": "not found (only needed for VNC)",
        "cli.doctor.playwright": "playwright driver",
        "cli.doctor.driver_missing": "not found: {error}",
        "cli.doctor.driver_broken": "does not start: {error}",
        "cli.doctor.token_missing": "not set",
        "cli.doctor.admins_missing": "empty",
        "cli.doctor.proxy_label": "Telegram proxy ({url})",
        "cli.doctor.proxy_none": "Telegram proxy",
        "cli.doctor.proxy_missing": (
            "not set (UWP_TELEGRAM_PROXY): needed wherever api.telegram.org is blocked"
        ),
        "cli.doctor.secret_missing": (
            "not set: proxy passwords are stored in plain text"
        ),
        "cli.doctor.secret_short": (
            "{length} characters: a long random key is required (32+ characters)"
        ),
        "cli.doctor.db": "database",
        "cli.doctor.all_ok": "Everything is fine.",
        "cli.proxy.bad_scheme": "unsupported scheme “{scheme}” (http, https or socks5)",
        "cli.proxy.no_host_port": "the proxy address has no host or port",
        "cli.proxy.unreachable": "proxy {host}:{port} is unreachable: {error}",
        "cli.proxy.socks_missing": "socks proxies need the aiohttp-socks package",
        "cli.proxy.http_status": "api.telegram.org returned {status}",
        "cli.proxy.request_failed": "the request through the proxy failed: {error}",
        "cli.proxy.timeout": "the proxy did not answer within {seconds:.0f} s",
        "cli.proxy.loopback_hint": (
            "; inside a container 127.0.0.1 is the container itself — "
            "run it with network_mode: host or use the host address"
        ),
        "cli.browser.downloading": "Downloading {url}",
        "cli.browser.sha_mismatch": "❌ sha256 mismatch: {digest}",
        "cli.browser.sha_ok": "sha256 matches, extracting.",
        "cli.browser.installed": "Browser installed: {path}",
        "cli.browser.export_hint": "Add UWP_BROWSER_PATH={path} to the environment",
        "cli.browser.unsafe_path": "unsafe path in the archive: {path}",
        "cli.parsers.login_required": " (login required)",
        "cli.site.empty": "No sites. Run `uwp init`.",
        "cli.site.col_slug": "slug",
        "cli.site.col_state": "state",
        "cli.site.col_interval": "interval",
        "cli.site.col_next": "next",
        "cli.site.col_parser": "parser",
        "cli.site.list_header": (
            "{slug:<20} {state:<10} {interval:<12} {next:<18} {parser}"
        ),
        "cli.site.state_on": "enabled",
        "cli.site.state_off": "disabled",
        "cli.site.created": "Created the site {slug} ({name}); it is disabled for now.",
        "cli.site.not_found": "❌ Site “{slug}” not found.",
        "cli.site.toggled": "Site “{name}” is now {state}.",
        "cli.site.updated": "Site “{name}” updated.",
        "cli.site.nothing_to_change": (
            "Nothing to change: pass --interval and/or --name."
        ),
        "cli.site.bad_interval": "❌ Interval: {error}",
        "cli.site.queued": "Run queued (job {job}).",
        "cli.site.already_running": "A run is already queued or running.",
        "cli.profile.empty": "No persistent profiles.",
        "cli.profile.created": "Created profile {id} “{name}”.",
        "cli.profile.deleted": "Profile {id} deleted.",
        "cli.profile.not_found": "❌ Profile {id} not found.",
        "cli.profile.vnc_link": "Link: {url}",
        "cli.profile.vnc_password": "Password: {password}",
        "cli.profile.vnc_hint": "Press Ctrl+C to end the session.",
        "cli.profile.session_finished": "Session finished.",
        "cli.proxy.empty": "No proxies.",
        "cli.proxy.added": "Added proxy {id} “{name}”.",
        "cli.proxy.check_ok": "✅ {text}",
        "cli.proxy.check_failed": "❌ {text}",
        "cli.vnc.empty": "No active sessions.",
        "cli.vnc.line": "{id:<4} profile {profile} port {port}",
        "cli.vnc.not_found": "❌ Session {id} not found.",
        "cli.vnc.already_stopped": "Session {id} is already finished.",
        "cli.vnc.stop_queued": (
            "Session {id} is marked for stopping; the worker will kill it."
        ),
        "cli.export.not_found": "❌ Run or site “{target}” not found.",
        "cli.export.no_report": "❌ The run has no report file.",
        "cli.export.written": "Report: {path}",
        "cli.run.once_done": "One worker cycle finished.",
        "cli.db.upgraded": "Schema upgraded: {path}",
        "cli.help.init": "create the database, admins and a sample site",
        "cli.help.doctor": "check the environment",
        "cli.help.doctor_quiet": "stay silent on success",
        "cli.help.doctor_offline": (
            "do not use the network (as the container healthcheck does)"
        ),
        "cli.help.doctor_worker": (
            "a process without Telegram: UWP_BOT_TOKEN not required"
        ),
        "cli.help.browser": "browser management",
        "cli.help.browser_install": "download fingerprint-chromium",
        "cli.help.browser_version": "version (defaults to the settings)",
        "cli.help.browser_url": "archive URL; checked with sha256",
        "cli.help.browser_dest": "installation directory",
        "cli.help.parsers": "parsers",
        "cli.help.parsers_list": "list parsers",
        "cli.help.site": "sites",
        "cli.help.site_list": "list sites",
        "cli.help.site_add": "add a site",
        "cli.help.site_enable": "enable a site",
        "cli.help.site_disable": "disable a site",
        "cli.help.site_run": "run now",
        "cli.help.site_set": "change the settings",
        "cli.help.profile": "profiles",
        "cli.help.profile_list": "list profiles",
        "cli.help.profile_create": "create a profile",
        "cli.help.profile_login": "log in through VNC",
        "cli.help.profile_delete": "delete a profile",
        "cli.help.proxy": "proxies",
        "cli.help.proxy_list": "list proxies",
        "cli.help.proxy_add": "add a proxy",
        "cli.help.proxy_test": "check a proxy",
        "cli.help.vnc": "login sessions",
        "cli.help.vnc_list": "active sessions",
        "cli.help.vnc_stop": "stop a session",
        "cli.help.export": "save the report of a run",
        "cli.help.export_target": "run id or site slug",
        "cli.help.run": "run the bot and the worker together",
        "cli.help.run_once": "one worker cycle",
        "cli.help.bot": "the bot only",
        "cli.help.worker": "the worker only",
        "cli.help.db": "database",
        "cli.help.db_upgrade": "apply the migrations",
    },
}
