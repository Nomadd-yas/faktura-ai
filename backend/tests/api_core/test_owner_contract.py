"""Видимость разборов по владельцу, форма ответов и коды ошибок (BACKEND_ONLY §1, §3, §7.1, §11)."""
from __future__ import annotations

import pytest

from .conftest import API, upload


@pytest.fixture
def auth_b(client, services) -> dict[str, str]:
    """Второй пользователь в том же рабочем пространстве, что и админ."""
    from faktura.domain.security import hash_password
    from faktura.storage import DEFAULT_WORKSPACE_ID

    with services.db.uow() as uow:
        uow.users.create(workspace_id=DEFAULT_WORKSPACE_ID, email="b@example.com", name="B",
                         password_hash=hash_password("b-password-123"), is_admin=False)
    r = client.post(f"{API}/auth/login", json={"email": "b@example.com", "password": "b-password-123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _nf(resp):
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "not_found"


def test_other_user_cannot_read_run(client, auth, auth_b, run_two):
    rid = run_two["id"]
    assert client.get(f"{API}/runs/{rid}", headers=auth).status_code == 200
    _nf(client.get(f"{API}/runs/{rid}", headers=auth_b))
    _nf(client.get(f"{API}/runs/{rid}/summary", headers=auth_b))
    _nf(client.get(f"{API}/runs/{rid}/submission.zip", headers=auth_b))
    _nf(client.get(f"{API}/results", params={"run": rid}, headers=auth_b))
    _nf(client.get(f"{API}/issues", params={"run": rid}, headers=auth_b))
    _nf(client.get(f"{API}/papers/solar/D001", params={"run": rid}, headers=auth_b))
    _nf(client.get(f"{API}/papers/solar/D001/pdf", params={"run": rid}, headers=auth_b))
    _nf(client.get(f"{API}/papers/solar/D001", headers=auth_b))  # без run - тоже чужое
    _nf(client.get(f"{API}/papers/solar/D001/pdf", headers=auth_b))
    lst = client.get(f"{API}/runs", headers=auth_b).json()
    assert lst["data"] == [] and lst["total"] == 0 and lst["next_cursor"] is None


def test_paper_run_ids_only_own(client, auth, auth_b, pdfs):
    a = upload(client, auth, [pdfs["D001"]]).json()
    b = upload(client, auth_b, [pdfs["D001"]]).json()
    got = client.get(f"{API}/papers/solar/D001", headers=auth_b).json()
    assert got["meta"]["run_ids"] == [b["id"]] and a["id"] not in got["meta"]["run_ids"]


def test_admin_sees_only_own_runs(client, auth, auth_b, pdfs):
    upload(client, auth_b, [pdfs["D001"]])
    assert client.get(f"{API}/runs", headers=auth).json()["total"] == 0


def test_runs_total(client, auth, pdfs):
    upload(client, auth, [pdfs["D001"]])
    upload(client, auth, [pdfs["D002"]])
    page = client.get(f"{API}/runs", params={"limit": 1}, headers=auth).json()
    assert page["total"] == 2 and len(page["data"]) == 1 and page["next_cursor"]


def test_same_filename_in_different_folders_accepted(client, auth, pdfs):
    files = [("file", ("a/X.pdf", pdfs["D001"].read_bytes(), "application/pdf")),
             ("file", ("b/X.pdf", pdfs["D002"].read_bytes(), "application/pdf"))]
    r = client.post(f"{API}/runs", files=files, headers=auth)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["rejected"] == []
    assert [f["id"] for f in body["files"]] == ["X", "X-2"]


def test_same_content_is_duplicate(client, auth, pdfs):
    pdf = pdfs["D001"].read_bytes()
    files = [("file", ("a/X.pdf", pdf, "application/pdf")), ("file", ("b/X.pdf", pdf, "application/pdf"))]
    body = client.post(f"{API}/runs", files=files, headers=auth).json()
    assert [r["code"] for r in body["rejected"]] == ["duplicate"]
    assert [f["id"] for f in body["files"]] == ["X"]


def test_health_shape(client):
    r = client.get(f"{API}/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["version"] and "model_version" in body


def test_user_shape(client, auth):
    u = client.get(f"{API}/auth/me", headers=auth).json()
    assert u["role"] == "" and u["team"] == "" and u["pending_email"] is None
    assert {"id", "email", "name", "role", "team", "color", "is_admin", "pending_email"} <= set(u)


def test_register_removed(client):
    assert client.post(f"{API}/auth/register", json={"email": "x@y.zz", "password": "12345678"}).status_code in (404, 405)


def test_error_has_request_id_and_codes(client, auth):
    r = client.get(f"{API}/runs/run_nope", headers=auth)
    assert r.json()["error"]["request_id"] == r.headers["X-Request-Id"]
    from faktura.api.base import errors  # noqa: F401
    from faktura.domain.errors import ERROR_CATALOG

    assert ERROR_CATALOG["extractor_unavailable"][0] == 503
    assert ERROR_CATALOG["too_many_pages"][0] == 422
    assert ERROR_CATALOG["issue_auto_fixed"][0] == 409
    assert ERROR_CATALOG["too_large"][0] == 413 and ERROR_CATALOG["unsupported_media_type"][0] == 415


def test_extractor_error_public_code():
    from faktura.api.base.errors import app_error_response
    from faktura.domain.errors import ExtractError

    resp = app_error_response(ExtractError("ml_unavailable", "Сервис разбора временно недоступен"))
    assert resp.status_code == 503
    assert b"extractor_unavailable" in resp.body and "ML" not in resp.body.decode()
