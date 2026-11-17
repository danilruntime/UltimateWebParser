"""Enum words shared by the bot and the ``.ods`` metadata sheet.

This module holds no words of its own: every label is looked up in the
catalogue, so the run line in the chat and the metadata row in the report
cannot drift apart. A value outside the enum is returned unchanged, which is
what a row written by an older version needs.
"""

from __future__ import annotations

from uwp.i18n import Lang, has_key, t

_STATUS = "report.status"
_KIND = "report.kind"
_ERROR = "report.error"
_EXPORT = "report.export"


def _word(prefix: str, value, lang: Lang | str) -> str:
    """The translated value, or ``value`` itself when the catalogue has no key."""
    text = str(value)
    key = f"{prefix}.{text}"
    # An unknown value is data, not a typo: a run stored by an older version
    # must still render in the report instead of raising in ``t``.
    return t(lang, key) if has_key(lang, key) else text


def status(value, lang: Lang | str) -> str:
    """Localised name of a run status; unknown values stay as they are."""
    return _word(_STATUS, value, lang)


def kind(value, lang: Lang | str) -> str:
    """Localised name of a profile kind."""
    return _word(_KIND, value, lang)


def error(value, lang: Lang | str) -> str:
    """Localised name of an error kind."""
    return _word(_ERROR, value, lang)


def export(value, lang: Lang | str) -> str:
    """Localised name of an export mode."""
    return _word(_EXPORT, value, lang)
