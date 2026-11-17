"""Encrypts the API keys saved on the Settings page, so Postgres only ever holds ciphertext.

The master key is TRIPWIRE_SECRET_KEY: 32 random bytes as 64 hex characters (`openssl rand -hex 32`),
the same format as Laminar's own AEAD_SECRET_KEY. On AWS, Terraform generates it in
infra/persistent/secrets.tf, so a destroy + restore keeps the value the saved keys were encrypted with.

Fernet (from `cryptography`) does the work: AES with an authentication check, a fresh random IV per
value, and a versioned format, so a value can't be read or changed without the master key.

Two errors, both with a message the API can show as-is:
  * SecretKeyError: TRIPWIRE_SECRET_KEY is missing or malformed (nothing can be saved or read).
  * UnreadableError: a stored value doesn't decrypt with this master key (it was saved with
    another one, or the bytes were changed).
"""

import base64
import os
import re

from cryptography.fernet import Fernet, InvalidToken

ENV_NAME = "TRIPWIRE_SECRET_KEY"
_HEX_KEY = re.compile(r"[0-9a-fA-F]{64}")


class SecretKeyError(Exception):
    """TRIPWIRE_SECRET_KEY is missing or isn't 64 hex characters."""


class UnreadableError(Exception):
    """A stored value doesn't decrypt with the current TRIPWIRE_SECRET_KEY."""


def _fernet(environ=os.environ):
    secret = environ.get(ENV_NAME, "").strip()
    if not secret:
        raise SecretKeyError(f"{ENV_NAME} isn't set on the server, so keys can't be saved or read. "
                             "Add one to .env (openssl rand -hex 32), or redeploy on AWS.")
    if not _HEX_KEY.fullmatch(secret):
        raise SecretKeyError(f"{ENV_NAME} must be 64 hex characters (openssl rand -hex 32).")
    return Fernet(base64.urlsafe_b64encode(bytes.fromhex(secret)))


def encrypt(plaintext, environ=os.environ):
    """The stored form of `plaintext` (bytes for a bytea column)."""
    return _fernet(environ).encrypt(plaintext.encode())


def decrypt(stored, environ=os.environ):
    """The plaintext of a value made by encrypt()."""
    fernet = _fernet(environ)
    try:
        return fernet.decrypt(bytes(stored)).decode()
    except InvalidToken as error:
        raise UnreadableError(f"a saved key can't be decrypted with this {ENV_NAME}") from error
