"""Этап B: facts.query (keyset), facets, update_review, add_manual."""
from __future__ import annotations

from decimal import Decimal

import pytest

from faktura.domain.entities import FactDraft, FactFilter, PaperStats, ReviewStatus, SortKey
from faktura.domain.errors import AppError

NOT_FOUND = (AppError, KeyError, LookupError)
BAD = (ValueError, AppError)


def _walk(u, flt, sort, limit=100, max_pages=200):
    """Обход страниц; возвращает список id в порядке выдачи."""
    out, cursor = [], None
    for _ in range(max_pages):
        page = u.facts.query(flt, sort, limit, cursor)
        out += [r.fact.id for r in page.data]
        cursor = page.next_cursor
        if not cursor:
            return out
    raise AssertionError("курсор не заканчивается")


@pytest.fixture
def thousand(db, ws_id, corpus, new_paper, process):
    """1000 фактов в 4 статьях; значения с множеством совпадений (проверка устойчивости при равных ключах)."""
    ids = {}
    for k in range(4):
        p = new_paper(f"D{k + 1:03d}", year=2019 + k)
        res = process(p, n=250, base=Decimal(k % 2)).result  # значения 0..2.4 / 1..3.4 -> много повторов
        ids[p.id] = set(res.fact_ids.values())
    return ids


# ---- keyset ----------------------------------------------------------------------------------



def test_query_walk_1000_by_100_no_gaps_no_dups(db, ws_id, thousand):
    allids = set().union(*thousand.values())
    with db.uow() as u:
        got = _walk(u, FactFilter(workspace_id=ws_id), [SortKey(field="normalized_value")], 100)
    assert len(got) == 1000 and len(set(got)) == 1000 and set(got) == allids


































# ---- фильтры ---------------------------------------------------------------------------------

def _q(db, **kw):
    with db.uow() as u:
        return _walk(u, FactFilter(**kw), [SortKey(field="normalized_value")], 500)




























# ---- facets ----------------------------------------------------------------------------------











# ---- update_review ---------------------------------------------------------------------------

def _one(db, ws_id):
    with db.uow() as u:
        return u.facts.query(FactFilter(workspace_id=ws_id), [], 1, None).data[0].fact






















# ---- add_manual ------------------------------------------------------------------------------

def _draft(**kw):
    base = dict(ref="MAN1", target={"entity": "results", "id": "R900", "field": "metric_value"},
                param="metric_value", metric_name="PCE", raw_value="18,2", raw_unit="%",
                normalized_value=Decimal("18.2"), normalized_unit="%", norm_status="ok", source_page=3,
                source_fragment="PCE of 18.2%", source_kind="manual")
    base.update(kw)
    return FactDraft(**base)






