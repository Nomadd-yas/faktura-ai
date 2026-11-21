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




def test_dataset_json_matches_zip_rows(client, auth, run_two):
    rid = run_two["id"]
    j = client.get(f"{API}/runs/{rid}/dataset.json", headers=auth).json()
    z = zipfile.ZipFile(io.BytesIO(client.get(f"{API}/runs/{rid}/submission.zip", headers=auth).content))
    for t in ("publications", "results", "issues"):
        assert len(_csv(z, t)) == len(j[t])
    assert [r["result_id"] for r in _csv(z, "results")] == [r["result_id"] for r in j["results"]]








def test_dataset_owner_only(client, auth, auth_b, run_two):  # noqa: F811
    rid = run_two["id"]
    for ext in ("json", "xlsx"):
        r = client.get(f"{API}/runs/{rid}/dataset.{ext}", headers=auth_b)
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert client.get(f"{API}/runs/run_nope/dataset.json", headers=auth).status_code == 404






