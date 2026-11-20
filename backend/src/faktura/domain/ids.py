"""Идентификаторы и ключи (план 4.3, К-6)."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import Any, Iterable


def new_id(prefix: str) -> str:
    """Случайный идентификатор: prefix + '_' + 12 символов base32."""
    raw = base64.b32encode(secrets.token_bytes(8)).decode().lower().rstrip("=")
    return f"{prefix}_{raw[:12]}"


def stable_id(prefix: str, *parts: object) -> str:
    """Детерминированный идентификатор (sha256 -> base32) для iss_ и anc_."""
    h = hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).digest()
    return f"{prefix}_{base64.b32encode(h).decode().lower().rstrip('=')[:12]}"


def sha8(obj: Any) -> str:
    data = obj if isinstance(obj, bytes) else json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False).encode()
    return hashlib.sha256(data).hexdigest()[:8]


def issue_key(check_name: str, record_ids: Iterable[str], params: dict | None = None) -> str:
    """Внутренний ключ замечания I-... (К-6): от check_name и множества record_ids;
    при пустых record_ids - от параметров."""
    ids = sorted(set(record_ids))
    basis = ids if ids else sorted((params or {}).items(), key=lambda kv: kv[0])
    return "I-" + sha8([check_name, basis])
