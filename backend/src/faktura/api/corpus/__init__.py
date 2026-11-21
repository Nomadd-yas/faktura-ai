"""API: корпус, файлы, прогоны, SSE, /extract, экспорт, профили, паспорт (план 5.8). Заглушки.

Эндпоинты: GET /corpus/overview; POST/GET /corpora, GET/PATCH/DELETE /corpora/{id}, POST /corpora/{id}/restore,
POST/GET /corpora/{id}/files, POST /corpora/{id}/files/{file_id}/retry, POST/GET /corpora/{id}/runs,
GET /corpora/{id}/summary; GET /runs/{id}, POST /runs/{id}/cancel, GET /runs/{id}/output; POST /extract;
POST /papers; GET/POST /profiles, GET/PATCH /profiles/{id}; GET /passport; GET /events, POST /events/token;
POST /exports, GET /exports/{id}, GET /exports/{id}/file; заглушка POST /exports/preview.
"""
from __future__ import annotations

from fastapi import APIRouter

from .routes_manage import router as manage_router
from .routes_export import router as export_router
from .routes_runs import router as runs_router

ROUTERS: list[APIRouter] = [runs_router, manage_router, export_router]  # ядро v1: /runs (CORE_API §2, §4)

__all__ = ["ROUTERS"]
