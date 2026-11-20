"""Строки 7 таблиц сдачи по одной статье: ровно то, что экспорт CSV выдал бы для этой статьи.

Один путь для CSV и для таблиц `sub_*` в БД: select_for_submission -> sanitize -> number_issues ->
`domain.io.tables_from_bundles`.
"""
from __future__ import annotations

from typing import Sequence

from ..domain.entities import Fact
from ..domain.profile import ExtractionProfile
from ..domain.records import PaperBundle
from .sanitize import number_issues, sanitize
from .select import select_for_submission

__all__ = ["paper_submission_tables"]


def paper_submission_tables(bundle: PaperBundle, profile: ExtractionProfile, *,
                            manual_facts: Sequence[Fact] = ()) -> tuple[dict[str, list[dict[str, str]]], list[str]]:
    """(таблица -> строки-словари как в CSV, события). Статья вне области - только строка publications."""
    from ..domain.io import tables_from_bundles

    sel = select_for_submission(bundle, profile, manual_facts=manual_facts)
    san = sanitize([sel.bundle])
    b = number_issues(san.bundles[0])
    return tables_from_bundles([b]), [*sel.events, *(f"sanitize: {e}" for e in san.events)]
