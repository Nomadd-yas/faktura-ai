"""Контейнер зависимостей, общий для CLI и API. Собирает pipeline.build_services."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..config import Settings
from .ports import EvalAdapter, EventBus, ExtractAdapter, Executor, FileStore, Normalizer, ParseAdapter


@dataclass
class Services:
    settings: Settings
    db: Any  # faktura.storage.Database
    store: FileStore
    parser: ParseAdapter
    normalizer: Normalizer
    bus: EventBus
    executor: Executor
    extractor_factory: Callable[[], ExtractAdapter]
    evaluator: EvalAdapter
