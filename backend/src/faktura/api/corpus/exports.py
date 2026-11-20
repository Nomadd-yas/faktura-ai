"""Задачи экспорта: POST /exports, GET /exports/{id} (план 5.8)."""
from __future__ import annotations

from typing import Any

from ...domain.entities import CurrentUser, Export


class ExportJobs:
    def submit(self, scope: dict[str, Any], options: dict[str, Any], user: CurrentUser) -> Export:
        raise NotImplementedError("api.corpus.ExportJobs.submit")

    def status(self, export_id: str) -> Export:
        raise NotImplementedError("api.corpus.ExportJobs.status")
