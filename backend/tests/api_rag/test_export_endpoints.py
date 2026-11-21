"""§4g для области rag: dataset.json, dataset.xlsx, набор по всем разборам."""
from __future__ import annotations

import io
import zipfile

from .conftest import API, TABLES, upload


def test_rag_dataset_json(client, auth, rag_run):
    rid = rag_run["id"]
    r = client.get(f"{API}/runs/{rid}/dataset.json", headers=auth)
    assert r.status_code == 200
    assert f'filename="submission-rag-{rid}.json"' in r.headers["content-disposition"]
    j = r.json()
    assert list(j) == ["meta", *TABLES]
    assert list(j["meta"]) == ["generated_by", "area", "tables"] and j["meta"]["area"] == "rag"
    assert j["meta"]["tables"] == {t: len(j[t]) for t in TABLES} and j["results"]
    assert all(isinstance(p["in_scope"], bool) for p in j["publications"])
    assert all(isinstance(p["authors"], list) for p in j["publications"] if p["authors"] is not None)
    mv = [x["metric_value"] for x in j["results"] if x["metric_value"] is not None]
    assert mv and all(isinstance(v, (int, float)) for v in mv)
    assert any(v is None for x in j["results"] for v in x.values())


def test_rag_dataset_xlsx(client, auth, rag_run):
    rid = rag_run["id"]
    j = client.get(f"{API}/runs/{rid}/dataset.json", headers=auth).json()
    r = client.get(f"{API}/runs/{rid}/dataset.xlsx", headers=auth)
    assert r.status_code == 200 and f'filename="results-rag-{rid}.xlsx"' in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    sheet = z.read("xl/worksheets/sheet1.xml").decode()
    assert 'name="Факты"' in z.read("xl/workbook.xml").decode()
    assert sheet.count("<row ") == len(j["results"]) + 1 and "metric_name" in sheet
    first = next(x for x in j["results"] if x["metric_value"] is not None)
    assert f"<v>{first['metric_value']}</v>" in sheet


def test_rag_all_runs_zip_and_check(client, auth, pdfs, rag_run):
    second = upload(client, auth, list(pdfs.values())[:1]).json()
    assert second["id"] != rag_run["id"]
    r = client.get(f"{API}/submission.zip", params={"domain": "rag"}, headers=auth)
    assert r.status_code == 200, r.text
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert {f"{t}.csv" for t in TABLES} | {"PASSPORT.md", "passport.json"} == set(z.namelist())
    pubs = z.read("publications.csv").decode("utf-8-sig").splitlines()
    ids = [ln.split(",")[0] for ln in pubs[1:]]
    assert len(ids) == len(set(ids)) == int(r.headers["x-export-papers"])  # статья из двух разборов - один раз
    c = client.get(f"{API}/submission.zip", params={"domain": "rag", "check": "true"}, headers=auth)
    assert c.status_code == 200, c.text
    assert c.headers["x-check-status"] in ("ok", "warnings") and "check_report.txt" in zipfile.ZipFile(
        io.BytesIO(c.content)).namelist()
    assert client.get(f"{API}/submission.zip", params={"domain": "solar"}, headers=auth).status_code == 400
