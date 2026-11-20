"""Представления статьи: card, layout, anchors, coverage, structure, processing, trace, source (план 5.9)."""
from __future__ import annotations

from typing import Any

from ...domain.services import Services


def card(services: Services, paper_id: str) -> dict[str, Any]: raise NotImplementedError("api.papers.views.card")
def layout(services: Services, paper_id: str) -> dict[str, Any]: raise NotImplementedError("api.papers.views.layout")
def anchors(services: Services, paper_id: str) -> dict[str, Any]:
    raise NotImplementedError("api.papers.views.anchors")
def coverage(services: Services, paper_id: str) -> dict[str, Any]:
    raise NotImplementedError("api.papers.views.coverage")
def structure(services: Services, paper_id: str) -> dict[str, Any]:
    raise NotImplementedError("api.papers.views.structure")
def processing(services: Services, paper_id: str) -> dict[str, Any]:
    raise NotImplementedError("api.papers.views.processing")
def trace(services: Services, fact_id: str) -> dict[str, Any]: raise NotImplementedError("api.papers.views.trace")
def source(services: Services, fact_id: str) -> dict[str, Any]: raise NotImplementedError("api.papers.views.source")
