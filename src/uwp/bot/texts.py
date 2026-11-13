"""Russian user-facing strings for the bot.

No user-visible English may leak from here: the bot speaks Russian only.
"""


def esc(value) -> str:
    """Escape a user-supplied string for the HTML parse mode."""
    from html import escape

    return escape(str(value), quote=False)


ACCESS_DENIED = "⛔ Доступ только для администратора."
SITES_HEADER = "🌐 Сайты"
SITE_ADDED = "Сайт добавлен: {name}"
SITE_STARTED = "▶️ Запуск поставлен в очередь."
SITE_ALREADY_RUNNING = "⏳ Прогон уже идёт или стоит в очереди."
SITE_ENABLED = "🟢 Сайт включён, первый прогон запустится сразу."
SITE_DISABLED = "🔴 Сайт выключен, очередь очищена."
SITE_DELETED = "🗑 Сайт удалён."
DELETE = "🗑 Удалить"
CONFIRM_DELETE = "Удалить сайт «{name}»? Очередь прогонов и история будут стёрты."
NO_SITES = "Пока нет ни одного сайта. Нажмите «➕ Добавить сайт»."
BACK = "⬅️ Назад"
RUN_NOW = "▶️ Запустить сейчас"
SETTINGS = "⚙️ Настройки"
TOGGLE_ON = "🟢 Включить"
TOGGLE_OFF = "🔴 Выключить"
ADD_SITE = "➕ Добавить сайт"
HELP = (
    "Я парсер сайтов под управлением администратора.\n\n"
    "/sites — список сайтов\n"
    "/runs — последние прогоны\n"
    "/help — эта справка\n"
    "/id — ваш Telegram ID"
)
UNKNOWN_COMMAND = "Не понял команду. /help — список команд."


def status_label(value) -> str:
    """Russian name of a run status."""
    from uwp import labels

    return labels.status(value)


def kind_label(value) -> str:
    """Russian name of a profile kind."""
    from uwp import labels

    return labels.kind(value)


def error_label(value) -> str:
    """Russian name of an error kind."""
    from uwp import labels

    return labels.error(value)


def export_label(mode) -> str:
    """Russian name of an export mode."""
    from uwp import labels

    return labels.export(mode)


def site_card(site, next_run_local: str | None) -> str:
    """The text of one site card."""
    state = "🟢 включён" if site.enabled else "🔴 выключен"
    lines = [
        f"<b>{esc(site.name)}</b>",
        f"Парсер: <code>{site.parser_key}</code>",
        f"Состояние: {state}",
        f"Интервал: каждые {site.interval_min_min}–{site.interval_max_min} мин",
        f"Профиль: {_profile_label(site)}",
        f"Экспорт: {_export_label(site)}",
    ]
    if next_run_local:
        lines.append(f"Следующий прогон: {next_run_local}")
    if site.last_status:
        lines.append(f"Последний прогон: {status_label(site.last_status)}")
    return "\n".join(lines)


def _profile_label(site) -> str:
    if str(site.profile_mode) == "persistent":
        return f"постоянный (id {site.profile_id})" if site.profile_id else "постоянный"
    return "чистый (случайный отпечаток)"


def _export_label(site) -> str:
    return export_label(site.export_mode)


ASK_INTERVAL = (
    "Введите интервал в минутах: одно число (например 60) "
    "или диапазон (например 45-90)."
)
INTERVAL_SAVED = "✅ Интервал сайта «{name}»: каждые {low}–{high} мин."
CHOOSE_PROFILE = "Выберите профиль:"
PROFILE_SET = "✅ Профиль: {label}"
CHOOSE_PROXY = "Выберите прокси:"
PROXY_SET = "✅ Прокси: {name}"
PARAMS_HEADER = "🔧 Параметры парсера. Нажмите, чтобы изменить:"
ASK_PARAM = "Введите новое значение параметра «{title}»:"
PARAM_SAVED = "✅ Параметр «{name}» сохранён."
EXPORT_SET = "✅ Экспорт: {mode}"


def profile_label(site) -> str:
    if str(site.profile_mode) == "persistent":
        return f"постоянный (id {site.profile_id})" if site.profile_id else "постоянный"
    return "чистый (случайный отпечаток)"


PROFILES_HEADER = "👤 Профили для входа"
NO_PROFILES = "Пока нет постоянных профилей. Нажмите «➕ Новый профиль»."
ASK_PROFILE_NAME = "Введите имя нового профиля:"
PROFILE_CREATED = "✅ Профиль «{name}» создан."
PROFILE_DELETED = "🗑 Профиль удалён."
PROFILE_DONE = "✅ Вход в профиль «{name}» завершён."
PROXIES_HEADER = "🌍 Прокси"
NO_PROXIES = "Пока нет прокси. Нажмите «➕ Новый прокси»."
ASK_PROXY = (
    "Отправьте прокси в формате «имя http://user:pass@host:port» (имя необязательно)."
)
PROXY_ADDED = "✅ Прокси добавлен."
PROXY_DELETED = "🗑 Прокси удалён."
RUNS_HEADER = "📜 Последние прогоны:"
NO_RUNS = "Прогонов ещё не было."
MENU = "Главное меню:"


def profile_card(profile) -> str:
    status = {
        "free": "свободен",
        "locked": "используется",
        "login": "ожидает входа",
        "expired": "истёк",
    }.get(str(profile.status), str(profile.status))
    return (
        f"👤 <b>{esc(profile.name)}</b>\n"
        f"Состояние: {status}\n"
        f"Отпечаток: {profile.platform}, {profile.brand} {profile.brand_version}\n"
        f"Часовой пояс: {profile.timezone}, язык: {profile.locale}"
    )


def proxy_card(proxy) -> str:
    auth = f", пользователь {proxy.username}" if proxy.username else ""
    country = f"\nСтрана: {proxy.country}" if proxy.country else ""
    return (
        f"🌍 <b>{esc(proxy.name)}</b>\n"
        f"{proxy.scheme}://{proxy.host}:{proxy.port}{auth}{country}"
    )
