"""CRUD корпусов, restore, X-Action-Id, overview и summary (REQ-071, REQ-072, REQ-100, REQ-112).

Контракт: api_contract §4.4 (№99-105, 116), §1.6; spec/13 §13.2, §13.8.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

API = "/api/v1"


def test_routers_exported():
    from fastapi import APIRouter

    from faktura.api.corpus import ROUTERS

    assert ROUTERS and all(isinstance(r, APIRouter) for r in ROUTERS)


def test_requires_auth(client):
    assert client.get(f"{API}/corpora").status_code == 401
    assert client.get(f"{API}/corpus/overview").status_code == 401


# ---- POST /corpora ---------------------------------------------------------------------------------------------
def test_create_corpus_201_shape_and_action(client, auth):
    r = client.post(f"{API}/corpora", json={"name": "Солнечные", "collection": "solar"}, headers=auth)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["corpus_id"].startswith("crp_")
    assert body["name"] == "Солнечные" and body["collection"] == "solar" and body["kind"] == "regular"
    assert body["profile_id"] == "prf_solar_tz_v1"
    for k in ("files_total", "papers_done", "papers_failed", "facts", "open_issues"):
        assert body[k] == 0
    assert body["action_id"].startswith("act_")
    assert r.headers["X-Action-Id"] == body["action_id"]


@pytest.mark.parametrize("body,field", [
    ({"name": "", "collection": "solar"}, "name"),
    ({"name": "   ", "collection": "solar"}, "name"),
    ({"collection": "solar"}, "name"),
    ({"name": "X", "collection": "biology"}, "collection"),
])
def test_create_corpus_validation_400(client, auth, body, field):
    r = client.post(f"{API}/corpora", json=body, headers=auth)
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "validation_error" and err["field"] == field and err["message"]


def test_create_corpus_rag_collection_not_supported_in_wave1(client, auth):
    # У-21: коллекция только solar; rag - задел модели данных
    r = client.post(f"{API}/corpora", json={"name": "RAG", "collection": "rag"}, headers=auth)
    assert r.status_code == 400


def test_create_corpus_unknown_profile(client, auth):
    r = client.post(f"{API}/corpora", json={"name": "X", "collection": "solar", "profile_id": "prf_nope"},
                    headers=auth)
    assert r.status_code in (400, 404)


# ---- GET /corpora, GET /corpora/{id} -----------------------------------------------------------------------
def test_list_corpora_page_shape_and_collection_filter(client, auth, mkcorpus):
    a, b = mkcorpus("A"), mkcorpus("B")
    r = client.get(f"{API}/corpora", headers=auth)
    assert r.status_code == 200
    body = r.json()
    ids = [c["corpus_id"] for c in body["data"]]
    assert {a["corpus_id"], b["corpus_id"]} <= set(ids)
    assert "total" in body and "next_cursor" in body
    assert client.get(f"{API}/corpora?collection=solar", headers=auth).json()["total"] >= 2


def test_list_corpora_pagination_cursor(client, auth, mkcorpus):
    for i in range(3):
        mkcorpus(f"P{i}")
    first = client.get(f"{API}/corpora?limit=2", headers=auth).json()
    assert len(first["data"]) == 2 and first["next_cursor"]
    second = client.get(f"{API}/corpora?limit=2&cursor={first['next_cursor']}", headers=auth).json()
    assert {c["corpus_id"] for c in first["data"]}.isdisjoint({c["corpus_id"] for c in second["data"]})


def test_list_corpora_excludes_soft_deleted(client, auth, mkcorpus):
    c = mkcorpus("ToDelete")
    assert client.delete(f"{API}/corpora/{c['corpus_id']}", headers=auth).status_code == 200
    ids = [x["corpus_id"] for x in client.get(f"{API}/corpora", headers=auth).json()["data"]]
    assert c["corpus_id"] not in ids


def test_list_corpora_bad_limit(client, auth):
    assert client.get(f"{API}/corpora?limit=abc", headers=auth).status_code in (400, 422)


def test_get_corpus_and_404(client, auth, mkcorpus):
    c = mkcorpus("One")
    r = client.get(f"{API}/corpora/{c['corpus_id']}", headers=auth)
    assert r.status_code == 200 and r.json()["name"] == "One"
    r = client.get(f"{API}/corpora/crp_missing", headers=auth)
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


# ---- PATCH -----------------------------------------------------------------------------------------------------
def test_patch_corpus_rename_with_action(client, auth, mkcorpus):
    c = mkcorpus("Old")
    r = client.patch(f"{API}/corpora/{c['corpus_id']}", json={"name": "New"}, headers=auth)
    assert r.status_code == 200
    assert r.json()["name"] == "New" and r.json()["action_id"] == r.headers["X-Action-Id"]
    assert client.get(f"{API}/corpora/{c['corpus_id']}", headers=auth).json()["name"] == "New"


def test_patch_corpus_validation_and_404(client, auth, mkcorpus):
    c = mkcorpus()
    r = client.patch(f"{API}/corpora/{c['corpus_id']}", json={"name": ""}, headers=auth)
    assert r.status_code == 400 and r.json()["error"]["field"] == "name"
    assert client.patch(f"{API}/corpora/crp_missing", json={"name": "x"}, headers=auth).status_code == 404


def test_patch_corpus_profile_id(client, auth, mkcorpus):
    c = mkcorpus()
    r = client.patch(f"{API}/corpora/{c['corpus_id']}", json={"profile_id": "prf_solar_tz_v1"}, headers=auth)
    assert r.status_code == 200 and r.json()["profile_id"] == "prf_solar_tz_v1"


# ---- DELETE / restore --------------------------------------------------------------------------------------------
def test_delete_soft_then_restore(client, auth, mkcorpus):
    c = mkcorpus("Del")
    r = client.delete(f"{API}/corpora/{c['corpus_id']}", headers=auth)
    assert r.status_code == 200
    assert r.json()["action_id"] == r.headers["X-Action-Id"]
    assert client.get(f"{API}/corpora/{c['corpus_id']}", headers=auth).status_code == 404
    r = client.post(f"{API}/corpora/{c['corpus_id']}/restore", headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["corpus_id"] == c["corpus_id"] and r.json()["deleted_at"] is None
    assert r.json()["action_id"] == r.headers["X-Action-Id"]
    assert client.get(f"{API}/corpora/{c['corpus_id']}", headers=auth).status_code == 200


def test_delete_404(client, auth):
    assert client.delete(f"{API}/corpora/crp_missing", headers=auth).status_code == 404


def test_restore_unknown_404(client, auth):
    assert client.post(f"{API}/corpora/crp_missing/restore", headers=auth).status_code == 404


def test_restore_after_30_days_gone_410(ctx, mkcorpus):
    client, services, auth = ctx
    c = mkcorpus("Old")
    client.delete(f"{API}/corpora/{c['corpus_id']}", headers=auth)
    # окно восстановления 30 суток: сдвигаем deleted_at в прошлое напрямую в БД
    # (исполнителю модуля: нужен CorporaRepo.update(corpus_id, **fields) либо аналог)
    with services.db.uow() as uow:
        uow.corpora.update(c["corpus_id"], deleted_at=datetime.now(timezone.utc) - timedelta(days=31))
    r = client.post(f"{API}/corpora/{c['corpus_id']}/restore", headers=auth)
    assert r.status_code == 410 and r.json()["error"]["code"] == "gone"


def test_restore_within_window_edge_29_days_ok(ctx, mkcorpus):
    client, services, auth = ctx
    c = mkcorpus("Edge")
    client.delete(f"{API}/corpora/{c['corpus_id']}", headers=auth)
    with services.db.uow() as uow:
        uow.corpora.update(c["corpus_id"], deleted_at=datetime.now(timezone.utc) - timedelta(days=29))
    assert client.post(f"{API}/corpora/{c['corpus_id']}/restore", headers=auth).status_code == 200


def test_delete_during_active_run_409(make_client, h, make_pdf):
    ex = h.GatedExecutor()
    client, _, auth = make_client(executor=ex)
    c = client.post(f"{API}/corpora", json={"name": "Busy", "collection": "solar"}, headers=auth).json()
    pdf = make_pdf(["page"], name="B001.pdf").read_bytes()
    h.upload_zip(client, auth, c["corpus_id"], h.make_zip({"B001.pdf": pdf}))
    assert h.start_run(client, auth, c["corpus_id"]).status_code == 202
    r = client.delete(f"{API}/corpora/{c['corpus_id']}", headers=auth)
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"
    ex.open()
    ex.wait_idle()


# ---- чужое рабочее пространство ----------------------------------------------------------------------------
def test_corpus_of_other_workspace_is_404(ctx, mkcorpus):
    from faktura.api.base.auth import issue_tokens
    from faktura.domain.entities import User

    client, _, auth = ctx
    c = mkcorpus("Mine")
    other = User(id="usr_other", workspace_id="ws_other", email="o@example.com")
    token = issue_tokens(other, remember=False, now=datetime.now(timezone.utc)).access_token
    r = client.get(f"{API}/corpora/{c['corpus_id']}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code in (401, 404)


# ---- GET /corpus/overview (REQ-100, REQ-112) ---------------------------------------------------------------
def test_overview_empty_true_without_corpora(client, auth):
    r = client.get(f"{API}/corpus/overview", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["empty"] is True and body["corpora"] == []


def test_overview_lists_corpus_with_zero_counters(client, auth, mkcorpus):
    c = mkcorpus("Ov")
    body = client.get(f"{API}/corpus/overview", headers=auth).json()
    assert body["empty"] is False
    row = next(x for x in body["corpora"] if x["corpus_id"] == c["corpus_id"])
    assert row["files"] == {"total": 0, "done": 0, "failed": 0, "processing": 0}
    assert row["facts"] == 0 and row["open_issues"] == 0
    assert row["active_run"] is None and row["last_run"] is None
    assert set(body["totals"]) >= {"papers", "facts", "open_issues", "errors_by_rule_top"}


def test_overview_collection_filter(client, auth, mkcorpus):
    mkcorpus("S")
    assert client.get(f"{API}/corpus/overview?collection=solar", headers=auth).json()["empty"] is False
    assert client.get(f"{API}/corpus/overview?collection=rag", headers=auth).json()["empty"] is True


@pytest.mark.data
def test_overview_counters_after_run(client, auth, mkcorpus, h, dev_zip3):
    c = mkcorpus("Run")
    assert h.upload_zip(client, auth, c["corpus_id"], dev_zip3).status_code == 202
    run = h.start_run(client, auth, c["corpus_id"]).json()
    final = h.wait_run(client, auth, run["run_id"])
    assert final["status"] in ("done", "done_with_errors")
    body = client.get(f"{API}/corpus/overview", headers=auth).json()
    row = next(x for x in body["corpora"] if x["corpus_id"] == c["corpus_id"])
    assert row["files"]["total"] == 3 and row["files"]["done"] == 3
    assert row["facts"] > 0
    assert row["last_run"]["run_id"] == run["run_id"] and row["active_run"] is None
    assert body["totals"]["papers"] >= 3 and body["totals"]["facts"] == row["facts"]


@pytest.mark.perf
def test_overview_perf_100ms(client, auth, mkcorpus):
    for i in range(20):
        mkcorpus(f"perf{i}")
    client.get(f"{API}/corpus/overview", headers=auth)  # прогрев
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        r = client.get(f"{API}/corpus/overview", headers=auth)
        times.append(time.perf_counter() - t0)
        assert r.status_code == 200
    assert max(times) <= 0.1 * 3, f"overview {max(times) * 1000:.0f} мс (порог 100 мс, допуск x3)"


# ---- GET /corpora/{id}/summary (spec/13 §13.8) ---------------------------------------------------------------
def test_summary_empty_corpus_shape(client, auth, mkcorpus):
    c = mkcorpus("Sum")
    r = client.get(f"{API}/corpora/{c['corpus_id']}/summary", headers=auth)
    assert r.status_code == 200
    s = r.json()
    assert s["corpus_id"] == c["corpus_id"] and s["collection"] == "solar"
    assert s["profile"]["id"] == "prf_solar_tz_v1" and "version" in s["profile"]
    assert set(s["files"]) >= {"total", "done", "failed", "duplicates", "processing"}
    assert set(s["papers"]) >= {"total", "in_scope", "out_of_scope", "possible_duplicates", "with_issues"}
    assert set(s["facts"]) >= {"total", "accepted", "rejected", "new", "manual", "norm_failed", "from_table",
                               "from_text", "from_figure", "mean_confidence"}
    assert s["issues"]["open"] == 0 and isinstance(s["issues"]["by_rule"], list)
    assert isinstance(s["coverage"], list) and isinstance(s["not_found"], list) and s["errors"] == []
    assert s["last_run"] is None


def test_summary_404(client, auth):
    assert client.get(f"{API}/corpora/crp_missing/summary", headers=auth).status_code == 404


@pytest.mark.data
def test_summary_after_run_matches_spec_13_8(client, auth, mkcorpus, h, dev_zip3):
    c = mkcorpus("SumRun")
    h.upload_zip(client, auth, c["corpus_id"], dev_zip3)
    run = h.start_run(client, auth, c["corpus_id"]).json()
    h.wait_run(client, auth, run["run_id"])
    s = client.get(f"{API}/corpora/{c['corpus_id']}/summary", headers=auth).json()
    assert s["files"]["total"] == 3 and s["files"]["done"] == 3
    assert s["papers"]["total"] == 3 and s["facts"]["total"] > 0
    assert s["last_run"]["run_id"] == run["run_id"] and s["last_run"]["status"] in ("done", "done_with_errors")
    for rule in s["issues"]["by_rule"]:  # только каталог проверок (У-18)
        assert set(rule) >= {"check_name", "title", "severity", "open", "checked"}
    pce = next(x for x in s["coverage"] if x["field"] in ("pce", "PCE"))
    assert pce["papers_total"] == 3 and 0 <= pce["pct"] <= 100
    assert set(pce) >= {"field", "label", "papers_with", "papers_total", "pct"}
    for nf in s["not_found"]:
        assert nf["papers_with"] == 0
    assert s["facts"]["from_table"] + s["facts"]["from_text"] + s["facts"]["from_figure"] <= s["facts"]["total"]
