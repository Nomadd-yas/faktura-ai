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




















