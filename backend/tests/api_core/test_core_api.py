"""Ядро API v1 (CORE_API.md §2-4): разбор, 7 таблиц, results/issues, submission.zip."""
from __future__ import annotations

import io
import zipfile

from faktura.domain.tables import TABLES

from .conftest import API, upload


def err(resp, status, code):
    assert resp.status_code == status, resp.text
    assert resp.json()["error"]["code"] == code, resp.text










def test_upload_rejections(client, auth, pdfs):
    pdf = pdfs["D001"].read_bytes()
    files = [("file", ("D001.pdf", pdf, "application/pdf")),
             ("file", ("copy/D001_copy.pdf", pdf, "application/pdf")),
             ("file", ("readme.txt", b"hello", "text/plain")),
             ("file", ("empty.pdf", b"", "application/pdf")),
             ("file", ("broken.pdf", b"not a pdf at all", "application/pdf"))]
    r = client.post(f"{API}/runs", files=files, headers=auth)
    assert r.status_code == 202, r.text
    rej = {x["name"]: x for x in r.json()["rejected"]}
    assert rej["readme.txt"]["code"] == "unsupported_file"
    assert rej["readme.txt"]["message"] == "Можно загрузить только PDF"
    assert rej["empty.pdf"]["code"] == "empty_file"
    assert rej["broken.pdf"]["code"] == "broken_pdf"
    assert rej["copy/D001_copy.pdf"]["code"] == "duplicate"
    assert [f["id"] for f in r.json()["files"]] == ["D001"]






def test_archive_and_files_in_one_request(client, auth, pdfs):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.write(pdfs["D001"], "D001.pdf")
    files = [("archive", ("batch.zip", buf.getvalue(), "application/zip")),
             ("file", ("D002.pdf", pdfs["D002"].read_bytes(), "application/pdf"))]
    r = client.post(f"{API}/runs", files=files, headers=auth)
    assert r.status_code == 202, r.text
    assert {f["name"] for f in r.json()["files"]} == {"D001.pdf", "D002.pdf"}




def test_not_found_and_isolation(client, auth, run_two, services):
    rid = run_two["id"]
    err(client.get(f"{API}/runs/run_nope", headers=auth), 404, "not_found")
    err(client.get(f"{API}/runs/run_nope/summary", headers=auth), 404, "not_found")
    err(client.get(f"{API}/runs/run_nope/submission.zip", headers=auth), 404, "not_found")
    err(client.get(f"{API}/papers/solar/D999", headers=auth), 404, "not_found")
    err(client.get(f"{API}/results", headers=auth), 400, "validation_error")
    err(client.get(f"{API}/issues", headers=auth), 400, "validation_error")
    # другой пользователь (другое рабочее пространство) не видит чужой разбор
    from faktura.domain.security import hash_password

    with services.db.uow() as uow:
        uow.workspaces.ensure("ws_other")
        uow.users.create(workspace_id="ws_other", email="o@example.com", name="O",
                         password_hash=hash_password("other-password-1"), is_admin=False)
    tok = client.post(f"{API}/auth/login", json={"email": "o@example.com", "password": "other-password-1"}).json()
    h2 = {"Authorization": f"Bearer {tok['access_token']}"}
    err(client.get(f"{API}/runs/{rid}", headers=h2), 404, "not_found")
    assert client.get(f"{API}/runs", headers=h2).json()["data"] == []
    err(client.get(f"{API}/papers/solar/D001", headers=h2), 404, "not_found")


def test_results_filters_sort_paging(client, auth, run_two):
    rid = run_two["id"]

    def g(**p):
        return client.get(f"{API}/results", params={"run": rid, **p}, headers=auth)

    total = g(limit=1000).json()["total"]
    assert total > 20
    pce = g(metric="PCE", limit=1000).json()
    assert 0 < pce["total"] < total and all(d["row"]["metric_name"] == "PCE" for d in pce["data"])
    sc = g(scored="true", limit=1000).json()
    assert 0 < sc["total"] <= total
    d1 = g(pid="D001", limit=1000).json()
    assert d1["total"] and all(d["row"]["publication_id"] == "D001" for d in d1["data"])
    nodup = g(duplicates="exclude", limit=1000).json()
    assert nodup["total"] <= total and all(d["row"]["duplicate_of"] == "" for d in nodup["data"])

    seen, cur = [], None
    while True:
        page = g(limit=40, **({"cursor": cur} if cur else {})).json()
        seen += [(d["row"]["publication_id"], d["row"]["result_id"]) for d in page["data"]]
        cur = page["next_cursor"]
        if not cur:
            break
    assert len(seen) == len(set(seen)) == total

    vals = [float(d["row"]["metric_value"]) for d in g(sort="value", dir="desc", metric="PCE", limit=1000).json()["data"]
            if d["row"]["metric_value"]]
    assert vals == sorted(vals, reverse=True) and len(vals) > 1
    pages = [int(d["row"]["source_page"]) for d in g(sort="page", limit=1000).json()["data"] if d["row"]["source_page"]]
    assert pages == sorted(pages)
    ms = [d["row"]["metric_name"] for d in g(sort="metric", limit=1000).json()["data"]]
    assert ms == sorted(ms)
    # страницы с python-сортировкой тоже склеиваются
    a = g(sort="value", dir="desc", limit=30).json()
    b = g(sort="value", dir="desc", limit=30, cursor=a["next_cursor"]).json()
    ids_a = {d["row"]["result_id"] + d["row"]["publication_id"] for d in a["data"]}
    ids_b = {d["row"]["result_id"] + d["row"]["publication_id"] for d in b["data"]}
    assert a["next_cursor"] and ids_b and not (ids_a & ids_b)
    err(g(sort="bogus"), 400, "validation_error")
    err(g(limit=0), 400, "validation_error")




def test_pdf_range_etag(client, auth, run_two, pdfs):
    rid = run_two["id"]
    url = f"{API}/papers/solar/D001/pdf"
    full = client.get(url, params={"run": rid}, headers=auth)
    assert full.status_code == 200 and full.headers["content-type"] == "application/pdf"
    assert full.content == pdfs["D001"].read_bytes() and full.headers["accept-ranges"] == "bytes"
    etag = full.headers["etag"]
    part = client.get(url, params={"run": rid}, headers={**auth, "Range": "bytes=0-99"})
    assert part.status_code == 206 and part.content == full.content[:100]
    assert part.headers["content-range"] == f"bytes 0-99/{len(full.content)}"
    tail = client.get(url, headers={**auth, "Range": "bytes=-50"})
    assert tail.status_code == 206 and tail.content == full.content[-50:]
    assert client.get(url, headers={**auth, "If-None-Match": etag}).status_code == 304
    assert client.get(url, headers={**auth, "Range": f"bytes={len(full.content) + 5}-"}).status_code == 416
    err(client.get(f"{API}/papers/solar/D777/pdf", headers=auth), 404, "not_found")








# --- набор для сдачи: состав, имя файла, ?check=true -----------------------------------------------

_OK_SCRIPT = ('import sys\nprint("внимание: stub warning")\nprint("таблиц: 7, ошибок: 0")\n'
              'print("проверено цитат: 3, не найдено: 0")\nsys.exit(0)\n')
_BAD_SCRIPT = ('import sys\nprint("ОШИБКА: results.csv: плохая строка")\nprint("таблиц: 7, ошибок: 1")\nsys.exit(1)\n')


def _stub(tmp_path, services, body):
    p = tmp_path / "check_stub.py"
    p.write_text(body, encoding="utf-8")
    services.settings.check_script = p




def test_submission_zip_check_ok(client, auth, pdfs, services, tmp_path):
    rid = upload(client, auth, [pdfs["D001"]]).json()["id"]
    _stub(tmp_path, services, _OK_SCRIPT)
    z = client.get(f"{API}/runs/{rid}/submission.zip", params={"check": "true"}, headers=auth)
    assert z.status_code == 200, z.text
    assert z.headers["x-check-status"] == "warnings"
    zf = zipfile.ZipFile(io.BytesIO(z.content))
    assert "stub warning" in zf.read("check_report.txt").decode("utf-8")
    assert "PASSPORT.md" in zf.namelist()


