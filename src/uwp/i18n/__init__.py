"""Human-facing strings in Russian and English.

Every string the bot, the CLI or a report shows lives here, one module per
domain (``bot``, ``cli``, ``reports``, ``parsers``, ``errors``). Code asks for a
key; a missing key is a ``KeyError`` at the call site instead of a Russian word
leaking into an English chat.

The catalogue is merged once and cached: the admin gate and every handler read
it on each update, and re-importing all five modules per message is pure
overhead.
"""

from __future__ import annotations

import functools
import importlib
from enum import StrEnum
from types import ModuleType


class Lang(StrEnum):
    ru = "ru"
    en = "en"


DEFAULT_LANG = Lang.ru
SUPPORTED: tuple[str, ...] = ("ru", "en")

# One module per area of the interface. Separate files keep two people from
# editing the same catalogue; ``_catalog`` refuses duplicate keys between them.
_DOMAINS: tuple[str, ...] = ("bot", "cli", "reports", "parsers", "errors")


def normalize(code: str | None) -> Lang | None:
    """Telegram's language_code → our Lang, or None when we do not translate it."""
    if not code:
        return None
    base = code.split("-")[0].strip().lower()
    return Lang(base) if base in SUPPORTED else None


@functools.lru_cache(maxsize=1)
def _catalog() -> dict[Lang, dict[str, str]]:
    merged: dict[Lang, dict[str, str]] = {lang: {} for lang in Lang}
    # Key → the domain that first defined it. Tracking the owner across both
    # languages is what catches a key that one domain has in ru and another in
    # en: comparing ru to ru alone would let that pair through.
    owners: dict[str, str] = {}
    for name in _DOMAINS:
        module: ModuleType = importlib.import_module(f"{__name__}.{name}")
        domains: dict[Lang, dict[str, str]] = {}
        for lang in Lang:
            try:
                domain = module.CATALOG[lang]
            except KeyError:
                raise RuntimeError(
                    f"{name} has no {lang.value!r} CATALOG entry"
                ) from None
            if not domain:
                raise RuntimeError(f"{name} has no {lang.value} strings")
            domains[lang] = domain
        for key in set().union(*domains.values()):
            owner = owners.get(key)
            # Two domains defining one key is invisible at runtime: the later
            # module simply wins and the screen shows a word from another area.
            if owner is not None:
                raise RuntimeError(
                    f"{name} redefines {key!r}, already owned by {owner}"
                )
            owners[key] = name
        for lang, domain in domains.items():
            merged[lang].update(domain)
    return merged


def catalog_keys() -> dict[Lang, set[str]]:
    """Every key per language, for the parity test and diagnostics."""
    keys = {lang: set(catalogue) for lang, catalogue in _catalog().items()}
    # An empty catalogue satisfies every "both sides match" assertion while the
    # service shows nothing translated, so refuse it explicitly.
    if not keys[Lang.ru]:
        raise RuntimeError("the translation catalogue is empty")
    return keys


def has_key(lang: Lang | str, key: str) -> bool:
    """Whether ``key`` is translated for ``lang``; an unknown code is never a hit."""
    try:
        resolved = Lang(lang)
    except ValueError:
        return False
    return key in _catalog()[resolved]


def t(lang: Lang | str, key: str, **kwargs: object) -> str:
    """Translate ``key`` and substitute placeholders, if any."""
    try:
        template = _catalog()[Lang(lang)][key]
    except KeyError:
        raise KeyError(f"missing translation: {key} [{lang}]") from None
    # No kwargs means no formatting: a template containing a literal ``{`` must
    # reach the chat verbatim instead of crashing on ``str.format``.
    return template.format(**kwargs) if kwargs else template
