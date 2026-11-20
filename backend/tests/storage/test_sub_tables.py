"""Таблицы шаблона сдачи sub_*: схема 1:1 с эталоном, запись, чтение, фильтры, пагинация."""
from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from faktura.domain.errors import AppError
from faktura.storage import schema as S

GOLD = Path(__file__).resolve().parents[3] / "data" / "caseB_kejs5" / "dev_gold"
NAMES = ("publications", "materials", "devices", "layers", "measurements", "results", "issues")
SERVICE = {"seq", "paper_id", "corpus_id", "position"}


def _gold_headers(name: str) -> list[str]:
    line = (GOLD / f"{name}.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    return line.split(",")


@pytest.mark.data
@pytest.mark.parametrize("name", NAMES)
def test_schema_matches_gold_headers(name):
    t = S.SUB[name]
    assert t.name == f"sub_{name}"
    assert [c.name for c in t.columns if c.name not in SERVICE] == _gold_headers(name)
    for c in t.columns:
        if c.name not in SERVICE:
            assert isinstance(c.type, sa.Text) and not c.nullable


def test_service_columns_and_indexes():
    t = S.sub_results
    assert {"seq", "paper_id", "corpus_id", "position"} <= set(t.c.keys())
    assert t.c.seq.primary_key
    fk = next(iter(t.c.paper_id.foreign_keys))
    assert fk.column.table.name == "papers" and fk.ondelete == "CASCADE"
    idx = {tuple(c.name for c in i.columns) for i in t.indexes}
    assert {("paper_id", "position"), ("corpus_id", "publication_id"), ("corpus_id", "metric_name")} <= idx


def _res(pid, n, metric="PCE", **kw):
    return {"publication_id": pid, "result_id": f"R{n:03d}", "device_id": kw.get("device_id", "DV1"),
            "measurement_id": "MS1", "material_id": "M1", "metric_name": metric, "metric_value": str(n),
            "duplicate_of": kw.get("duplicate_of", "")}


@pytest.fixture
def filled(db, corpus, new_paper):
    papers = {}
    for pid, off in (("D001", 0), ("D002", 100)):
        p = new_paper(pid)
        papers[pid] = p
        rows = [_res(pid, off + i, metric=("PCE", "Voc", "FF")[i % 3], device_id="DV1" if i < 5 else "DV2",
                     duplicate_of="R000" if i == 9 else "") for i in range(10)]
        issues = [{"publication_id": pid, "issue_id": f"I{i}", "check_name": "range" if i % 2 else "contradiction",
                   "record_ids": f"R{i:03d}", "description": f"d{i}"} for i in range(1, 5)]
        with db.uow() as u:
            u.sub.replace(p.id, corpus.id, {"publications": [{"publication_id": pid, "title": "T"}],
                                            "results": rows, "issues": issues})
    return papers


def test_paper_tables_all_seven_keys_ordered(db, filled):
    with db.uow() as u:
        t = u.sub.paper_tables(filled["D001"].id)
    assert list(t) == list(NAMES)
    assert t["publications"][0]["title"] == "T" and t["publications"][0]["year"] == ""
    assert [r["result_id"] for r in t["results"]] == [f"R{i:03d}" for i in range(10)] or len(t["results"]) == 10
    assert t["materials"] == []
    assert list(t["results"][0]) == [c.name for c in S.sub_results.columns if c.name not in SERVICE]


def test_replace_overwrites_and_none_becomes_empty(db, corpus, filled):
    p = filled["D001"]
    with db.uow() as u:
        u.sub.replace(p.id, corpus.id, {"results": [{"publication_id": "D001", "result_id": "R1", "note": None}]})
        t = u.sub.paper_tables(p.id)
        other = u.sub.paper_tables(filled["D002"].id)
    assert len(t["results"]) == 1 and t["results"][0]["note"] == "" and t["issues"] == []
    assert len(other["results"]) == 10


def test_list_results_filters(db, corpus, filled):
    with db.uow() as u:
        rows, total, nxt = u.sub.list_results(corpus.id, limit=100)
        assert (len(rows), total, nxt) == (20, 20, None)
        assert u.sub.list_results(corpus.id, pids=["D002"])[1] == 10
        assert u.sub.list_results(corpus.id, metrics=["PCE"])[1] == 8
        r, tot, _ = u.sub.list_results(corpus.id, metrics=["Voc"], device_id="DV2")
        assert {x["metric_name"] for x in r} == {"Voc"} and {x["device_id"] for x in r} == {"DV2"}
        assert u.sub.list_results(corpus.id, exclude_duplicates=True)[1] == 18
        assert u.sub.list_results(corpus.id, measurement_id="nope")[1] == 0
        assert u.sub.list_results(corpus.id, measurement_id="MS1", material_id="M1")[1] == 20
        assert u.sub.list_results("other_corpus")[1] == 0


def test_list_results_pagination_keyset(db, corpus, filled):
    with db.uow() as u:
        seen, cur = [], None
        while True:
            rows, total, cur = u.sub.list_results(corpus.id, limit=7, cursor=cur)
            assert total == 20
            seen += [(r["publication_id"], r["result_id"]) for r in rows]
            if cur is None:
                break
        assert len(seen) == 20 and len(set(seen)) == 20 and seen[0] == ("D001", "R000")


@pytest.mark.parametrize("sort", ["publication", "metric"])
def test_list_results_sorts_and_cursor_signature(db, corpus, filled, sort):
    with db.uow() as u:
        got, cur = [], None
        while True:
            rows, _, cur = u.sub.list_results(corpus.id, sort=sort, limit=6, cursor=cur)
            got += rows
            if cur is None:
                break
        key = "metric_name" if sort == "metric" else "publication_id"
        assert len(got) == 20 and [r[key] for r in got] == sorted(r[key] for r in got)
        _, _, c1 = u.sub.list_results(corpus.id, sort=sort, limit=3)
        with pytest.raises(AppError):
            u.sub.list_results(corpus.id, sort="seq", limit=3, cursor=c1)
        with pytest.raises(AppError):
            u.sub.list_results(corpus.id, sort="bogus")
        with pytest.raises(AppError):
            u.sub.list_results(corpus.id, cursor="@@@")


def test_list_issues_filters_and_pages(db, corpus, filled):
    with db.uow() as u:
        rows, total, _ = u.sub.list_issues(corpus.id)
        assert total == 8 and len(rows) == 8
        assert u.sub.list_issues(corpus.id, pids=["D001"], check_names=["range"])[1] == 2
        seen, cur = [], None
        while True:
            rows, _, cur = u.sub.list_issues(corpus.id, limit=3, cursor=cur)
            seen += [(r["publication_id"], r["issue_id"]) for r in rows]
            if cur is None:
                break
        assert len(set(seen)) == 8


def test_soft_deleted_paper_hidden(db, corpus, filled):
    with db.uow() as u:
        u.papers.soft_delete(filled["D001"].id)
    with db.uow() as u:
        assert u.sub.list_results(corpus.id)[1] == 10
        assert u.sub.list_issues(corpus.id)[1] == 4
