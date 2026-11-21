"""Поток NDJSON ответа ML-1 (spec BACKEND_ONLY §10: «принимать потоком и писать пачками»; docs/design/ML_NDJSON.md).

Запрос: `Accept: application/x-ndjson` (и поле формы `stream=ndjson`). Ответ - `Content-Type: application/x-ndjson`,
каждая строка - JSON-объект с полем `type`:

    {"type":"header","protocol":"ndjson/1","ok":true,"model_version":"...","publication_id":"...", "meta":{...}, ...}
    {"type":"table","table":"results","rows":[... до 1000 строк ...]}      (сколько нужно, порядок таблиц любой)
    {"type":"end","counts":{"results":28000,...},"ok":true}
  либо вместо `end` - ошибка (после первой строки статус HTTP уже 200):
    {"type":"error","code":"ml_error","message":"...","retryable":false,"detail":"..."}

Поток без `end` и без `error` - обрыв (`StreamTruncated`, ретраится как потеря связи). Счётчики `end.counts`
сверяются с принятыми строками. Обычный JSON-ответ остаётся режимом по умолчанию на стороне ML-1; клиент понимает оба
(выбор - по `Content-Type` ответа).
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Iterator

NDJSON_TYPE = "application/x-ndjson"
PROTOCOL = "ndjson/1"
ROWS_PER_LINE = 1000
# служебные ключи заголовка, не попадающие в тело ответа
_HEADER_ONLY = frozenset({"type", "protocol", "layout", "has_dataset"})
_FLAT_TABLES = ("publications", "materials", "devices", "layers", "measurements", "results", "issues")


class StreamError(Exception):
    """Ошибка внутри потока (строка `{"type":"error",...}`)."""

    def __init__(self, code: str, message: str, retryable: bool = False, detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = bool(retryable)
        self.detail = detail


class StreamTruncated(Exception):
    """Поток закончился без строки `end`."""


class StreamInvalid(Exception):
    """Нарушен формат потока (не JSON, нет заголовка, не совпали счётчики)."""


def _line(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str) + "\n").encode("utf-8")


def events_from_raw(raw: dict, rows_per_line: int = ROWS_PER_LINE) -> Iterator[bytes]:
    """Сырой ответ контракта §7.0.1 (плоский: `publication` + таблицы, либо `tables: {...}`) -> строки NDJSON."""
    nested = isinstance(raw.get("tables"), dict)
    if nested:
        tables = {t: rows for t, rows in raw["tables"].items() if isinstance(rows, list)}
        head = {k: v for k, v in raw.items() if k != "tables"}
    else:
        tables = {t: raw[t] for t in _FLAT_TABLES[1:] if isinstance(raw.get(t), list)}
        head = {k: v for k, v in raw.items() if k not in _FLAT_TABLES and k != "publication"}
        if isinstance(raw.get("publication"), dict):
            tables = {"publications": [raw["publication"]], **tables}
    try:
        yield _line({"type": "header", "protocol": PROTOCOL, "layout": "nested" if nested else "flat", **head})
        counts: dict[str, int] = {}
        for table, rows in tables.items():
            counts[table] = len(rows)
            for i in range(0, len(rows), rows_per_line):
                yield _line({"type": "table", "table": table, "rows": rows[i:i + rows_per_line]})
        yield _line({"type": "end", "counts": counts})
    except Exception as e:  # noqa: BLE001 - заголовок ответа уже отправлен: ошибка идёт строкой потока
        msg = f"{type(e).__name__}: {e}"
        yield _line({"type": "error", "code": "ml_error", "message": msg, "retryable": False, "detail": msg})


def read_stream(lines: Iterable[str | bytes]) -> tuple[dict, dict[str, list], dict[str, int]]:
    """Читает строки потока -> (заголовок, {таблица: [строки]}, counts из `end`).

    Строки таблиц складываются по мере прихода (клиент не держит весь ответ одной строкой/объектом).
    Бросает StreamError (строка error), StreamTruncated (нет end), StreamInvalid (формат)."""
    header: dict | None = None
    tables: dict[str, list] = {}
    counts: dict[str, int] | None = None
    for raw_line in lines:
        if isinstance(raw_line, bytes):
            raw_line = raw_line.decode("utf-8")
        if not raw_line.strip():
            continue  # пустые строки (keep-alive) допустимы
        try:
            ev = json.loads(raw_line)
        except ValueError as e:
            raise StreamInvalid(f"строка потока не JSON: {e}") from e
        if not isinstance(ev, dict):
            raise StreamInvalid("строка потока не объект")
        kind = ev.get("type")
        if kind == "error":
            raise StreamError(str(ev.get("code") or "ml_error"),
                              str(ev.get("message") or ev.get("detail") or "ошибка в потоке ответа ML-1"),
                              bool(ev.get("retryable")), ev.get("detail"))
        if counts is not None:
            raise StreamInvalid("данные после строки end")
        if kind == "header":
            if header is not None:
                raise StreamInvalid("повторный заголовок потока")
            header = ev
        elif header is None:
            raise StreamInvalid("поток начинается не с заголовка")
        elif kind == "table":
            name, rows = ev.get("table"), ev.get("rows")
            if not isinstance(name, str) or not isinstance(rows, list):
                raise StreamInvalid("строка table без имени таблицы или списка rows")
            tables.setdefault(name, []).extend(rows)
        elif kind == "end":
            c = ev.get("counts")
            counts = {str(k): int(v) for k, v in c.items()} if isinstance(c, dict) else {}
        # неизвестные типы строк игнорируются (расширяемость)
    if header is None:
        raise StreamTruncated("пустой поток ответа")
    if counts is None:
        raise StreamTruncated("поток оборван: нет строки end")
    for name, n in counts.items():
        got = len(tables.get(name, ()))
        if got != n:
            raise StreamInvalid(f"таблица {name}: принято {got} строк, в end.counts - {n}")
    return header, tables, counts


def assemble_body(header: dict, tables: dict[str, list], protocol: str) -> dict:
    """Тело, эквивалентное обычному JSON-ответу данного протокола (его и разбирают существующие адаптеры)."""
    body = {k: v for k, v in header.items() if k not in _HEADER_ONLY}
    if protocol == "pdf_dataset":
        body["dataset"] = tables if header.get("has_dataset", True) else None
        return body
    if header.get("layout") == "nested":
        body["tables"] = tables
        return body
    pubs = tables.get("publications")
    if pubs:
        body["publication"] = pubs[0]
    for t, rows in tables.items():
        if t != "publications":
            body[t] = rows
    return body
