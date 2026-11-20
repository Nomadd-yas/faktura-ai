"""Разбор ответа ML-1 по spec/07 §7.0.1 (план 5.2, REQ-064, К-1). Единственное место, где разбирается этот формат.

Конверт: {"model_version", "publication": {...}, "materials": [...], "devices": [...], "layers": [...],
"measurements": [...], "results": [...], "issues": [...]} - строки 7 таблиц сдачи плюс служебные
`_bbox` ([x0, y0, x1, y1] в пунктах PDF), `_page`, `_confidence`.

Правила:
- невалидный конверт (не объект, нет объекта `publication` с `publication_id`) -> ExtractError("ml_schema_mismatch");
- битая запись (не объект, нет id, поле неверного типа - объект вместо числа и т. п.) отбрасывается с warning;
- текст в числовом поле («1,5», «63-68») сохраняется строкой с пометкой в `meta.notes_backend` - его чистит sanitize;
- `metric_value`, `metric_unit`, `normalized_value`, `normalized_unit` от ML отбрасываются (их считает бэк, REQ-028);
  если ML прислал только `metric_value` без `raw_value`, значение переносится в `raw_value` с пометкой;
- `spread_value` от ML СОХРАНЯЕТСЯ;
- `publication_id` во всех строках - тот, что передал бэк (имя файла без расширения);
- `_bbox/_page/_confidence` -> RecordMeta; прочие поля вне шаблона -> `meta.extra` (unmapped_values);
- `ml_order` - порядок принятых записей в массиве ответа; замечания ML получают `computed_by="ml"`.
"""
from __future__ import annotations

import copy
import math
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from ..domain.errors import ExtractError
from ..domain.extraction import ExtractOutcome
from ..domain.ids import issue_key
from ..domain.io import parse_cell
from ..domain.records import ROW_MODELS, PaperBundle, Publication, RecordMeta
from ..domain.tables import BOOL_FIELDS, ID_COLUMN, INTEGER_FIELDS, LIST_FIELDS, NUMERIC_FIELDS, TABLES

ROW_TABLES: tuple[str, ...] = ("materials", "devices", "layers", "measurements", "results", "issues")
# поля results, которые заполняет только бэк (нормализация, REQ-028)
BACKEND_RESULT_FIELDS: frozenset[str] = frozenset({"metric_value", "metric_unit", "normalized_value", "normalized_unit"})
META_KEYS = ("_bbox", "_page", "_confidence")
UNKNOWN_MODEL_VERSION = "unknown"


class _Bad(Exception):
    """Запись не может быть принята."""


def _schema_error(message: str) -> ExtractError:
    return ExtractError("ml_schema_mismatch", f"Ответ ML не соответствует контракту §7.0.1: {message}")


# ---------------------------------------------------------------------------------------------
# приведение значений ячеек
# ---------------------------------------------------------------------------------------------

def _number(table: str, col: str, v: Any) -> Decimal | int:
    if isinstance(v, float):
        if not math.isfinite(v):
            raise _Bad(f"{col}: не число ({v})")
        d = Decimal(repr(v))
    else:
        d = Decimal(int(v))
    if (table, col) in INTEGER_FIELDS and d == d.to_integral_value():
        return int(d)
    return d


def coerce_cell(table: str, col: str, v: Any, notes: list[str]) -> Any:
    """Значение JSON -> значение поля модели записи. Неверный тип -> _Bad (запись отбрасывается)."""
    key = (table, col)
    if v is None:
        return [] if key in LIST_FIELDS else None
    if isinstance(v, (dict,)):
        raise _Bad(f"{col}: объект вместо значения")
    if key in LIST_FIELDS:
        if isinstance(v, str):
            return parse_cell(table, col, v)
        if isinstance(v, list):
            out: list[str] = []
            for x in v:
                if x is None:
                    continue
                if isinstance(x, (dict, list)):
                    raise _Bad(f"{col}: вложенный объект в списке")
                s = str(x).strip()
                if s:
                    out.append(s)
            return out
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return [str(v)]
        raise _Bad(f"{col}: ожидается список")
    if isinstance(v, list):
        raise _Bad(f"{col}: список вместо значения")
    if key in BOOL_FIELDS:
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)) and v in (0, 1):
            return bool(v)
        if isinstance(v, str):
            r = parse_cell(table, col, v)
            if isinstance(r, str):
                notes.append(f"{col}: не разобрано «{r}»")
            return r
        raise _Bad(f"{col}: ожидается true/false")
    if key in NUMERIC_FIELDS:
        if isinstance(v, bool):
            raise _Bad(f"{col}: true/false вместо числа")
        if isinstance(v, (int, float)):
            return _number(table, col, v)
        if isinstance(v, str):
            r = parse_cell(table, col, v)
            if isinstance(r, str):
                notes.append(f"{col}: не разобрано «{r}»")
            return r
        raise _Bad(f"{col}: ожидается число")
    # текстовое поле
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if not math.isfinite(v):
            raise _Bad(f"{col}: не число ({v})")
        return format(Decimal(repr(v)), "f") if "e" in repr(v).lower() else repr(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return None if v.strip() == "" else v
    raise _Bad(f"{col}: неподдерживаемый тип {type(v).__name__}")


def _confidence(v: Any, where: str, warnings: list[str]) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        warnings.append(f"{where}: _confidence не число - пропущено")
        return None
    try:
        f = float(v)
    except ValueError:
        warnings.append(f"{where}: _confidence не число - пропущено")
        return None
    if not math.isfinite(f) or f < 0 or f > 1:
        warnings.append(f"{where}: _confidence={v} вне 0..1 - пропущено")
        return None
    return f


def _bbox(v: Any, where: str, warnings: list[str]) -> tuple[float, float, float, float] | None:
    if v is None:
        return None
    if isinstance(v, dict):  # допускаем и {x0,y0,x1,y1}
        v = [v.get("x0"), v.get("y0"), v.get("x1"), v.get("y1")]
    if not isinstance(v, (list, tuple)) or len(v) != 4:
        warnings.append(f"{where}: _bbox не [x0, y0, x1, y1] - пропущено")
        return None
    try:
        vals = tuple(float(x) for x in v if not isinstance(x, bool))
    except (TypeError, ValueError):
        vals = ()
    if len(vals) != 4 or not all(math.isfinite(x) for x in vals):
        warnings.append(f"{where}: _bbox не [x0, y0, x1, y1] - пропущено")
        return None
    return vals  # type: ignore[return-value]


def _page(v: Any, where: str, warnings: list[str]) -> int | None:
    if v is None:
        return None
    try:
        if isinstance(v, bool):
            raise ValueError
        n = int(float(v))
    except (TypeError, ValueError, OverflowError):
        warnings.append(f"{where}: _page={v!r} не номер страницы - пропущено")
        return None
    if n < 1:
        warnings.append(f"{where}: _page={n} меньше 1 - пропущено")
        return None
    return n


def _int_page(v: Any) -> int | None:
    if isinstance(v, int) and not isinstance(v, bool):
        return v if v >= 1 else None
    if isinstance(v, Decimal):
        try:
            return int(v) if v == v.to_integral_value() and v >= 1 else None
        except (InvalidOperation, ValueError):
            return None
    return None


# ---------------------------------------------------------------------------------------------
# записи
# ---------------------------------------------------------------------------------------------

def _build_record(table: str, obj: Any, publication_id: str, order: int, where: str, warnings: list[str]):
    if not isinstance(obj, dict):
        raise _Bad("запись не объект")
    cols = TABLES[table]
    values: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    notes: list[str] = []
    for k, v in obj.items():
        if not isinstance(k, str):
            continue
        if k in META_KEYS:
            continue
        if k == "publication_id":
            if v not in (None, "") and str(v) != publication_id:
                notes.append(f"publication_id от ML «{v}» заменён на «{publication_id}»")
            continue
        if table == "results" and k in BACKEND_RESULT_FIELDS:
            continue
        if k in cols:
            values[k] = coerce_cell(table, k, v, notes)
        else:
            extra[k] = copy.deepcopy(v)

    id_col = ID_COLUMN[table]
    if table == "issues":
        if not values.get("check_name"):
            raise _Bad("нет check_name")
        values["check_name"] = str(values["check_name"]).strip()
        values["issue_id"] = str(values.get("issue_id") or "").strip()
    else:
        rid = values.get(id_col)
        if rid is None or str(rid).strip() == "":
            raise _Bad(f"нет {id_col}")
        values[id_col] = str(rid).strip()

    if table == "results" and values.get("raw_value") in (None, ""):
        mv = obj.get("metric_value")
        if mv not in (None, "") and not isinstance(mv, (dict, list, bool)):
            values["raw_value"] = str(mv)
            if values.get("raw_unit") in (None, "") and isinstance(obj.get("metric_unit"), str):
                values["raw_unit"] = obj["metric_unit"]
            notes.append("raw_value взят из metric_value ML")

    meta = RecordMeta(
        ml_order=order,
        confidence=_confidence(obj.get("_confidence"), where, warnings),
        page=_page(obj.get("_page"), where, warnings),
        bbox=_bbox(obj.get("_bbox"), where, warnings),
        notes_backend=notes,
        extra=extra,
    )
    if meta.page is None and table in ("results", "issues"):
        meta.page = _int_page(values.get("source_page"))
    try:
        if table == "issues":
            rec = ROW_MODELS[table](publication_id=publication_id, meta=meta, computed_by="ml",
                                    confidence=meta.confidence, **values)
            rec.key = issue_key(rec.check_name, rec.record_ids, {"description": rec.description or ""})
        else:
            rec = ROW_MODELS[table](publication_id=publication_id, meta=meta, **values)
    except ValidationError as e:
        first = e.errors()[0] if e.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", ()))
        raise _Bad(f"{loc}: {first.get('msg', 'неверное значение')}") from e
    return rec


def _build_publication(obj: dict, publication_id: str, warnings: list[str]) -> Publication:
    values: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    notes: list[str] = []
    for k, v in obj.items():
        if not isinstance(k, str) or k in META_KEYS or k == "publication_id":
            continue
        if k in TABLES["publications"]:
            try:
                values[k] = coerce_cell("publications", k, v, notes)
            except _Bad as e:  # конверт не отбрасываем: пустим поле
                warnings.append(f"publication: {e} - поле пропущено")
        else:
            extra[k] = copy.deepcopy(v)
    ml_pid = obj.get("publication_id")
    if str(ml_pid).strip() != publication_id:
        notes.append(f"publication_id от ML «{ml_pid}» заменён на «{publication_id}»")
    meta = RecordMeta(ml_order=0, confidence=_confidence(obj.get("_confidence"), "publication", warnings),
                      page=_page(obj.get("_page"), "publication", warnings),
                      bbox=_bbox(obj.get("_bbox"), "publication", warnings), notes_backend=notes, extra=extra)
    try:
        return Publication(publication_id=publication_id, meta=meta, **values)
    except ValidationError:
        warnings.append("publication: поля не прошли проверку - оставлен только publication_id")
        return Publication(publication_id=publication_id, meta=meta)


def parse_extract_response(data: dict, publication_id: str) -> ExtractOutcome:
    """Битая запись -> warning и пропуск; невалидный конверт -> ExtractError('ml_schema_mismatch').
    _bbox/_page/_confidence -> RecordMeta; поля вне шаблона -> meta.extra; ml_order - порядок в ответе.
    metric_value/metric_unit/normalized_* от ML отбрасываются (считает бэк, REQ-028); spread_value СОХРАНЯЕТСЯ."""
    if not isinstance(data, dict):
        raise _schema_error("ответ не JSON-объект")
    pub = data.get("publication")
    if not isinstance(pub, dict):
        raise _schema_error("нет объекта publication")
    ml_pid = pub.get("publication_id")
    if ml_pid is None or isinstance(ml_pid, (dict, list, bool)) or str(ml_pid).strip() == "":
        raise _schema_error("нет publication.publication_id")
    pid = str(publication_id or "").strip() or str(ml_pid).strip()

    warnings: list[str] = []
    mv = data.get("model_version")
    if isinstance(mv, (str, int, float)) and not isinstance(mv, bool) and str(mv).strip():
        model_version = str(mv).strip()
    else:
        model_version = UNKNOWN_MODEL_VERSION
        warnings.append("в ответе ML нет model_version")

    bundle = PaperBundle(publication=_build_publication(pub, pid, warnings))
    for table in ROW_TABLES:
        arr = data.get(table)
        if arr is None:
            continue
        if not isinstance(arr, list):
            warnings.append(f"{table}: ожидается массив, получено {type(arr).__name__} - таблица пропущена")
            continue
        kept = getattr(bundle, table)
        for i, obj in enumerate(arr):
            where = f"{table}[{i}]"
            try:
                kept.append(_build_record(table, obj, pid, len(kept), where, warnings))
            except _Bad as e:
                rid = obj.get(ID_COLUMN[table]) if isinstance(obj, dict) else None
                warnings.append(f"{where}{f' ({rid})' if rid else ''}: запись отброшена - {e}")

    ml_warnings = data.get("warnings")
    if isinstance(ml_warnings, list):
        warnings.extend(f"ML: {w}" for w in ml_warnings if isinstance(w, str) and w.strip())

    return ExtractOutcome(bundle=bundle, model_version=model_version, warnings=warnings, raw=copy.deepcopy(data))
