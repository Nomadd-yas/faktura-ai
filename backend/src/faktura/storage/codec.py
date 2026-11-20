"""Кодирование значений для JSON-столбцов без потерь (Decimal, кортежи) и записей 7 таблиц."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from pydantic import BaseModel

_DEC = "$dec"


def enc(v: Any) -> Any:
    """Python -> JSON-совместимое значение; Decimal -> {"$dec": "..."} (точно, с исходной записью)."""
    if v is None or isinstance(v, (bool, int, float, str)):
        if isinstance(v, Enum):
            return v.value
        return v
    if isinstance(v, Decimal):
        return {_DEC: str(v)}
    if isinstance(v, dict):
        return {str(k): enc(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [enc(x) for x in v]
    if isinstance(v, Enum):
        return enc(v.value)
    if isinstance(v, BaseModel):
        return enc(v.model_dump())
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v)


def dec(v: Any) -> Any:
    """Обратное к enc."""
    if isinstance(v, dict):
        if len(v) == 1 and _DEC in v:
            return Decimal(v[_DEC])
        return {k: dec(x) for k, x in v.items()}
    if isinstance(v, list):
        return [dec(x) for x in v]
    return v


def dump_model(m: BaseModel, *, exclude_defaults: bool = True) -> Any:
    """Модель -> JSON (без полей со значением по умолчанию: они восстанавливаются при чтении)."""
    return enc(m.model_dump(exclude_defaults=exclude_defaults))


def load_model(cls: type[BaseModel], data: Any) -> Any:
    return cls.model_validate(dec(data))


# --- числа фактов --------------------------------------------------------------------------------

def to_decimal(v: Any, *, field: str = "value") -> Decimal | None:
    """None/Decimal/int/float/str -> Decimal; иначе ValueError."""
    if v is None:
        return None
    if isinstance(v, bool):
        raise ValueError(f"{field}: ожидается число")
    if isinstance(v, Decimal):
        d = v
    elif isinstance(v, int):
        d = Decimal(v)
    elif isinstance(v, float):
        d = Decimal(repr(v))
    elif isinstance(v, str):
        try:
            d = Decimal(v.strip().replace(",", "."))
        except InvalidOperation as e:
            raise ValueError(f"{field}: не число «{v}»") from e
    else:
        raise ValueError(f"{field}: ожидается число")
    if not d.is_finite():
        raise ValueError(f"{field}: не конечное число")
    return d


def dec_to_str(d: Decimal | None) -> str | None:
    return None if d is None else str(d)


def str_to_dec(s: str | None) -> Decimal | None:
    return None if s is None else Decimal(s)


def dec_to_float(d: Decimal | None) -> float | None:
    return None if d is None else float(d)
