"""Оценка по dev_gold по spec/14 §14.7 (только 11 metric_name ТЗ; REQ-116…REQ-119, К-3).

- `evaluate(gold_dir, pred_dir)` / `evaluate_tables(gold, pred)` - P/R/F1 по метрикам, точность атрибутов;
- `evaluate_issues(...)` - совпадение замечаний по (publication_id, check_name, frozenset(record_ids));
- `accuracy_response(report, ...)` - форма `GET /checks/accuracy` (spec/04 §4.13);
- `LocalEval`, `FakturaEvalAdapter` - реализации `EvalAdapter`.
"""
from __future__ import annotations

from ..domain.ports import EvalReport, IssueEvalReport
from .accuracy import ERROR_KIND_LABELS, PARAM_LABELS, SOURCE_LABELS, accuracy_response, report_markdown
from .adapters import EvalToolError, FakturaEvalAdapter, LocalEval
from .core import evaluate, evaluate_issues, evaluate_issues_tables, evaluate_tables, source_kind

__all__ = ["EvalReport", "IssueEvalReport", "evaluate", "evaluate_tables", "evaluate_issues",
           "evaluate_issues_tables", "accuracy_response", "report_markdown", "source_kind", "LocalEval",
           "FakturaEvalAdapter", "EvalToolError", "PARAM_LABELS", "SOURCE_LABELS", "ERROR_KIND_LABELS"]
