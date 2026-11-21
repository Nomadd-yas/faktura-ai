"""Паспорт набора разбора: один источник для `passport.json` / `passport.md` и для `submission.zip` (§7а.6)."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ...domain.entities import CorpusFileStatus
from ...domain.services import Services
from ...passport.run_passport import build_run_passport
from .views import RunData


def run_passport(services: Services, d: RunData | Sequence[RunData]) -> dict[str, Any]:
    """Паспорт области разбора (или нескольких разборов одной области, новые первыми); версии модели/правил/словаря -
    по разобранным статьям набора."""
    from ...checks.rules import RULES_VERSION

    datas = [d] if isinstance(d, RunData) else list(d)
    domain = datas[0].corpus.collection
    models: set[str] = set()
    versions: dict[str, str] = {}
    for rd in datas:
        for f in rd.files:
            paper = rd.papers.get(f.paper_id) if f.paper_id else None
            if paper is None or f.status != CorpusFileStatus.done or paper.status.value != "done":
                continue
            if paper.model_version:
                models.add(paper.model_version)
            for k in ("dictionary", "rules"):
                v = (paper.versions or {}).get(k)
                if v:
                    versions.setdefault(k, v)
    norm = services.normalizer
    dictionary = versions.get("dictionary")
    if domain == "solar":
        dictionary = dictionary or str(getattr(norm, "dictionary_version", "") or "") or None
    return build_run_passport(domain, model_versions=models, dictionary=dictionary,
                              rules=versions.get("rules") or (RULES_VERSION if domain == "solar" else None))
