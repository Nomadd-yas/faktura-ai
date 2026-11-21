"""API v2 - см. routes_v2. Ниже - старое описание (план 5.10, заменено §9).
API: правки фактов, ручные факты, замечания, проверки, журнал, отмена (план 5.10). Заглушки.

Эндпоинты: POST /facts/{id}/accept|reject|restore, PATCH /facts/{id}, POST /facts/bulk, POST /papers/{id}/facts,
POST /papers/{id}/facts/guess, POST /normalize/preview; GET /issues, GET /papers/{id}/issues,
POST /issues/resolve, POST /issues/{id}/reopen; GET /checks, GET /checks/rules; GET /history,
POST /history/undo, POST /actions/{id}/undo.
"""
from __future__ import annotations

from fastapi import APIRouter

from .routes_v2 import router as _v2_router

ROUTERS: list[APIRouter] = [_v2_router]  # v2 (BACKEND_ONLY §9): правки, замечания, журнал

__all__ = ["ROUTERS"]
