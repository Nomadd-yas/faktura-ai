"""Нормализация записей bundle только через протокол Normalizer (план 5.6; spec/06; REQ-028, REQ-035, REQ-037, REQ-038).

Порядок шагов (каждый покрыт тестом `tests/pipeline/test_normalize.py`):
1. `results.metric_name` <- `match_metric`, если имя не каноническое; исходное имя - в `meta.norm_trace`.
2. `results`: `normalize(metric_name, raw_value, raw_unit)` -> `normalized_*`; `metric_*` = `normalized_*`;
   `failed` -> пусто и `meta.norm_issue`; строки без `raw_value` не трогаются.
3. `spread_value`: значение ML не затирается; `±` заполняет только пустой разброс; расхождение - в
   `meta.notes_backend`; единицы разброса не пересчитываются.
4. `materials`: `bandgap_value` через `normalize_field`; `formula_normalized` <- `canon_material`, если пусто.
5. `layers`: `layer_thickness` через `normalize_field`; `layer_function` вне словаря <- `layer_function()`,
   исходное значение - в `note`.
6. `devices`: `active_area` через `normalize_field`; `encapsulation` <- `normalize_encapsulation` (К-7).
7. `measurements`: `spectrum` <- `normalize_spectrum`; `light_intensity` - число.
8. Единица только при значении (`UNIT_FOR_VALUE`).

Функция идемпотентна: повторный вызов на результате ничего не меняет. Вход не меняется (работа на копии).
Нераспознанное поле (`failed`) остаётся как есть: его приводит или очищает санитайзер сдачи.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from ..domain.ports import NormResult, Normalizer
from ..domain.profile import ExtractionProfile
from ..domain.records import NormStatus, PaperBundle, Result, TraceStep
from ..domain.tables import EVAL_METRICS, TARGET_UNITS, UNIT_FOR_VALUE, VOCAB

MATCH_STEP = "Показатель"
_OK_STATUSES = ("ok", "converted", "passthrough")


def _text(v: Any) -> str | None:
    """Значение ячейки -> текст для нормализатора (Decimal без экспоненты)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, Decimal):
        if not v.is_finite():
            return str(v)
        s = format(v, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s or "0"
    s = str(v).strip()
    return s or None


def _keep_source(source: Any, normalized: Decimal | None, *more: Any) -> Decimal | None:
    """Значение не менялось - запись как в источнике (71.0 остаётся 71.0, без нормализации хвостовых нулей).
    Сравниваются source и more (например, уже заданные ML normalized_value / metric_value)."""
    if isinstance(normalized, Decimal):
        for cand in (source, *more):
            if isinstance(cand, (Decimal, str)):
                d = _dec(cand)
                if d is not None and d == normalized:
                    return d
    return normalized


def _empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, list) and not v)


def _dec(v: Any) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, Decimal):
        return v if v.is_finite() else None
    if isinstance(v, int):
        return Decimal(v)
    try:
        d = Decimal(str(v).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _append_note(current: str | None, note: str | None) -> str | None:
    if not note:
        return current
    if current and note in current:
        return current
    return f"{current}; {note}" if current else note


def _add_backend_note(meta, note: str) -> None:
    if note not in meta.notes_backend:
        meta.notes_backend.append(note)


def _canonical_metrics(profile: ExtractionProfile) -> set[str]:
    return set(profile.metric_names()) | set(EVAL_METRICS) | set(TARGET_UNITS)


# --- results -------------------------------------------------------------------------------------

def _step_metric_name(r: Result, normalizer: Normalizer, canonical: set[str]) -> list[TraceStep]:
    """Шаг 1. Возвращает шаги трассы сопоставления (старые сохраняются - идемпотентность)."""
    kept = [t for t in r.meta.norm_trace if t.step == MATCH_STEP]
    name = r.metric_name
    if not isinstance(name, str) or not name.strip() or name in canonical:
        return kept
    matched = normalizer.match_metric(name)
    if matched and matched != name:
        kept.append(TraceStep(step=MATCH_STEP, before=name, after=matched, note="синоним показателя"))
        r.metric_name = matched
    return kept


def _step_result_value(r: Result, normalizer: Normalizer, trace: list[TraceStep]) -> None:
    """Шаги 2-3."""
    raw = _text(r.raw_value)
    if raw is None:
        r.meta.norm_trace = trace
        return
    res: NormResult = normalizer.normalize(r.metric_name or "", raw, _text(r.raw_unit))
    status = res.norm_status
    r.meta.norm_trace = trace + list(res.trace)
    try:
        r.meta.norm_status = NormStatus(status)
    except ValueError:
        r.meta.norm_status = None
    if status == "failed" or (status in _OK_STATUSES and res.normalized_value is None):
        r.normalized_value = None
        r.normalized_unit = None
        r.metric_value = None
        r.metric_unit = None
        r.meta.norm_issue = res.issue or "unit_inconsistency"
    elif status == "not_applicable" and res.normalized_value is None:
        r.meta.norm_issue = None
    else:
        nv = _keep_source(r.raw_value, res.normalized_value, r.normalized_value, r.metric_value)
        if (status == "converted" and res.normalized_unit == "%" and isinstance(nv, Decimal) and nv.is_finite()
                and nv == nv.to_integral_value() and nv.as_tuple().exponent >= 0):
            nv = nv.quantize(Decimal("0.1"))  # доля -> %: целое с одним знаком (0.71 -> 71.0), как в эталоне
        r.normalized_value = nv
        r.normalized_unit = res.normalized_unit or None
        r.metric_value = r.normalized_value
        r.metric_unit = r.normalized_unit
        r.meta.norm_issue = res.issue
    # шаг 3: разброс
    pm = res.spread_value
    if pm is not None:
        ml = r.spread_value
        if _empty(ml):
            r.spread_value = pm
        else:
            ml_dec = _dec(ml)
            if ml_dec is None or ml_dec != pm:
                _add_backend_note(r.meta, f"spread_value: модель дала {_text(ml)}, в тексте ± {_text(pm)}; "
                                          f"оставлено значение модели")


# --- поля других таблиц ---------------------------------------------------------------------------

def _step_field(row: Any, table: str, column: str, normalizer: Normalizer) -> None:
    """Числовое поле с единицей (bandgap, thickness, area): при успехе - значение и целевая единица."""
    value = getattr(row, column)
    if _empty(value) or isinstance(value, bool):
        return
    unit_col = UNIT_FOR_VALUE.get((table, column), (table, None))[1]
    unit = getattr(row, unit_col) if unit_col else None
    res = normalizer.normalize_field(table, column, _text(value), _text(unit))
    if res.norm_status in _OK_STATUSES and res.normalized_value is not None:
        setattr(row, column, _keep_source(value, res.normalized_value))
        if unit_col and res.normalized_unit:
            setattr(row, unit_col, res.normalized_unit)
        if res.norm_status == "converted":
            _add_backend_note(row.meta, f"{column}: {_text(value)} {_text(unit) or ''}".rstrip()
                              + f" -> {_text(res.normalized_value)} {res.normalized_unit or ''}".rstrip())
    elif res.norm_status == "failed":
        row.meta.norm_issue = res.issue or row.meta.norm_issue


def _step_materials(b: PaperBundle, normalizer: Normalizer) -> None:
    for m in b.materials:
        _step_field(m, "materials", "bandgap_value", normalizer)
        if _empty(m.formula_normalized) and isinstance(m.material_name_raw, str) and m.material_name_raw.strip():
            f = normalizer.canon_material(m.material_name_raw)
            if f:
                m.formula_normalized = f


def _step_layers(b: PaperBundle, normalizer: Normalizer) -> None:
    vocab = VOCAB[("layers", "layer_function")]
    for la in b.layers:
        _step_field(la, "layers", "layer_thickness", normalizer)
        fn = la.layer_function
        if isinstance(fn, str) and fn.strip() and fn not in vocab:
            pos = la.layer_order if isinstance(la.layer_order, int) and not isinstance(la.layer_order, bool) else None
            lf = normalizer.layer_function(fn, pos)
            value = lf.value if lf.value in vocab else None
            la.layer_function = value
            note = lf.note or f"исходно: {fn.strip()}"
            la.note = _append_note(la.note, note)


def _step_devices(b: PaperBundle, normalizer: Normalizer) -> None:
    for d in b.devices:
        _step_field(d, "devices", "active_area", normalizer)
        # encapsulation - свободный текст (README шаблона не задаёт словарь; эталон: «unencapsulated»): как есть
        enc = d.encapsulation
        if isinstance(enc, bool):
            d.encapsulation = "encapsulated" if enc else "unencapsulated"


def _step_measurements(b: PaperBundle, normalizer: Normalizer) -> None:
    for ms in b.measurements:
        if isinstance(ms.spectrum, str) and ms.spectrum.strip():
            s = normalizer.normalize_spectrum(ms.spectrum)
            # только чистый вариант написания («AM 1.5G»); пояснения в скобках (эталон) не затираются
            if s and re.sub(r"[\s_-]", "", ms.spectrum).casefold() == re.sub(r"[\s_-]", "", s).casefold():
                ms.spectrum = s
        li = ms.light_intensity
        if isinstance(li, str) and li.strip():
            p = normalizer.parse_number(li)
            if p.ok and p.value is not None and not p.is_range:
                ms.light_intensity = p.value
                if _empty(ms.intensity_unit) and p.rest:
                    ms.intensity_unit = p.rest.strip()


def _step_units(b: PaperBundle) -> None:
    # единицы bandgap/thickness/area при пустом значении не очищаются: эталон оставляет «eV» у пустой ширины зоны
    # (README шаблона такого правила не содержит); очищаются только единицы показателей results
    for r in b.results:
        if _empty(r.metric_value) and not _empty(r.metric_unit):
            r.metric_unit = None
        if _empty(r.normalized_value) and not _empty(r.normalized_unit):
            r.normalized_unit = None


def apply_normalization(bundle: PaperBundle, normalizer: Normalizer, profile: ExtractionProfile) -> PaperBundle:
    """Шаги: metric_name -> match_metric; results.normalize -> normalized_*, metric_* = normalized_*; spread_value
    (ML не затирается); materials (bandgap, formula); layers (thickness, layer_function); devices (area,
    encapsulation); measurements (spectrum); единица только при значении (REQ-037)."""
    b = bundle.model_copy(deep=True)
    canonical = _canonical_metrics(profile)
    for r in b.results:
        trace = _step_metric_name(r, normalizer, canonical)
        _step_result_value(r, normalizer, trace)
    _step_materials(b, normalizer)
    _step_layers(b, normalizer)
    _step_devices(b, normalizer)
    _step_measurements(b, normalizer)
    _step_units(b)
    return b
