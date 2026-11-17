"""Encryption of stored secrets (proxy and VNC passwords).

``UWP_SECRET_KEY`` is optional, and without it a secret is kept as is. With a
key set, :meth:`uwp.services.proxies.ProxyService.add` stores a Fernet token
instead, and every place that talks to the upstream proxy has to decrypt before
use — sending the token as a password authenticates nothing.

The Fernet key is derived with PBKDF2-HMAC-SHA256, not a bare SHA-256: a leaked
database full of tokens must not be a fast offline oracle for the secret. There
is no per-value salt to store, so the application salt below is fixed; the
iteration count is what makes guessing the secret expensive.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

# Fernet tokens always start with the base64 of version 0x80.
FERNET_PREFIX = "gAAAAA"
# Application-wide PBKDF2 salt. Changing it makes every stored token
# undecryptable unless the old key is kept in ``_legacy_fernet``.
_KDF_SALT = b"uwp.secretbox.v2"
_KDF_ITERATIONS = 480_000


class SecretUnreadable(ValueError):
    """A stored token cannot be read; ``code`` says why, in the reader's language.

    This module has no ``Settings``, so it cannot pick a language. It used to
    raise a ready-made Russian sentence which ``delivery`` printed verbatim into
    an English chat. ``str()`` stays a developer-facing English line for the
    log; the chat renders ``code`` through the catalogue.

    Still a ``ValueError``: the CLI and the worker already catch that.
    """

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(
            "the stored secret cannot be decrypted: UWP_SECRET_KEY is missing"
            if code == "error.secret.missing_key"
            else "the stored secret cannot be decrypted: UWP_SECRET_KEY is wrong"
        )


@functools.lru_cache(maxsize=32)
def fernet(secret: str) -> Fernet:
    """A Fernet key derived from the settings secret (any length is fine).

    The derivation is deliberately slow; :func:`functools.lru_cache` keeps the
    per-call cost out of hot paths such as proxy resolution per run.
    """
    key = hashlib.pbkdf2_hmac(
        "sha256", secret.encode(), _KDF_SALT, _KDF_ITERATIONS, dklen=32
    )
    return Fernet(base64.urlsafe_b64encode(key))


@functools.lru_cache(maxsize=32)
def _legacy_fernet(secret: str) -> Fernet:
    """The pre-2.0 derivation; kept only to read rows written by older builds."""
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def encrypt(value: str, secret: str) -> str:
    return fernet(secret).encrypt(value.encode()).decode()


def looks_encrypted(value: str) -> bool:
    return value.startswith(FERNET_PREFIX)


def decrypt(value: str, secret: str | None) -> str:
    """Return the plain secret; a plain value is passed through unchanged."""
    if not looks_encrypted(value):
        return value
    if not secret:
        raise SecretUnreadable("error.secret.missing_key")
    try:
        return fernet(secret).decrypt(value.encode()).decode()
    except InvalidToken:
        pass
    # Rows written before the KDF change still use the SHA-256 derivation. A
    # wrong key fails both, so the fallback cannot mask a key mismatch.
    try:
        return _legacy_fernet(secret).decrypt(value.encode()).decode()
    except InvalidToken:
        log.warning("cannot decrypt a stored secret: wrong UWP_SECRET_KEY")
        raise SecretUnreadable("error.secret.wrong_key") from None
