"""Прогоны, отмена, /extract и выход прогона (REQ-075, REQ-076, REQ-077, REQ-114, REQ-115).

Контракт: api_contract §4.5 (№110-115), §3.4, У-32; spec/13 §13.5-13.6.
"""
from __future__ import annotations

import pytest

API = "/api/v1"
COUNT_KEYS = {"total", "queued", "processing", "done", "failed", "skipped", "cancelled"}


def _gated_corpus(make_client, h, make_pdf, n: int = 2):
    ex = h.GatedExecutor()
    client, services, auth = make_client(executor=ex)
    c = client.post(f"{API}/corpora", json={"name": "Gate", "collection": "solar"}, headers=auth).json()
    z = h.make_zip({f"G{i}.pdf": make_pdf([f"text {i}"], name=f"G{i}.pdf").read_bytes() for i in range(n)})
    assert h.upload_zip(client, auth, c["corpus_id"], z).status_code == 202
    return ex, client, services, auth, c


# ---- POST /corpora/{id}/runs ---------------------------------------------------------------------------------
@pytest.mark.data
def test_run_on_three_dev_pdfs_reaches_done(client, auth, mkcorpus, dev_zip3, h):
    c = mkcorpus()
    h.upload_zip(client, auth, c["corpus_id"], dev_zip3)
    r = h.start_run(client, auth, c["corpus_id"], "new_only")
    assert r.status_code == 202, r.text
    run = r.json()
    assert run["run_id"].startswith("run_") and run["corpus_id"] == c["corpus_id"] and run["kind"] == "new_only"
    assert set(run["counts"]) >= COUNT_KEYS and run["counts"]["total"] == 3
    assert run["output"] is None
    final = h.wait_run(client, auth, run["run_id"])
    assert final["status"] == "done" and final["progress"] == 100
    assert final["counts"]["done"] == 3 and final["counts"]["queued"] == 0
    assert final["facts_found"] > 0 and final["finished_at"]
    # файлы стали done и ссылаются на статьи
    files = client.get(f"{API}/corpora/{c['corpus_id']}/files", headers=auth).json()["data"]
    assert all(f["status"] == "done" and f["paper_id"] for f in files)


def test_run_defaults_and_shape_gated(make_client, h, make_pdf):
    ex, client, _, auth, c = _gated_corpus(make_client, h, make_pdf)
    r = h.start_run(client, auth, c["corpus_id"], "full")
    assert r.status_code == 202
    run = r.json()
    assert run["status"] in ("queued", "running")
    assert run["concurrency"] == 4 and run["keep_review"] is True and run["model_version"]
    assert run["paused"] is False and run["pause_reason"] is None
    assert run["counts"]["total"] == 2
    ex.open()
    ex.wait_idle()


@pytest.mark.parametrize("body", [
    {"kind": "bogus"},
    {},
    {"kind": "full", "concurrency": 0},
    {"kind": "full", "concurrency": 1000},
    {"kind": "selected"},                       # selected без file_ids
    {"kind": "selected", "file_ids": []},
])
def test_run_validation_400(client, auth, mkcorpus, body):
    c = mkcorpus()
    r = client.post(f"{API}/corpora/{c['corpus_id']}/runs", json=body, headers=auth)
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error"


def test_run_unknown_corpus_404(client, auth, h):
    assert h.start_run(client, auth, "crp_missing").status_code == 404


def test_run_selected_unknown_file_id_400_or_404(client, auth, mkcorpus):
    c = mkcorpus()
    r = client.post(f"{API}/corpora/{c['corpus_id']}/runs", json={"kind": "selected", "file_ids": ["cf_nope"]},
                    headers=auth)
    assert r.status_code in (400, 404)


def test_run_on_empty_corpus_is_ok_or_400(client, auth, mkcorpus, h):
    c = mkcorpus()
    r = h.start_run(client, auth, c["corpus_id"], "new_only")
    assert r.status_code in (202, 400)
    if r.status_code == 202:
        assert r.json()["counts"]["total"] == 0


def test_second_run_during_active_409_conflict(make_client, h, make_pdf):
    ex, client, _, auth, c = _gated_corpus(make_client, h, make_pdf)
    assert h.start_run(client, auth, c["corpus_id"]).status_code == 202
    r = h.start_run(client, auth, c["corpus_id"])
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "conflict" and err["message"]
    ex.open()
    ex.wait_idle()
    # после завершения новый прогон снова разрешён
    assert h.start_run(client, auth, c["corpus_id"], "full").status_code == 202


def test_runs_of_different_corpora_independent(make_client, h, make_pdf):
    ex, client, _, auth, c1 = _gated_corpus(make_client, h, make_pdf)
    c2 = client.post(f"{API}/corpora", json={"name": "Second", "collection": "solar"}, headers=auth).json()
    h.upload_zip(client, auth, c2["corpus_id"], h.make_zip({"S1.pdf": make_pdf(["s"], name="S1.pdf").read_bytes()}))
    assert h.start_run(client, auth, c1["corpus_id"]).status_code == 202
    assert h.start_run(client, auth, c2["corpus_id"]).status_code == 202
    ex.open()
    ex.wait_idle()


# ---- GET runs --------------------------------------------------------------------------------------------------
def test_list_runs_newest_first_and_get_run(make_client, h, make_pdf):
    ex, client, _, auth, c = _gated_corpus(make_client, h, make_pdf)
    first = h.start_run(client, auth, c["corpus_id"], "new_only").json()
    ex.open()
    ex.wait_idle()
    h.wait_run(client, auth, first["run_id"])
    second = h.start_run(client, auth, c["corpus_id"], "full").json()
    h.wait_run(client, auth, second["run_id"])
    data = client.get(f"{API}/corpora/{c['corpus_id']}/runs", headers=auth).json()["data"]
    assert [r["run_id"] for r in data][:2] == [second["run_id"], first["run_id"]]
    g = client.get(f"{API}/runs/{first['run_id']}", headers=auth)
    assert g.status_code == 200 and g.json()["run_id"] == first["run_id"]


def test_get_run_404_and_list_unknown_corpus_404(client, auth):
    assert client.get(f"{API}/runs/run_missing", headers=auth).status_code == 404
    assert client.get(f"{API}/corpora/crp_missing/runs", headers=auth).status_code == 404


# ---- POST /runs/{id}/cancel ------------------------------------------------------------------------------------
def test_cancel_active_run_cancelling_then_cancelled(make_client, h, make_pdf):
    ex, client, _, auth, c = _gated_corpus(make_client, h, make_pdf, n=3)
    run = h.start_run(client, auth, c["corpus_id"]).json()
    r = client.post(f"{API}/runs/{run['run_id']}/cancel", headers=auth)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "cancelling"
    ex.open()
    ex.wait_idle()
    final = h.wait_run(client, auth, run["run_id"], until=("cancelled", "done", "failed"))
    assert final["status"] == "cancelled"
    assert final["counts"]["cancelled"] + final["counts"]["done"] == 3


def test_cancel_finished_run_409(make_client, h, make_pdf):
    ex, client, _, auth, c = _gated_corpus(make_client, h, make_pdf, n=1)
    ex.open()
    run = h.start_run(client, auth, c["corpus_id"]).json()
    ex.wait_idle()
    h.wait_run(client, auth, run["run_id"])
    r = client.post(f"{API}/runs/{run['run_id']}/cancel", headers=auth)
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"


def test_cancel_unknown_404(client, auth):
    assert client.post(f"{API}/runs/run_missing/cancel", headers=auth).status_code == 404


# ---- POST /extract + GET /runs/{id}/output (У-32) ------------------------------------------------------------
def _extract(client, auth, data: bytes, **form):
    return client.post(f"{API}/extract", headers=auth, data={"domain": "solar", **form},
                       files={"archive": ("in.zip", data, "application/zip")})


def test_extract_202_creates_extract_corpus_and_run(make_client, h, make_pdf):
    ex = h.GatedExecutor()
    client, _, auth = make_client(executor=ex)
    z = h.make_zip({"E1.pdf": make_pdf(["e"], name="E1.pdf").read_bytes()})
    r = _extract(client, auth, z)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["run_id"].startswith("run_") and body["corpus_id"].startswith("crp_")
    corpus = client.get(f"{API}/corpora/{body['corpus_id']}", headers=auth).json()
    assert corpus["kind"] == "extract" and corpus["name"].startswith("Извлечение")
    run = client.get(f"{API}/runs/{body['run_id']}", headers=auth).json()
    assert run["kind"] == "full"
    ex.open()
    ex.wait_idle()


def test_output_before_finish_409_run_not_finished(make_client, h, make_pdf):
    ex = h.GatedExecutor()
    client, _, auth = make_client(executor=ex)
    z = h.make_zip({"E1.pdf": make_pdf(["e"], name="E1.pdf").read_bytes()})
    run_id = _extract(client, auth, z).json()["run_id"]
    r = client.get(f"{API}/runs/{run_id}/output", headers=auth)
    assert r.status_code == 409 and r.json()["error"]["code"] == "run_not_finished"
    ex.open()
    ex.wait_idle()


@pytest.mark.data
def test_extract_output_zip_has_7_csv_and_meta(client, auth, dev_zip3, h):
    r = _extract(client, auth, dev_zip3)
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    final = h.wait_run(client, auth, run_id)
    assert final["status"] in ("done", "done_with_errors")
    assert final["output"]["available"] is True and final["output"]["url"].endswith(f"/runs/{run_id}/output")
    assert final["output"]["csv_files"] == 7
    out = client.get(f"{API}/runs/{run_id}/output", headers=auth)
    assert out.status_code == 200
    assert out.headers["content-type"].startswith("application/zip")
    assert f'filename="{run_id}.zip"' in out.headers["content-disposition"]
    assert out.headers.get("X-Check-Status") in ("ok", "warnings", "failed")
    names = h.zip_names(out.content)
    for n in h.CSV_NAMES:
        assert n in names
    assert any(n.startswith("_meta/") for n in names)
    assert "_meta/manifest.json" in names


@pytest.mark.data
def test_regular_run_output_404(client, auth, mkcorpus, dev_zip3, h):
    c = mkcorpus()
    h.upload_zip(client, auth, c["corpus_id"], dev_zip3)
    run = h.start_run(client, auth, c["corpus_id"]).json()
    h.wait_run(client, auth, run["run_id"])
    r = client.get(f"{API}/runs/{run['run_id']}/output", headers=auth)
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_output_unknown_run_404(client, auth):
    assert client.get(f"{API}/runs/run_missing/output", headers=auth).status_code == 404


@pytest.mark.parametrize("kw,code", [
    ({"data": {"domain": "rag"}}, 400),         # домен кроме solar - W2/задел
    ({"data": {}}, 400),                        # нет domain
])
def test_extract_validation(client, auth, h, make_pdf, kw, code):
    z = h.make_zip({"E1.pdf": make_pdf(["e"], name="E1.pdf").read_bytes()})
    r = client.post(f"{API}/extract", headers=auth, files={"archive": ("in.zip", z, "application/zip")}, **kw)
    assert r.status_code == code and r.json()["error"]["code"] == "validation_error"


def test_extract_without_archive_400(client, auth):
    r = client.post(f"{API}/extract", headers=auth, data={"domain": "solar"})
    assert r.status_code == 400


def test_extract_archive_not_zip_415(client, auth):
    assert _extract(client, auth, b"definitely not zip").status_code == 415


def test_extract_zip_too_large_413(make_client, h):
    import os

    client, _, auth = make_client(max_zip_mb=1)
    z = h.make_zip({"P.pdf": b"%PDF-1.4\n" + os.urandom(2 * 1024 * 1024)})
    assert _extract(client, auth, z).status_code == 413


def test_extract_zip_without_pdfs_422_or_400(client, auth, h):
    r = _extract(client, auth, h.make_zip({"a.txt": b"hello"}))
    assert r.status_code in (400, 422)


def test_extract_unknown_profile_400_or_404(client, auth, h, make_pdf):
    z = h.make_zip({"E1.pdf": make_pdf(["e"], name="E1.pdf").read_bytes()})
    assert _extract(client, auth, z, profile_id="prf_nope").status_code in (400, 404)
