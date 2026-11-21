"""E2E 2: вход, health, 401/404 на run-API. Полные сценарии /runs - в tests/api_core."""
from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import pytest

from e2e_support import EXCLUDED_PAPER, E2E_PAPERS, TABLES

pytestmark = pytest.mark.data
API = "/api/v1"


@pytest.fixture
def client(e2e_settings):
    from fastapi.testclient import TestClient

    from faktura.api.base.app import create_app

    with TestClient(create_app(e2e_settings)) as c:
        r = c.post(f"{API}/auth/login", json={"email": e2e_settings.admin_email,
                                              "password": e2e_settings.admin_password, "remember": False})
        assert r.status_code == 200, r.text
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        yield c


def _wait(client, url: str, done, timeout: float = 120.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = client.get(url)
        assert r.status_code == 200, r.text
        if done(r.json()):
            return r.json()
        time.sleep(0.5)
    pytest.fail(f"таймаут ожидания {url}")


def test_health_and_auth(client):
    assert client.get(f"{API}/health").status_code == 200
    me = client.get(f"{API}/auth/me")
    assert me.status_code == 200 and me.json()["email"]
    r = client.post(f"{API}/auth/login", json={"email": "x@y.z", "password": "wrong-password", "remember": False})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"


def test_unauthorized_and_not_found(client):
    from fastapi.testclient import TestClient

    anon = TestClient(client.app)
    assert anon.get(f"{API}/runs").status_code == 401
    assert client.get(f"{API}/runs/run_nope").status_code == 404
