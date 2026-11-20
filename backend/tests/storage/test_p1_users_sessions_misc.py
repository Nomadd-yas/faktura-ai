"""Этап B: users, sessions, idempotency, gold."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from faktura.domain.errors import AppError

NOT_FOUND = (AppError, KeyError, LookupError)


def _mkuser(u, email="a@example.com", ws="ws_test", **kw):
    return u.users.create(workspace_id=ws, email=email, password_hash="$argon2id$x", **kw)


# ---- users -----------------------------------------------------------------------------------

def test_user_create_get(db):
    with db.uow() as u:
        a = _mkuser(u, name="Анна", is_admin=True)
    assert a.id and a.email == "a@example.com" and a.name == "Анна" and a.is_admin is True and a.disabled is False
    with db.uow() as u:
        assert u.users.get(a.id) == a


def test_user_get_unknown(db):
    with db.uow() as u:
        assert u.users.get("usr_nope") is None
        assert u.users.get_by_email("no@example.com") is None


def test_user_get_by_email_case_insensitive(db):
    with db.uow() as u:
        a = _mkuser(u, "Mixed@Example.com")
    with db.uow() as u:
        assert u.users.get_by_email("mixed@example.COM").id == a.id


def test_user_duplicate_email_rejected(db):
    with pytest.raises(Exception) as ei:
        with db.uow() as u:
            _mkuser(u, "dup@example.com")
            _mkuser(u, "DUP@example.com")
    assert ei.type is not NotImplementedError


def test_user_update(db):
    with db.uow() as u:
        a = _mkuser(u)
        b = u.users.update(a.id, name="Новое", disabled=True, password_hash="$argon2id$y")
    assert b.name == "Новое" and b.disabled is True and b.password_hash == "$argon2id$y"
    with db.uow() as u:
        assert u.users.get(a.id) == b


def test_user_update_unknown(db):
    with pytest.raises(NOT_FOUND):
        with db.uow() as u:
            u.users.update("usr_nope", name="x")


def test_user_list_by_workspace(db):
    with db.uow() as u:
        a, b = _mkuser(u, "a@x.org"), _mkuser(u, "b@x.org")
        _mkuser(u, "c@x.org", ws="ws_other")
    with db.uow() as u:
        assert {x.id for x in u.users.list("ws_test")} == {a.id, b.id}
        assert u.users.list("ws_none") == []


# ---- sessions --------------------------------------------------------------------------------

def _exp(days=30):
    return datetime.now(timezone.utc) + timedelta(days=days)


def test_session_create_get_by_hash(db):
    with db.uow() as u:
        usr = _mkuser(u)
        s = u.sessions.create(usr.id, "hash-1", _exp())
    assert s.id and s.user_id == usr.id
    with db.uow() as u:
        g = u.sessions.get_by_hash("hash-1")
    assert g is not None and g.id == s.id


def test_session_unknown_hash(db):
    with db.uow() as u:
        assert u.sessions.get_by_hash("nope") is None


def test_session_token_not_stored_plain(db):
    """В БД лежит только хеш: поиск по самому хешу работает, повторное создание того же хеша запрещено."""
    with pytest.raises(Exception) as ei:
        with db.uow() as u:
            usr = _mkuser(u)
            u.sessions.create(usr.id, "same-hash", _exp())
            u.sessions.create(usr.id, "same-hash", _exp())
    assert ei.type is not NotImplementedError


def test_session_chain_and_revoke(db):
    """Цепочка ротации refresh: parent_id; гашение отзывает сессию."""
    with db.uow() as u:
        usr = _mkuser(u)
        s1 = u.sessions.create(usr.id, "h1", _exp())
        s2 = u.sessions.create(usr.id, "h2", _exp(), parent_id=s1.id)
        u.sessions.revoke(s1.id)
    with db.uow() as u:
        r1, r2 = u.sessions.get_by_hash("h1"), u.sessions.get_by_hash("h2")
    assert r1 is None or getattr(r1, "revoked_at", None) is not None
    assert r2 is not None and getattr(r2, "revoked_at", None) is None
    assert getattr(r2, "parent_id", s1.id) == s1.id and s2.id


def test_session_revoke_idempotent_and_unknown(db):
    with db.uow() as u:
        usr = _mkuser(u)
        s = u.sessions.create(usr.id, "h1", _exp())
        u.sessions.revoke(s.id)
        u.sessions.revoke(s.id)
    with pytest.raises(NOT_FOUND):
        with db.uow() as u:
            u.sessions.revoke("ses_nope")


def test_session_requires_existing_user(db):
    with pytest.raises(Exception) as ei:
        with db.uow() as u:
            u.sessions.create("usr_nope", "h", _exp())
    assert ei.type is not NotImplementedError


# ---- idempotency -----------------------------------------------------------------------------

def test_idempotency_put_get(clocked_db):
    resp = {"status": 200, "body": {"id": "x", "список": [1, 2]}}
    with clocked_db.uow() as u:
        u.idempotency.put("usr_1", "key-1", resp)
    with clocked_db.uow() as u:
        assert u.idempotency.get("usr_1", "key-1") == resp


def test_idempotency_missing(clocked_db):
    with clocked_db.uow() as u:
        assert u.idempotency.get("usr_1", "nope") is None


def test_idempotency_per_user(clocked_db):
    with clocked_db.uow() as u:
        u.idempotency.put("usr_1", "k", {"a": 1})
        assert u.idempotency.get("usr_2", "k") is None
        u.idempotency.put("usr_2", "k", {"a": 2})
        assert u.idempotency.get("usr_1", "k") == {"a": 1} and u.idempotency.get("usr_2", "k") == {"a": 2}


def test_idempotency_ttl_default_24h(clocked_db, clock):
    with clocked_db.uow() as u:
        u.idempotency.put("usr_1", "k", {"a": 1})
        clock.advance(hours=23, minutes=59)
        assert u.idempotency.get("usr_1", "k") == {"a": 1}
        clock.advance(minutes=2)
        assert u.idempotency.get("usr_1", "k") is None


def test_idempotency_custom_ttl(clocked_db, clock):
    with clocked_db.uow() as u:
        u.idempotency.put("usr_1", "k", {"a": 1}, ttl_h=1)
        clock.advance(minutes=61)
        assert u.idempotency.get("usr_1", "k") is None


def test_idempotency_first_write_wins_or_overwrites_consistently(clocked_db):
    with clocked_db.uow() as u:
        u.idempotency.put("usr_1", "k", {"v": 1})
        u.idempotency.put("usr_1", "k", {"v": 2})
        assert u.idempotency.get("usr_1", "k") in ({"v": 1}, {"v": 2})


def test_idempotency_survives_new_uow(clocked_db):
    with clocked_db.uow() as u:
        u.idempotency.put("usr_1", "k", {"v": [1, None, True]})
    with clocked_db.uow() as u:
        assert u.idempotency.get("usr_1", "k") == {"v": [1, None, True]}


# ---- gold ------------------------------------------------------------------------------------

def test_gold_get_unknown_none(db):
    with db.uow() as u:
        assert u.gold.get("solar") is None


def test_gold_load_from_mini_dir_and_get(db, mini_gold_dir):
    with db.uow() as u:
        gs = u.gold.load_from_dir("solar", mini_gold_dir)
        assert gs is not None
    with db.uow() as u:
        got = u.gold.get("solar")
    assert got is not None
    assert u_len(got) == u_len(gs) and u_len(got) >= 2


def u_len(gs) -> int:
    """Число статей эталона: у GoldSet это bundles / papers / publications."""
    for attr in ("bundles", "papers", "publications"):
        v = getattr(gs, attr, None)
        if v is not None:
            return len(v)
    raise AssertionError("GoldSet без списка статей")


def test_gold_reload_replaces(db, mini_gold_dir, tmp_path):
    with db.uow() as u:
        u.gold.load_from_dir("solar", mini_gold_dir)
        u.gold.load_from_dir("solar", mini_gold_dir)
    with db.uow() as u:
        assert u_len(u.gold.get("solar")) == 2  # повторная загрузка не удваивает


def test_gold_datasets_isolated(db, mini_gold_dir):
    with db.uow() as u:
        u.gold.load_from_dir("solar", mini_gold_dir)
        assert u.gold.get("rag") is None


def test_gold_missing_dir(db, tmp_path):
    with pytest.raises((FileNotFoundError, NotADirectoryError, AppError)):
        with db.uow() as u:
            u.gold.load_from_dir("solar", tmp_path / "nope")


def test_gold_empty_dir_is_empty_set_or_error(db, tmp_path):
    (tmp_path / "e").mkdir()
    try:
        with db.uow() as u:
            gs = u.gold.load_from_dir("solar", tmp_path / "e")
    except (ValueError, AppError):
        return
    assert u_len(gs) == 0


@pytest.mark.data
def test_gold_load_dev_gold(db, dev_gold_dir, gold_bundles):
    with db.uow() as u:
        u.gold.load_from_dir("solar", dev_gold_dir)
    with db.uow() as u:
        assert u_len(u.gold.get("solar")) == len(gold_bundles)
