"""faktura eval | check | users | serve | python -m faktura (план 5.6)."""
from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from faktura.domain.tables import EVAL_METRICS, TABLES

BACKEND = Path(__file__).resolve().parents[2]


def _walk(obj, key):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            yield from _walk(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, key)


# --- eval ---------------------------------------------------------------------------------------------------------

def test_eval_self_is_perfect(run_cli, mini_gold_dir, tmp_path):
    rep = tmp_path / "rep.json"
    r = run_cli("eval", "--gold", mini_gold_dir, "--pred", mini_gold_dir, "--json", rep)
    assert r.exit_code == 0, r
    data = json.loads(rep.read_text(encoding="utf-8"))
    ps, rs = [v for v in _walk(data, "precision") if v is not None], [v for v in _walk(data, "recall") if v is not None]
    assert ps and rs and all(v == 1 for v in ps + rs)
    assert set(data["per_metric"]) <= set(EVAL_METRICS)


def test_eval_prints_metrics_table(run_cli, mini_gold_dir):
    r = run_cli("eval", "--gold", mini_gold_dir, "--pred", mini_gold_dir)
    assert r.exit_code == 0 and "PCE" in r.output and "Voc" in r.output


def test_eval_detects_wrong_value(run_cli, mini_gold_dir, tmp_path):
    pred = tmp_path / "pred"
    shutil.copytree(mini_gold_dir, pred)
    p = pred / "results.csv"
    # eval сравнивает normalized_value (К-3), поэтому портим все вхождения, а не только metric_value
    txt = p.read_text(encoding="utf-8-sig").replace("12.89", "99.9")
    p.write_text(txt, encoding="utf-8-sig")
    rep = tmp_path / "rep.json"
    r = run_cli("eval", "--gold", mini_gold_dir, "--pred", pred, "--json", rep)
    assert r.exit_code == 0
    data = json.loads(rep.read_text(encoding="utf-8"))
    assert any(v is not None and v < 1 for v in _walk(data, "precision"))


def test_eval_issues_flag(run_cli, mini_gold_dir, tmp_path):
    rep = tmp_path / "rep.json"
    r = run_cli("eval", "--gold", mini_gold_dir, "--pred", mini_gold_dir, "--issues", "--json", rep)
    assert r.exit_code == 0 and re.search(r"(?i)recall|замечан|issue", r.output)


def test_eval_missing_gold_exit_1(run_cli, tmp_path, mini_gold_dir):
    assert run_cli("eval", "--gold", tmp_path / "nope", "--pred", mini_gold_dir).exit_code == 1


def test_eval_pred_without_csv_exit_1(run_cli, tmp_path, mini_gold_dir):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert run_cli("eval", "--gold", mini_gold_dir, "--pred", empty).exit_code == 1


# --- check ---------------------------------------------------------------------------------------------------------------

@pytest.fixture
def csv_dir(mini_gold_dir, tmp_path):
    d = tmp_path / "csv"
    shutil.copytree(mini_gold_dir, d)
    return d


def test_check_ok(run_cli, csv_dir, pdf_dir, fake_validator):
    r = run_cli("check", csv_dir, pdf_dir, "--script", fake_validator("ok"))
    assert r.exit_code == 0 and "Ошибок: 0" in r.output


def test_check_errors_exit_3(run_cli, csv_dir, pdf_dir, fake_validator):
    r = run_cli("check", csv_dir, pdf_dir, "--script", fake_validator("errors"))
    assert r.exit_code == 3 and "metric_value" in r.output


def test_check_crash_exit_3(run_cli, csv_dir, pdf_dir, fake_validator):
    assert run_cli("check", csv_dir, pdf_dir, "--script", fake_validator("crash")).exit_code == 3


def test_check_missing_dirs_exit_1(run_cli, tmp_path, fake_validator):
    assert run_cli("check", tmp_path / "x", tmp_path / "y", "--script", fake_validator("ok")).exit_code == 1


def test_check_missing_script_not_success(run_cli, csv_dir, pdf_dir, tmp_path):
    r = run_cli("check", csv_dir, pdf_dir, "--script", tmp_path / "nope.py")
    assert r.exit_code in (1, 3)


# --- users -----------------------------------------------------------------------------------------------------------------

@pytest.fixture
def db_url(tmp_path):
    return f"sqlite:///{(tmp_path / 'u.db').as_posix()}"


def test_users_create_and_list(run_cli, db_url):
    r = run_cli("users", "create", "ann@example.com", "--password", "long-enough-1", "--db", db_url)
    assert r.exit_code == 0, r
    out = run_cli("users", "list", "--db", db_url)
    assert out.exit_code == 0 and "ann@example.com" in out.output


def test_users_create_short_password_exit_1(run_cli, db_url):
    r = run_cli("users", "create", "ann@example.com", "--password", "short", "--db", db_url)
    assert r.exit_code == 1
    assert "ann@example.com" not in run_cli("users", "list", "--db", db_url).output


def test_users_create_boundary_8_chars(run_cli, db_url):
    assert run_cli("users", "create", "a@b.co", "--password", "12345678", "--db", db_url).exit_code == 0
    assert run_cli("users", "create", "c@d.co", "--password", "1234567", "--db", db_url).exit_code == 1


def test_users_create_duplicate_email_exit_1(run_cli, db_url):
    run_cli("users", "create", "ann@example.com", "--password", "long-enough-1", "--db", db_url)
    assert run_cli("users", "create", "ann@example.com", "--password", "long-enough-2", "--db", db_url).exit_code == 1


def test_users_create_generates_password_with_flag(run_cli, db_url):
    r = run_cli("users", "create", "gen@example.com", "--generate", "--db", db_url)
    assert r.exit_code == 0 and len(r.output.strip()) >= 8


def test_users_create_requires_password_or_generate(run_cli, db_url):
    assert run_cli("users", "create", "nopw@example.com", "--db", db_url).exit_code == 1


def test_users_admin_flag_listed(run_cli, db_url):
    run_cli("users", "create", "boss@example.com", "--password", "long-enough-1", "--admin", "--db", db_url)
    assert "admin" in run_cli("users", "list", "--db", db_url).output.lower()


def test_users_disable_enable(run_cli, db_url):
    run_cli("users", "create", "ann@example.com", "--password", "long-enough-1", "--db", db_url)
    assert run_cli("users", "disable", "ann@example.com", "--db", db_url).exit_code == 0
    assert "disabled" in run_cli("users", "list", "--db", db_url).output.lower()
    assert run_cli("users", "enable", "ann@example.com", "--db", db_url).exit_code == 0
    assert "disabled" not in run_cli("users", "list", "--db", db_url).output.lower()


def test_users_unknown_email_exit_1(run_cli, db_url):
    run_cli("users", "list", "--db", db_url)
    for cmd in ("disable", "enable"):
        assert run_cli("users", cmd, "ghost@example.com", "--db", db_url).exit_code == 1
    assert run_cli("users", "reset-password", "ghost@example.com", "--password", "long-enough-1",
                   "--db", db_url).exit_code == 1


def test_users_reset_password_changes_hash_and_validates(run_cli, db_url):
    from faktura.domain.security import verify_password
    from faktura.storage import Database
    run_cli("users", "create", "ann@example.com", "--password", "long-enough-1", "--db", db_url)
    assert run_cli("users", "reset-password", "ann@example.com", "--password", "tiny", "--db", db_url).exit_code == 1
    assert run_cli("users", "reset-password", "ann@example.com", "--password", "brand-new-pass",
                   "--db", db_url).exit_code == 0
    db = Database(db_url)
    with db.uow() as uow:
        h = uow.users.get_by_email("ann@example.com").password_hash
    assert verify_password("brand-new-pass", h) and not verify_password("long-enough-1", h)


def test_users_password_not_echoed_when_given(run_cli, db_url):
    r = run_cli("users", "create", "ann@example.com", "--password", "secret-pass-123", "--db", db_url)
    assert "secret-pass-123" not in r.output


def test_users_list_never_prints_hash(run_cli, db_url):
    run_cli("users", "create", "ann@example.com", "--password", "long-enough-1", "--db", db_url)
    out = run_cli("users", "list", "--db", db_url).output
    assert "argon2" not in out and "$" not in out


# --- serve / __main__ -------------------------------------------------------------------------------------------------------------

def test_serve_calls_uvicorn_with_factory(run_cli, monkeypatch):
    import uvicorn
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: calls.append((a, kw)))
    r = run_cli("serve", "--host", "0.0.0.0", "--port", "9123")
    assert r.exit_code == 0, r
    (a, kw), = calls
    target = a[0] if a else kw.get("app")
    assert isinstance(target, str) and target.endswith("create_app") and kw.get("factory") is True
    assert kw["host"] == "0.0.0.0" and kw["port"] == 9123


def test_serve_defaults(run_cli, monkeypatch):
    import uvicorn
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: calls.append(kw))
    assert run_cli("serve").exit_code == 0
    assert calls[0]["host"] == "127.0.0.1" and calls[0]["port"] == 8000


def test_serve_bad_port_exit_1(run_cli):
    assert run_cli("serve", "--port", "abc").exit_code == 1


def test_module_entrypoint_help():
    env = {**__import__("os").environ, "PYTHONPATH": str(BACKEND / "src")}
    p = subprocess.run([sys.executable, "-m", "faktura", "--help"], capture_output=True, text=True, cwd=BACKEND,
                       env=env, timeout=60)
    assert p.returncode == 0
    for cmd in ("extract", "import-raw", "eval", "check", "users", "serve"):
        assert cmd in p.stdout


def test_extract_help_lists_options():
    env = {**__import__("os").environ, "PYTHONPATH": str(BACKEND / "src")}
    p = subprocess.run([sys.executable, "-m", "faktura", "extract", "--help"], capture_output=True, text=True,
                       cwd=BACKEND, env=env, timeout=60)
    assert p.returncode == 0
    for opt in ("--in", "--out", "--check", "--extractor", "--extract-url", "--retry-delays", "--no-dedupe",
                "--cache-dir", "--no-cache", "--no-backend-rules", "--server", "--fail-fast"):
        assert opt in p.stdout
