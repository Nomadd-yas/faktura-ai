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














# ---- sessions --------------------------------------------------------------------------------

def _exp(days=30):
    return datetime.now(timezone.utc) + timedelta(days=days)








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






# ---- idempotency -----------------------------------------------------------------------------















# ---- gold ------------------------------------------------------------------------------------





def u_len(gs) -> int:
    """Число статей эталона: у GoldSet это bundles / papers / publications."""
    for attr in ("bundles", "papers", "publications"):
        v = getattr(gs, attr, None)
        if v is not None:
            return len(v)
    raise AssertionError("GoldSet без списка статей")










