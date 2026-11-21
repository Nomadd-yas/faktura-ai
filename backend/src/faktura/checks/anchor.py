"""Источник замечаний бэка: source_page и source_fragment из записи-якоря (план 5.3).

- scope=record: якорь - первая запись в упорядоченных record_ids. Берутся её source_page и source_fragment;
  без цитаты - ссылка «Table N, строка <device_label>» или «Figure N».
- scope=paper: якорь - первая запись results типа J-V (затем - первая запись со страницей); если записей нет -
  страница 1 и publications.title. Заголовок проверяется QuoteChecker: не найден - фрагмент пустой и пометка
  в meta.notes_backend; None (не проверяется) или нет проверяльщика - заголовок остаётся.
"""
from __future__ import annotations

from ..domain.ports import QuoteChecker
from ..domain.records import IssueRecord, Result
from ._util import PaperIndex, is_rejected


def _page(v: object) -> int | str | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    return v  # type: ignore[return-value]


def result_reference(r: Result, idx: PaperIndex) -> str | None:
    """Ссылка вместо цитаты: «Table 2, строка B4» или «Figure 3»."""
    table = (r.source_table or "").strip()
    if table:
        label = idx.device_label(r)
        return f"{table}, строка {label}" if label else table
    fig = (r.source_figure or "").strip()
    if fig:
        return fig
    return None


def source_from_result(r: Result, idx: PaperIndex) -> tuple[int | str | None, str | None]:
    frag = (r.source_fragment or "").strip()
    return _page(r.source_page), (r.source_fragment if frag else result_reference(r, idx))


def fill_record_source(issue: IssueRecord, idx: PaperIndex, quote: QuoteChecker | None = None) -> IssueRecord:
    """source_page/source_fragment у якоря; quote_found - у реальной цитаты якоря (не ссылки «Table N, ...»)."""
    if not issue.record_ids:
        return issue
    anchor = idx.results.get(issue.record_ids[0])
    if anchor is None:
        return issue
    issue.source_page, issue.source_fragment = source_from_result(anchor, idx)
    frag = (anchor.source_fragment or "").strip()
    if frag:
        if quote is not None:
            issue.meta.quote_found = quote.check(_page(anchor.source_page), frag)  # type: ignore[arg-type]
        elif anchor.meta.quote_found is not None:
            issue.meta.quote_found = anchor.meta.quote_found
    return issue


def _paper_anchor(idx: PaperIndex) -> Result | None:
    rows = [r for r in idx.sorted(idx.bundle.results) if not is_rejected(r)]
    for r in rows:
        ms = idx.measurement(r)
        if ms is not None and ms.measurement_type == "J-V" and _page(r.source_page) is not None:
            return r
    for r in rows:
        if _page(r.source_page) is not None:
            return r
    return None


def fill_paper_source(issue: IssueRecord, idx: PaperIndex, quote: QuoteChecker | None) -> IssueRecord:
    anchor = _paper_anchor(idx)
    if anchor is not None:
        issue.source_page, issue.source_fragment = source_from_result(anchor, idx)
        return issue
    title = (idx.bundle.publication.title or "").strip()
    issue.source_page = 1
    if not title:
        issue.source_fragment = None
        return issue
    found = quote.check(1, title) if quote is not None else None
    if found is False:
        issue.source_fragment = None
        issue.meta.notes_backend.append("цитата-заголовок не найдена на странице 1; фрагмент оставлен пустым")
        issue.meta.quote_found = False
    else:
        issue.source_fragment = title
        issue.meta.quote_found = found
    return issue
