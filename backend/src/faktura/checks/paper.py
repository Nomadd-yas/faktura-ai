"""check_paper: всё вместе для одной статьи (план 5.3).

Порядок: (опц.) автосвязывание повторов -> проверка находок ML -> правила бэка -> слияние -> ключи.
Находки ML - замечания bundle.issues с computed_by='ml' и ранее слитые (merged=True: в них есть находка ML);
прежние замечания бэка без слияния пересчитываются заново. При profile.flags.backend_rules=False правила бэка
не считаются и слияния нет (строгий roundtrip: замечания ML выходят как есть, мультимножество ключей
сохраняется). bundle не меняется: связи повторов возвращаются в CheckOutcome.links, а правила считаются на
копии, где эти связи уже применены.
"""
from __future__ import annotations

from time import perf_counter

from pydantic import BaseModel, Field

from ..domain.ports import QuoteChecker
from ..domain.profile import ExtractionProfile
from ..domain.records import IssueRecord, PaperBundle
from .links import DuplicateLink, link_duplicates
from .merge import assign_keys, check_ml_issues, merge_issues
from .rules import run_backend_rules


class CheckOutcome(BaseModel):
    issues: list[IssueRecord] = Field(default_factory=list)
    links: list[DuplicateLink] = Field(default_factory=list)
    timings: dict[str, float] = Field(default_factory=dict)


def _with_links(bundle: PaperBundle, links: list[DuplicateLink]) -> PaperBundle:
    if not links:
        return bundle
    to = {l.result_id: l for l in links}
    results = []
    for r in bundle.results:
        link = to.get(r.result_id)
        if link is None:
            results.append(r)
            continue
        c = r.model_copy(deep=True)
        c.duplicate_of = link.duplicate_of
        c.meta.linked_by_backend = True
        results.append(c)
    return bundle.model_copy(update={"results": results})


def _ml_findings(bundle: PaperBundle) -> list[IssueRecord]:
    out = []
    for i in bundle.issues:
        if i.computed_by == "ml":
            out.append(i)
        elif i.merged:
            out.append(i.model_copy(update={"computed_by": "ml", "merged": False}))
    return out


def check_paper(bundle: PaperBundle, profile: ExtractionProfile, *, quote: QuoteChecker | None = None,
                autolink: bool = False) -> CheckOutcome:
    timings: dict[str, float] = {}
    t_all = t = perf_counter()

    def lap(name: str) -> None:
        nonlocal t
        now = perf_counter()
        timings[name] = round((now - t) * 1000, 3)
        t = now

    pid = bundle.publication_id
    links = link_duplicates(bundle) if autolink else []
    work = _with_links(bundle, links)
    lap("links_ms")
    ml = check_ml_issues(_ml_findings(bundle), work, quote)
    lap("ml_check_ms")
    if profile.flags.backend_rules:
        backend = run_backend_rules(work, profile.rules, quote=quote)
        lap("rules_ms")
        merged = merge_issues(backend, ml)
        lap("merge_ms")
    else:
        merged = ml
    for i in merged:
        i.publication_id = pid
    issues = assign_keys(merged, pid)
    lap("keys_ms")
    timings["total_ms"] = round((perf_counter() - t_all) * 1000, 3)
    return CheckOutcome(issues=issues, links=links, timings=timings)
