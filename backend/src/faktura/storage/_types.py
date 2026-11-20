"""Типы storage, которых пока нет в faktura.domain (план 4: модуль заводит у себя, координатор переносит)."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from ..domain.entities import User
from ..domain.io import tables_from_bundles
from ..domain.records import PaperBundle
from ..domain.tables import TABLES

DEFAULT_WORKSPACE_ID = "ws_default"
DEFAULT_WORKSPACE_NAME = "Команда хакатона"
RESTORE_WINDOW_DAYS = 30


class StoredUser(User):
    """User + поля профиля spec/03 §3.1 (наружу их отдаёт api_base)."""
    role: str | None = None
    team: str | None = None
    color: str | None = None
    focus: str | None = None
    last_login_at: datetime | None = None


class Workspace(BaseModel):
    id: str
    name: str
    created_at: datetime | None = None


class RefreshSession(BaseModel):
    id: str
    user_id: str
    token_hash: str
    parent_id: str | None = None
    remember: bool = True
    expires_at: datetime
    revoked_at: datetime | None = None
    user_agent: str | None = None
    ip: str | None = None
    created_at: datetime | None = None


class GoldSet(BaseModel):
    """Эталон (dev_gold): статьи как PaperBundle в порядке файлов."""
    id: str
    dataset: str
    source_dir: str | None = None
    loaded_at: datetime | None = None
    bundles: list[PaperBundle] = Field(default_factory=list)

    @property
    def paper_count(self) -> int:
        return len(self.bundles)

    @property
    def result_count(self) -> int:
        return sum(len(b.results) for b in self.bundles)

    def by_publication(self) -> dict[str, PaperBundle]:
        return {b.publication_id: b for b in self.bundles}

    def write_dir(self, directory: Path) -> Path:
        """7 CSV эталона в каталог (для EvalAdapter.evaluate(gold_dir, pred_dir))."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        rows = tables_from_bundles(self.bundles)
        for t, cols in TABLES.items():
            with open(directory / f"{t}.csv", "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(cols))
                w.writeheader()
                w.writerows(rows[t])
        return directory


class Synonym(BaseModel):
    id: str
    workspace_id: str
    collection: str
    param: str
    text: str
    custom: bool = True
    created_by: str | None = None
    created_at: datetime | None = None
