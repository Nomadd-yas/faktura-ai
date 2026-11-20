"""POST /papers: служебный корпус «Загруженные вручную», start_or_append, без 409 (REQ-076, У-30).

Контракт: api_contract §1.6, У-30; spec/04 §4.6 (форма ответа), BACKEND §8.
"""
from __future__ import annotations

import pytest

API = "/api/v1"


def _post_paper(client, auth, make_pdf, name: str, headers: dict | None = None, text: str | None = None):
    data = make_pdf([text or name], name=name).read_bytes()
    return client.post(f"{API}/papers", headers={**auth, **(headers or {})},
                       files=[("file", (name, data, "application/pdf"))], data={"collection": "solar"})


def _manual_corpus(client, auth) -> dict:
    data = client.get(f"{API}/corpora", headers=auth).json()["data"]
    manual = [c for c in data if c["kind"] == "manual"]
    assert len(manual) == 1
    return manual[0]


def test_post_paper_202_shape_and_action(make_client, h, make_pdf):
    ex = h.GatedExecutor()
    client, _, auth = make_client(executor=ex)
    r = _post_paper(client, auth, make_pdf, "manual_one.pdf")
    assert r.status_code == 202, r.text
    item = r.json()["data"][0]
    assert item["paper"]["id"].startswith("pap_")
    assert item["paper"]["file"]["name"] == "manual_one.pdf" and len(item["paper"]["file"]["sha256"]) == 64
    assert item["paper"]["processing"]["status"] in ("queued", "processing")
    assert item["action_id"] == r.headers["X-Action-Id"]
    ex.open()
    ex.wait_idle()


def test_post_paper_creates_manual_corpus_once(make_client, h, make_pdf):
    ex = h.GatedExecutor()
    client, _, auth = make_client(executor=ex)
    _post_paper(client, auth, make_pdf, "m1.pdf")
    ex.open()
    ex.wait_idle()
    _post_paper(client, auth, make_pdf, "m2.pdf")
    c = _manual_corpus(client, auth)
    assert c["name"] == "Загруженные вручную"
    ex.wait_idle()


def test_two_posts_during_active_run_both_202_single_run(make_client, h, make_pdf):
    ex = h.GatedExecutor()
    client, _, auth = make_client(executor=ex)
    r1 = _post_paper(client, auth, make_pdf, "a1.pdf")
    r2 = _post_paper(client, auth, make_pdf, "a2.pdf")
    assert r1.status_code == 202 and r2.status_code == 202
    c = _manual_corpus(client, auth)
    runs = client.get(f"{API}/corpora/{c['corpus_id']}/runs", headers=auth).json()["data"]
    assert len(runs) == 1                      # один прогон, второй файл добавлен в активный
    assert runs[0]["counts"]["total"] == 2
    ex.open()
    ex.wait_idle()


def test_post_paper_after_finished_run_starts_new_selected_run(make_client, h, make_pdf):
    ex = h.GatedExecutor()
    client, _, auth = make_client(executor=ex)
    ex.open()
    _post_paper(client, auth, make_pdf, "b1.pdf")
    ex.wait_idle()
    c = _manual_corpus(client, auth)
    first = client.get(f"{API}/corpora/{c['corpus_id']}/runs", headers=auth).json()["data"][0]
    h.wait_run(client, auth, first["run_id"])
    assert _post_paper(client, auth, make_pdf, "b2.pdf").status_code == 202
    ex.wait_idle()
    runs = client.get(f"{API}/corpora/{c['corpus_id']}/runs", headers=auth).json()["data"]
    assert len(runs) == 2 and runs[0]["kind"] == "selected"


def test_post_paper_never_409_even_with_user_corpus_run(make_client, h, make_pdf):
    ex = h.GatedExecutor()
    client, _, auth = make_client(executor=ex)
    c = client.post(f"{API}/corpora", json={"name": "U", "collection": "solar"}, headers=auth).json()
    h.upload_zip(client, auth, c["corpus_id"], h.make_zip({"U1.pdf": make_pdf(["u"], name="U1.pdf").read_bytes()}))
    h.start_run(client, auth, c["corpus_id"])
    assert _post_paper(client, auth, make_pdf, "other.pdf").status_code == 202   # служебный корпус независим
    ex.open()
    ex.wait_idle()


def test_post_paper_multiple_files_in_one_request(make_client, h, make_pdf):
    ex = h.GatedExecutor()
    client, _, auth = make_client(executor=ex)
    files = [("file", (n, make_pdf([n], name=n).read_bytes(), "application/pdf")) for n in ("x1.pdf", "x2.pdf")]
    r = client.post(f"{API}/papers", headers=auth, files=files)
    assert r.status_code == 202 and len(r.json()["data"]) == 2
    ex.open()
    ex.wait_idle()


def test_post_paper_non_pdf_error_per_file_207(client, auth, make_pdf):
    good = make_pdf(["ok"], name="ok.pdf").read_bytes()
    files = [("file", ("ok.pdf", good, "application/pdf")), ("file", ("x.docx", b"PK", "application/zip"))]
    r = client.post(f"{API}/papers", headers=auth, files=files)
    assert r.status_code in (207, 415)
    if r.status_code == 207:
        assert r.json()["errors"][0]["code"] == "unsupported_file" and r.json()["errors"][0]["file"] == "x.docx"


def test_post_paper_no_file_400(client, auth):
    r = client.post(f"{API}/papers", headers=auth, data={"collection": "solar"})
    assert r.status_code == 400


def test_post_paper_too_large_413(make_client, make_pdf):
    client, _, auth = make_client(max_file_mb=1)
    big = make_pdf(["x"], name="b.pdf").read_bytes() + b"\0" * (2 * 1024 * 1024)
    r = client.post(f"{API}/papers", headers=auth, files=[("file", ("b.pdf", big, "application/pdf"))])
    assert r.status_code == 413


def test_post_paper_requires_auth(client, make_pdf):
    data = make_pdf(["x"], name="x.pdf").read_bytes()
    assert client.post(f"{API}/papers", files=[("file", ("x.pdf", data, "application/pdf"))]).status_code == 401


def test_post_paper_idempotency_key_replays_same_response(make_client, h, make_pdf):
    ex = h.GatedExecutor()
    client, _, auth = make_client(executor=ex)
    hdr = {"Idempotency-Key": "key-123"}
    r1 = _post_paper(client, auth, make_pdf, "i1.pdf", hdr)
    r2 = _post_paper(client, auth, make_pdf, "i1.pdf", hdr)
    assert r1.status_code == r2.status_code == 202
    assert r1.json()["data"][0]["paper"]["id"] == r2.json()["data"][0]["paper"]["id"]
    c = _manual_corpus(client, auth)
    assert client.get(f"{API}/corpora/{c['corpus_id']}/files", headers=auth).json()["total"] == 1
    ex.open()
    ex.wait_idle()


def test_post_paper_exact_duplicate_marks_dup(make_client, h, make_pdf):
    ex = h.GatedExecutor()
    client, _, auth = make_client(executor=ex)
    ex.open()
    r1 = _post_paper(client, auth, make_pdf, "d1.pdf", text="identical")
    ex.wait_idle()
    h.wait_run(client, auth, client.get(f"{API}/corpora/{_manual_corpus(client, auth)['corpus_id']}/runs",
                                        headers=auth).json()["data"][0]["run_id"])
    r2 = _post_paper(client, auth, make_pdf, "d2.pdf", text="identical")
    # тот же sha256 -> дубль: либо dup_of у новой статьи, либо ссылка на прежнюю
    assert r1.status_code == 202 and r2.status_code == 202
    p2 = r2.json()["data"][0]["paper"]
    assert p2["dup_of"] is not None or p2["id"] == r1.json()["data"][0]["paper"]["id"]
