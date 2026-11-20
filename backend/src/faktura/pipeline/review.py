"""Перенос ручных правок и пересчёт замечаний (план 5.6, этап P1; spec/07 §7.3 «Версия модели»).

* `transfer_review(old, new)` - чистая функция: какие проверенные человеком факты прежнего разбора нашли пару
  в новом (прибор + показатель + измерение + место; машинное значение в пределах 0,5 %), а какие - нет
  (их конвейер сохраняет как ручные факты).
* `recheck_paper(uow, paper_id, profile)` - синхронный пересчёт замечаний статьи после правки (api_review):
  правила считаются по действующим значениям (`corrected_value ?? normalized_value`) с учётом ручных фактов,
  статусы разбора замечаний сохраняются (`issues.upsert_preserving_review`). ML не вызывается.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

from ..domain.entities import Fact, IssuesDiff, TransferResult
from ..domain.errors import AppError
from ..domain.ports import QuoteChecker
from ..domain.profile import ExtractionProfile
from ..domain.records import AddedBy, PaperBundle, ReviewStatus

VALUE_TOLERANCE = Decimal("0.005")  # 0,5 %


def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def review_key(f: Fact) -> tuple:
    """Прибор + показатель + измерение + место (страница, таблица, рисунок)."""
    metric = _s(f.metric_name) or _s(f.param)
    target = f.target or {}
    return (metric.lower(), _s(f.row_id), _s(target.get("measurement_id")), f.source_page,
            _s(f.source_table), _s(f.source_figure))


def values_match(a: Decimal | None, b: Decimal | None) -> bool:
    """Машинные значения совпадают: оба пусты или |a - b| <= 0,5 % от прежнего значения."""
    if a is None or b is None:
        return a is None and b is None
    if a == b:
        return True
    if a == 0:
        return False
    return abs(a - b) <= abs(a) * VALUE_TOLERANCE


def is_reviewed(f: Fact) -> bool:
    """Факт тронут человеком: принят, отклонён или исправлен."""
    return (f.review_status != ReviewStatus.new or f.corrected_value is not None
            or bool(_s(f.corrected_raw)))


def match_review(old_facts: Sequence[Fact], new_facts: Sequence[Fact]) -> tuple[list[tuple[Fact, Fact]], list[Fact]]:
    """Пары (прежний проверенный факт, новый факт) и прежние проверенные факты без пары. Один новый факт
    принимает не больше одного прежнего. Ручные факты (added_by=manual) не переносятся - они сохраняются как есть."""
    pairs: list[tuple[Fact, Fact]] = []
    unmatched: list[Fact] = []
    used: set[int] = set()
    candidates = list(new_facts)
    by_key: dict[tuple, list[int]] = {}
    for i, nf in enumerate(candidates):
        by_key.setdefault(review_key(nf), []).append(i)
    for of in old_facts:
        if of.added_by == AddedBy.manual or not is_reviewed(of):
            continue
        hit = None
        for i in by_key.get(review_key(of), []):
            if i in used:
                continue
            if values_match(of.normalized_value, candidates[i].normalized_value):
                hit = i
                break
        if hit is None:
            unmatched.append(of)
        else:
            used.add(hit)
            pairs.append((of, candidates[hit]))
    return pairs, unmatched


def transfer_review(old_facts: Sequence[Fact], new_facts: Sequence[Fact]) -> TransferResult:
    """Прибор + показатель + измерение + место; значение в пределах 0,5 %; несовпавшие -> ручные факты."""
    pairs, unmatched = match_review(old_facts, new_facts)
    return TransferResult(transferred=[o.id for o, _ in pairs], to_manual=[o.id for o in unmatched])


# --- пересчёт замечаний -----------------------------------------------------------------------------

class StoredQuoteChecker:
    """QuoteChecker без PDF: ответы прежней проверки цитат, сохранённые в meta записей и замечаний.
    Неизвестная цитата -> None (не проверяется). Нужен, чтобы пересчёт после правки давал те же цитаты."""

    def __init__(self, bundle: PaperBundle) -> None:
        from ..pdfio.quotes import validator_norm

        self._norm = validator_norm
        self._known: dict[tuple[int | None, str], bool] = {}
        rows: list[Any] = list(bundle.results) + list(bundle.issues)
        for r in rows:
            frag = getattr(r, "source_fragment", None)
            found = r.meta.quote_found
            if found is None or not isinstance(frag, str) or not frag.strip():
                continue
            page = r.source_page if isinstance(r.source_page, int) and not isinstance(r.source_page, bool) else None
            self._known[(page, validator_norm(frag))] = bool(found)

    def check(self, page: int | None, fragment: str | None) -> bool | None:
        if not fragment or not str(fragment).strip():
            return None
        key = (page if isinstance(page, int) else None, self._norm(str(fragment)))
        return self._known.get(key)


def bundle_for_checks(uow: Any, paper_id: str, profile: ExtractionProfile) -> PaperBundle:
    """Бандл статьи для проверок: правки человека (apply_review) + ручные факты-показатели строками results."""
    from ..export.select import materialize_manual_fact

    bundle = uow.papers.load_bundle(paper_id, apply_review=True)
    for f in uow.facts.manual_for_paper(paper_id):
        target = f.target or {}
        if target.get("entity") not in (None, "results") or target.get("id"):
            continue
        if f.review_status == ReviewStatus.rejected:
            continue
        r = materialize_manual_fact(f, bundle, profile)
        if r is not None:
            bundle.results.append(r)
    return bundle


def recheck_paper(uow: Any, paper_id: str, profile: ExtractionProfile, *,
                  quote: QuoteChecker | None = None) -> IssuesDiff:
    """Синхронный пересчёт замечаний после правки (api_review). Статусы разбора сохраняются; ML не вызывается."""
    from ..checks import check_paper

    paper = uow.papers.get(paper_id)
    if paper is None:
        raise AppError("not_found", f"Статья {paper_id} не найдена")
    bundle = bundle_for_checks(uow, paper_id, profile)
    in_scope = bundle.publication.in_scope
    if in_scope is False or (isinstance(in_scope, str) and in_scope.strip().lower() == "false"):
        issues = []
    else:
        checker = quote if quote is not None else StoredQuoteChecker(bundle)
        issues = check_paper(bundle, profile, quote=checker, autolink=False).issues
    return uow.issues.upsert_preserving_review(paper_id, issues)
