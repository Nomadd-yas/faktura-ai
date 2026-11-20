"""Ядро API v1 (CORE_API.md §2-4): разбор, 7 таблиц, results/issues, submission.zip."""
from __future__ import annotations

import io
import zipfile

from faktura.domain.tables import TABLES

from .conftest import API, upload


def err(resp, status, code):
    assert resp.status_code == status, resp.text
    assert resp.json()["error"]["code"] == code, resp.text


def test_requires_auth(client):
    for path in ("/runs", "/runs/run_x", "/results?run=run_x", "/issues?run=run_x", "/papers/solar/D001"):
        assert client.get(API + path).status_code == 401


def test_e2e_upload_to_zip(client, auth, pdfs):
    r = upload(client, auth, [pdfs["D001"], pdfs["D002"]])
    assert r.status_code == 202
    run = r.json()
    assert run["id"].startswith("run_") and run["domain"] == "solar"
    assert run["rejected"] == []
    assert run["counts"]["files"] == 2
    assert [f["id"] for f in run["files"]] == ["D001", "D002"]
    rid = run["id"]

    got = client.get(f"{API}/runs/{rid}", headers=auth).json()  # исполнитель синхронный: уже готово
    assert got["status"] == "done"
    assert got["counts"]["done"] == 2 and got["counts"]["error"] == 0
    assert got["results"] > 0 and all(f["has_data"] and f["status"] == "done" for f in got["files"])

    p = client.get(f"{API}/papers/solar/D001", params={"run": rid}, headers=auth)
    assert p.status_code == 200
    body = p.json()
    assert body["paper_status"] == "has_data"
    assert body["publication"]["publication_id"] == "D001"
    assert set(body) >= {"publication", "materials", "devices", "layers", "measurements", "results", "issues", "meta"}
    assert body["results"] and body["meta"]["file_status"] == "done" and rid in body["meta"]["run_ids"]
    assert set(body["results"][0]) == set(TABLES["results"])
    r0 = body["results"][0]["result_id"]
    assert "quote_found" in body["meta"]["results"][r0]

    res = client.get(f"{API}/results", params={"run": rid, "limit": 1000}, headers=auth).json()
    assert res["total"] == got["results"] and res["next_cursor"] is None
    item = res["data"][0]
    assert set(item) == {"row", "device_label", "paper_title", "meta"} and item["paper_title"]

    z = client.get(f"{API}/runs/{rid}/submission.zip", headers=auth)
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    assert z.headers["x-export-tables"] == "7" and z.headers["x-export-papers"] == "2"
    assert int(z.headers["x-export-results"]) == got["results"]
    assert "attachment" in z.headers["content-disposition"] and "UTF-8''" in z.headers["content-disposition"]
    zf = zipfile.ZipFile(io.BytesIO(z.content))
    assert sorted(zf.namelist()) == sorted(f"{t}.csv" for t in TABLES)
    for t, cols in TABLES.items():
        head = zf.read(f"{t}.csv").decode("utf-8-sig").splitlines()[0]
        assert head == ",".join(cols)
    assert len(zf.read("results.csv").decode("utf-8-sig").splitlines()) - 1 == got["results"]


def test_out_of_scope_and_summary(client, auth, pdfs):
    r = upload(client, auth, [pdfs["D001"], pdfs["D006"]], title="Мой разбор").json()
    assert r["title"] == "Мой разбор"
    rid = r["id"]
    got = client.get(f"{API}/runs/{rid}", headers=auth).json()
    by = {f["id"]: f for f in got["files"]}
    assert by["D006"]["status"] == "out" and by["D006"]["reason"] and not by["D006"]["has_data"]
    assert got["counts"]["out"] == 1 and got["counts"]["done"] == 1

    paper = client.get(f"{API}/papers/solar/D006", params={"run": rid}, headers=auth).json()
    assert paper["paper_status"] == "out" and paper["publication"]["in_scope"] == "false"
    assert paper["results"] == [] and paper["devices"] == []

    s = client.get(f"{API}/runs/{rid}/summary", headers=auth).json()
    assert s["files"] == 2 and s["papers_out"] == 1 and s["papers_in_scope"] == 1
    assert s["results"] == got["results"] and s["scored_results"] <= s["results"]
    assert [c["metric"] for c in s["coverage"]][:3] == ["PCE", "Voc", "Jsc"] and len(s["coverage"]) == 11
    assert all(c["total"] == 1 for c in s["coverage"])
    assert s["issues_total"] == got["issues"]

    z = client.get(f"{API}/runs/{rid}/submission.zip", headers=auth)
    zf = zipfile.ZipFile(io.BytesIO(z.content))
    assert "D006" in zf.read("publications.csv").decode("utf-8-sig")
    assert "D006" not in zf.read("results.csv").decode("utf-8-sig")


def test_title_default_and_list(client, auth, pdfs):
    a = upload(client, auth, [pdfs["D001"], pdfs["D002"]]).json()
    assert a["title"].startswith("2 статьи · ")
    b = upload(client, auth, [pdfs["D001"]]).json()
    lst = client.get(f"{API}/runs", headers=auth).json()
    assert [x["id"] for x in lst["data"]] == [b["id"], a["id"]] and lst["next_cursor"] is None
    assert set(lst["data"][0]["files"][0]) == {"id", "name", "status", "has_data", "results", "issues", "reason"}
    nofiles = client.get(f"{API}/runs", params={"files": "false"}, headers=auth).json()
    assert "files" not in nofiles["data"][0]
    p1 = client.get(f"{API}/runs", params={"limit": 1}, headers=auth).json()
    assert len(p1["data"]) == 1 and p1["next_cursor"]
    p2 = client.get(f"{API}/runs", params={"limit": 1, "cursor": p1["next_cursor"]}, headers=auth).json()
    assert p2["data"][0]["id"] == a["id"] and p2["next_cursor"] is None


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


def test_upload_errors(client, auth, pdfs):
    err(client.post(f"{API}/runs", headers=auth), 400, "no_files")
    err(upload(client, auth, [pdfs["D001"]], domain="rag"), 422, "domain_not_supported")
    err(client.post(f"{API}/runs", files=[("file", ("a.txt", b"x", "text/plain"))], headers=auth), 400, "no_files")
    err(client.post(f"{API}/runs", files={"archive": ("a.rar", b"x", "application/x-rar")}, headers=auth),
        415, "unsupported_media_type")
    err(client.post(f"{API}/runs", files={"archive": ("a.zip", b"not zip", "application/zip")}, headers=auth),
        415, "unsupported_media_type")
    r = client.post(f"{API}/runs", files=[("file", ("D001.pdf", pdfs["D001"].read_bytes()))])
    assert r.status_code == 401


def test_archive_upload(client, auth, pdfs):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.write(pdfs["D001"], "sub/D001.pdf")
        z.write(pdfs["D002"], "D002.pdf")
        z.writestr("notes.txt", "x")
    r = client.post(f"{API}/runs", files={"archive": ("batch.zip", buf.getvalue(), "application/zip")}, headers=auth)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["title"] == "batch.zip" and body["counts"]["files"] == 2
    assert {f["name"] for f in body["files"]} == {"sub/D001.pdf", "D002.pdf"}
    assert any(x["code"] == "unsupported_file" for x in body["rejected"])


def test_archive_and_files_in_one_request(client, auth, pdfs):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.write(pdfs["D001"], "D001.pdf")
    files = [("archive", ("batch.zip", buf.getvalue(), "application/zip")),
             ("file", ("D002.pdf", pdfs["D002"].read_bytes(), "application/pdf"))]
    r = client.post(f"{API}/runs", files=files, headers=auth)
    assert r.status_code == 202, r.text
    assert {f["name"] for f in r.json()["files"]} == {"D001.pdf", "D002.pdf"}


def test_zip_limits(client, auth, pdfs, services):
    services.settings.zip_max_files = 1
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.write(pdfs["D001"], "a.pdf")
        z.write(pdfs["D002"], "b.pdf")
    r = client.post(f"{API}/runs", files={"archive": ("x.zip", buf.getvalue(), "application/zip")}, headers=auth)
    assert r.status_code in (413, 422), r.text
    assert r.json()["error"]["code"] in ("too_large", "zip_bomb")


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


def test_issues(client, auth, run_two):
    rid = run_two["id"]
    r = client.get(f"{API}/issues", params={"run": rid, "limit": 1000}, headers=auth).json()
    got = client.get(f"{API}/runs/{rid}", headers=auth).json()
    assert r["total"] == got["issues"]
    if r["data"]:
        assert set(r["data"][0]) == {"row", "paper_title"} and set(r["data"][0]["row"]) == set(TABLES["issues"])
        name = r["data"][0]["row"]["check_name"]
        f = client.get(f"{API}/issues", params={"run": rid, "check_name": name}, headers=auth).json()
        assert f["total"] and all(d["row"]["check_name"] == name for d in f["data"])


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


def test_submission_in_progress_and_partial(client, auth, run_two, services):
    from faktura.domain.entities import RunStatus

    cid = "crp_" + run_two["id"][4:]
    with services.db.uow() as uow:
        run = uow.runs.list(cid, limit=1)[0]
        uow.runs.update(run.id, status=RunStatus.running)
    rid = run_two["id"]
    assert client.get(f"{API}/runs/{rid}", headers=auth).json()["status"] == "running"
    err(client.get(f"{API}/runs/{rid}/submission.zip", headers=auth), 409, "run_in_progress")
    z = client.get(f"{API}/runs/{rid}/submission.zip", params={"partial": "true"}, headers=auth)
    assert z.status_code == 200 and z.headers["x-export-papers"] == "2"


def test_file_statuses_mapping(client, auth, run_two, services):
    from faktura.domain.entities import CorpusFileStatus

    cid = "crp_" + run_two["id"][4:]
    with services.db.uow() as uow:
        files = uow.files.list(cid)
        uow.files.update_status(files[0].id, CorpusFileStatus.queued)
        uow.files.update_status(files[1].id, CorpusFileStatus.failed, error_message="сломалось")
    got = client.get(f"{API}/runs/{run_two['id']}", headers=auth).json()
    st = {f["id"]: f for f in got["files"]}
    assert st["D001"]["status"] == "queued" and st["D001"]["status_label"] == "в очереди"
    assert st["D002"]["status"] == "error" and st["D002"]["reason"] == "сломалось"
    assert got["counts"]["queued"] == 1 and got["counts"]["error"] == 1


def test_cors_exposes_export_headers(client):
    h = client.get(f"{API}/health", headers={"Origin": "http://localhost:5173"})
    exp = h.headers.get("access-control-expose-headers", "")
    assert "X-Export-Tables" in exp and "ETag" in exp
