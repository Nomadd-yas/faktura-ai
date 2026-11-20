"""Язык запросов к таблице данных (GET /data/query/parse, план 5.9)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..domain.entities import FactFilter
from ..domain.ports import Normalizer
from ..domain.profile import ExtractionProfile


class ParsedQuery(BaseModel):
    filters: list[dict[str, Any]] = Field(default_factory=list)
    unparsed: list[str] = Field(default_factory=list)
    metric: str | None = None


def parse_query(collection: str, q: str, normalizer: Normalizer, profile: ExtractionProfile) -> ParsedQuery:
    raise NotImplementedError("query.parse_query")


def to_fact_filter(parsed: ParsedQuery, params: dict[str, Any]) -> FactFilter:
    raise NotImplementedError("query.to_fact_filter")
