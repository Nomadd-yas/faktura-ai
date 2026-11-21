"""Обработка статьи областей без своих pydantic-моделей (rag): проверка по DomainSpec, лёгкая нормализация,
проверка цитат, сохранение в `sub_rows` (docs/design/DOMAINS.md §3).

Правила проверки ответа модели (`validate_tables`):
* колонки - ровно колонки шаблона, значения строками как в CSV (None -> "", bool -> true/false, список -> «;»);
* закрытые словари: регистр приводится к словарному, иначе ячейка очищается + предупреждение
  (publications.in_scope: пустое/неверное -> «true» + предупреждение, иначе статья выпала бы из выгрузки);
* рекомендуемые словари: только предупреждение, значение остаётся;
* ссылки (results -> experiments/configurations/datasets/tasks, duplicate_of, issues.record_ids): висячие
  очищаются + предупреждение; duplicate_of на себя и циклы очищаются;
* повтор ключа в таблице / запись без ключа (кроме issues) -> `ml_invalid_data` («Модель вернула некорректные данные: …»);
* статья вне области (in_scope=false): остаётся только строка publications;
* нормализация results: normalized_value только при простом числе и известной единице (значение не пересчитывается,
  доли и проценты не трогаются); пустой metric_value повторяет normalized_value.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..domain.entities import PaperStats, PaperStatus, ProcessOutcome
from ..domain.errors import AppError, ExtractError
from ..domain.extraction import ExtractRequest, GenericOutcome
from ..domain.registry import DomainSpec, get_domain
from ..ml.validate import invalid_error
from ._pdf import local_pdf

log = logging.getLogger(__name__)

# ссылки, которые check_submission.py проверяет сверх DomainSpec.refs
_EXTRA_REFS = {"results.duplicate_of": ("results", "result_id")}
_LIST_REFS = {"issues.record_ids": ("results", "result_id")}
_TABLE_RU = {"publications": "публикации", "configurations": "конфигурации", "datasets": "наборы данных",
             "tasks": "задачи", "experiments": "эксперименты", "results": "результаты", "issues": "замечания"}
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")
_TRUE = {"true", "1", "yes"}
_FALSE = {"false", "0", "no"}


def cell(v: Any) -> str:
    """Значение ответа модели -> ячейка CSV (строка)."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (list, tuple)):
        return ";".join(cell(x) for x in v if x is not None and cell(x))
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _plain_number(s: str) -> bool:
    return bool(_NUMBER.match(s.strip()))


def _int_year(s: str) -> int | None:
    try:
        n = int(float(s))
    except (TypeError, ValueError, OverflowError):
        return None
    return n if 1 <= n <= 9999 else None


def validate_tables(spec: DomainSpec, raw_tables: dict[str, list[dict[str, Any]]], publication_id: str
                    ) -> tuple[dict[str, list[dict[str, str]]], list[str]]:
    """(таблицы-строки, предупреждения). Тяжёлые нарушения -> ExtractError('ml_invalid_data')."""
    warnings: list[str] = []
    tables: dict[str, list[dict[str, str]]] = {}
    for name, cols in spec.tables.items():
        tables[name] = [{c: cell(r.get(c)) for c in cols} for r in raw_tables.get(name) or []]
    for r in tables["publications"]:
        r["publication_id"] = publication_id
    pub = tables["publications"][0] if tables["publications"] else {"publication_id": publication_id}
    tables["publications"] = [pub]
    for name, rows in tables.items():
        if name != "publications":
            for r in rows:
                r["publication_id"] = publication_id

    # in_scope / причина исключения; вне области - только publications
    scope = pub.get("in_scope", "").lower()
    pub["in_scope"] = "true" if scope in _TRUE else "false" if scope in _FALSE else ""
    if not pub["in_scope"]:
        if scope:
            warnings.append(f"publications.in_scope: значение «{scope}» вне словаря - принято «true»")
        pub["in_scope"] = "true"
    if pub["in_scope"] == "false":
        if not pub.get("exclusion_reason"):
            pub["exclusion_reason"] = "Причина исключения не указана"
            warnings.append("publications.exclusion_reason: статья вне области без причины - подставлена заглушка")
        return {n: (tables[n] if n == "publications" else []) for n in tables}, warnings
    if pub.get("exclusion_reason"):
        pub["exclusion_reason"] = ""
        warnings.append("publications.exclusion_reason: статья в области - причина исключения очищена")

    # ключи: повтор или пустой ключ - ошибка файла (issues без id нумеруются)
    problems: list[str] = []
    ids: dict[str, set[str]] = {}
    for name, rows in tables.items():
        key = spec.keys.get(name)
        if name == "publications" or not key:
            continue
        seen: set[str] = set()
        dup: list[str] = []
        n_empty = 0
        for r in rows:
            v = r[key]
            if not v:
                if name == "issues":
                    continue
                n_empty += 1
            elif v in seen:
                if v not in dup:
                    dup.append(v)
            seen.add(v)
        if name == "issues":
            n = 0
            for r in rows:
                if not r[key]:
                    n += 1
                    while f"I{n}" in seen:
                        n += 1
                    r[key] = f"I{n}"
                    seen.add(r[key])
        problems += [f"в таблице «{_TABLE_RU[name]}» повторяется идентификатор {v}" for v in dup]
        if n_empty:
            problems.append(f"в таблице «{_TABLE_RU[name]}» {n_empty} записей без идентификатора")
        ids[name] = seen
    if problems:
        raise invalid_error(problems)

    # словари
    for qual, allowed in spec.closed_enums.items():
        t, col = qual.split(".")
        if t == "publications":
            continue
        for r in tables.get(t, []):
            v = r.get(col, "")
            if not v or v in allowed:
                continue
            fixed = next((a for a in allowed if a.casefold() == v.casefold()), "")
            r[col] = fixed
            if not fixed:
                warnings.append(f"{t}.{col} ({r.get(spec.keys.get(t, ''), '')}): значение «{v}» вне словаря - ячейка очищена")
    for qual, allowed in spec.recommended_enums.items():
        t, col = qual.split(".")
        for r in tables.get(t, []):
            v = r.get(col, "")
            if v and v not in allowed:
                warnings.append(f"{t}.{col} ({r.get(spec.keys.get(t, ''), '')}): нестандартное значение «{v}»")

    # ссылки
    refs = {**spec.refs, **{k: v for k, v in _EXTRA_REFS.items() if k.split(".")[1] in spec.tables[k.split(".")[0]]}}
    for qual, (target, tkey) in refs.items():
        t, col = qual.split(".")
        known = ids.get(target, set())
        for r in tables.get(t, []):
            v = r.get(col, "")
            if v and v not in known:
                r[col] = ""
                warnings.append(f"{t}.{col} ({r.get(spec.keys.get(t, ''), '')}): ссылка «{v}» не найдена - очищена")
    for qual, (target, tkey) in _LIST_REFS.items():
        t, col = qual.split(".")
        if col not in spec.tables.get(t, ()):
            continue
        known = ids.get(target, set())
        for r in tables.get(t, []):
            parts = [x.strip() for x in r.get(col, "").split(";") if x.strip()]
            bad = [x for x in parts if x not in known]
            if bad:
                r[col] = ";".join(x for x in parts if x in known)
                warnings.append(f"{t}.{col} ({r.get(spec.keys.get(t, ''), '')}): ссылки не найдены - убраны: {', '.join(bad)}")
    by_id = {r["result_id"]: r for r in tables.get("results", [])}
    for r in tables.get("results", []):
        chain = [r["result_id"]]
        cur = r.get("duplicate_of", "")
        while cur:
            if cur in chain:
                r["duplicate_of"] = ""
                warnings.append(f"results.duplicate_of ({r['result_id']}): цикл или ссылка на саму запись - очищена")
                break
            chain.append(cur)
            cur = by_id[cur].get("duplicate_of", "") if cur in by_id else ""

    # лёгкая нормализация results: без пересчёта значений, долей и процентов
    for r in tables.get("results", []):
        mv, mu = r.get("metric_value", ""), r.get("metric_unit", "")
        nv, nu = r.get("normalized_value", ""), r.get("normalized_unit", "")
        if not mv and nv and _plain_number(nv):
            r["metric_value"] = mv = nv
            if not mu and nu:
                r["metric_unit"] = mu = nu
        elif not nv and mv and _plain_number(mv) and mu:
            r["normalized_value"], r["normalized_unit"] = mv, mu
            nv, nu = mv, mu
        if nv and not nu and mu:
            r["normalized_unit"] = nu = mu
        if nv and mv and _plain_number(nv) and _plain_number(mv) and float(nv) != float(mv):
            warnings.append(f"results ({r['result_id']}): metric_value «{mv}» не равно normalized_value «{nv}»")
        if nv and nu != mu:
            warnings.append(f"results ({r['result_id']}): metric_unit «{mu}» не равна normalized_unit «{nu}»")
    return tables, warnings


def check_quotes(quote, tables: dict[str, list[dict[str, str]]]) -> tuple[dict[str, bool | None], dict[str, bool | None]]:
    """(result_id -> quote_found, issue_id -> quote_found). None - цитата не проверяется."""
    memo: dict[tuple[str, str], bool | None] = {}

    def one(r: dict[str, str]) -> bool | None:
        frag, page = r.get("source_fragment", ""), r.get("source_page", "")
        if not frag.strip():
            return None
        k = (page, frag)
        if k not in memo:
            memo[k] = quote.check(page, frag)
        return memo[k]

    res = {r["result_id"]: one(r) for r in tables.get("results", []) if r.get("result_id")}
    iss = {r["issue_id"]: one(r) for r in tables.get("issues", []) if r.get("issue_id")}
    return res, iss


class GenericProcessor:
    """Обработка одной статьи области rag. Вызывается из PaperProcessor.process для run-области != solar."""

    def __init__(self, parent) -> None:
        self.parent = parent  # PaperProcessor: services, extractor, _fail, _publish

    def process(self, paper, file, *, mode: str, model_version: str,
                on_stage: Callable[[str, int], None] | None = None,
                abort: threading.Event | None = None) -> ProcessOutcome:
        p = self.parent
        db = p.services.db
        domain = paper.collection or "solar"
        if mode != "full":
            raise AppError("validation_error", "Для этой области доступен только полный разбор", field="mode")
        if file is None or not file.storage_key:
            code = (file.error_code if file is not None else None) or "pdf_broken"
            return p._fail(paper, code, (file.error_message if file else None) or "Нет PDF статьи")
        with db.uow() as uow:
            uow.papers.update(paper.id, status=PaperStatus.processing, error_code=None)
        try:
            with local_pdf(p.services.store, file.storage_key, f"{paper.publication_id}.pdf") as pdf:
                return self._process_pdf(paper, file, pdf, domain, model_version, on_stage, abort)
        except ExtractError as e:
            if e.code == "ml_unavailable":
                with db.uow() as uow:
                    uow.papers.update(paper.id, status=PaperStatus.queued)
                raise
            return p._fail(paper, e.code, e.message)
        except AppError as e:
            return p._fail(paper, e.code, e.message)

    def _process_pdf(self, paper, file, pdf: Path, domain: str, model_version: str,
                     on_stage: Callable[[str, int], None] | None, abort: threading.Event | None) -> ProcessOutcome:
        from ..pdfio.quotes import VariantQuoteIndex

        p = self.parent
        s = p.services
        spec = get_domain(domain)

        def stage(name: str, progress: int) -> None:
            if on_stage is not None:
                try:
                    on_stage(name, progress)
                except Exception:  # noqa: BLE001
                    log.debug("on_stage", exc_info=True)

        req = ExtractRequest(publication_id=paper.publication_id, pdf_path=pdf, sha256=paper.sha256 or "",
                             domain=domain, model_version=model_version or "latest")
        t0 = time.monotonic()
        out = p.extractor.extract(req)
        t1 = time.monotonic()
        if not isinstance(out, GenericOutcome):
            raise ExtractError("ml_schema_mismatch", "Сервис разбора вернул ответ другой области")
        stage("Нормализация", 60)
        tables, warns = validate_tables(spec, out.tables, paper.publication_id)
        t2 = time.monotonic()
        warnings = [*out.warnings, *warns]
        pub = tables["publications"][0]
        in_scope = pub["in_scope"] != "false"
        quotes_r: dict[str, bool | None] = {}
        quotes_i: dict[str, bool | None] = {}
        if in_scope and (tables["results"] or tables["issues"]):
            stage("Проверка", 85)
            quotes_r, quotes_i = check_quotes(VariantQuoteIndex(pdf), tables)
        t3 = time.monotonic()
        if abort is not None and abort.is_set():
            raise AppError("file_timeout", "Разбор файла прерван по таймауту")
        n_res, n_iss = len(tables["results"]), len(tables["issues"])
        mv = out.model_version
        versions = {"model": str(mv), "from_cache": "true" if out.from_cache else "false"}
        stats = PaperStats(open_issues=n_iss, facts_count=n_res)
        with s.db.uow() as uow:
            uow.sub.replace(paper.id, paper.corpus_id, domain, tables, quote_found=quotes_r,
                            issue_quote_found=quotes_i)
            fields: dict[str, Any] = dict(
                status=PaperStatus.done, error_code=None, model_version=str(mv), versions=versions,
                processed_at=uow.now(), in_scope=in_scope, title=pub.get("title") or None,
                year=_int_year(pub.get("year", "")), doi=pub.get("DOI") or None, stats=stats,
                needs_ocr=bool(file.is_scan))
            if file.page_count:
                fields["page_count"] = file.page_count
            uow.papers.update(paper.id, **fields)
        log.info("rag %s: извлечение %.2f с, проверка %.2f с, цитаты %.2f с, сохранение %.2f с (results=%d, issues=%d)",
                 paper.publication_id, t1 - t0, t2 - t1, t3 - t2, time.monotonic() - t3, n_res, n_iss)
        p._publish(paper.workspace_id, "paper.done", {"paper_id": paper.id, "chat_id": None, "title": pub.get("title"),
                                                      "facts": n_res, "issues": n_iss})
        return ProcessOutcome(paper_id=paper.id, status=PaperStatus.done, stats=stats, from_cache=out.from_cache,
                              warnings=warnings)
