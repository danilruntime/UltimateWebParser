"""Unit tests for proxy-password encryption at rest."""

from __future__ import annotations

import base64
import hashlib

import pytest
from cryptography.fernet import Fernet, InvalidToken

from uwp import secretbox

SECRET = "s" * 32


def test_roundtrip():
    token = secretbox.encrypt("p@ss", SECRET)

    assert token != "p@ss"
    assert secretbox.looks_encrypted(token)
    assert secretbox.decrypt(token, SECRET) == "p@ss"


def test_plain_value_passes_through():
    assert secretbox.decrypt("plain", None) == "plain"
    assert secretbox.decrypt("plain", SECRET) == "plain"


def test_encrypted_value_without_key_is_an_error():
    token = secretbox.encrypt("p@ss", SECRET)

    with pytest.raises(secretbox.SecretUnreadable) as caught:
        secretbox.decrypt(token, None)

    # A code, not a sentence: the reader's language is decided in delivery.
    assert caught.value.code == "error.secret.missing_key"
    # Still a ``ValueError``: the CLI and the worker catch that today.
    assert isinstance(caught.value, ValueError)


def test_wrong_key_is_an_error():
    token = secretbox.encrypt("p@ss", SECRET)

    with pytest.raises(secretbox.SecretUnreadable) as caught:
        secretbox.decrypt(token, "x" * 32)

    assert caught.value.code == "error.secret.wrong_key"
    assert isinstance(caught.value, ValueError)


def test_key_derivation_is_a_kdf_not_one_sha256():
    """A single SHA-256 of the secret is a fast offline oracle for a leaked DB."""
    legacy = Fernet(
        base64.urlsafe_b64encode(hashlib.sha256(SECRET.encode()).digest())
    )

    assert secretbox.fernet(SECRET).encrypt(b"x") != legacy.encrypt(b"x")
    token = secretbox.fernet(SECRET).encrypt(b"x").decode()
    with pytest.raises(InvalidToken):
        legacy.decrypt(token.encode())


def test_old_sha256_ciphertext_still_decrypts():
    """Rotating the KDF must not lock out rows encrypted by the previous release."""
    legacy = Fernet(
        base64.urlsafe_b64encode(hashlib.sha256(SECRET.encode()).digest())
    )
    token = legacy.encrypt(b"old-secret").decode()

    assert secretbox.decrypt(token, SECRET) == "old-secret"
