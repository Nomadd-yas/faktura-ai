"""R2: область rag через API (мок на dev_gold): разбор, сводка статьи, results/issues, submission.zip + check."""
from __future__ import annotations

import csv
import io
import json
import os
import zipfile

import pytest

from faktura.domain.registry import get_domain

from .conftest import API, SMALL, upload

SPEC = get_domain("rag")


def test_run_done_and_counts_match_gold(client, auth, rag_run, gold):
    rid = rag_run["id"]
    run = client.get(f"{API}/runs/{rid}", headers=auth).json()
    assert run["domain"] == "rag" and run["status"] == "done"
    assert run["counts"]["done"] + run["counts"]["ocr"] == 3 and run["counts"]["error"] == 0
    assert all(f["status"] in ("done", "ocr") and f["has_data"] for f in run["files"])
    assert run["results"] == sum(len(gold["results"][p]) for p in SMALL)
    assert run["issues"] == sum(len(gold["issues"].get(p, [])) for p in SMALL)
    for pid in SMALL:
        r = client.get(f"{API}/papers/rag/{pid}", params={"run": rid}, headers=auth)
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == {"publication", "counts", "metrics", "issues", "paper_status", "meta"}
        assert body["paper_status"] == "has_data"
        assert body["counts"] == {t: len(gold[t].get(pid, [])) for t in SPEC.tables}
        assert sum(m["n"] for m in body["metrics"]) == len(gold["results"][pid])
        assert len(body["issues"]) == len(gold["issues"].get(pid, []))
        assert body["publication"]["publication_id"] == pid and rid in body["meta"]["run_ids"]


def test_paper_tables_pagination(client, auth, rag_run, gold):
    rid = rag_run["id"]
    pid = "D007"
    r = client.get(f"{API}/papers/rag/{pid}", params={"run": rid, "tables": "configurations,results", "limit": 100},
                   headers=auth).json()
    assert set(r["tables"]) == {"configurations", "results"}
    assert r["tables"]["configurations"]["total"] == len(gold["configurations"][pid])
    res = r["tables"]["results"]
    assert res["total"] == len(gold["results"][pid]) and len(res["data"]) == 100 and res["next_cursor"]
    ids = [x["result_id"] for x in res["data"]]
    cur = res["next_cursor"]
    while cur:
        page = client.get(f"{API}/papers/rag/{pid}", params={"run": rid, "tables": "results", "limit": 300,
                                                              "cursor": cur}, headers=auth).json()["tables"]["results"]
        ids += [x["result_id"] for x in page["data"]]
        cur = page["next_cursor"]
    assert ids == [x["result_id"] for x in gold["results"][pid]]
    bad = client.get(f"{API}/papers/rag/{pid}", params={"run": rid, "tables": "devices"}, headers=auth)
    assert bad.status_code in (400, 422) and bad.json()["error"]["code"] == "validation_error"
    assert client.get(f"{API}/papers/xyz/{pid}", headers=auth).status_code == 422


def test_results_filters_and_sort(client, auth, rag_run, gold):
    rid = rag_run["id"]
    total = sum(len(gold["results"][p]) for p in SMALL)
    first = client.get(f"{API}/results", params={"run": rid, "limit": 1000}, headers=auth).json()
    assert first["total"] == total and set(first["data"][0]) == {"row", "paper_title", "meta"}
    assert set(first["data"][0]["row"]) == set(SPEC.tables["results"])
    assert first["data"][0]["paper_title"]
    row = next(x["row"] for x in first["data"] if x["row"]["config_id"] and x["row"]["dataset_id"])
    for col in ("metric_name", "config_id", "dataset_id", "experiment_id", "task_id"):
        want = [r for p in SMALL for r in gold["results"][p] if r[col] == row[col]]
        got = client.get(f"{API}/results", params={"run": rid, col: row[col], "limit": 1000}, headers=auth).json()
        assert got["total"] == len(want), col  # config_id и т. п. совпадают в разных статьях
        assert all(x["row"][col] == row[col] for x in got["data"])
    both = client.get(f"{API}/results", params={"run": rid, "config_id": row["config_id"],
                                                 "pid": row["publication_id"], "limit": 1000}, headers=auth).json()
    assert both["total"] == len([r for r in gold["results"][row["publication_id"]]
                                 if r["config_id"] == row["config_id"]])
    seen, cur = [], None  # пагинация + сортировка в БД
    while True:
        p = {"run": rid, "limit": 200, "sort": "page", "dir": "desc"}
        if cur:
            p["cursor"] = cur
        page = client.get(f"{API}/results", params=p, headers=auth).json()
        seen += page["data"]
        cur = page["next_cursor"]
        if not cur:
            break
    assert len(seen) == total
    pages = [int(x["row"]["source_page"]) for x in seen if x["row"]["source_page"]]
    assert pages == sorted(pages, reverse=True)
    assert client.get(f"{API}/results", params={"run": rid, "device_id": "x"}, headers=auth).status_code == 400
    sc = client.get(f"{API}/results", params={"run": rid, "scored": "true", "limit": 1}, headers=auth).json()
    assert sc["total"] == total  # scored у rag игнорируется
    assert client.get(f"{API}/results", params={"run": rid, "limit": 1001}, headers=auth).status_code == 400


def test_issues_summary_list(client, auth, rag_run, gold):
    rid = rag_run["id"]
    n_iss = sum(len(gold["issues"].get(p, [])) for p in SMALL)
    iss = client.get(f"{API}/issues", params={"run": rid}, headers=auth).json()
    assert iss["total"] == n_iss and set(iss["data"][0]) == {"row", "paper_title"}
    s = client.get(f"{API}/runs/{rid}/summary", headers=auth).json()
    total = sum(len(gold["results"][p]) for p in SMALL)
    assert s["results"] == total and s["scored_results"] == total and s["issues_total"] == n_iss
    assert s["papers_in_scope"] == 3 and s["files_error"] == 0
    assert s["coverage"] and set(s["coverage"][0]) == {"metric", "n", "total", "results"}
    ns = [c["n"] for c in s["coverage"]]
    assert ns == sorted(ns, reverse=True) and s["coverage"][0]["total"] == 3
    lst = client.get(f"{API}/runs", headers=auth).json()
    assert lst["total"] == 1 and lst["data"][0]["domain"] == "rag"


def test_submission_zip_and_check(client, auth, rag_run, gold):
    rid = rag_run["id"]
    z = client.get(f"{API}/runs/{rid}/submission.zip", headers=auth)
    assert z.status_code == 200 and z.headers["x-export-tables"] == "7"
    zf = zipfile.ZipFile(io.BytesIO(z.content))
    assert sorted(zf.namelist()) == sorted([*(f"{t}.csv" for t in SPEC.tables), "PASSPORT.md", "passport.json"])
    for t, cols in SPEC.tables.items():
        rows = list(csv.DictReader(io.StringIO(zf.read(f"{t}.csv").decode("utf-8-sig"), newline="")))
        assert list(rows[0].keys()) == list(cols)
        assert len(rows) == sum(len(gold[t].get(p, [])) for p in SMALL), t
    passport = json.loads(zf.read("passport.json"))
    assert passport["collection"] == "rag" and len(passport["tables"]) == 7
    assert "results.result_origin" in passport["vocabularies"]["closed"]
    assert "не пересчитываются" in passport["normalization"] and passport["versions"]["model"]
    assert "Нормализация" in zf.read("PASSPORT.md").decode("utf-8")

    c = client.get(f"{API}/runs/{rid}/submission.zip", params={"check": "true"}, headers=auth)
    assert c.status_code == 200, c.text[:500]
    assert c.headers["x-check-status"] in ("ok", "warnings")
    rep = zipfile.ZipFile(io.BytesIO(c.content)).read("check_report.txt").decode("utf-8")
    assert "ERROR:" not in rep and "fontTools" not in rep


def test_unknown_domain(client, auth, pdfs):
    r = upload(client, auth, [pdfs["D020"]], domain="xyz")
    assert r.status_code == 422 and r.json()["error"]["code"] == "domain_not_supported"


@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("FAKTURA_TEST_D014"), reason="медленный: FAKTURA_TEST_D014=1 (D014, 28 тыс. строк)")
def test_d014_28k_save_time(client, auth, rag_dir, tmp_path, gold, caplog):
    import logging
    import shutil
    import time

    caplog.set_level(logging.INFO)

    d = tmp_path / "big"
    d.mkdir()
    shutil.copy(rag_dir / "dev" / "D014.pdf", d / "D014.pdf")
    t0 = time.time()
    r = upload(client, auth, [d / "D014.pdf"])
    took = time.time() - t0
    assert r.status_code == 202
    rid = r.json()["id"]
    run = client.get(f"{API}/runs/{rid}", headers=auth).json()
    assert run["status"] == "done" and run["results"] == len(gold["results"]["D014"])
    print(f"\nD014: разбор целиком {took:.1f} с, results={run['results']}")
    t1 = time.time()
    page = client.get(f"{API}/results", params={"run": rid, "metric_name": "EM", "limit": 100, "sort": "value"},
                      headers=auth).json()
    print(f"/results (фильтр+sort) {time.time() - t1:.2f} с, total={page['total']}")
    for r in caplog.records:
        if r.getMessage().startswith("rag D014"):
            print(r.getMessage())
    assert took < 300


def _scan_pdf(path):
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 300, 400))
    pix.clear_with(180)
    page.insert_image(page.rect, pixmap=pix)
    doc.save(path)
    doc.close()
    return path


def test_scan_gets_ocr_status(client, auth, tmp_path, gold):
    d = tmp_path / "scans"
    d.mkdir()
    r = upload(client, auth, [_scan_pdf(d / "D020.pdf"), _scan_pdf(d / "ZZZ.pdf")])
    assert r.status_code == 202, r.text
    rid = r.json()["id"]
    run = client.get(f"{API}/runs/{rid}", headers=auth).json()
    by = {f["id"]: f for f in run["files"]}
    assert by["D020"]["status"] == "ocr" and by["D020"]["status_label"] == "скан, распознано OCR"
    assert by["D020"]["has_data"] and by["D020"]["results"] == len(gold["results"]["D020"])
    assert by["ZZZ"]["status"] == "nodata"  # скан без результатов остаётся nodata
    assert run["counts"]["ocr"] == 1 and run["counts"]["nodata"] == 1 and run["status"] == "done"
    p = client.get(f"{API}/papers/rag/D020", params={"run": rid}, headers=auth).json()
    assert p["paper_status"] == "has_data" and p["meta"]["is_scan"] is True
    res = client.get(f"{API}/results", params={"run": rid, "limit": 5}, headers=auth).json()
    assert all(x["meta"]["quote_found"] is None for x in res["data"])  # у скана цитаты не проверяются
    s = client.get(f"{API}/runs/{rid}/summary", headers=auth).json()
    assert s["papers_in_scope"] == 2
