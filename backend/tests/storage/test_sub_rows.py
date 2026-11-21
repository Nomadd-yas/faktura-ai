"""sub_rows: запись и чтение, пачки, фильтры/сортировка, сводка, переезд из sub_*, реестр областей."""
from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from faktura.domain.errors import AppError
from faktura.domain.registry import DOMAINS, RAG_SCHEMA_PATH, get_domain
from faktura.domain.tables import EVAL_METRICS, ID_COLUMN, LIST_FIELDS, TABLES, VOCAB
from faktura.export.sanitize import REFERENCES
from faktura.storage import schema as S


def _res(pid, n, metric="PCE", value=None, page="", **kw):
    return {"publication_id": pid, "result_id": f"R{n:03d}", "device_id": kw.get("device_id", "DV1"),
            "metric_name": metric, "metric_value": str(n) if value is None else value, "source_page": page,
            "duplicate_of": kw.get("duplicate_of", "")}


def _count(u, **where):
    t = S.sub_rows
    q = sa.select(sa.func.count()).select_from(t)
    for k, v in where.items():
        q = q.where(t.c[k] == v)
    return u.conn.execute(q).scalar()


# --- реестр ---------------------------------------------------------------------------------------









# --- запись / чтение ------------------------------------------------------------------------------

def test_roundtrip_solar_and_derived_columns(db, corpus, new_paper):
    p = new_paper("D001")
    rows = [_res("D001", 1, "PCE", "20.5", "3"), _res("D001", 2, "Voc", "abc", "x")]
    issues = [{"publication_id": "D001", "issue_id": "I1", "check_name": "range", "record_ids": "R001"}]
    with db.uow() as u:
        u.sub.replace(p.id, corpus.id, "solar", {"publications": [{"publication_id": "D001", "title": "T"}],
                                                  "results": rows, "issues": issues},
                      quote_found={"R001": True, "R002": None})
        t = u.sub.paper_tables(p.id)
        assert list(t) == list(TABLES) and t["materials"] == []
        assert t["results"][0]["note"] == "" and list(t["results"][0]) == list(TABLES["results"])
        got = {r.row_id: r for r in u.conn.execute(sa.select(S.sub_rows).where(
            S.sub_rows.c.table_name.in_(["results", "issues"])))}
        assert got["R001"].num_value == 20.5 and got["R001"].source_page == 3 and got["R001"].quote_found is True
        assert got["R002"].num_value is None and got["R002"].source_page is None and got["R002"].quote_found is None
        assert got["R001"].metric_name == "PCE" and got["R001"].domain == "solar"
        assert got["I1"].check_name == "range" and got["I1"].metric_name is None
        assert u.sub.paper_quotes(p.id) == {"R001": True, "R002": None}
        u.sub.replace(p.id, corpus.id, "solar", {"results": [_res("D001", 9)]})
        assert _count(u, paper_id=p.id) == 1






@pytest.fixture
def filled(db, corpus, new_paper):
    out = {}
    for pid, off in (("D001", 0), ("D002", 100)):
        p = out[pid] = new_paper(pid)
        rows = [_res(pid, off + i, ("PCE", "Voc", "FF")[i % 3], str(10 - i) if i != 4 else "",
                     page=str(i + 1) if i != 6 else "", device_id="DV1" if i < 5 else "DV2",
                     duplicate_of="R000" if i == 9 else "") for i in range(10)]
        with db.uow() as u:
            u.sub.replace(p.id, corpus.id, "solar", {"results": rows})
    return out


def _all(u, corpus, **kw):
    got, cur = [], None
    while True:
        rows, total, cur = u.sub.list_results(corpus.id, limit=6, cursor=cur, **kw)
        got += rows
        if cur is None:
            return got, total














# --- переезд sub_* -> sub_rows --------------------------------------------------------------------

def test_migration_copies_old_sub_tables_once(db, corpus, new_paper):
    p = new_paper("D001")
    old = [{"paper_id": p.id, "corpus_id": corpus.id, "position": i, "publication_id": "D001",
            "result_id": f"R{i}", "metric_name": "PCE", "metric_value": str(i), "source_page": "2"}
           for i in range(3)]
    with db.uow() as u:
        u.conn.execute(S.sub_results.insert(), old)
        u.conn.execute(S.sub_publications.insert(), [{"paper_id": p.id, "corpus_id": corpus.id, "position": 0,
                                                      "publication_id": "D001", "title": "T"}])
        u.conn.execute(S.sub_issues.insert(), [{"paper_id": p.id, "corpus_id": corpus.id, "position": 0,
                                                "publication_id": "D001", "issue_id": "I1", "check_name": "range"}])
        assert _count(u) == 0
    db.create_all()
    with db.uow() as u:
        assert _count(u) == 5 and _count(u, domain="solar") == 5
        t = u.sub.paper_tables(p.id)
        assert [r["result_id"] for r in t["results"]] == ["R0", "R1", "R2"] and t["publications"][0]["title"] == "T"
        rows, total, _ = u.sub.list_results(corpus.id, sort="value", dir="desc")
        assert total == 3 and rows[0]["metric_value"] == "2"
        assert u.sub.list_issues(corpus.id, check_names=["range"])[1] == 1
        assert u.sub.paper_quotes(p.id) == {"R0": None, "R1": None, "R2": None}
    db.create_all()
    with db.uow() as u:
        assert _count(u) == 5
        # старые таблицы не тронуты
        assert u.conn.execute(sa.select(sa.func.count()).select_from(S.sub_results)).scalar() == 3
