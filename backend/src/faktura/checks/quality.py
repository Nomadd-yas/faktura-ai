"""Качество и покрытие статьи (spec/03 §3.4, spec/04 §4.7). paper_coverage - единственный владелец расчёта
покрытия: его используют и summary корпуса, и GET /papers/{id}/coverage."""
from __future__ import annotations

from typing import Sequence

from ..domain.entities import Coverage, Fact, FactDraft, Quality
from ..domain.profile import ExtractionProfile
from ..domain.records import IssueRecord, IssueStatus, PaperBundle, ReviewStatus
from ..domain.tables import CHECK_CATALOG
from ._util import is_rejected

_NORMALIZED = {"ok", "converted", "passthrough", "not_applicable"}
_SEVERITY_WEIGHT = {"error": 1.0, "warn": 0.5, "info": 0.2}
# пороги уровня: score >= 0.8 -> good, >= 0.5 -> doubt, иначе bad
GOOD_FROM, DOUBT_FROM = 0.8, 0.5


def _s(v: object) -> str:
    return "" if v is None else str(getattr(v, "value", v))


def _counts_as_open(i: IssueRecord) -> bool:
    info = CHECK_CATALOG.get(i.check_name)
    if info is not None and not info.exported:
        return False  # внутренние правила интерфейса (unit_conversion, low_confidence) - справка
    return _s(i.status) == IssueStatus.open.value


def paper_quality(facts: Sequence[Fact | FactDraft], issues: list[IssueRecord]) -> Quality:
    """details = {total, sourced_pct, normalized_pct, accepted, issues} (spec/04 §4.7 quality).

    total - факты кроме отклонённых; sourced - есть source_page и непустой source_fragment; normalized -
    norm_status ok|converted|passthrough|not_applicable; accepted - review_status=accepted; issues - открытые
    замечания (status=open, без внутренних правил). score в [0; 1]:
    0.4·sourced + 0.3·normalized + 0.3·(1 − min(1, Σвес_открытых / total)), вес error 1, warn 0.5, info 0.2.
    Нет фактов -> score и level = None."""
    live = [f for f in facts if _s(getattr(f, "review_status", None)) != ReviewStatus.rejected.value]
    total = len(live)
    sourced = sum(1 for f in live if f.source_page is not None and (f.source_fragment or "").strip())
    normalized = sum(1 for f in live if _s(f.norm_status) in _NORMALIZED)
    accepted = sum(1 for f in live if _s(getattr(f, "review_status", None)) == ReviewStatus.accepted.value)
    open_issues = [i for i in issues if _counts_as_open(i)]
    weight = 0.0
    for i in open_issues:
        sev = i.severity or (CHECK_CATALOG[i.check_name].severity if i.check_name in CHECK_CATALOG else "warn")
        weight += _SEVERITY_WEIGHT.get(sev, 0.5)
    details = {
        "total": total,
        "sourced_pct": round(100.0 * sourced / total, 1) if total else 0.0,
        "normalized_pct": round(100.0 * normalized / total, 1) if total else 0.0,
        "accepted": accepted,
        "issues": len(open_issues),
    }
    if total == 0:
        return Quality(score=None, level=None, details=details)
    issue_part = 1.0 - min(1.0, weight / total)
    score = round(0.4 * sourced / total + 0.3 * normalized / total + 0.3 * issue_part, 4)
    score = min(1.0, max(0.0, score))
    level = "good" if score >= GOOD_FROM else "doubt" if score >= DOUBT_FROM else "bad"
    return Quality(score=score, level=level, details=details)


def _has_value(v: object) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, tuple)):
        return bool(v)
    return True


def paper_coverage(bundle: PaperBundle, profile: ExtractionProfile) -> Coverage:
    """total - включённые поля профиля; filled - поля хотя бы с одним действующим значением (не отклонено,
    extraction_status != absent); missing - ключи незаполненных полей в порядке профиля; ratio = filled/total."""
    metrics_with_value: set[str] = set()
    for r in bundle.results:
        if is_rejected(r) or r.extraction_status == "absent" or not r.metric_name:
            continue
        if _has_value(PaperBundle.effective_value(r)):
            metrics_with_value.add(r.metric_name)
    filled, total, missing = 0, 0, []
    seen: set[tuple[str, str, str | None]] = set()
    for f in profile.fields:
        if not f.enabled:
            continue
        ident = (f.table, f.key, f.column)
        if ident in seen:
            continue
        seen.add(ident)
        total += 1
        if f.table == "results" and f.column is None:
            ok = f.key in metrics_with_value
        else:
            rows = [bundle.publication] if f.table == "publications" else getattr(bundle, f.table, None) or []
            col = f.column or f.key
            ok = any(not is_rejected(row) and _has_value(getattr(row, col, None)) for row in rows)
        if ok:
            filled += 1
        else:
            missing.append(f.key)
    ratio = (filled / total) if total else None
    return Coverage(filled=filled, total=total, ratio=ratio, missing=missing)

