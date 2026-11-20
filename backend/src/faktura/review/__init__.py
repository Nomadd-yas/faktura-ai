"""Сервис правок: принять/отклонить/исправить факт, ручные факты, замечания, отмена (план 5.10). Заглушки."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..domain.entities import Action, CurrentUser, Fact, FactFilter, IssuesDiff, Paper
from ..domain.services import Services


class MutationResult(BaseModel):
    fact: Fact | None = None
    action: Action | None = None
    issues_changed: IssuesDiff = Field(default_factory=IssuesDiff)
    paper: Paper | None = None


class BulkResult(BaseModel):
    changed: int = 0
    skipped: int = 0
    action: Action | None = None


class ResolveResult(BaseModel):
    resolved: list[str] = Field(default_factory=list)
    action: Action | None = None


class ReopenResult(BaseModel):
    issue_id: str
    action: Action | None = None


class UndoResult(BaseModel):
    undone: Action | None = None
    changed: list[str] = Field(default_factory=list)


class ReviewService:
    def __init__(self, services: Services) -> None:
        self.services = services

    def accept(self, user: CurrentUser, fact_id: str, *, group_id: str | None = None) -> MutationResult:
        raise NotImplementedError("review.accept")

    def reject(self, user: CurrentUser, fact_id: str, *, group_id: str | None = None) -> MutationResult:
        raise NotImplementedError("review.reject")

    def restore(self, user: CurrentUser, fact_id: str, *, group_id: str | None = None) -> MutationResult:
        raise NotImplementedError("review.restore")

    def patch_value(self, user: CurrentUser, fact_id: str, raw: str) -> MutationResult:
        raise NotImplementedError("review.patch_value")

    def add_manual(self, user: CurrentUser, paper_id: str, body: dict[str, Any]) -> MutationResult:
        raise NotImplementedError("review.add_manual")

    def guess(self, paper_id: str, body: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("review.guess")

    def bulk(self, user: CurrentUser, action: str, *, fact_ids: list[str] | None = None,
             filter: FactFilter | None = None) -> BulkResult:
        raise NotImplementedError("review.bulk")

    def resolve_issues(self, user: CurrentUser, issue_ids: list[str], how: str, accept_fact_id: str | None = None,
                       merge_action_ids: list[str] | None = None) -> ResolveResult:
        raise NotImplementedError("review.resolve_issues")

    def reopen_issue(self, user: CurrentUser, issue_id: str) -> ReopenResult:
        raise NotImplementedError("review.reopen_issue")

    def undo(self, user: CurrentUser, action_id: str | None = None) -> UndoResult:
        raise NotImplementedError("review.undo")
