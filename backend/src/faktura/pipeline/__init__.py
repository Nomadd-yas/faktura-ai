"""Конвейер обработки, корпус, прогоны, сборка выхода (план 5.6). Заглушки."""
from __future__ import annotations

from .build import build_services
from .corpus import AddFilesResult, CorpusService
from .events import InlineExecutor, InMemoryEventBus, ThreadExecutor
from .facts import build_facts
from .normalize import apply_normalization
from .output import build_output
from .processor import PaperProcessor, detect_semantic_duplicate
from .review import recheck_paper, transfer_review
from .runs import RunService

__all__ = [
    "build_services", "AddFilesResult", "CorpusService", "InlineExecutor", "InMemoryEventBus", "ThreadExecutor",
    "build_facts", "apply_normalization", "build_output", "PaperProcessor", "detect_semantic_duplicate",
    "recheck_paper", "transfer_review", "RunService",
]
