"""Tests for tripwire/crypto.py: saved keys are encrypted with TRIPWIRE_SECRET_KEY."""

import pytest

from tripwire import crypto

SECRET = {"TRIPWIRE_SECRET_KEY": "ab" * 32}
OTHER_SECRET = {"TRIPWIRE_SECRET_KEY": "cd" * 32}


def test_crypto_round_trips_a_key():
    stored = crypto.encrypt("sk-proj-1234567890", SECRET)
    assert crypto.decrypt(stored, SECRET) == "sk-proj-1234567890"


def test_crypto_never_stores_the_plaintext():
    stored = crypto.encrypt("sk-proj-1234567890", SECRET)
    assert b"sk-proj-1234567890" not in stored


def test_crypto_encrypts_the_same_key_differently_each_time():
    assert crypto.encrypt("same-key", SECRET) != crypto.encrypt("same-key", SECRET)


def test_crypto_accepts_bytes_like_values_from_postgres():
    stored = memoryview(crypto.encrypt("lmnr-key", SECRET))
    assert crypto.decrypt(stored, SECRET) == "lmnr-key"


def test_crypto_with_another_secret_key_raises_unreadable():
    stored = crypto.encrypt("lmnr-key", SECRET)
    with pytest.raises(crypto.UnreadableError, match="TRIPWIRE_SECRET_KEY"):
        crypto.decrypt(stored, OTHER_SECRET)


def test_crypto_with_changed_bytes_raises_unreadable():
    stored = bytearray(crypto.encrypt("lmnr-key", SECRET))
    middle = len(stored) // 2
    stored[middle] = ord("A") if stored[middle] != ord("A") else ord("B")  # still valid base64
    with pytest.raises(crypto.UnreadableError):
        crypto.decrypt(bytes(stored), SECRET)


@pytest.mark.parametrize("environ", [{}, {"TRIPWIRE_SECRET_KEY": "  "}])
def test_crypto_without_a_secret_key_explains_how_to_set_one(environ):
    with pytest.raises(crypto.SecretKeyError, match="openssl rand -hex 32"):
        crypto.encrypt("lmnr-key", environ)


@pytest.mark.parametrize("secret", ["too-short", "ab" * 31, "zz" * 32, "ab" * 33])
def test_crypto_rejects_a_secret_key_that_is_not_64_hex_characters(secret):
    with pytest.raises(crypto.SecretKeyError, match="64 hex characters"):
        crypto.encrypt("lmnr-key", {"TRIPWIRE_SECRET_KEY": secret})


def test_crypto_accepts_uppercase_hex_and_surrounding_spaces():
    environ = {"TRIPWIRE_SECRET_KEY": f"  {'AB' * 32}\n"}
    assert crypto.decrypt(crypto.encrypt("lmnr-key", environ), SECRET) == "lmnr-key"
