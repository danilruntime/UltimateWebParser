"""Russian labels for domain enums.

Lives outside the bot package because the ``.ods`` metadata sheet needs the
same words as the chat UI; neither side should own the mapping.
"""

RUN_STATUS = {
    "ok": "успешно",
    "partial": "частично",
    "failed": "ошибка",
    "canceled": "отменён",
    "retrying": "повтор",
    "running": "идёт",
}

PROFILE_KIND = {
    "persistent": "постоянный",
    "clean": "чистый",
}

ERROR_KIND = {
    "login_required": "нужен вход",
    "blocked": "блокировка",
    "transient": "временная ошибка",
    "parser_error": "ошибка парсера",
    "config_error": "ошибка настроек",
}

EXPORT_MODE = {
    "always": "всегда",
    "changes": "только изменения",
    "never": "никогда",
}


def status(value) -> str:
    """Russian name of a run status; unknown values stay as they are."""
    text = str(value)
    return RUN_STATUS.get(text, text)


def kind(value) -> str:
    """Russian name of a profile kind."""
    text = str(value)
    return PROFILE_KIND.get(text, text)


def error(value) -> str:
    """Russian name of an error kind."""
    text = str(value)
    return ERROR_KIND.get(text, text)


def export(value) -> str:
    """Russian name of an export mode."""
    text = str(value)
    return EXPORT_MODE.get(text, text)
