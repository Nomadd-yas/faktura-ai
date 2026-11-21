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

def test_registry_solar_matches_tables():
    d = get_domain("solar")
    assert d.tables == TABLES and list(d.tables) == list(TABLES)
    assert d.keys == ID_COLUMN and d.scored_metrics == EVAL_METRICS and len(d.scored_metrics) == 11
    assert set(d.closed_enums) == {f"{t}.{c}" for t, c in VOCAB}
    assert d.list_fields == {f"{t}.{c}" for t, c in LIST_FIELDS}
    assert {(k, *v) for k, v in d.refs.items()} == {(f"{t}.{c}", tgt, ID_COLUMN[tgt]) for t, c, tgt in REFERENCES}
    assert not d.summary_by_default


def test_registry_rag_matches_vendored_schema():
    raw = json.loads(RAG_SCHEMA_PATH.read_text(encoding="utf-8"))
    d = get_domain("rag")
    assert {k: list(v) for k, v in d.tables.items()} == raw["tables"] and len(d.tables) == 7
    assert d.keys == raw["keys"]
    assert {k: list(v) for k, v in d.closed_enums.items()} == raw["enums"]
    assert "results.metric_direction" in d.recommended_enums
    assert d.refs["results.config_id"] == ("configurations", "config_id")
    assert d.scored_metrics is None and d.summary_by_default and d.check_exit_review_ok
    assert set(DOMAINS) == {"solar", "rag"}


def test_registry_unknown_domain():
    with pytest.raises(AppError) as e:
        get_domain("nope")
    assert e.value.code == "domain_not_supported" and e.value.http_status == 422


def test_rag_schema_is_package_data():
    import tomllib
    from pathlib import Path

    cfg = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    assert cfg["tool"]["setuptools"]["package-data"]["faktura.domain"] == ["data/*.json"]


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


def test_roundtrip_rag(db, corpus, new_paper):
    p = new_paper("D001")
    tables = {"publications": [{"publication_id": "D001"}],
              "configurations": [{"publication_id": "D001", "config_id": "C1", "top_k": "5"}],
              "results": [{"publication_id": "D001", "result_id": "R1", "config_id": "C1", "metric_name": "EM",
                           "metric_value": "0.5", "source_page": "2"},
                          {"publication_id": "D001", "result_id": "R2", "config_id": "C2", "metric_name": "F1",
                           "metric_value": "0.7", "duplicate_of": "R1"}]}
    with db.uow() as u:
        u.sub.replace(p.id, corpus.id, "rag", tables)
        t = u.sub.paper_tables(p.id, "rag")
        assert list(t) == list(get_domain("rag").tables)
        assert t["configurations"][0]["top_k"] == "5" and t["datasets"] == []
        assert list(t["results"][0]) == list(get_domain("rag").tables["results"])
        rows, total, _ = u.sub.list_results(corpus.id, domain="rag", filters={"config_id": "C1"})
        assert total == 1 and rows[0]["result_id"] == "R1"
        assert u.sub.list_results(corpus.id, domain="rag", exclude_duplicates=True)[1] == 1
        assert u.sub.list_results(corpus.id, domain="rag", filters={"config_id": "zzz"})[1] == 0
        s = u.sub.paper_summary(p.id)
        assert s["results"] == 2 and s["configurations"] == 1 and s["publications"] == 1
        assert s["metrics"] == [{"metric_name": "EM", "n": 1}, {"metric_name": "F1", "n": 1}]


def test_chunked_insert_5000_rows(db, corpus, new_paper):
    p = new_paper("D001")
    rows = [_res("D001", i, "PCE", str(i % 50)) for i in range(5000)]
    with db.uow() as u:
        u.sub.replace(p.id, corpus.id, "solar", {"results": rows})
    with db.uow() as u:
        assert _count(u, paper_id=p.id) == 5000
        got = u.sub.paper_tables(p.id)["results"]
        assert [r["result_id"] for r in got] == [r["result_id"] for r in rows]
        _, total, nxt = u.sub.list_results(corpus.id, limit=10)
        assert total == 5000 and nxt


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


def test_list_results_filters(db, corpus, filled):
    with db.uow() as u:
        assert u.sub.list_results(corpus.id)[1] == 20
        assert u.sub.list_results(corpus.id, pids=["D002"])[1] == 10
        assert u.sub.list_results(corpus.id, metrics=["PCE"])[1] == 8
        assert u.sub.list_results(corpus.id, metrics=["Voc"], device_id="DV2")[1] == 2
        assert u.sub.list_results(corpus.id, exclude_duplicates=True)[1] == 18
        assert u.sub.list_results("other")[1] == 0


@pytest.mark.parametrize("sort,key", [("seq", None), ("publication", "publication_id"), ("metric", "metric_name")])
def test_keyset_sorts_and_cursor_signature(db, corpus, filled, sort, key):
    with db.uow() as u:
        got, total = _all(u, corpus, sort=sort)
        assert len(got) == total == 20 and len({r["result_id"] for r in got}) == 20
        if key:
            assert [r[key] for r in got] == sorted(r[key] for r in got)
        _, _, c1 = u.sub.list_results(corpus.id, sort=sort, limit=3)
        other = "value" if sort == "seq" else "seq"
        with pytest.raises(AppError):
            u.sub.list_results(corpus.id, sort=other, limit=3, cursor=c1)
        with pytest.raises(AppError):
            u.sub.list_results(corpus.id, sort="bogus")


@pytest.mark.parametrize("sort,col", [("value", "metric_value"), ("page", "source_page")])
@pytest.mark.parametrize("dir", ["asc", "desc"])
def test_numeric_sorts_blanks_last(db, corpus, filled, sort, col, dir):
    with db.uow() as u:
        got, total = _all(u, corpus, sort=sort, dir=dir)
    assert len(got) == total == 20
    nums = [float(r[col]) for r in got if r[col] != ""]
    assert nums == sorted(nums, reverse=dir == "desc")
    assert all(r[col] == "" for r in got[len(nums):]) and len(nums) < 20


def test_desc_publication_offset_cursor(db, corpus, filled):
    with db.uow() as u:
        got, _ = _all(u, corpus, sort="publication", dir="desc")
        assert [r["publication_id"] for r in got][0] == "D002" and len(got) == 20
        _, _, c1 = u.sub.list_results(corpus.id, sort="publication", dir="desc", limit=3)
        with pytest.raises(AppError):
            u.sub.list_results(corpus.id, sort="publication", dir="asc", limit=3, cursor=c1)


def test_list_issues_and_soft_delete(db, corpus, filled):
    p = filled["D001"]
    issues = [{"publication_id": "D001", "issue_id": f"I{i}", "check_name": "range" if i % 2 else "typo"}
              for i in range(1, 6)]
    with db.uow() as u:
        u.sub.replace(p.id, corpus.id, "solar", {"results": [_res("D001", 1)], "issues": issues})
        assert u.sub.list_issues(corpus.id)[1] == 5
        assert u.sub.list_issues(corpus.id, check_names=["range"])[1] == 3
        seen, cur = [], None
        while True:
            rows, _, cur = u.sub.list_issues(corpus.id, limit=2, cursor=cur)
            seen += [r["issue_id"] for r in rows]
            if cur is None:
                break
        assert seen == [f"I{i}" for i in range(1, 6)]
        u.papers.soft_delete(p.id)
    with db.uow() as u:
        assert u.sub.list_results(corpus.id)[1] == 10 and u.sub.list_issues(corpus.id)[1] == 0


def test_paper_summary_solar(db, corpus, filled):
    with db.uow() as u:
        s = u.sub.paper_summary(filled["D001"].id)
        assert s["results"] == 10
        assert sorted(m["n"] for m in s["metrics"]) == [3, 3, 4] and s["metrics"][0]["metric_name"] == "PCE"
        assert u.sub.counts_by_paper(corpus.id, "results") == {filled["D001"].id: 10, filled["D002"].id: 10}


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
