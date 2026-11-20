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
        # A profile a login cannot use: ``vnc.py`` raises these as codes.
        "error.vnc.profile_not_persistent": (
            "для входа нужен постоянный профиль, а не временный"
        ),
        "error.vnc.profile_busy": (
            "профиль уже занят: закройте сессию входа или дождитесь её завершения"
        ),
        "error.vnc.profile_unsaved": "профиль ещё не сохранён",
        "error.vnc.storepasswd_failed": (
            "x11vnc не сохранил пароль сессии"
        ),
        # The stack's own failures: ``vnc.py`` emits a code, delivery renders it.
        "error.vnc.port_taken": (
            "порт {port} уже занят: похоже, запущен второй стек UWP"
        ),
        "error.vnc.xvfb_failed": (
            "Xvfb не поднял дисплей :{display}: {detail}"
        ),
        "error.vnc.browser_failed": "браузер не запустился: {detail}",
        "error.vnc.websockify_failed": (
            "websockify не занял порт {port}: {detail}"
        ),
        "error.vnc.x11vnc_failed": (
            "x11vnc не слушает порт {port}: {detail}"
        ),
        # --- sites --------------------------------------------------------
        "error.site.not_found": "сайт {id} не найден",
        "error.site.name_empty": "название сайта не может быть пустым",
        "error.site.name_too_long": "название сайта длиннее {max} символов",
        "error.parser.unknown": "неизвестный парсер: {parser}",
        "error.parser.invalid_settings": (
            "нерабочие настройки парсера: {detail}"
        ),
        # --- a run -------------------------------------------------------
        "error.profile.none_assigned": (
            "у сайта нет постоянного профиля: выберите его в настройках"
        ),
        "error.run.timeout": "прогон не уложился в {seconds} с",
        "error.browser.missing": (
            "браузер fingerprint-chromium не найден: запустите "
            "«uwp browser install»"
        ),
        "error.bridge.unavailable": (
            "прокси-мост не загрузился: {detail}"
        ),
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
        "error.vnc.profile_not_persistent": (
            "VNC login needs a persistent profile, not a temporary one"
        ),
        "error.vnc.profile_busy": (
            "the profile is already in use: finish the login session or wait"
        ),
        "error.vnc.profile_unsaved": "the profile is not stored yet",
        "error.vnc.storepasswd_failed": (
            "x11vnc could not store the session password"
        ),
        "error.vnc.port_taken": (
            "port {port} is already taken: a second UWP stack looks alive"
        ),
        "error.vnc.xvfb_failed": (
            "Xvfb did not bring up display :{display}: {detail}"
        ),
        "error.vnc.browser_failed": "the login browser did not start: {detail}",
        "error.vnc.websockify_failed": (
            "websockify did not take port {port}: {detail}"
        ),
        "error.vnc.x11vnc_failed": (
            "x11vnc is not listening on port {port}: {detail}"
        ),
        "error.site.not_found": "site {id} not found",
        "error.site.name_empty": "the site name cannot be empty",
        "error.site.name_too_long": "the site name is longer than {max} characters",
        "error.parser.unknown": "unknown parser: {parser}",
        "error.parser.invalid_settings": "invalid parser settings: {detail}",
        "error.profile.none_assigned": (
            "the site has no persistent profile: pick one in its settings"
        ),
        "error.run.timeout": "the run did not finish within {seconds} s",
        "error.browser.missing": (
            "the fingerprint-chromium browser is missing: run "
            "“uwp browser install”"
        ),
        "error.bridge.unavailable": (
            "the proxy bridge did not load: {detail}"
        ),
    },
}
