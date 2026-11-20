"""Этап B: seed_admin (пароль из env хешируется через domain.security)."""
from __future__ import annotations

import pytest

from faktura.config import Settings
from faktura.domain.errors import AppError
from faktura.domain.security import verify_password
from faktura.storage import seed_admin


def _settings(tmp_path, **kw) -> Settings:
    base = dict(_env_file=None, db_url="sqlite:///x.db", storage_dir=tmp_path / "s", cache_dir=tmp_path / "c",
                jwt_secret="s" * 32, admin_email="admin@example.com", admin_password="admin-password-1",
                cookie_secure=False)
    base.update(kw)
    return Settings(**base)


def test_seed_admin_creates_admin(db, tmp_path):
    seed_admin(db, _settings(tmp_path))
    with db.uow() as u:
        a = u.users.get_by_email("admin@example.com")
    assert a is not None and a.is_admin is True and a.disabled is False and a.workspace_id


def test_seed_admin_hashes_password(db, tmp_path):
    seed_admin(db, _settings(tmp_path))
    with db.uow() as u:
        a = u.users.get_by_email("admin@example.com")
    assert a.password_hash and "admin-password-1" not in a.password_hash
    assert a.password_hash.startswith("$argon2")
    assert verify_password("admin-password-1", a.password_hash) is True
    assert verify_password("wrong-password", a.password_hash) is False


def test_seed_admin_idempotent(db, tmp_path):
    st = _settings(tmp_path)
    seed_admin(db, st)
    with db.uow() as u:
        first = u.users.get_by_email("admin@example.com")
    seed_admin(db, st)
    with db.uow() as u:
        again = u.users.get_by_email("admin@example.com")
        assert len([x for x in u.users.list(first.workspace_id) if x.email == "admin@example.com"]) == 1
    assert again.id == first.id and again.password_hash == first.password_hash  # пароль не перехеширован


@pytest.mark.parametrize("pw", ["", "short", "1234567"])
def test_seed_admin_short_password_fails(db, tmp_path, pw):
    with pytest.raises(AppError) as ei:
        seed_admin(db, _settings(tmp_path, admin_password=pw))
    assert ei.value.code == "validation_error"
    with db.uow() as u:
        assert u.users.get_by_email("admin@example.com") is None


def test_seed_admin_minimum_length_accepted(db, tmp_path):
    seed_admin(db, _settings(tmp_path, admin_password="12345678"))
    with db.uow() as u:
        assert u.users.get_by_email("admin@example.com") is not None


def test_seed_admin_email_normalized(db, tmp_path):
    seed_admin(db, _settings(tmp_path, admin_email="Admin@Example.COM"))
    with db.uow() as u:
        assert u.users.get_by_email("admin@example.com") is not None


def test_seed_admin_does_not_log_password(db, tmp_path, caplog):
    seed_admin(db, _settings(tmp_path))
    assert "admin-password-1" not in caplog.text
