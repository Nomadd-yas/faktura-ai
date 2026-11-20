"""API: правки фактов, ручные факты, замечания, проверки, журнал, отмена (план 5.10). Заглушки.

Эндпоинты: POST /facts/{id}/accept|reject|restore, PATCH /facts/{id}, POST /facts/bulk, POST /papers/{id}/facts,
POST /papers/{id}/facts/guess, POST /normalize/preview; GET /issues, GET /papers/{id}/issues,
POST /issues/resolve, POST /issues/{id}/reopen; GET /checks, GET /checks/rules; GET /history,
POST /history/undo, POST /actions/{id}/undo.
"""
from __future__ import annotations

from fastapi import APIRouter

ROUTERS: list[APIRouter] = []  # роутеры добавляет исполнитель модуля

__all__ = ["ROUTERS"]
