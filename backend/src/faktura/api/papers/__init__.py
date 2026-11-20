"""API: статьи, страницы, факты (чтение), таблица фактов, язык запросов, точность (план 5.9). Заглушки.

Эндпоинты: GET /papers, GET/DELETE /papers/{id}, POST /papers/{id}/restore, GET /papers/{id}/pages/{n}, /file,
/layout, /anchors, /coverage, /structure, /processing, POST /papers/{id}/reprocess, PUT /papers/{id}/collection;
GET /facts, /facts/{id}, /facts/{id}/source, /facts/{id}/trace, GET /papers/{id}/facts, GET /facts/table;
GET /data/query/parse, /data/rows, /data/filter-options, /data/compare; GET /papers/{id}/links, /cited-by;
GET /checks/accuracy; POST /admin/gold; GET /dictionary; POST /admin/reprocess.
"""
from __future__ import annotations

from fastapi import APIRouter

ROUTERS: list[APIRouter] = []  # роутеры добавляет исполнитель модуля

__all__ = ["ROUTERS"]
