"""Выборка записей для сдачи по spec/14 §14.5; выгрузка ручных фактов (план 5.4; REQ-024, REQ-025, REQ-034,
REQ-041, REQ-057, К-14, У-13, У-35).

Правила `select_for_submission`:
- упавшая статья (`failed=True`) -> `PaperBundle.failed_stub` (только publication_id, К-14);
- `in_scope = false` -> только строка publications (REQ-025);
- results: `rejected` - не выгружается; `absent` - только при `profile.flags.export_absent`;
  `meta.corrected_value` -> `normalized_value` и `metric_value` (`raw_value` из статьи остаётся);
- issues: `status = ok` - нет; находки ML с `confidence < profile.flags.ml_issue_min_confidence` - нет;
  внутренние правила интерфейса (`CHECK_CATALOG[..].exported = False`) - нет; замечание, все записи
  которого отброшены (отклонены человеком), - нет;
- факты из `manual_facts` (любой `Fact` с `target = {entity, id, field}`):
  * `rejected` полевой факт очищает ячейку и её единицу (REQ-037); `rejected` факт значения результата
    отбрасывает строку results;
  * ручной факт-показатель без своей строки -> строка results `RM<n>` (`materialize_manual_fact`);
  * полевой факт с `corrected_value` заменяет значение ячейки; ручной полевой факт заполняет пустую ячейку.
Вход не меняется.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Sequence

from pydantic import BaseModel, Field

from ..domain.entities import Fact
from ..domain.profile import ExtractionProfile, param_to_metric
from ..domain.records import AddedBy, IssueStatus, PaperBundle, Result, ReviewStatus
from ..domain.tables import (CHECK_CATALOG, ID_COLUMN, INTEGER_FIELDS, NUMERIC_FIELDS, TABLES, TARGET_UNITS,
                             TARGET_UNITS_FIELDS, UNIT_FOR_VALUE)

__all__ = ["SelectResult", "select_for_submission", "materialize_manual_fact", "manual_fact_problem"]

_RESULT_VALUE_FIELDS = {None, "", "metric_value", "normalized_value", "raw_value"}
_RM_RE = re.compile(r"^RM(\d+)$")


class SelectResult(BaseModel):
    bundle: PaperBundle
    events: list[str] = Field(default_factory=list)  # для errors.log


def _target(fact: Fact) -> tuple[str | None, str | None, str | None]:
    t = fact.target or {}
    return t.get("entity"), t.get("id"), t.get("field")


def _metric_of(fact: Fact) -> str | None:
    if fact.metric_name:
        return fact.metric_name
    return param_to_metric(fact.param) if fact.param else None


def _profile_value_column(profile: ExtractionProfile, table: str) -> str | None:
    """Колонка значения таблицы по профилю (bandgap_value, layer_thickness, active_area, light_intensity)."""
    cols = [f.column for f in profile.fields if f.table == table and f.column]
    return cols[0] if len(cols) == 1 else None


def _clear_field(row: Any, table: str, field: str) -> None:
    setattr(row, field, [] if isinstance(getattr(row, field, None), list) else None)
    unit = UNIT_FOR_VALUE.get((table, field))
    if unit:
        setattr(row, unit[1], None)


def _field_value(table: str, field: str, fact: Fact, corrected: bool) -> Any:
    if (table, field) in NUMERIC_FIELDS:
        v: Decimal | None = fact.corrected_value if corrected else fact.value
        if v is None:
            return None
        return int(v) if (table, field) in INTEGER_FIELDS and v == v.to_integral_value() else v
    text = fact.corrected_raw if corrected and fact.corrected_raw else (fact.raw_value or fact.corrected_raw)
    return text


def manual_fact_problem(fact: Fact) -> str | None:
    """Почему ручной факт-показатель не попадёт в сдачу (None - попадёт). Для `Fact.exportable` в API (У-35)."""
    if fact.review_status == ReviewStatus.rejected:
        return "факт отклонён"
    if _metric_of(fact) is None:
        return "не показатель results"
    if fact.value is None:
        return "нет значения"
    if fact.source_page is None:
        return "нет страницы"
    has_quote = bool((fact.source_fragment or "").strip())
    has_ref = bool((fact.source_table or "").strip() or (fact.source_figure or "").strip())
    if not (has_quote or has_ref):
        return "нет цитаты или ссылки на таблицу/рисунок"
    return None


def _next_rm(bundle: PaperBundle) -> str:
    used = {r.result_id for r in bundle.results}
    n = 1
    while f"RM{n}" in used:
        n += 1
    return f"RM{n}"


def materialize_manual_fact(fact: Fact, bundle: PaperBundle, profile: ExtractionProfile) -> Result | None:
    """Ручной факт-показатель -> строка results (result_id RM<n>); без страницы и цитаты/ссылки - None.

    device_id - из `fact.row_id`, если такой прибор есть; measurement_id - из `target.measurement_id` или
    единственного измерения прибора; material_id - поглотитель прибора. raw_* - из `corrected_raw` (иначе
    `raw_value`), normalized_* = metric_* = действующее значение (`corrected_value ?? normalized_value`)."""
    if manual_fact_problem(fact) is not None:
        return None
    metric = _metric_of(fact)
    value = fact.value
    unit = fact.normalized_unit
    if not unit:
        unit = next((f.unit for f in profile.fields if f.key == metric and f.table == "results"), None)
        unit = unit or TARGET_UNITS.get(metric or "")
    devices = {d.device_id: d for d in bundle.devices}
    device = devices.get(fact.row_id) if fact.row_id else None
    target = fact.target or {}
    measurement_id = target.get("measurement_id")
    ms_ids = {m.measurement_id for m in bundle.measurements}
    if measurement_id not in ms_ids:
        measurement_id = None
    if measurement_id is None and device is not None:
        own = [m.measurement_id for m in bundle.measurements if device.device_id in (m.device_id or [])]
        measurement_id = own[0] if len(own) == 1 else None
    duplicate_of = fact.duplicate_of if fact.duplicate_of in {r.result_id for r in bundle.results} else None
    order = max((r.meta.ml_order for r in bundle.results), default=-1) + 1
    raw = fact.corrected_raw if fact.corrected_raw else fact.raw_value
    r = Result(
        publication_id=bundle.publication_id,
        result_id=_next_rm(bundle),
        device_id=device.device_id if device else None,
        measurement_id=measurement_id,
        material_id=device.absorber_material_id if device else None,
        metric_name=metric,
        metric_value=value,
        metric_unit=unit,
        raw_value=raw,
        raw_unit=fact.raw_unit,
        normalized_value=value,
        normalized_unit=unit,
        result_origin="primary",
        source_page=fact.source_page,
        source_table=(fact.source_table or None),
        source_figure=(fact.source_figure or None),
        source_fragment=(fact.source_fragment or "").strip() or None,
        extraction_status="extracted",
        duplicate_of=duplicate_of,
    )
    r.meta.ml_order = order
    r.meta.added_by = AddedBy.manual
    r.meta.review_status = fact.review_status
    r.meta.page = fact.source_page
    r.meta.confidence = fact.confidence
    r.meta.quote_found = fact.quote_found
    r.meta.corrected_value = fact.corrected_value
    r.meta.extra = {"fact_id": fact.id}
    return r


def _drop_results(b: PaperBundle, drop: set[str]) -> None:
    """Убирает строки results; повторы отброшенной основной записи переподвешиваются к первому оставшемуся
    повтору; замечания теряют ссылки, а замечание без оставшихся записей уходит."""
    if not drop:
        return
    kept = [r for r in b.results if r.result_id not in drop]
    for main in drop:
        dups = [r for r in kept if r.duplicate_of == main]
        if dups:
            dups[0].duplicate_of = None
            for d in dups[1:]:
                d.duplicate_of = dups[0].result_id
    b.results = kept
    issues = []
    for iss in b.issues:
        if iss.record_ids and all(x in drop for x in iss.record_ids):
            continue
        iss.record_ids = [x for x in iss.record_ids if x not in drop]
        issues.append(iss)
    b.issues = issues


def _out_of_scope(v: Any) -> bool:
    return v is False or (isinstance(v, str) and v.strip().lower() == "false")


def _row_by_id(b: PaperBundle, table: str, rid: str | None):
    if not rid or table not in TABLES:
        return None
    if table == "publications":
        return b.publication if b.publication.publication_id == rid else None
    col = ID_COLUMN[table]
    return next((r for r in getattr(b, table) if getattr(r, col) == rid), None)


def select_for_submission(bundle: PaperBundle, profile: ExtractionProfile, *, failed: bool = False,
                          manual_facts: Sequence[Fact] = ()) -> SelectResult:
    """rejected - нет; corrected_value -> normalized_value и metric_value; absent - только при export_absent;
    находки ML с confidence < порога - нет; issues со status=ok - нет; in_scope=false -> только publications;
    failed=True -> PaperBundle.failed_stub (К-14); ручные факты -> строки results."""
    pid = bundle.publication_id
    if failed:
        return SelectResult(bundle=PaperBundle.failed_stub(pid))
    events: list[str] = []
    b = bundle.model_copy(deep=True)
    if _out_of_scope(b.publication.in_scope):
        return SelectResult(bundle=PaperBundle(publication=b.publication))

    # строки results
    drop: set[str] = set()
    for r in b.results:
        if r.meta.review_status == ReviewStatus.rejected:
            drop.add(r.result_id)
        elif r.extraction_status == "absent" and not profile.flags.export_absent:
            drop.add(r.result_id)
        elif r.meta.corrected_value is not None:
            r.normalized_value = r.meta.corrected_value
            r.metric_value = r.meta.corrected_value
    # отклонённая или исправленная запись других таблиц - это её поле значения по профилю
    for table in ("materials", "devices", "layers", "measurements"):
        col = _profile_value_column(profile, table)
        if not col:
            continue
        for row in getattr(b, table):
            if row.meta.review_status == ReviewStatus.rejected:
                _clear_field(row, table, col)
            elif row.meta.corrected_value is not None:
                setattr(row, col, row.meta.corrected_value)

    # факты (поля, отклонения, ручные показатели)
    manual: list[Fact] = []
    for f in manual_facts:
        if f.publication_id != pid:
            continue
        entity, rid, field = _target(f)
        if entity == "results":
            row = _row_by_id(b, "results", rid)
            if f.review_status == ReviewStatus.rejected:
                if row is not None:
                    if field in _RESULT_VALUE_FIELDS:
                        drop.add(row.result_id)
                    elif field in TABLES["results"]:
                        _clear_field(row, "results", field)
                continue
            if row is None:
                if f.added_by == AddedBy.manual:
                    manual.append(f)
                elif rid:
                    events.append(f"{pid}: факт {f.id} ссылается на results[{rid}], которой нет — пропущен")
                continue
            if f.corrected_value is not None and field in _RESULT_VALUE_FIELDS:
                row.normalized_value = f.corrected_value
                row.metric_value = f.corrected_value
            continue
        if entity not in TABLES or entity == "issues":
            continue
        row = _row_by_id(b, entity, rid)
        if row is None or not field or field not in TABLES[entity]:
            if f.review_status != ReviewStatus.rejected:
                events.append(f"{pid}: факт {f.id} ссылается на {entity}[{rid}].{field}, которой нет — пропущен")
            continue
        if f.review_status == ReviewStatus.rejected:
            _clear_field(row, entity, field)
            continue
        corrected = f.corrected_value is not None or bool(f.corrected_raw)
        current = getattr(row, field, None)
        if corrected or (f.added_by == AddedBy.manual and current in (None, "", [])):
            new = _field_value(entity, field, f, corrected)
            if new is None:
                continue
            setattr(row, field, new)
            unit = UNIT_FOR_VALUE.get((entity, field))
            target_unit = TARGET_UNITS_FIELDS.get((entity, field))
            if unit and target_unit:
                setattr(row, unit[1], target_unit)

    _drop_results(b, drop)

    # ручные показатели -> RM<n>
    for f in manual:
        r = materialize_manual_fact(f, b, profile)
        if r is None:
            events.append(f"{pid}: ручной факт {f.id} ({f.param}) не выгружен: {manual_fact_problem(f)}")
            continue
        b.results.append(r)

    # замечания
    threshold = profile.flags.ml_issue_min_confidence
    issues = []
    for iss in b.issues:
        if iss.status == IssueStatus.ok:
            continue
        info = CHECK_CATALOG.get(iss.check_name)
        if info is not None and not info.exported:
            continue
        if iss.computed_by == "ml" and iss.confidence is not None and iss.confidence < threshold:
            continue
        issues.append(iss)
    b.issues = issues
    return SelectResult(bundle=b, events=events)
