"""Приём файлов: multipart и ZIP, лимиты, W2-поля, список файлов, retry (REQ-073, REQ-074).

Контракт: api_contract §4.4 (№106-109), §1.5 (413/415/422), §1.7; spec/13 §13.4.
"""
from __future__ import annotations

import pytest

API = "/api/v1"


def _pdf(make_pdf, name: str, text: str = "page") -> bytes:
    return make_pdf([text], name=name).read_bytes()


def _files_url(c) -> str:
    return f"{API}/corpora/{c['corpus_id']}/files"


@pytest.mark.data
def test_zip_with_three_dev_pdfs_202_accepted_3(client, auth, mkcorpus, dev_zip3, h):
    c = mkcorpus()
    r = h.upload_zip(client, auth, c["corpus_id"], dev_zip3)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["accepted"] == 3 and body["duplicates"] == 0 and body["rejected"] == []
    assert len(body["files"]) == 3
    f = body["files"][0]
    assert f["file_id"].startswith("cf_") and f["status"] == "pending" and len(f["sha256"]) == 64
    assert f["publication_id"] == f["name"].rsplit(".", 1)[0].split("/")[-1]


def test_multipart_single_pdf_accepted_pending(client, auth, mkcorpus, make_pdf):
    c = mkcorpus()
    r = client.post(_files_url(c), headers=auth,
                    files=[("file", ("P001.pdf", _pdf(make_pdf, "P001.pdf"), "application/pdf"))])
    assert r.status_code == 202, r.text
    assert r.json()["accepted"] == 1 and r.json()["files"][0]["source"] == "upload"


def test_multipart_many_files(client, auth, mkcorpus, make_pdf):
    c = mkcorpus()
    files = [("file", (f"P{i:03d}.pdf", _pdf(make_pdf, f"P{i:03d}.pdf", f"t{i}"), "application/pdf"))
             for i in range(1, 6)]
    r = client.post(_files_url(c), headers=auth, files=files)
    assert r.status_code == 202 and r.json()["accepted"] == 5


def test_multipart_more_than_200_files_413(client, auth, mkcorpus, make_pdf):
    c = mkcorpus()
    data = _pdf(make_pdf, "x.pdf")
    files = [("file", (f"F{i}.pdf", data, "application/pdf")) for i in range(201)]
    r = client.post(_files_url(c), headers=auth, files=files)
    assert r.status_code == 413 and r.json()["error"]["code"] == "file_too_large"


def test_multipart_total_over_max_upload_mb_413(make_client, make_pdf):
    client, _, auth = make_client(max_upload_mb=1)
    c = client.post(f"{API}/corpora", json={"name": "L", "collection": "solar"}, headers=auth).json()
    big = _pdf(make_pdf, "big.pdf") + b"\0" * (700 * 1024)
    files = [("file", ("A.pdf", big, "application/pdf")), ("file", ("B.pdf", big, "application/pdf"))]
    r = client.post(_files_url(c), headers=auth, files=files)
    assert r.status_code == 413


def test_single_file_over_50mb_413(make_client, make_pdf):
    client, _, auth = make_client(max_file_mb=1)  # лимит снижен для скорости
    c = client.post(f"{API}/corpora", json={"name": "L", "collection": "solar"}, headers=auth).json()
    big = _pdf(make_pdf, "big.pdf") + b"\0" * (2 * 1024 * 1024)
    r = client.post(_files_url(c), headers=auth, files=[("file", ("big.pdf", big, "application/pdf"))])
    assert r.status_code == 413 and r.json()["error"]["code"] == "file_too_large"


def test_zip_over_max_zip_mb_413(make_client, h, make_pdf):
    client, _, auth = make_client(max_zip_mb=1)
    c = client.post(f"{API}/corpora", json={"name": "Z", "collection": "solar"}, headers=auth).json()
    import os

    blob = os.urandom(2 * 1024 * 1024)  # несжимаемые данные
    z = h.make_zip({"P001.pdf": _pdf(make_pdf, "P001.pdf") + blob})
    r = h.upload_zip(client, auth, c["corpus_id"], z)
    assert r.status_code == 413
    assert r.json()["error"]["code"] in ("file_too_large", "zip_too_large")


def test_zip_bomb_unpacked_over_limit_422_or_413(make_client, h):
    client, _, auth = make_client(zip_max_unpacked_gb=0)
    c = client.post(f"{API}/corpora", json={"name": "Z", "collection": "solar"}, headers=auth).json()
    z = h.make_zip({"P001.pdf": b"%PDF-1.4\n" + b"0" * 4096})
    r = h.upload_zip(client, auth, c["corpus_id"], z)
    assert r.status_code in (413, 422)
    assert r.json()["error"]["code"] in ("zip_bomb", "zip_too_large")


def test_archive_not_zip_415(client, auth, mkcorpus):
    c = mkcorpus()
    r = client.post(_files_url(c), headers=auth, files={"archive": ("a.zip", b"not a zip at all", "application/zip")})
    assert r.status_code == 415 and r.json()["error"]["code"] == "unsupported_file"


def test_non_pdf_in_zip_is_rejected_not_error(client, auth, mkcorpus, h, make_pdf):
    c = mkcorpus()
    z = h.make_zip({"readme.txt": b"hello", "P001.pdf": _pdf(make_pdf, "P001.pdf")})
    r = h.upload_zip(client, auth, c["corpus_id"], z)
    assert r.status_code == 202
    body = r.json()
    assert body["accepted"] == 1
    assert [x["code"] for x in body["rejected"]] == ["unsupported_file"]
    assert body["rejected"][0]["name"] == "readme.txt" and body["rejected"][0]["message"]


def test_pdf_extension_without_signature_is_rejected(client, auth, mkcorpus, h):
    c = mkcorpus()
    r = h.upload_zip(client, auth, c["corpus_id"], h.make_zip({"fake.pdf": b"MZ this is not a pdf"}))
    assert r.status_code == 202 and r.json()["accepted"] == 0
    assert r.json()["rejected"][0]["code"] == "unsupported_file"


def test_multipart_non_pdf_415(client, auth, mkcorpus):
    c = mkcorpus()
    r = client.post(_files_url(c), headers=auth, files=[("file", ("a.docx", b"PK\x03\x04", "application/zip"))])
    assert r.status_code in (415, 202)
    if r.status_code == 202:
        assert r.json()["accepted"] == 0 and r.json()["rejected"][0]["code"] == "unsupported_file"


def test_duplicate_name_in_zip_rejected(client, auth, mkcorpus, h, make_pdf):
    c = mkcorpus()
    z = h.make_zip({"a/P001.pdf": _pdf(make_pdf, "P001.pdf", "one"), "b/P001.pdf": _pdf(make_pdf, "P001.pdf", "two")})
    r = h.upload_zip(client, auth, c["corpus_id"], z)
    assert r.status_code == 202
    body = r.json()
    assert body["accepted"] == 1
    assert [x["code"] for x in body["rejected"]] == ["duplicate_name"]


def test_same_sha256_counted_as_duplicate(client, auth, mkcorpus, h, make_pdf):
    c = mkcorpus()
    data = _pdf(make_pdf, "P001.pdf", "same")
    assert h.upload_zip(client, auth, c["corpus_id"], h.make_zip({"P001.pdf": data})).json()["accepted"] == 1
    r = h.upload_zip(client, auth, c["corpus_id"], h.make_zip({"P777.pdf": data}))
    assert r.status_code == 202 and r.json()["duplicates"] == 1


def test_zip_slip_path_is_rejected(client, auth, mkcorpus, h, make_pdf):
    c = mkcorpus()
    z = h.make_zip({"../evil.pdf": _pdf(make_pdf, "e.pdf"), "ok.pdf": _pdf(make_pdf, "o.pdf", "ok")})
    r = h.upload_zip(client, auth, c["corpus_id"], z)
    assert r.status_code in (202, 422)
    if r.status_code == 202:
        assert r.json()["accepted"] == 1


@pytest.mark.parametrize("field", ["urls", "upload_id", "server_path"])
def test_w2_fields_400_validation_error(client, auth, mkcorpus, field):
    c = mkcorpus()
    r = client.post(_files_url(c), headers=auth, data={field: "http://example.com/a.pdf"})
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "validation_error" and "Пока не поддерживается" in err["message"]


def test_w2_urls_in_json_body_400(client, auth, mkcorpus):
    c = mkcorpus()
    r = client.post(_files_url(c), headers=auth, json={"urls": ["http://x/a.pdf"]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error"


def test_upload_without_file_or_archive_400(client, auth, mkcorpus):
    c = mkcorpus()
    r = client.post(_files_url(c), headers=auth, data={})
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error"


def test_upload_to_unknown_corpus_404(client, auth, make_pdf):
    r = client.post(f"{API}/corpora/crp_missing/files", headers=auth,
                    files=[("file", ("P1.pdf", _pdf(make_pdf, "P1.pdf"), "application/pdf"))])
    assert r.status_code == 404


def test_upload_does_not_start_run(client, auth, mkcorpus, make_pdf):
    c = mkcorpus()
    client.post(_files_url(c), headers=auth, files=[("file", ("P1.pdf", _pdf(make_pdf, "P1.pdf"), "application/pdf"))])
    assert client.get(f"{API}/corpora/{c['corpus_id']}/runs", headers=auth).json()["data"] == []


# ---- GET /corpora/{id}/files -------------------------------------------------------------------------------
@pytest.fixture
def corpus_with_files(client, auth, mkcorpus, h, make_pdf):
    c = mkcorpus("Files")
    names = ["alpha.pdf", "beta.pdf", "gamma.pdf"]
    z = h.make_zip({n: _pdf(make_pdf, n, n) for n in names} | {"notes.txt": b"x"})
    assert h.upload_zip(client, auth, c["corpus_id"], z).status_code == 202
    return c


def test_list_files_shape(client, auth, corpus_with_files):
    r = client.get(_files_url(corpus_with_files), headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3 and len(body["data"]) == 3
    f = body["data"][0]
    for k in ("file_id", "corpus_id", "name", "publication_id", "source", "status", "progress", "facts", "issues",
              "attempts", "updated_at"):
        assert k in f
    assert f["status"] == "pending"


def test_list_files_filter_status_q_sort_pagination(client, auth, corpus_with_files):
    url = _files_url(corpus_with_files)
    assert client.get(url + "?status=done", headers=auth).json()["data"] == []
    assert client.get(url + "?status=pending&status=done", headers=auth).json()["total"] == 3
    assert [f["name"] for f in client.get(url + "?q=bet", headers=auth).json()["data"]] == ["beta.pdf"]
    names = [f["name"] for f in client.get(url + "?sort=name&dir=desc", headers=auth).json()["data"]]
    assert names == sorted(names, reverse=True)
    p1 = client.get(url + "?sort=name&limit=2", headers=auth).json()
    assert len(p1["data"]) == 2 and p1["next_cursor"]
    p2 = client.get(url + f"?sort=name&limit=2&cursor={p1['next_cursor']}", headers=auth).json()
    assert [f["name"] for f in p2["data"]] == ["gamma.pdf"] and p2["next_cursor"] is None


def test_list_files_has_errors_filter(client, auth, corpus_with_files):
    assert client.get(_files_url(corpus_with_files) + "?has_errors=true", headers=auth).json()["total"] == 0


@pytest.mark.parametrize("qs", ["sort=bogus", "dir=sideways", "limit=0", "status=teleported"])
def test_list_files_bad_params_400(client, auth, corpus_with_files, qs):
    assert client.get(_files_url(corpus_with_files) + "?" + qs, headers=auth).status_code in (400, 422)


def test_list_files_unknown_corpus_404(client, auth):
    assert client.get(f"{API}/corpora/crp_x/files", headers=auth).status_code == 404


# ---- POST /corpora/{id}/files/{file_id}/retry --------------------------------------------------------------------
def test_retry_file_creates_selected_run_202(client, auth, corpus_with_files):
    fid = client.get(_files_url(corpus_with_files), headers=auth).json()["data"][0]["file_id"]
    r = client.post(f"{_files_url(corpus_with_files)}/{fid}/retry", headers=auth)
    assert r.status_code == 202, r.text
    run = r.json()
    assert run["kind"] == "selected" and run["corpus_id"] == corpus_with_files["corpus_id"]
    assert run["counts"]["total"] == 1


def test_retry_unknown_file_404(client, auth, corpus_with_files):
    assert client.post(f"{_files_url(corpus_with_files)}/cf_missing/retry", headers=auth).status_code == 404


def test_retry_file_of_other_corpus_404(client, auth, corpus_with_files, mkcorpus):
    fid = client.get(_files_url(corpus_with_files), headers=auth).json()["data"][0]["file_id"]
    other = mkcorpus("Other")
    assert client.post(f"{_files_url(other)}/{fid}/retry", headers=auth).status_code == 404


def test_retry_during_active_run_409(make_client, h, make_pdf):
    ex = h.GatedExecutor()
    client, _, auth = make_client(executor=ex)
    c = client.post(f"{API}/corpora", json={"name": "R", "collection": "solar"}, headers=auth).json()
    h.upload_zip(client, auth, c["corpus_id"], h.make_zip({"P1.pdf": _pdf(make_pdf, "P1.pdf")}))
    fid = client.get(_files_url(c), headers=auth).json()["data"][0]["file_id"]
    assert h.start_run(client, auth, c["corpus_id"]).status_code == 202
    r = client.post(f"{_files_url(c)}/{fid}/retry", headers=auth)
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"
    ex.open()
    ex.wait_idle()
