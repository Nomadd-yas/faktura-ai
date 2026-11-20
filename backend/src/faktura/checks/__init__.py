"""Проверки (spec/05, план 5.3): правила бэка, проверка находок ML, слияние, ключи, повторы,
сопоставимость, качество и покрытие статьи.

Модуль опирается только на faktura.domain: цитаты проверяются через протокол QuoteChecker.
"""
from __future__ import annotations

from .links import DuplicateLink, comparability_key, link_duplicates
from .merge import assign_keys, check_ml_issues, merge_issues
from .paper import CheckOutcome, check_paper
from .quality import paper_coverage, paper_quality
from .rules import BACKEND_RULES, PLAUSIBILITY_LIMITS, RULES_VERSION, run_backend_rules

__all__ = [
    "RULES_VERSION", "BACKEND_RULES", "PLAUSIBILITY_LIMITS", "DuplicateLink", "CheckOutcome",
    "run_backend_rules", "check_ml_issues", "merge_issues", "assign_keys", "link_duplicates", "comparability_key",
    "paper_quality", "paper_coverage", "check_paper",
]
