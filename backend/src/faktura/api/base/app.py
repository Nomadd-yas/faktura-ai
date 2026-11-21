"""Фабрика приложения FastAPI (план 5.7, 4.5).

Запуск: ``uvicorn --factory faktura.api.base.app:create_app`` (или ``faktura serve``).
"""
from __future__ import annotations

import importlib
import inspect
import logging

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

from ... import config as _config
from ...config import Settings
from ...domain.services import Services
from .context import make_context
from .errors import JSON_MEDIA_TYPE, install_error_handlers
from .middleware import CatchAllMiddleware, CorsMiddleware, RequestIdMiddleware

log = logging.getLogger("faktura.api")

API_TITLE = "Faktura API"
API_VERSION = "1.1"


_ADD_ROUTE_PARAMS = tuple(p for p in inspect.signature(FastAPI.add_api_route).parameters
                          if p not in ("self", "path", "endpoint", "methods"))


def add_router(app: FastAPI, router: APIRouter, prefix: str) -> None:
    """Подключает роутер так, чтобы его маршруты были обычными ``APIRoute`` в ``app.routes`` (с префиксом).

    FastAPI 0.16x в ``include_router`` хранит вложенный роутер целиком (``_IncludedRouter``), и маршруты не видны
    в ``app.routes``. Если в роутере есть что-то кроме ``APIRoute`` (вложенные роутеры, websocket) - обычный
    ``include_router``."""
    if not all(isinstance(r, APIRoute) for r in router.routes):
        app.include_router(router, prefix=prefix)
        return
    for r in router.routes:
        assert isinstance(r, APIRoute)
        kw = {k: getattr(r, k) for k in _ADD_ROUTE_PARAMS if hasattr(r, k)}
        app.add_api_route(prefix + r.path, r.endpoint, methods=sorted(r.methods or ()), **kw)


def _router_modules() -> tuple[str, ...]:
    import faktura.api as api_pkg  # noqa: PLC0415 - читаем список в момент вызова (тесты его подменяют)

    return tuple(api_pkg.ROUTER_MODULES)


def _is_missing(name: str, exc: ModuleNotFoundError) -> bool:
    """ModuleNotFoundError относится к самому пакету (или его родителю), а не к его зависимости."""
    missing = exc.name or ""
    return bool(missing) and (name == missing or name.startswith(missing + "."))


def mount_router_modules(app: FastAPI, prefix: str) -> list[str]:
    """Подключает ``ROUTERS`` пакетов из ``faktura.api.ROUTER_MODULES``. Нет пакета - пропуск с предупреждением;
    любая другая ошибка импорта (в т. ч. отсутствующая зависимость внутри пакета) роняет запуск."""
    mounted: list[str] = []
    for name in _router_modules():
        try:
            mod = importlib.import_module(name)
        except ModuleNotFoundError as e:
            if _is_missing(name, e):
                log.warning("Пакет роутеров %s не найден — пропущен", name)
                continue
            raise
        if not hasattr(mod, "ROUTERS"):
            raise RuntimeError(f"В пакете {name} нет списка ROUTERS")
        for r in mod.ROUTERS:
            add_router(app, r, prefix)
        mounted.append(name)
    return mounted


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    """services=None -> лениво faktura.pipeline.build_services (при первом обращении). Роутеры модулей из
    api.ROUTER_MODULES подключаются через importlib внутри функции: если пакета нет (ModuleNotFoundError именно
    этого модуля) - пропуск с предупреждением в лог; любая другая ошибка импорта роняет запуск. Префикс API_PREFIX.
    Эндпоинты base: GET /health; POST /auth/login|refresh|logout, GET /auth/me; GET/PATCH /me;
    GET /collections; GET/POST /admin/users, PATCH /admin/users/{id}; заглушки P2 (X-Faktura-Stub: 1)."""
    from .. import API_PREFIX  # noqa: PLC0415
    from . import routes_auth, routes_stubs, routes_users  # noqa: PLC0415

    settings = settings if settings is not None else _config.get_settings()
    app = FastAPI(
        title=API_TITLE, version=API_VERSION,
        description="Бэкенд Faktura: извлечение данных из научных PDF. Контракт — docs/wave1/api_contract.md",
        openapi_url=f"{API_PREFIX}/openapi.json", docs_url=f"{API_PREFIX}/docs", redoc_url=None,
    )
    app.state.faktura = make_context(settings, services)
    install_error_handlers(app)

    # порядок: add_middleware ставит новый слой снаружи -> RequestId(CORS(CatchAll(app)))
    app.add_middleware(CatchAllMiddleware)
    app.add_middleware(CorsMiddleware, origins=list(settings.cors_origins or []))
    app.add_middleware(RequestIdMiddleware)

    for r in (routes_users.router, routes_auth.router, routes_stubs.router):
        add_router(app, r, API_PREFIX)
    mount_router_modules(app, API_PREFIX)
    log.debug("Приложение собрано (JSON %s)", JSON_MEDIA_TYPE)
    return app
