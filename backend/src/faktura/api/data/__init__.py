"""API данных §15: GET /papers/{domain}/{pid}[/pdf|/ocr|/related], /results, /results/summary, /issues,
/issues/stats, /compare, заглушки /graph и /search. Заглушки: роутеры добавляет исполнитель api_data."""
from __future__ import annotations

from fastapi import APIRouter

ROUTERS: list[APIRouter] = []

__all__ = ["ROUTERS"]
