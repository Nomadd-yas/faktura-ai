"""Общий код выгрузок разбора: сбор таблиц с правками v2, ZIP набора, JSON и Excel (CORE_API §4, §4g).

Один источник для `/runs/{id}/submission.zip`, `/runs/{id}/dataset.json|xlsx` и `/submission.zip?domain=` (все разборы)."""
from __future__ import annotations

import io
import json
import logging
import math
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi.responses import Response

from ...domain.entities import CorpusFileStatus, RunStatus
from ...domain.errors import AppError
from ...domain.registry import get_domain
from ...domain.services import Services
from .views import RunData

log = logging.getLogger("faktura.api")

_IN_PROGRESS = (RunStatus.queued, RunStatus.running, RunStatus.cancelling)
_FILE_PENDING = (CorpusFileStatus.pending, CorpusFileStatus.queued, CorpusFileStatus.processing,
                 CorpusFileStatus.downloading)
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass
class Collected:
    """Таблицы выгрузки и всё, что нужно ZIP: счётчик статей, PDF для проверки, данные разборов для паспорта."""

    domain: str
    tables: dict[str, list[dict]]
    papers: int = 0
    pdf_keys: list[tuple[str, str]] = field(default_factory=list)
    any_scan: bool = False
    datas: list[RunData] = field(default_factory=list)


def collect_tables(uow, corpora: list, partial: bool = False) -> Collected:
    """Таблицы по разборам `corpora` (одной области; новые первыми) с правками и состояниями замечаний v2.

    Статья с одним publication_id берётся из самого свежего разбора, где она разобрана; упавшая статья (solar) даёт
    строку publications только с id и лишь если нигде не разобрана. Идущий разбор без `partial` - run_in_progress."""
    from ...review.runs_v2 import export_paper_tables  # v2: правки и состояния замечаний (§9)

    spec = get_domain(corpora[0].collection)
    col = Collected(spec.name, {t: [] for t in spec.tables})
    done: set[str] = set()
    placeholders: dict[str, dict] = {}
    for corpus in corpora:
        d = RunData(uow, corpus)
        if d.run is not None and d.run.status in _IN_PROGRESS and not partial:
            raise AppError("run_in_progress")
        col.datas.append(d)
        seen: set[str] = set()  # в пределах одного разбора первая запись с publication_id побеждает
        for f in d.files:
            pid = f.publication_id
            if pid in done or pid in seen:
                continue
            paper = d.papers.get(f.paper_id) if f.paper_id else None
            ok = paper is not None and f.status == CorpusFileStatus.done and paper.status.value == "done"
            if not ok:
                if partial and f.status in _FILE_PENDING:
                    continue
                if spec.name == "solar" and pid not in placeholders:
                    row = {"publication_id": pid}  # упавшая: только id
                    placeholders[pid] = row
                    col.tables["publications"].append(row)
                    col.papers += 1
                # rag: у упавшей статьи нет данных, строка без in_scope не прошла бы проверку - в выгрузку не входит
                seen.add(pid)
                continue
            seen.add(pid)
            done.add(pid)
            if pid in placeholders:  # в более свежем разборе упала, в этом разобрана
                col.tables["publications"].remove(placeholders.pop(pid))
            else:
                col.papers += 1
            if f.storage_key:
                col.pdf_keys.append((pid, f.storage_key))
            col.any_scan = col.any_scan or bool(f.is_scan)
            for t, rows in export_paper_tables(uow, corpus.id, spec.name, paper.id).items():
                col.tables[t].extend(rows)
    canon_results(col.tables.get("results") or [])
    return col


def canon_results(rows: list[dict]) -> list[dict]:
    """Последний шаг выгрузки results (обе области, после правок v2, повторов и отклонений): при заполненном
    normalized_value пара metric_* = normalized_* (check_submission: «metric_unit differs from normalized_unit»,
    «metric_value differs from normalized_value»). Исходная запись из статьи остаётся только в raw_value/raw_unit;
    если raw пуст, туда уходит прежнее metric_* до замены. Нет normalized_unit - единица статьи считается целевой."""
    for r in rows:
        nv = str(r.get("normalized_value") or "").strip()
        if not nv:
            continue
        mv = str(r.get("metric_value") or "").strip()
        mu = str(r.get("metric_unit") or "").strip()
        nu = str(r.get("normalized_unit") or "").strip() or mu
        if not str(r.get("raw_value") or "").strip() and mv:
            r["raw_value"] = mv
            if not str(r.get("raw_unit") or "").strip() and mu:
                r["raw_unit"] = mu
        r["normalized_value"], r["normalized_unit"] = nv, nu
        r["metric_value"], r["metric_unit"] = nv, nu
    return rows


def export_name(title: str | None) -> str:
    """«Набор для сдачи — {title}.zip» без символов, недопустимых в именах файлов."""
    stem = re.sub(r'[\\/:*?"<>|\r\n]+', "_", (title or "").strip()).strip(" .")
    return f"Набор для сдачи — {stem}.zip" if stem else "Набор для сдачи.zip"


def run_check(services: Services, tables: dict[str, list[dict]], pdf_keys: list[tuple[str, str]],
              domain: str = "solar", ocr: bool = False):
    """check_submission.py области на выгруженных CSV и PDF разбора (во временной папке). Скрипт - по области
    (solar: settings.check_script, rag: settings.rag_check_script). rag: коды выхода 0/1/2, `--ocr` для сканов."""
    from ...config import domain_paths
    from ...export import run_check_script, run_rag_check, table_bytes

    spec = get_domain(domain)
    script = domain_paths(services.settings, domain)[2]
    if script is None or not Path(script).is_file():
        raise AppError("extractor_unavailable", "Проверка недоступна: скрипт проверки не настроен", http_status=503)
    tmp = Path(tempfile.mkdtemp(prefix="faktura_zipcheck_"))
    try:
        csv_dir, pdf_dir = tmp / "csv", tmp / "pdfs"
        csv_dir.mkdir()
        pdf_dir.mkdir()
        for t, cols in spec.tables.items():
            (csv_dir / f"{t}.csv").write_bytes(table_bytes(t, tables[t], cols))
        for pid, key in pdf_keys:
            try:
                services.store.copy_to(key, pdf_dir / f"{pid}.pdf")
            except Exception:  # noqa: BLE001
                log.warning("PDF %s не скопирован для проверки", pid)
        if domain == "solar":
            return run_check_script(Path(script), csv_dir, pdf_dir, timeout_s=services.settings.check_timeout_s)
        return run_rag_check(Path(script), csv_dir, pdf_dir, timeout_s=services.settings.check_timeout_s, ocr=ocr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _export_headers(col: Collected, filename: str) -> dict[str, str]:
    return {"X-Export-Tables": str(len(col.tables)), "X-Export-Results": str(len(col.tables["results"])),
            "X-Export-Papers": str(col.papers), "X-Export-Filename": quote(filename, safe="")}


def _disposition(ascii_name: str, filename: str) -> str:
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


def submission_response(services: Services, col: Collected, passport: dict, title: str | None, check: bool) -> Response:
    """ZIP набора: 7 CSV, PASSPORT.md, passport.json (+ check_report.txt при `check`)."""
    from ...export import check_status, table_bytes
    from ...passport import passport_json_bytes, passport_md_bytes

    spec = get_domain(col.domain)
    headers: dict[str, str] = {}
    report = None
    if check:
        report = run_check(services, col.tables, col.pdf_keys, spec.name,
                           ocr=col.any_scan and shutil.which("tesseract") is not None)
        status = check_status(report)
        if status == "failed":
            raise AppError("submission_invalid", "Набор не прошёл проверку check_submission.py", details={
                "errors": report.errors[:50], "errors_total": len(report.errors), "crashed": report.crashed,
                "warnings": len(report.warnings), "report": (report.raw_output or "")[-4000:]})
        headers["X-Check-Status"] = status or "ok"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for t, cols in spec.tables.items():
            z.writestr(f"{t}.csv", table_bytes(t, col.tables[t], cols))
        z.writestr("PASSPORT.md", passport_md_bytes(passport))
        z.writestr("passport.json", passport_json_bytes(passport))
        if report is not None:
            z.writestr("check_report.txt", (report.raw_output or "").encode("utf-8"))
    filename = export_name(title)
    return Response(buf.getvalue(), media_type="application/zip", headers={
        "Content-Disposition": _disposition("submission.zip", filename), **_export_headers(col, filename), **headers})


# --- dataset.json: те же ключи и типы, что в «Один файл JSON» фронта (ExportView.getJson) ------------------------------
_NUM_SOLAR = frozenset("year layer_order active_area bandgap_value metric_value normalized_value source_page "
                       "spread_value n_devices".split())
_NUM_RAG = frozenset("year source_page metric_value normalized_value baseline_value metric_k top_k chunk_size "
                     "chunk_overlap latency_ms tokens_per_query sample_size".split())
_BOOL = frozenset({"in_scope", "mask_used", "certified"})
_NUM_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def _number(v: str) -> int | float | None:
    s = v.strip()
    if not _NUM_RE.match(s):
        return None
    x = float(s)
    if not math.isfinite(x):
        return None
    return int(x) if x.is_integer() and abs(x) < 1e15 else x


def _typed(domain: str, table: str, col: str, raw: Any) -> Any:
    v = "" if raw is None else str(raw)
    if v == "":
        return None
    nums = _NUM_RAG if domain == "rag" else _NUM_SOLAR
    if col in nums and (n := _number(v)) is not None:
        return n
    if col in _BOOL and (domain == "solar" or col == "in_scope"):
        return v == "true"
    if col == "record_ids" or (domain == "solar" and col == "device_id" and table == "measurements") \
            or (domain == "rag" and col == "authors"):
        return [s.strip() for s in v.split(";")]
    return v


def dataset_json(col: Collected) -> dict[str, Any]:
    spec = get_domain(col.domain)
    meta: dict[str, Any] = {"generated_by": "Фактура AI"}
    if col.domain == "rag":
        meta["area"] = "rag"
    meta["tables"] = {t: len(col.tables[t]) for t in spec.tables}
    out: dict[str, Any] = {"meta": meta}
    for t, cols in spec.tables.items():
        out[t] = [{c: _typed(col.domain, t, c, r.get(c)) for c in cols} for r in col.tables[t]]
    return out


def dataset_json_response(col: Collected, stem: str) -> Response:
    name = f"submission-{'rag-' if col.domain == 'rag' else ''}{stem}.json"
    body = json.dumps(dataset_json(col), ensure_ascii=False, indent=2).encode("utf-8")
    return Response(body, media_type="application/json", headers={
        "Content-Disposition": _disposition(name, name), **_export_headers(col, name)})


# --- dataset.xlsx: один лист «Факты» с таблицей results, числа числами (как toXlsx фронта) ----------------------------
_XNUM = re.compile(r"^-?(0|[1-9]\d*)([.,]\d+)?$")
_XCTRL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")
_X = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
_NS = "http://schemas.openxmlformats.org/"


def _col_letters(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _esc(s: str) -> str:
    return _XCTRL.sub("", s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def results_xlsx(head: tuple[str, ...], rows: list[dict]) -> bytes:
    """Минимальная книга .xlsx (zipfile): лист «Факты», заголовок и строки; пустые ячейки не пишутся."""
    lines = []
    body = [["" if r.get(c) is None else str(r.get(c)) for c in head] for r in rows]
    for ri, cells in enumerate([list(head)] + body):
        out = []
        for ci, c in enumerate(cells):
            ref = f"{_col_letters(ci)}{ri + 1}"
            if ri > 0 and _XNUM.match(c):
                out.append(f'<c r="{ref}"><v>{c.replace(",", ".")}</v></c>')
            elif c != "":
                out.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{_esc(c)}</t></is></c>')
        lines.append(f'<row r="{ri + 1}">{"".join(out)}</row>')
    rel = _NS + "officeDocument/2006/relationships"
    ct = "application/vnd.openxmlformats-officedocument.spreadsheetml"
    parts = {
        "[Content_Types].xml": _X + f'<Types xmlns="{_NS}package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="{ct}.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="{ct}.worksheet+xml"/></Types>',
        "_rels/.rels": _X + f'<Relationships xmlns="{_NS}package/2006/relationships"><Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": _X + f'<workbook xmlns="{_NS}spreadsheetml/2006/main" xmlns:r="{rel}"><sheets><sheet name="Факты" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": _X + f'<Relationships xmlns="{_NS}package/2006/relationships"><Relationship Id="rId1" Type="{rel}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": _X + f'<worksheet xmlns="{_NS}spreadsheetml/2006/main"><sheetData>{"".join(lines)}</sheetData></worksheet>',
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(name, data.encode("utf-8"))
    return buf.getvalue()


def dataset_xlsx_response(col: Collected, stem: str) -> Response:
    spec = get_domain(col.domain)
    name = f"results-{'rag-' if col.domain == 'rag' else ''}{stem}.xlsx"
    return Response(results_xlsx(spec.tables["results"], col.tables["results"]), media_type=XLSX_MIME, headers={
        "Content-Disposition": _disposition(name, name), **_export_headers(col, name)})
