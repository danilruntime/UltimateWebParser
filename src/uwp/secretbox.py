"""Encryption of stored proxy passwords.

``UWP_SECRET_KEY`` is optional, and without it a proxy password is kept as is.
With a key set, :meth:`uwp.services.proxies.ProxyService.add` stores a Fernet
token instead, and every place that talks to the upstream proxy has to decrypt
before use — sending the token as a password authenticates nothing.
"""
from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

# Fernet tokens always start with the base64 of version 0x80.
FERNET_PREFIX = "gAAAAA"


def fernet(secret: str) -> Fernet:
    """A Fernet key derived from the settings secret (any length is fine)."""
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def encrypt(value: str, secret: str) -> str:
    return fernet(secret).encrypt(value.encode()).decode()


def looks_encrypted(value: str) -> bool:
    return value.startswith(FERNET_PREFIX)


def decrypt(value: str, secret: str | None) -> str:
    """Return the plain password; a plain value is passed through unchanged."""
    if not looks_encrypted(value):
        return value
    if not secret:
        raise ValueError("пароль зашифрован, но UWP_SECRET_KEY не задан")
    try:
        return fernet(secret).decrypt(value.encode()).decode()
    except InvalidToken:
        log.warning("cannot decrypt a proxy password: wrong UWP_SECRET_KEY")
        raise ValueError(
            "не удалось расшифровать пароль: проверьте UWP_SECRET_KEY"
        ) from None
