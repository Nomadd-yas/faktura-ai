"""JWT HS256 (PyJWT), refresh-сессии, ограничители частоты (BACKEND §2.1, план 5.7).

* Время берётся только из ``utcnow()`` этого модуля (тесты подменяют его через monkeypatch).
* Access - JWT на 15 мин: ``sub``, ``workspace_id`` (и ``wid`` по BACKEND §2.1), ``is_admin``, ``exp``, ``iat``, ``jti``.
* Refresh - непрозрачная строка ``rt_…``; в БД хранится только sha256 (``SessionsRepo``). Каждый refresh выдаёт новую
  сессию с ``parent_id`` и гасит старую; предъявление погашенного токена гасит всю цепочку ротации.
* Ключ подписи - ``faktura.config.get_settings().jwt_secret`` либо явный аргумент ``secret`` (приложение передаёт
  ключ из своих Settings). Если ключ не задан - случайный ключ процесса (токены не переживут перезапуск).
"""
from __future__ import annotations

import hashlib
import logging
import math
import secrets
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from pydantic import BaseModel, ValidationError

from ...domain.entities import User
from ...domain.errors import AppError
from . import errors as _errors  # noqa: F401 - регистрирует коды входа в ERROR_CATALOG

log = logging.getLogger("faktura.api.auth")

UTC = timezone.utc
ACCESS_TTL_S = 900
REFRESH_PREFIX = "rt_"
JWT_ALG = "HS256"
COOKIE_NAME = "faktura_refresh"
MAX_TRACKED_KEYS = 10_000  # после этого числа ключей лимитеры вычищают устаревшие записи

_PROCESS_SECRET = secrets.token_urlsafe(48)
_warned_secret = False


def utcnow() -> datetime:
    """Единственный источник времени api.base (подменяется в тестах)."""
    return datetime.now(UTC)


def as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    remember: bool = False


class Claims(BaseModel):
    sub: str
    workspace_id: str
    is_admin: bool = False
    exp: int
    iat: int


def _settings_secret() -> str:
    global _warned_secret
    from ... import config  # noqa: PLC0415 - get_settings подменяется в тестах

    secret = config.get_settings().jwt_secret
    if secret:
        return secret
    if not _warned_secret:
        log.warning("FAKTURA_JWT_SECRET не задан: используется случайный ключ процесса")
        _warned_secret = True
    return _PROCESS_SECRET


def resolve_secret(secret: str | None) -> str:
    return secret or _settings_secret()


def new_refresh_token() -> str:
    return REFRESH_PREFIX + secrets.token_urlsafe(32)


def hash_refresh(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_access(user: User, *, now: datetime, secret: str | None = None, ttl_s: int = ACCESS_TTL_S) -> str:
    iat = int(as_utc(now).timestamp())
    payload = {"sub": user.id, "workspace_id": user.workspace_id, "wid": user.workspace_id,
               "is_admin": bool(user.is_admin), "iat": iat, "exp": iat + int(ttl_s),
               "jti": secrets.token_hex(8)}
    return jwt.encode(payload, resolve_secret(secret), algorithm=JWT_ALG)


def issue_tokens(user: User, *, remember: bool, now: datetime, secret: str | None = None,
                 access_ttl_s: int = ACCESS_TTL_S) -> TokenPair:
    """Пара токенов. Refresh только генерируется: сессию в БД заводит ``SessionService``."""
    return TokenPair(access_token=issue_access(user, now=now, secret=secret, ttl_s=access_ttl_s),
                     refresh_token=new_refresh_token(), expires_in=int(access_ttl_s), remember=bool(remember))


def decode_access(token: str, *, now: datetime, secret: str | None = None) -> Claims:
    """Проверка подписи и срока. ``AppError('token_expired')`` / ``AppError('unauthorized')``."""
    if not isinstance(token, str) or not token or token.count(".") != 2 or len(token) > 4096:
        raise AppError("unauthorized", "Нужно войти")
    try:
        payload = jwt.decode(token, resolve_secret(secret), algorithms=[JWT_ALG],
                             options={"verify_exp": False, "verify_iat": False, "verify_nbf": False,
                                      "require": ["sub", "exp", "iat"]})
    except jwt.InvalidTokenError:
        raise AppError("unauthorized", "Нужно войти") from None
    if "workspace_id" not in payload and "wid" in payload:
        payload["workspace_id"] = payload["wid"]
    try:
        claims = Claims.model_validate(payload)
    except ValidationError:
        raise AppError("unauthorized", "Нужно войти") from None
    if as_utc(now).timestamp() >= claims.exp:
        raise AppError("token_expired", "Сессия истекла")
    return claims


# --- ограничители ------------------------------------------------------------------------------

def _retry_after(oldest: datetime, window: timedelta, now: datetime) -> int:
    return max(1, int(math.ceil((oldest + window - now).total_seconds())))


class LoginLimiter:
    """Ограничение неудачных попыток входа: ключ ``email:<почта>`` - 5 за 15 мин, ``ip:<адрес>`` - 20 за 15 мин.

    Хранится в памяти экземпляра (у каждого приложения свой). ``check`` бросает ``too_many_attempts`` с
    ``details.retry_after`` (секунды до момента, когда попыток станет меньше лимита)."""

    def __init__(self, email_limit: int = 5, ip_limit: int = 20, window_s: int = 15 * 60) -> None:
        self.email_limit = email_limit
        self.ip_limit = ip_limit
        self.window = timedelta(seconds=window_s)
        self._fails: dict[str, deque[datetime]] = {}
        self._lock = threading.Lock()

    def limit_for(self, key: str) -> int:
        return self.ip_limit if key.startswith("ip:") else self.email_limit

    def _prune(self, key: str, now: datetime) -> deque[datetime] | None:
        q = self._fails.get(key)
        if q is None:
            return None
        border = now - self.window
        while q and q[0] <= border:
            q.popleft()
        if not q:
            self._fails.pop(key, None)
            return None
        return q

    def check(self, key: str, *, now: datetime) -> None:
        now = as_utc(now)
        with self._lock:
            q = self._prune(key, now)
            limit = self.limit_for(key)
            if q is None or len(q) < limit:
                return
            ts = sorted(q)
            retry = _retry_after(ts[len(ts) - limit], self.window, now)
        minutes = max(1, math.ceil(retry / 60))
        raise AppError("too_many_attempts", f"Слишком много попыток. Попробуйте через {minutes} мин",
                       details={"retry_after": retry})

    def fail(self, key: str, *, now: datetime) -> None:
        now = as_utc(now)
        with self._lock:
            if len(self._fails) > MAX_TRACKED_KEYS:
                for k in list(self._fails):
                    self._prune(k, now)
            self._prune(key, now)
            self._fails.setdefault(key, deque()).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


class RateLimiter:
    """Скользящее окно: ``limit`` обращений за ``window_s`` на ключ (по умолчанию 600 в минуту).

    Отклонённые обращения не учитываются (блокировка не продлевается бесконечно)."""

    def __init__(self, limit: int = 600, window_s: int = 60, *, code: str = "too_many_requests",
                 message: str | None = None) -> None:
        self.limit = limit
        self.window = timedelta(seconds=window_s)
        self.code = code
        self.message = message
        self._hits: dict[str, deque[datetime]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, *, now: datetime) -> None:
        now = as_utc(now)
        with self._lock:
            border = now - self.window
            if key not in self._hits and len(self._hits) > MAX_TRACKED_KEYS:
                for k in [k for k, q in self._hits.items() if not q or q[-1] <= border]:
                    del self._hits[k]
            q = self._hits.setdefault(key, deque())
            while q and q[0] <= border:
                q.popleft()
            if len(q) >= self.limit:
                retry = _retry_after(q[0], self.window, now)
                retry = min(retry, max(1, int(self.window.total_seconds())))
            else:
                q.append(now)
                return
        raise AppError(self.code, self.message, details={"retry_after": retry})


# --- refresh-сессии ----------------------------------------------------------------------------

@dataclass
class SessionTokens:
    user: User
    pair: TokenPair
    max_age: int  # секунды жизни cookie/refresh


class SessionService:
    """Выдача, ротация и гашение refresh-сессий поверх ``uow.sessions`` (storage.SessionsRepo)."""

    def __init__(self, *, secret: str | None, access_ttl_s: int = ACCESS_TTL_S, refresh_ttl_days: int = 30,
                 refresh_short_ttl_h: int = 12) -> None:
        self.secret = secret
        self.access_ttl_s = access_ttl_s
        self.long_ttl = timedelta(days=refresh_ttl_days)
        self.short_ttl = timedelta(hours=refresh_short_ttl_h)

    def ttl(self, remember: bool) -> timedelta:
        return self.long_ttl if remember else self.short_ttl

    def start(self, uow: Any, user: User, *, remember: bool, now: datetime, parent_id: str | None = None,
              ip: str | None = None, user_agent: str | None = None) -> SessionTokens:
        pair = issue_tokens(user, remember=remember, now=now, secret=self.secret, access_ttl_s=self.access_ttl_s)
        ttl = self.ttl(remember)
        uow.sessions.create(user.id, hash_refresh(pair.refresh_token), as_utc(now) + ttl, parent_id=parent_id,
                            remember=bool(remember), ip=ip, user_agent=(user_agent or "")[:300] or None)
        return SessionTokens(user=user, pair=pair, max_age=int(ttl.total_seconds()))

    def rotate(self, uow: Any, token: str | None, *, now: datetime, ip: str | None = None,
               user_agent: str | None = None) -> SessionTokens | AppError:
        """Новая пара по refresh. При ошибке возвращает (а не бросает) AppError, чтобы гашение цепочки
        зафиксировалось транзакцией до ответа 401."""
        now = as_utc(now)
        if not token or not isinstance(token, str) or len(token) > 512:
            return AppError("refresh_invalid")
        session = uow.sessions.get_by_hash(hash_refresh(token))
        if session is None:
            return AppError("refresh_invalid")
        if session.revoked_at is not None:
            # повторное предъявление погашенного токена - признак кражи: гасим всю цепочку
            uow.sessions.revoke_chain(session.id)
            log.warning("Повторное предъявление refresh: цепочка сессии %s погашена", session.id)
            return AppError("refresh_invalid")
        if as_utc(session.expires_at) <= now:
            uow.sessions.revoke(session.id)
            return AppError("refresh_invalid")
        user = uow.users.get(session.user_id)
        if user is None:
            uow.sessions.revoke_chain(session.id)
            return AppError("refresh_invalid")
        if user.disabled:
            uow.sessions.revoke_all_for_user(user.id)
            return AppError("account_disabled")
        uow.sessions.revoke(session.id)
        return self.start(uow, user, remember=session.remember, now=now, parent_id=session.id, ip=ip,
                          user_agent=user_agent)

    def revoke(self, uow: Any, token: str | None) -> str | None:
        """Гасит сессию по токену; возвращает user_id владельца (или None)."""
        if not token or not isinstance(token, str) or len(token) > 512:
            return None
        session = uow.sessions.get_by_hash(hash_refresh(token))
        if session is None:
            return None
        if session.revoked_at is None:
            uow.sessions.revoke(session.id)
        return session.user_id
