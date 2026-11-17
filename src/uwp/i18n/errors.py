"""Messages raised by a service and shown by both the CLI and the bot.

A service returns data, not prose: ``ProxyService.test`` returns a
:class:`~uwp.services.proxies.ProxyCheck` and the caller renders it. The few
``ValueError`` texts a service does raise still need a language, and the
deployment's ``default_language`` is the only one available to a worker. These
keys therefore live outside both ``bot.*`` and ``cli.*``: either side may read
one, and a shared key keeps the two sentences from drifting.
"""

from uwp.i18n import Lang

CATALOG: dict[Lang, dict[str, str]] = {
    Lang.ru: {
        # --- proxy URL parsing -------------------------------------------
        "error.proxy.bad_scheme": "неподдерживаемая схема прокси: {scheme}",
        "error.proxy.no_host": "в адресе прокси нет хоста",
        "error.proxy.bad_port": "некорректный порт прокси",
        "error.proxy.no_port": "в адресе прокси нет порта",
        "error.proxy.duplicate": "прокси с именем «{name}» уже есть",
        # --- proxy reachability check ------------------------------------
        # ``check.*``, not ``error.*``: a reachable proxy is a success verdict,
        # and filing it under ``error.`` misleads anyone grepping the catalogue.
        "check.proxy.reachable": "доступен, {ms} мс",
        "error.proxy.not_found": "прокси не найден",
        "error.proxy.timeout": "таймаут {seconds} с",
        "error.proxy.connection": "ошибка соединения: {detail}",
        # --- profile ------------------------------------------------------
        "error.profile.name_empty": "имя профиля не может быть пустым",
        "error.profile.duplicate": "профиль с именем «{name}» уже есть",
        "error.profile.not_found": "профиль {id} не найден",
        "error.profile.locked": "профиль {id} занят прогоном: сначала остановите его",
        "error.profile.in_use_same_host": (
            "профиль уже открыт другим браузером (pid {pid}); "
            "закройте сессию входа или дождитесь её завершения"
        ),
        "error.profile.in_use_foreign_host": (
            "профиль уже открыт браузером на хосте {host}; "
            "закройте сессию входа или дождитесь её завершения"
        ),
        # --- stored secrets ------------------------------------------------
        "error.secret.missing_key": (
            "секрет зашифрован, но UWP_SECRET_KEY не задан"
        ),
        "error.secret.wrong_key": (
            "не удалось расшифровать секрет: проверьте UWP_SECRET_KEY"
        ),
        # --- VNC ----------------------------------------------------------
        "error.vnc.session_not_found": "сессия {id} не найдена",
        # --- sites --------------------------------------------------------
        "error.site.not_found": "сайт {id} не найден",
        "error.parser.unknown": "неизвестный парсер: {parser}",
    },
    Lang.en: {
        "error.proxy.bad_scheme": "unsupported proxy scheme: {scheme}",
        "error.proxy.no_host": "the proxy address has no host",
        "error.proxy.bad_port": "invalid proxy port",
        "error.proxy.no_port": "the proxy address has no port",
        "error.proxy.duplicate": "a proxy named “{name}” already exists",
        "check.proxy.reachable": "reachable, {ms} ms",
        "error.proxy.not_found": "proxy not found",
        "error.proxy.timeout": "timeout {seconds} s",
        "error.proxy.connection": "connection error: {detail}",
        "error.profile.name_empty": "the profile name cannot be empty",
        "error.profile.duplicate": "a profile named “{name}” already exists",
        "error.profile.not_found": "profile {id} not found",
        "error.profile.locked": "profile {id} is in use by a run: stop it first",
        "error.profile.in_use_same_host": (
            "the profile is already open in another browser (pid {pid}); "
            "finish the login session or wait for it"
        ),
        "error.profile.in_use_foreign_host": (
            "the profile is already open in a browser on host {host}; "
            "finish the login session or wait for it"
        ),
        "error.secret.missing_key": (
            "the secret is encrypted but UWP_SECRET_KEY is not set"
        ),
        "error.secret.wrong_key": (
            "cannot decrypt the stored secret: check UWP_SECRET_KEY"
        ),
        "error.vnc.session_not_found": "session {id} not found",
        "error.site.not_found": "site {id} not found",
        "error.parser.unknown": "unknown parser: {parser}",
    },
}
