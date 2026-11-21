"""§4g: dataset.json, dataset.xlsx и набор по всем разборам (solar)."""
from __future__ import annotations

import csv
import io
import re
import zipfile

from faktura.api.corpus.exports import results_xlsx

from .conftest import API, upload
from .test_owner_contract import auth_b  # noqa: F401 - фикстура второго пользователя

SOLAR_NUM = {"year", "metric_value", "normalized_value", "source_page", "n_devices"}


def _csv(z: zipfile.ZipFile, table: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(z.read(f"{table}.csv").decode("utf-8-sig"))))


def _review(client, auth, run, rid, **body):
    return client.patch(f"{API}/runs/{run}/papers/solar/D001/results/{rid}/review", json=body, headers=auth)


def _d001_results(client, auth, run):
    return client.get(f"{API}/papers/solar/D001", params={"run": run}, headers=auth).json()["results"]


def test_dataset_json_shape(client, auth, run_two):
    rid = run_two["id"]
    r = client.get(f"{API}/runs/{rid}/dataset.json", headers=auth)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert f'filename="submission-{rid}.json"' in r.headers["content-disposition"]
    assert r.headers["x-export-papers"] == "2"
    j = r.json()
    names = ["publications", "materials", "devices", "layers", "measurements", "results", "issues"]
    assert list(j) == ["meta", *names]
    assert j["meta"]["generated_by"] == "Фактура AI" and "area" not in j["meta"]
    assert j["meta"]["tables"] == {n: len(j[n]) for n in names} and len(j["results"]) > 0
    from faktura.domain.tables import TABLES
    for n in names:
        assert all(list(row) == list(TABLES[n]) for row in j[n])
    res = j["results"][0]
    assert isinstance(res["metric_value"], (int, float)) and isinstance(res["source_page"], int)
    assert res["spread_value"] is None or isinstance(res["spread_value"], (int, float))
    assert any(v is None for row in j["results"] for v in row.values())  # пустое - null, не ""
    assert not any(v == "" for n in names for row in j[n] for v in row.values())
    assert isinstance(j["publications"][0]["in_scope"], bool) and isinstance(j["publications"][0]["year"], int)
    assert all(isinstance(m["device_id"], list) for m in j["measurements"] if m["device_id"] is not None)


def test_dataset_json_matches_zip_rows(client, auth, run_two):
    rid = run_two["id"]
    j = client.get(f"{API}/runs/{rid}/dataset.json", headers=auth).json()
    z = zipfile.ZipFile(io.BytesIO(client.get(f"{API}/runs/{rid}/submission.zip", headers=auth).content))
    for t in ("publications", "results", "issues"):
        assert len(_csv(z, t)) == len(j[t])
    assert [r["result_id"] for r in _csv(z, "results")] == [r["result_id"] for r in j["results"]]


def test_dataset_json_applies_v2_reviews(client, auth, pdfs):
    run = upload(client, auth, [pdfs["D001"]]).json()["id"]
    rows = _d001_results(client, auth, run)
    a, b = rows[0]["result_id"], rows[1]["result_id"]
    assert _review(client, auth, run, a, status="rejected").status_code == 200
    assert _review(client, auth, run, b, status="accepted", corrected_value="12.5").status_code == 200
    j = client.get(f"{API}/runs/{run}/dataset.json", headers=auth).json()
    by = {r["result_id"]: r for r in j["results"]}
    assert a not in by
    assert by[b]["normalized_value"] == 12.5 and by[b]["metric_value"] == 12.5
    assert "Исправлено вручную" in by[b]["note"]
    assert all(a not in (i["record_ids"] or []) for i in j["issues"])
    # xlsx тоже без отклонённой строки и с исправленным значением
    x = client.get(f"{API}/runs/{run}/dataset.xlsx", headers=auth)
    sheet = zipfile.ZipFile(io.BytesIO(x.content)).read("xl/worksheets/sheet1.xml").decode()
    assert f">{a}<" not in sheet and "<v>12.5</v>" in sheet


def test_dataset_xlsx_opens_and_has_values(client, auth, run_two):
    rid = run_two["id"]
    r = client.get(f"{API}/runs/{rid}/dataset.xlsx", headers=auth)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert f'filename="results-{rid}.xlsx"' in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert z.testzip() is None
    assert 'name="Факты"' in z.read("xl/workbook.xml").decode()
    sheet = z.read("xl/worksheets/sheet1.xml").decode()
    j = client.get(f"{API}/runs/{rid}/dataset.json", headers=auth).json()
    assert sheet.count("<row ") == len(j["results"]) + 1
    assert "metric_name" in sheet and j["results"][0]["result_id"] in sheet
    first = j["results"][0]
    assert f"<v>{first['metric_value']}</v>" in sheet  # число числом, не строкой
    assert re.search(r'<c r="G2"><v>', sheet)  # metric_value - седьмая колонка
    # openpyxl читает файл так же
    import openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(r.content)).active
    assert ws.title == "Факты" and ws["A1"].value == "publication_id" and ws["G2"].value == first["metric_value"]
    assert ws.max_column == 27  # колонки за Z (AA) тоже на месте


def test_results_xlsx_escapes_and_numbers():
    data = results_xlsx(("a", "b", "c"), [{"a": "x<&>\x01", "b": "3,5", "c": "007"}])
    sheet = zipfile.ZipFile(io.BytesIO(data)).read("xl/worksheets/sheet1.xml").decode()
    assert "x&lt;&amp;&gt;" in sheet and "<v>3.5</v>" in sheet
    assert ">007<" in sheet  # ведущие нули - текст


def test_dataset_owner_only(client, auth, auth_b, run_two):  # noqa: F811
    rid = run_two["id"]
    for ext in ("json", "xlsx"):
        r = client.get(f"{API}/runs/{rid}/dataset.{ext}", headers=auth_b)
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert client.get(f"{API}/runs/run_nope/dataset.json", headers=auth).status_code == 404


def test_dataset_in_progress_and_partial(client, auth, run_two, services):
    from faktura.domain.entities import RunStatus

    cid = "crp_" + run_two["id"][4:]
    with services.db.uow() as uow:
        uow.runs.update(uow.runs.list(cid, limit=1)[0].id, status=RunStatus.running)
    rid = run_two["id"]
    for ext in ("json", "xlsx"):
        r = client.get(f"{API}/runs/{rid}/dataset.{ext}", headers=auth)
        assert r.status_code == 409 and r.json()["error"]["code"] == "run_in_progress"
        assert client.get(f"{API}/runs/{rid}/dataset.{ext}", params={"partial": "true"}, headers=auth).status_code == 200
    r = client.get(f"{API}/submission.zip", params={"domain": "solar"}, headers=auth)
    assert r.status_code == 409 and r.json()["error"]["code"] == "run_in_progress"


def test_all_runs_zip_structure_and_dedup(client, auth, pdfs):
    first = upload(client, auth, [pdfs["D001"], pdfs["D002"]]).json()["id"]
    second = upload(client, auth, [pdfs["D001"], pdfs["D006"]]).json()["id"]
    # правка во втором (свежем) разборе: из него и должна взяться D001
    rows = client.get(f"{API}/papers/solar/D001", params={"run": second}, headers=auth).json()["results"]
    rid = rows[0]["result_id"]
    assert client.patch(f"{API}/runs/{second}/papers/solar/D001/results/{rid}/review",
                        json={"status": "rejected"}, headers=auth).status_code == 200
    r = client.get(f"{API}/submission.zip", params={"domain": "solar"}, headers=auth)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.headers["x-export-tables"] == "7" and r.headers["x-export-papers"] == "3"
    assert "filename*=UTF-8''" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(z.namelist()) == sorted([f"{t}.csv" for t in (
        "publications", "materials", "devices", "layers", "measurements", "results", "issues")]
        + ["PASSPORT.md", "passport.json"])
    pubs = [p["publication_id"] for p in _csv(z, "publications")]
    assert sorted(pubs) == ["D001", "D002", "D006"]
    res = _csv(z, "results")
    assert int(r.headers["x-export-results"]) == len(res)
    d001 = [x for x in res if x["publication_id"] == "D001"]
    assert rid not in {x["result_id"] for x in d001}  # D001 из второго разбора (с отклонённой строкой), один раз
    only_second = client.get(f"{API}/runs/{second}/submission.zip", headers=auth).content
    z2 = zipfile.ZipFile(io.BytesIO(only_second))
    assert len([x for x in _csv(z2, "results") if x["publication_id"] == "D001"]) == len(d001)
    assert [x for x in res if x["publication_id"] == "D002"] == \
        [x for x in _csv(zipfile.ZipFile(io.BytesIO(
            client.get(f"{API}/runs/{first}/submission.zip", headers=auth).content)), "results")
         if x["publication_id"] == "D002"]
    import json
    assert len(json.loads(z.read("passport.json"))["domains"]) == 1


def test_all_runs_zip_is_owner_only_and_validation(client, auth, auth_b, pdfs):  # noqa: F811
    upload(client, auth, [pdfs["D001"]])
    # у второго пользователя нет разборов: no_files, чужие данные не попадают
    r = client.get(f"{API}/submission.zip", params={"domain": "solar"}, headers=auth_b)
    assert r.status_code == 400 and r.json()["error"]["code"] == "no_files"
    r = client.get(f"{API}/submission.zip", params={"domain": "chem"}, headers=auth)
    assert r.status_code == 422 and r.json()["error"]["code"] == "domain_not_supported"
    assert client.get(f"{API}/submission.zip", params={"domain": "solar"}).status_code == 401
    # нет разборов области rag у владельца
    r = client.get(f"{API}/submission.zip", params={"domain": "rag"}, headers=auth)
    assert r.status_code == 400 and r.json()["error"]["code"] == "no_files"
