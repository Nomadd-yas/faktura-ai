"""Удалённый CLI (P2-R): extract --server на httpx.MockTransport (api_contract У-31, plan 5.6)."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from faktura.domain.tables import TABLES

TABLE_FILES = sorted(f"{t}.csv" for t in TABLES)


def make_zip(check_status="ok") -> bytes:
    buf = io.BytesIO()
    def entry(name: str) -> zipfile.ZipInfo:  # фикстура clock замораживает time.time около 0: дата 1970 в ZIP нельзя
        return zipfile.ZipInfo(name, (2024, 1, 1, 0, 0, 0))

    with zipfile.ZipFile(buf, "w") as zf:
        for t, cols in TABLES.items():
            zf.writestr(entry(f"{t}.csv"), "﻿" + ",".join(cols) + "\r\n")
        zf.writestr(entry("check_report.txt"), "Ошибок: 0\n" if check_status != "failed" else "Ошибок: 3\n")
    return buf.getvalue()


class FakeApi:
    """Минимальный сервер контракта: логин -> корпус -> файлы -> прогон -> опрос -> экспорт -> файл."""

    def __init__(self, *, run_polls=2, export_polls=2, check_status="ok", login_status=200,
                 final_run_status="done") -> None:
        self.calls: list[tuple[str, str]] = []
        self.auth: list[str | None] = []
        self.bodies: dict[str, bytes] = {}
        self.run_polls_left, self.export_polls_left = run_polls, export_polls
        self.check_status, self.login_status, self.final_run_status = check_status, login_status, final_run_status

    def handler(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path.removeprefix("/api/v1")
        key = (req.method, path)
        self.calls.append(key)
        self.auth.append(req.headers.get("authorization"))
        self.bodies[f"{req.method} {path}"] = req.content
        if key == ("POST", "/auth/login"):
            if self.login_status != 200:
                return httpx.Response(self.login_status, json={"error": {"code": "invalid_credentials", "message": "x"}})
            return httpx.Response(200, json={"access_token": "tok123", "token_type": "Bearer", "expires_in": 900,
                                             "user": {"id": "usr_1", "email": "a@b.co"}})
        if key == ("POST", "/corpora"):
            return httpx.Response(201, json={"id": "crp_1", "corpus_id": "crp_1", "name": "cli", "kind": "extract"})
        if key == ("POST", "/corpora/crp_1/files"):
            return httpx.Response(202, json={"accepted": 2, "duplicates": 0, "rejected": [], "files": []})
        if key == ("POST", "/corpora/crp_1/runs"):
            return httpx.Response(202, json={"id": "run_1", "run_id": "run_1", "status": "queued"})
        if key == ("GET", "/runs/run_1"):
            if self.run_polls_left > 0:
                self.run_polls_left -= 1
                return httpx.Response(200, json={"id": "run_1", "status": "running", "progress": 0.5})
            return httpx.Response(200, json={"id": "run_1", "status": self.final_run_status, "progress": 1})
        if key == ("POST", "/exports"):
            return httpx.Response(202, json={"export_id": "exp_1", "id": "exp_1", "status": "processing"})
        if key == ("GET", "/exports/exp_1"):
            if self.export_polls_left > 0:
                self.export_polls_left -= 1
                return httpx.Response(200, json={"export_id": "exp_1", "id": "exp_1", "status": "processing"})
            st = "failed" if self.check_status == "failed" else "done"
            body = {"export_id": "exp_1", "id": "exp_1", "status": st, "url": "/api/v1/exports/exp_1/file",
                    "check": {"status": self.check_status}}
            if st == "failed":
                body["error"] = {"code": "submission_invalid", "message": "Набор не прошёл проверку: 3 ошибки"}
            return httpx.Response(200, json=body)
        if key == ("GET", "/exports/exp_1/file"):
            return httpx.Response(200, content=make_zip(self.check_status),
                                  headers={"content-type": "application/zip", "X-Check-Status": self.check_status})
        return httpx.Response(404, json={"error": {"code": "not_found", "message": req.url.path}})


@pytest.fixture
def api(monkeypatch, clock):
    holder = {}

    def make(**kw) -> FakeApi:
        a = FakeApi(**kw)
        orig = httpx.Client.__init__

        def patched(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(a.handler)
            orig(self, *args, **kwargs)

        monkeypatch.setattr(httpx.Client, "__init__", patched)
        holder["a"] = a
        return a

    return make


@pytest.fixture
def remote(run_cli, pdf_dir, tmp_path):
    def _run(*extra, env=None):
        e = {"FAKTURA_EMAIL": "a@b.co", "FAKTURA_PASSWORD": "long-enough-1"}
        e.update(env or {})
        return run_cli("extract", "--in", pdf_dir, "--out", tmp_path / "out", "--server", "http://api.test",
                       *extra, env=e)
    _run.out = tmp_path / "out"
    return _run


def test_full_sequence(api, remote):
    a = api()
    r = remote()
    assert r.exit_code == 0, r
    order = [c for c in a.calls if c != ("GET", "/runs/run_1") and c != ("GET", "/exports/exp_1")]
    assert order == [("POST", "/auth/login"), ("POST", "/corpora"), ("POST", "/corpora/crp_1/files"),
                     ("POST", "/corpora/crp_1/runs"), ("POST", "/exports"), ("GET", "/exports/exp_1/file")]
    assert a.calls.count(("GET", "/runs/run_1")) == 3  # 2 раза running + done
    assert a.calls.count(("GET", "/exports/exp_1")) == 3


def test_login_uses_email_and_password_from_env(api, remote):
    a = api()
    remote()
    body = json.loads(a.bodies["POST /auth/login"])
    assert body["email"] == "a@b.co" and body["password"] == "long-enough-1"


def test_bearer_token_on_all_later_calls(api, remote):
    a = api()
    remote()
    assert a.auth[0] is None
    assert set(a.auth[1:]) == {"Bearer tok123"}


def test_creates_corpus_with_solar_collection(api, remote):
    a = api()
    remote()
    body = json.loads(a.bodies["POST /corpora"])
    assert body["collection"] == "solar" and body["name"]


def test_uploads_pdf_files_multipart(api, remote):
    a = api()
    remote()
    raw = a.bodies["POST /corpora/crp_1/files"]
    assert b"D001.pdf" in raw and b"D003.pdf" in raw and b"%PDF" in raw


def test_export_requests_submission_for_corpus(api, remote):
    a = api()
    remote()
    body = json.loads(a.bodies["POST /exports"])
    assert body["format"] == "submission" and body["scope"] == {"corpus_id": "crp_1"}


def test_run_started_full_by_default(api, remote):
    a = api()
    remote()
    assert json.loads(a.bodies["POST /corpora/crp_1/runs"])["kind"] in ("full", "new_only")


def test_polling_pauses_between_requests(api, remote, clock):
    api()
    remote()
    assert len(clock.slept) >= 4 and all(s > 0 for s in clock.slept)


def test_files_unpacked_into_out(api, remote):
    api()
    remote()
    assert sorted(p.name for p in remote.out.glob("*.csv")) == TABLE_FILES
    assert (remote.out / "check_report.txt").exists() or (remote.out / "_meta" / "check_report.txt").exists()


def test_failed_check_exit_3_but_files_unpacked(api, remote):
    api(check_status="failed")
    r = remote()
    assert r.exit_code == 3, r
    assert sorted(p.name for p in remote.out.glob("*.csv")) == TABLE_FILES
    assert "3" in (r.output + r.stderr)


def test_warnings_status_is_success(api, remote):
    api(check_status="warnings")
    assert remote().exit_code == 0


def test_run_done_with_errors_exit_2(api, remote):
    api(final_run_status="done_with_errors")
    r = remote()
    assert r.exit_code == 2, r
    assert sorted(p.name for p in remote.out.glob("*.csv")) == TABLE_FILES


def test_login_rejected_exit_1(api, remote):
    a = api(login_status=401)
    r = remote()
    assert r.exit_code == 1
    assert [c for c in a.calls if c[0] == "POST" and c[1] != "/auth/login"] == []
    assert "long-enough-1" not in r.output + r.stderr


@pytest.mark.parametrize("missing", ["FAKTURA_EMAIL", "FAKTURA_PASSWORD"])
def test_missing_credentials_exit_1(api, remote, missing):
    a = api()
    r = remote(env={missing: ""})
    assert r.exit_code == 1 and a.calls == []


def test_api_key_rejected_exit_1_with_message(api, remote):
    a = api()
    r = remote("--api-key", "k-123")
    assert r.exit_code == 1
    assert "FAKTURA_EMAIL" in r.output + r.stderr and "FAKTURA_PASSWORD" in r.output + r.stderr
    assert a.calls == []


def test_server_url_without_scheme_exit_1(api, run_cli, pdf_dir, tmp_path):
    api()
    r = run_cli("extract", "--in", pdf_dir, "--out", tmp_path / "o", "--server", "not a url",
                env={"FAKTURA_EMAIL": "a@b.co", "FAKTURA_PASSWORD": "long-enough-1"})
    assert r.exit_code == 1


def test_server_unreachable_exit_1(monkeypatch, remote, clock):
    def boom(req):
        raise httpx.ConnectError("refused", request=req)

    orig = httpx.Client.__init__
    monkeypatch.setattr(httpx.Client, "__init__",
                        lambda self, *a, **kw: orig(self, *a, **{**kw, "transport": httpx.MockTransport(boom)}))
    r = remote()
    assert r.exit_code == 1 and "Traceback" not in r.output + r.stderr
