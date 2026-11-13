"""Russian user-facing strings for the bot.

No user-visible English may leak from here: the bot speaks Russian only.
"""

ACCESS_DENIED = "⛔ Доступ только для администратора."
SITES_HEADER = "🌐 Сайты"
SITE_ADDED = "Сайт добавлен: {name}"
SITE_STARTED = "▶️ Запуск поставлен в очередь."
SITE_ALREADY_RUNNING = "⏳ Прогон уже идёт или стоит в очереди."
SITE_ENABLED = "🟢 Сайт включён, первый прогон запустится сразу."
SITE_DISABLED = "🔴 Сайт выключен, очередь очищена."
SITE_DELETED = "🗑 Сайт удалён."
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
    "/help — эта справка\n"
    "/id — ваш Telegram ID"
)
UNKNOWN_COMMAND = "Не понял команду. /help — список команд."


def site_card(site, next_run_local: str | None) -> str:
    """The text of one site card."""
    state = "🟢 включён" if site.enabled else "🔴 выключен"
    lines = [
        f"<b>{site.name}</b>",
        f"Парсер: <code>{site.parser_key}</code>",
        f"Состояние: {state}",
        f"Интервал: каждые {site.interval_min_min}–{site.interval_max_min} мин",
        f"Профиль: {_profile_label(site)}",
        f"Экспорт: {_export_label(site)}",
    ]
    if next_run_local:
        lines.append(f"Следующий прогон: {next_run_local}")
    if site.last_status:
        lines.append(f"Последний прогон: {site.last_status}")
    return "\n".join(lines)


def _profile_label(site) -> str:
    if str(site.profile_mode) == "persistent":
        return f"постоянный (id {site.profile_id})" if site.profile_id else "постоянный"
    return "чистый (случайный отпечаток)"


def _export_label(site) -> str:
    return {
        "always": "всегда",
        "changes": "только изменения",
        "never": "никогда",
    }.get(str(site.export_mode), str(site.export_mode))
