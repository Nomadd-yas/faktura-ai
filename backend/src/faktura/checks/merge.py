"""Находки ML: проверка ссылок и цитаты; слияние с правилами бэка (К-15, REQ-055, REQ-056); ключи К-6."""
from __future__ import annotations

import re
from typing import Any

from ..domain.ids import issue_key
from ..domain.ports import QuoteChecker
from ..domain.records import IssueRecord, PaperBundle
from ..domain.tables import CHECK_CATALOG

_REF_RE = re.compile(r",\s*строка\b", re.IGNORECASE)

Key = tuple[str, frozenset[str]]


def _k(i: IssueRecord) -> Key:
    return (i.check_name, frozenset(i.record_ids))


def _scope(check: str) -> str:
    info = CHECK_CATALOG.get(check)
    return info.scope if info is not None else "record"


def _empty(v: object) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def is_reference_fragment(fragment: str | None) -> bool:
    """Ссылка вида «Table 2, строка …» - не цитата, pypdf её не проверяет (spec/05 §5.1)."""
    return bool(fragment) and bool(_REF_RE.search(fragment or ""))


# --- check_ml_issues -------------------------------------------------------------------------------------------------

def check_ml_issues(issues: list[IssueRecord], bundle: PaperBundle,
                    quote: QuoteChecker | None) -> list[IssueRecord]:
    """Висячие record_ids и ненайденная цитата -> пометки в meta.notes_backend, без удаления (REQ-055).
    Вход не меняется; порядок и число записей сохраняются."""
    known = {r.result_id for r in bundle.results}
    out: list[IssueRecord] = []
    for src in issues:
        i = src.model_copy(deep=True)
        dangling = [rid for rid in i.record_ids if rid not in known]
        if dangling:
            i.meta.notes_backend.append("record_ids без записи в results: " + ", ".join(dangling))
        frag = i.source_fragment
        if quote is not None and not _empty(frag) and not is_reference_fragment(frag):
            page = i.source_page if isinstance(i.source_page, int) else None
            found = quote.check(page, frag)
            i.meta.quote_found = found
            if found is False:
                where = f"на странице {i.source_page}" if not _empty(i.source_page) else "в статье"
                i.meta.notes_backend.append(f"цитата source_fragment не найдена {where}")
        out.append(i)
    return out


# --- merge_issues ----------------------------------------------------------------------------------------------------

def _collapse(items: list[IssueRecord], *, by_paper_check: bool) -> list[IssueRecord]:
    """Схлопывание повторов по ключу k (и по check_name у правил scope=paper - только для бэка).
    Тексты повторов ML не теряются: описание дополняется, в numbers.merged_duplicates - их поля."""
    out: list[IssueRecord] = []
    seen: dict[Any, IssueRecord] = {}
    for i in items:
        key: Any = _k(i)
        if by_paper_check and _scope(i.check_name) == "paper" and not i.record_ids:
            key = ("paper", i.check_name)
        if key in seen:
            first = seen[key]
            if not by_paper_check and i.description and i.description != first.description:
                first.numbers.setdefault("merged_duplicates", []).append(
                    {"description": i.description, "source_page": i.source_page,
                     "source_fragment": i.source_fragment})
                first.description = f"{first.description or ''} | {i.description}".strip(" |")
                first.meta.notes_backend.append("объединены находки модели с одинаковыми check_name и record_ids")
            continue
        c = i.model_copy(deep=True)
        seen[key] = c
        out.append(c)
    return out


def _combine(be: IssueRecord, ml: IssueRecord, ids: list[str]) -> IssueRecord:
    m = be.model_copy(deep=True)
    m.record_ids = list(ids)
    if not _empty(ml.source_page):
        m.source_page = ml.source_page
    if not _empty(ml.source_fragment):
        m.source_fragment = ml.source_fragment
        m.meta.quote_found = ml.meta.quote_found
    if ml.confidence is not None:
        m.confidence = ml.confidence
    if _empty(m.description):
        m.description = ml.description
    if m.severity is None:
        m.severity = ml.severity
    extra = sorted(set(be.record_ids) - set(ids))
    if extra:
        m.numbers = {**m.numbers, "extra_record_ids": extra}
    if ml.description and ml.description != m.description:
        m.numbers = {**m.numbers, "ml_description": ml.description}
    for n in ml.meta.notes_backend:
        if n not in m.meta.notes_backend:
            m.meta.notes_backend.append(n)
    m.computed_by = "backend"
    m.merged = True
    return m


def merge_issues(backend: list[IssueRecord], ml: list[IssueRecord]) -> list[IssueRecord]:
    """Слияние находок ML и правил бэка (К-15, REQ-055).

    Замечания ML выгружаются как есть (issue_id, record_ids, текст, источник не меняются); повторы по ключу
    k = (check_name, frozenset(record_ids)) внутри ML не схлопываются. Правила бэка только ДОБАВЛЯЮТСЯ после
    ML-овских и пропускаются, если ML уже дал замечание с тем же check_name и тем же либо пересекающимся набором
    record_ids (для scope=paper с пустыми record_ids - с тем же check_name). Повторы внутри бэка схлопываются. Входные списки не меняются."""
    mls = [m.model_copy(deep=True) for m in ml]
    seen: set[Key] = {_k(m) for m in mls}
    paper_seen: set[str] = {m.check_name for m in mls if _scope(m.check_name) == "paper" and not m.record_ids}
    ml_ids: dict[str, set[str]] = {}
    for m in mls:
        ml_ids.setdefault(m.check_name, set()).update(m.record_ids)
    out: list[IssueRecord] = list(mls)
    for b in _collapse(backend, by_paper_check=True):
        if _k(b) in seen:
            continue
        if _scope(b.check_name) == "record" and set(b.record_ids) & ml_ids.get(b.check_name, set()):
            continue  # то же замечание ML с иным набором записей
        if _scope(b.check_name) == "paper" and not b.record_ids and b.check_name in paper_seen:
            continue
        out.append(b)
    return out


# --- assign_keys -----------------------------------------------------------------------------------------------------

def _key_params(i: IssueRecord) -> dict[str, Any]:
    if _scope(i.check_name) == "paper":
        return {}  # не больше одного на статью: ключ переживает изменение текста
    params: dict[str, Any] = {"description": i.description or ""}
    if i.numbers:
        params["numbers"] = {k: v for k, v in i.numbers.items() if k not in ("merged_duplicates", "ml_description")}
    return params


def assign_keys(issues: list[IssueRecord], publication_id: str) -> list[IssueRecord]:
    """Внутренний ключ I-… (К-6) через domain.ids.issue_key: от check_name и множества record_ids (порядок не
    важен), при пустых record_ids - от параметров находки (у правил scope=paper - только от check_name).
    Совпадения внутри статьи получают суффиксы -2, -3 … в порядке списка. Возвращает копии."""
    out: list[IssueRecord] = []
    used: set[str] = set()
    for src in issues:
        i = src.model_copy(deep=True)
        if not i.publication_id:
            i.publication_id = publication_id
        base = issue_key(i.check_name, i.record_ids, _key_params(i))
        key, n = base, 1
        while key in used:
            n += 1
            key = f"{base}-{n}"
        used.add(key)
        i.key = key
        out.append(i)
    return out
