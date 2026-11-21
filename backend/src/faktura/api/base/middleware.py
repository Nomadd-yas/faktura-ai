"""ASGI-middleware: X-Request-Id, CORS по белому списку, перехват необработанных исключений (api_contract §1.3, §1.5).

Порядок (снаружи внутрь): RequestId -> CORS -> CatchAll -> FastAPI (обработчики AppError и валидации).
Так ``X-Request-Id`` есть в каждом ответе (включая отказ CORS), а заголовки CORS - и у ответов 500.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import JSON_MEDIA_TYPE, error_payload

log = logging.getLogger("faktura.api")

REQUEST_ID_HEADER = "X-Request-Id"
_SAFE_RID = re.compile(r"^[A-Za-z0-9._:\-]{1,200}$")

CORS_ALLOW_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
CORS_ALLOW_HEADERS = ("Authorization", "Content-Type", "X-Request-Id", "Last-Event-ID", "Idempotency-Key", "Range",
                      "If-None-Match", "If-Range")
CORS_EXPOSE_HEADERS = ("X-Action-Id", "X-Request-Id", "Content-Disposition", "X-Check-Status", "X-Faktura-Stub",
                       "Retry-After", "ETag", "Content-Range", "Accept-Ranges", "X-Export-Tables",
                       "X-Export-Results", "X-Export-Papers", "X-Export-Filename", "X-Export-Rejected")
CORS_MAX_AGE = 600


def new_request_id() -> str:
    return uuid.uuid4().hex


def _state(scope: Scope) -> dict[str, Any]:
    st = scope.get("state")
    if st is None:
        st = scope["state"] = {}
    return st  # type: ignore[return-value]


async def _send_json(send: Send, status: int, body: dict[str, Any], headers: dict[str, str]) -> None:
    import json

    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    hdrs = [(b"content-type", JSON_MEDIA_TYPE.encode()), (b"content-length", str(len(raw)).encode())]
    hdrs += [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()]
    await send({"type": "http.response.start", "status": status, "headers": hdrs})
    await send({"type": "http.response.body", "body": raw})


class RequestIdMiddleware:
    """Сквозной id: значение клиента (если безопасное и не длиннее 200) или новый uuid; всегда в ответе."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        given = Headers(scope=scope).get(REQUEST_ID_HEADER)
        rid = given.strip() if given and _SAFE_RID.match(given.strip()) else new_request_id()
        _state(scope)["request_id"] = rid

        started = time.perf_counter()
        status = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = rid
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            user = getattr(_state(scope).get("faktura_user"), "id", None) or "-"
            log.info("%s %s -> %s %.0f мс request_id=%s user=%s", scope.get("method", "-"), scope.get("path", "-"),
                     status, (time.perf_counter() - started) * 1000, rid, user)


class CorsMiddleware:
    """CORS только для источников из белого списка; с учётными данными; ``*`` не используется.

    Предварительный запрос с чужого источника - 403 в едином формате ошибок без заголовков CORS."""

    def __init__(self, app: ASGIApp, origins: list[str] | tuple[str, ...]) -> None:
        self.app = app
        self.origins = {o.strip().rstrip("/") for o in origins if o and o.strip() and o.strip() != "*"}

    def allowed(self, origin: str | None) -> bool:
        return bool(origin) and origin.rstrip("/") in self.origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if not origin:
            await self.app(scope, receive, send)
            return
        ok = self.allowed(origin)
        if scope["method"] == "OPTIONS" and "access-control-request-method" in headers:
            if not ok:
                body = error_payload("forbidden", "Источник запроса не разрешён (CORS)", status=403)
                await _send_json(send, 403, body, {"Vary": "Origin"})
                return
            hdrs = {
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Methods": ", ".join(CORS_ALLOW_METHODS),
                "Access-Control-Allow-Headers": ", ".join(CORS_ALLOW_HEADERS),
                "Access-Control-Max-Age": str(CORS_MAX_AGE),
                "Vary": "Origin",
                "Content-Length": "0",
            }
            await send({"type": "http.response.start", "status": 204,
                        "headers": [(k.lower().encode(), v.encode("latin-1")) for k, v in hdrs.items()]})
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_with_cors(message: Message) -> None:
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                if ok:
                    h["Access-Control-Allow-Origin"] = origin
                    h["Access-Control-Allow-Credentials"] = "true"
                    h["Access-Control-Expose-Headers"] = ", ".join(CORS_EXPOSE_HEADERS)
                h.add_vary_header("Origin")
            await send(message)

        await self.app(scope, receive, send_with_cors)


class CatchAllMiddleware:
    """Необработанное исключение -> 500 ``internal_error`` с ``request_id`` (без подробностей наружу)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def send_tracking(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_tracking)
        except Exception:
            rid = _state(scope).get("request_id")
            log.exception("Необработанная ошибка %s %s [request_id=%s]", scope.get("method"), scope.get("path"), rid)
            if started:
                raise
            body = error_payload("internal_error", status=500, request_id=rid)
            await _send_json(send, 500, body, {REQUEST_ID_HEADER: rid} if rid else {})
