"""Строгая проверка ответа модели после разбора конверта (spec BACKEND_ONLY §4.3, шаг 2).

Тяжёлые нарушения (дубль id в таблице, запись без ключевого id) -> `ExtractError("ml_invalid_data")` с понятной
русской причиной. Лёгкие - запись остаётся, плохая ячейка очищается, в `warnings` попадает пояснение:
* значение вне словаря (кроме `layer_function` - его приводит нормализация, и логических полей);
* висячая ссылка на прибор/материал/измерение/результат;
* `duplicate_of` на себя или по кругу.
Сырой ответ (`ExtractOutcome.raw`) не меняется.
"""
from __future__ import annotations

import logging
from typing import Any

from ..domain.errors import ExtractError
from ..domain.records import PaperBundle
from ..domain.tables import BOOL_FIELDS, ID_COLUMN, VOCAB

log = logging.getLogger("faktura.extractor")

INVALID_CODE = "ml_invalid_data"
INVALID_PREFIX = "Модель вернула некорректные данные"
MAX_SHOWN = 3

# (таблица, колонка, целевая таблица) - одиночные ссылки
_REFS: tuple[tuple[str, str, str], ...] = (
    ("devices", "absorber_material_id", "materials"),
    ("layers", "device_id", "devices"),
    ("results", "device_id", "devices"),
    ("results", "measurement_id", "measurements"),
    ("results", "material_id", "materials"),
)
_TABLE_RU = {"materials": "материалы", "devices": "приборы", "layers": "слои", "measurements": "измерения",
             "results": "результаты", "issues": "замечания"}
_SKIP_VOCAB = {("layers", "layer_function")} | set(BOOL_FIELDS)


def invalid_error(problems: list[str]) -> ExtractError:
    """ExtractError с публичным текстом «Модель вернула некорректные данные: ...» (без технических терминов)."""
    shown = "; ".join(problems[:MAX_SHOWN])
    if len(problems) > MAX_SHOWN:
        shown += f" (и ещё {len(problems) - MAX_SHOWN})"
    log.warning("extractor %s: %s", INVALID_CODE, "; ".join(problems))
    return ExtractError(INVALID_CODE, f"{INVALID_PREFIX}: {shown}", details={"technical": "; ".join(problems)})


def _note(rec: Any, text: str) -> None:
    try:
        rec.meta.notes_backend.append(text)
    except AttributeError:
        pass


def validate_bundle(bundle: PaperBundle, warnings: list[str], severe: list[str] | None = None) -> None:
    """Проверяет и чинит `bundle` на месте. Тяжёлые нарушения (вместе с `severe` от разбора) -> ExtractError."""
    problems: list[str] = list(severe or [])
    ids: dict[str, set[str]] = {}
    for table, id_col in ID_COLUMN.items():
        if table == "publications":
            continue
        seen: set[str] = set()
        dup: list[str] = []
        for rec in getattr(bundle, table):
            rid = str(getattr(rec, id_col, "") or "").strip()
            if not rid:
                continue  # пустой issue_id допустим: бэк присвоит сам
            if rid in seen and rid not in dup:
                dup.append(rid)
            seen.add(rid)
        for rid in dup:
            problems.append(f"в таблице «{_TABLE_RU[table]}» повторяется идентификатор {rid}")
        ids[table] = seen
    if problems:
        raise invalid_error(problems)

    # словари
    for table in ("materials", "devices", "layers", "measurements", "results"):
        for rec in getattr(bundle, table):
            for (t, col), allowed in VOCAB.items():
                if t != table or (t, col) in _SKIP_VOCAB:
                    continue
                v = getattr(rec, col, None)
                if v is None or v == "" or isinstance(v, bool):
                    continue
                s = str(v).strip()
                if s in allowed:
                    if s != v:
                        setattr(rec, col, s)
                    continue
                fixed = next((a for a in sorted(allowed) if a.casefold() == s.casefold()), None)
                setattr(rec, col, fixed)
                where = f"{table}.{col} ({getattr(rec, ID_COLUMN[table])})"
                _note(rec, f"{col}: исходно «{v}»")
                if fixed is None:
                    warnings.append(f"{where}: значение «{v}» вне словаря - ячейка очищена")
                    log.info("ответ модели: %s=%r вне словаря, очищено", where, v)

    # висячие ссылки
    for table, col, target in _REFS:
        known = ids.get(target, set())
        for rec in getattr(bundle, table):
            v = getattr(rec, col, None)
            if v is None or v == "":
                continue
            if str(v).strip() not in known:
                setattr(rec, col, None)
                _note(rec, f"{col}: ссылка «{v}» не найдена - очищена")
                warnings.append(f"{table}.{col} ({getattr(rec, ID_COLUMN[table])}): ссылка «{v}» не найдена - очищена")
    known_devices = ids.get("devices", set())
    for m in bundle.measurements:
        bad = [d for d in (m.device_id or []) if d not in known_devices]
        if bad:
            m.device_id = [d for d in m.device_id if d in known_devices]
            _note(m, f"device_id: ссылки {', '.join(bad)} не найдены - убраны")
            warnings.append(f"measurements.device_id ({m.measurement_id}): ссылки не найдены - убраны: "
                            f"{', '.join(bad)}")

    # duplicate_of: на существующий результат, без петель и циклов
    by_id = {r.result_id: r for r in bundle.results}
    for r in bundle.results:
        dup = r.duplicate_of
        if not dup:
            continue
        if dup not in by_id:
            r.duplicate_of = None
            _note(r, f"duplicate_of: ссылка «{dup}» не найдена - очищена")
            warnings.append(f"results.duplicate_of ({r.result_id}): ссылка «{dup}» не найдена - очищена")
        elif dup == r.result_id:
            r.duplicate_of = None
            warnings.append(f"results.duplicate_of ({r.result_id}): ссылка на саму запись - очищена")
    for r in bundle.results:
        chain = [r.result_id]
        cur = r.duplicate_of
        while cur:
            if cur in chain:
                r.duplicate_of = None
                warnings.append(f"results.duplicate_of ({r.result_id}): цикл ссылок - очищена")
                break
            chain.append(cur)
            cur = by_id[cur].duplicate_of if cur in by_id else None
