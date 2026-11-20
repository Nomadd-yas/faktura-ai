"""Хеширование паролей (argon2id). Общее для storage.seed_admin, CLI users и api_base."""
from __future__ import annotations

import secrets
import string

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from .errors import AppError

MIN_PASSWORD_LEN = 8
_ph = PasswordHasher()


def validate_password(p: str) -> None:
    if not isinstance(p, str) or len(p) < MIN_PASSWORD_LEN:
        raise AppError("validation_error", f"Пароль короче {MIN_PASSWORD_LEN} символов", field="password")


def hash_password(p: str) -> str:
    validate_password(p)
    return _ph.hash(p)


def verify_password(p: str, h: str) -> bool:
    try:
        return _ph.verify(h, p)
    except (VerificationError, InvalidHashError):
        return False


def generate_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(16))
