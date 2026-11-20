"""Курсор keyset-пагинации (этап B).

Формат: base64url (без '=') от JSON-массива значений. Типы JSON сохраняются (str/int/float/bool/null);
Decimal кодируется как {"$dec": "..."}. Курсор непрозрачен для клиента; storage дополнительно кладёт
в него подпись сортировки, чтобы курсор от другой сортировки отвергался.
"""
from __future__ import annotations

import base64
import binascii
import json
from decimal import Decimal
from typing import Any, Sequence

from ..domain.errors import AppError

_BAD = "Некорректный курсор"


def _enc(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float, str)):
        if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
            raise ValueError("курсор: нечисловое значение float")
        return v
    if isinstance(v, Decimal):
        return {"$dec": str(v)}
    raise TypeError(f"курсор: тип {type(v).__name__} не поддерживается")


def _dec(v: Any) -> Any:
    if isinstance(v, dict):
        if set(v) == {"$dec"} and isinstance(v["$dec"], str):
            return Decimal(v["$dec"])
        raise ValueError(_BAD)
    if isinstance(v, list):
        raise ValueError(_BAD)
    return v


def encode_cursor(values: Sequence) -> str:
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise TypeError("курсор: ожидается последовательность значений")
    payload = json.dumps([_enc(v) for v in values], ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(s: str) -> tuple:
    if not isinstance(s, str) or not s or len(s) > 8192:
        raise AppError("validation_error", _BAD, field="cursor")
    try:
        raw = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, list):
            raise ValueError(_BAD)
        return tuple(_dec(v) for v in data)
    except (binascii.Error, ValueError, UnicodeDecodeError) as e:
        raise AppError("validation_error", _BAD, field="cursor") from e
