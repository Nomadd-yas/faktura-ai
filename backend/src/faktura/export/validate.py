"""Строгий валидатор и запуск check_submission.py (план 5.4; REQ-003, REQ-006, REQ-016…REQ-021, REQ-053).

`strict_validate` строже скрипта организаторов:
- error - всё, что валит `check_submission.py` (файлы и колонки, словари, числа, ссылки, повтор result_id),
  плюс уникальность остальных id (REQ-020), числа и логические значения в полях, которые скрипт не проверяет;
- warning - то, что скрипт печатает как «внимание», и мягкие правила REQ-021 (metric_* = normalized_*, единицы,
  check_name из каталога, единица без значения, замечание без страницы или цитаты).
Номер строки - как у скрипта: 1 - заголовок, первая строка данных - 2.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..domain.entities import CheckReport, ValidationProblem
from ..domain.tables import (BOOL_FIELDS, CHECK_CATALOG, EVAL_METRICS, ID_COLUMN, INTEGER_FIELDS, LIST_FIELDS,
                             NUMERIC_FIELDS, NUMERIC_STRICT, TABLES, TARGET_UNITS, UNIT_FOR_VALUE, VOCAB)
from .sanitize import REFERENCES

__all__ = ["strict_validate", "run_check_script", "parse_check_output"]

_KNOWN_METRICS = set(EVAL_METRICS) | set(TARGET_UNITS) | {"AVT", "CRI", "LUE", "bifaciality_factor"}
_FIELD_UNITS = {("materials", "bandgap_unit"): "eV", ("layers", "thickness_unit"): "nm",
                ("devices", "area_unit"): "cm2"}


def _p(level: str, code: str, msg: str, table: str | None = None, row: int | None = None,
       column: str | None = None) -> ValidationProblem:
    return ValidationProblem(level=level, code=code, table=table, row=row, column=column, message=msg)


def _cell(r: dict, c: str) -> str:
    v = r.get(c)
    return "" if v is None else str(v).strip()


def _is_number(s: str) -> bool:
    try:
        f = float(s)
    except ValueError:
        return False
    return f == f and f not in (float("inf"), float("-inf"))


def _is_integer(s: str) -> bool:
    try:
        d = Decimal(s)
    except InvalidOperation:
        return False
    return d.is_finite() and d == d.to_integral_value()


def strict_validate(tables: dict[str, list[dict[str, str]]]) -> list[ValidationProblem]:
    """REQ-020, REQ-021; пустые source_page/source_fragment у issues -> warning issue_source_missing."""
    probs: list[ValidationProblem] = []
    data: dict[str, list[dict[str, str]]] = {}
    for t, cols in TABLES.items():
        if t not in tables:
            probs.append(_p("error", "missing_table", f"нет таблицы {t}.csv", t))
            data[t] = []
            continue
        rows = tables[t] or []
        data[t] = rows
        missing = [c for c in cols if any(c not in r for r in rows)]
        if missing:
            probs.append(_p("error", "missing_columns", f"{t}.csv: нет колонок {', '.join(missing)}", t))
        extra = sorted({k for r in rows for k in r if k is not None and k not in cols})
        if extra:
            probs.append(_p("warning", "extra_columns", f"{t}.csv: лишние колонки {', '.join(extra)}", t))

    # словари, числа, логические
    for t, rows in data.items():
        for i, r in enumerate(rows, 2):
            for c in TABLES[t]:
                v = _cell(r, c)
                if not v:
                    continue
                key = (t, c)
                if key in VOCAB and v not in VOCAB[key]:
                    probs.append(_p("error", "vocab", f"{t}.csv, строка {i}: {c} = «{v}» — нет в списке допустимых",
                                    t, i, c))
                elif key in NUMERIC_FIELDS:
                    if not _is_number(v):
                        probs.append(_p("error", "not_number", f"{t}.csv, строка {i}: {c} = «{v}» — не число",
                                        t, i, c))
                    elif key in INTEGER_FIELDS and not _is_integer(v):
                        level = "error" if key in NUMERIC_STRICT else "warning"
                        probs.append(_p(level, "not_integer", f"{t}.csv, строка {i}: {c} = «{v}» — не целое",
                                        t, i, c))
                elif key in BOOL_FIELDS and v not in ("true", "false"):
                    probs.append(_p("error", "not_bool", f"{t}.csv, строка {i}: {c} = «{v}» — ожидается true/false",
                                    t, i, c))

    # публикации
    pubs: dict[str, dict] = {}
    for i, r in enumerate(data["publications"], 2):
        pid = _cell(r, "publication_id")
        if not pid:
            probs.append(_p("error", "empty_id", f"publications.csv, строка {i}: пустой publication_id",
                            "publications", i, "publication_id"))
            continue
        if pid in pubs:
            probs.append(_p("error", "duplicate_id", f"publications.csv, строка {i}: {pid} повторяется",
                            "publications", i, "publication_id"))
        pubs[pid] = r
        if _cell(r, "in_scope") == "false" and not _cell(r, "exclusion_reason"):
            probs.append(_p("warning", "no_exclusion_reason", f"publications.csv: {pid} исключена без exclusion_reason",
                            "publications", i, "exclusion_reason"))
    out_of_scope = {p for p, r in pubs.items() if _cell(r, "in_scope") == "false"}

    for t in ("materials", "devices", "layers", "measurements", "results", "issues"):
        col = ID_COLUMN[t]
        seen: dict[tuple[str, str], int] = {}
        for i, r in enumerate(data[t], 2):
            pid = _cell(r, "publication_id")
            if pid not in pubs:
                probs.append(_p("error", "unknown_publication",
                                f"{t}.csv, строка {i}: публикации {pid} нет в publications.csv", t, i, "publication_id"))
            elif pid in out_of_scope:
                probs.append(_p("warning", "out_of_scope_rows",
                                f"{t}.csv, строка {i}: у исключённой статьи {pid} не должно быть строк", t, i))
            rid = _cell(r, col)
            if not rid:
                probs.append(_p("error" if t == "results" else "warning", "empty_id",
                                f"{t}.csv, строка {i}: пустой {col}", t, i, col))
                continue
            k = (pid, rid)
            if k in seen:
                probs.append(_p("error", "duplicate_id",
                                f"{t}.csv, строка {i}: {col} {rid} повторяется (строка {seen[k]})", t, i, col))
            else:
                seen[k] = i

    # ссылки
    ids = {t: {(_cell(r, "publication_id"), _cell(r, ID_COLUMN[t])) for r in data[t]}
           for t in ("materials", "devices", "measurements", "results")}
    for t, c, target in REFERENCES:
        many = (t, c) in LIST_FIELDS
        for i, r in enumerate(data[t], 2):
            raw = _cell(r, c)
            vals = [x.strip() for x in raw.split(";")] if many else [raw]
            for v in vals:
                if v and (_cell(r, "publication_id"), v) not in ids[target]:
                    probs.append(_p("error", "dangling_ref",
                                    f"{t}.csv, строка {i}: {c} = {v} — такой записи нет в {target}.csv", t, i, c))

    # единица только при значении (REQ-037)
    for (t, vcol), (_, ucol) in UNIT_FOR_VALUE.items():
        for i, r in enumerate(data[t], 2):
            if _cell(r, ucol) and not _cell(r, vcol):
                probs.append(_p("warning", "unit_without_value",
                                f"{t}.csv, строка {i}: {ucol} = «{_cell(r, ucol)}» без {vcol}", t, i, ucol))
            elif _cell(r, ucol) and _cell(r, ucol) != _FIELD_UNITS[(t, ucol)]:
                probs.append(_p("warning", "unit", f"{t}.csv, строка {i}: {ucol} = «{_cell(r, ucol)}», "
                                f"ожидается {_FIELD_UNITS[(t, ucol)]}", t, i, ucol))

    # results: мягкие правила (REQ-021) и предупреждения скрипта
    for i, r in enumerate(data["results"], 2):
        status, origin = _cell(r, "extraction_status"), _cell(r, "result_origin")
        if origin == "primary" and not _cell(r, "device_id") and status == "extracted":
            probs.append(_p("warning", "no_device", f"results.csv, строка {i}: собственный результат без device_id",
                            "results", i, "device_id"))
        if origin == "cited" and not _cell(r, "cited_publication_id"):
            probs.append(_p("warning", "cited_without_ref",
                            f"results.csv, строка {i}: цитируемое значение без cited_publication_id",
                            "results", i, "cited_publication_id"))
        if status == "extracted" and not (_cell(r, "source_page") and (
                _cell(r, "source_fragment") or _cell(r, "source_table") or _cell(r, "source_figure"))):
            probs.append(_p("warning", "no_source", f"results.csv, строка {i}: не указано место извлечения",
                            "results", i, "source_page"))
        for a, b in (("metric_value", "normalized_value"), ("metric_unit", "normalized_unit")):
            va, vb = _cell(r, a), _cell(r, b)
            same = va == vb
            if not same and _is_number(va) and _is_number(vb):
                same = Decimal(va) == Decimal(vb)
            if not same:
                probs.append(_p("warning", "metric_ne_normalized",
                                f"results.csv, строка {i}: {a} «{va}» ≠ {b} «{vb}»", "results", i, a))
        name, unit = _cell(r, "metric_name"), _cell(r, "metric_unit")
        if name and name not in _KNOWN_METRICS:
            probs.append(_p("warning", "unknown_metric", f"results.csv, строка {i}: нестандартный metric_name «{name}»",
                            "results", i, "metric_name"))
        if name in TARGET_UNITS and unit and unit != TARGET_UNITS[name]:
            probs.append(_p("warning", "unit", f"results.csv, строка {i}: {name} в «{unit}», ожидается "
                            f"{TARGET_UNITS[name]}", "results", i, "metric_unit"))

    # issues
    for i, r in enumerate(data["issues"], 2):
        check = _cell(r, "check_name")
        if not check:
            probs.append(_p("warning", "empty_check_name", f"issues.csv, строка {i}: пустой check_name",
                            "issues", i, "check_name"))
        elif check not in CHECK_CATALOG or not CHECK_CATALOG[check].exported:
            probs.append(_p("warning", "unknown_check_name",
                            f"issues.csv, строка {i}: check_name «{check}» нет в каталоге spec/05 §5.2",
                            "issues", i, "check_name"))
        if not _cell(r, "source_page") or not _cell(r, "source_fragment"):
            probs.append(_p("warning", "issue_source_missing",
                            f"issues.csv, строка {i}: у замечания нет source_page или source_fragment",
                            "issues", i, "source_page" if not _cell(r, "source_page") else "source_fragment"))
    return probs


# --- check_submission.py -------------------------------------------------------------------------

_SUMMARY_RE = re.compile(r"таблиц:\s*\d+.*?ошибок:\s*(\d+)")
_QUOTES_RE = re.compile(r"проверено цитат:\s*(\d+),\s*не найдено:\s*(\d+)")
_TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\)")


def _decode(b: bytes | str | None) -> str:
    if b is None:
        return ""
    if isinstance(b, str):
        return b
    return b.decode("utf-8", errors="replace")


def parse_check_output(text: str, exit_code: int | None) -> CheckReport:
    """Разбор вывода check_submission.py: строки «ОШИБКА:» и «внимание:», итоговая строка, трассировка."""
    errors: list[str] = []
    warnings: list[str] = []
    summary = False
    qc = qb = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("ОШИБКА:"):
            errors.append(s[len("ОШИБКА:"):].strip())
        elif s.startswith("внимание:"):
            warnings.append(s[len("внимание:"):].strip())
        if _SUMMARY_RE.search(s):
            summary = True
            m = _QUOTES_RE.search(s)
            qc, qb = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    crashed = bool(_TRACEBACK_RE.search(text)) or exit_code not in (0, 1) or (exit_code == 1 and not errors)
    ok = (not crashed) and exit_code == 0 and not errors
    rep = CheckReport(ran=True, ok=ok, crashed=crashed, errors=[] if crashed and not summary else errors,
                      warnings=warnings, quotes_checked=qc, quotes_bad=qb, raw_output=text, exit_code=exit_code)
    return rep


def run_check_script(script: Path, csv_dir: Path, pdf_dir: Path, timeout_s: int = 900) -> CheckReport:
    """subprocess(sys.executable), PYTHONUTF8=1, PYTHONIOENCODING=utf-8; crashed=True при трассировке,
    таймауте, отсутствии скрипта или неожиданном коде выхода. Исключений не бросает (REQ-004, REQ-006)."""
    script = Path(script)
    if not script.is_file():
        return CheckReport(ran=False, ok=False, crashed=True, raw_output=f"скрипт проверки не найден: {script}")
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, str(script), str(csv_dir), str(pdf_dir)]
    try:
        proc = subprocess.run(cmd, capture_output=True, env=env, timeout=timeout_s, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as e:
        out = _decode(e.stdout) + _decode(e.stderr)
        return CheckReport(ran=True, ok=False, crashed=True,
                           raw_output=f"{out}\nпроверка не уложилась в {timeout_s} с".lstrip("\n"))
    except OSError as e:
        return CheckReport(ran=False, ok=False, crashed=True, raw_output=f"не удалось запустить проверку: {e}")
    out, err = _decode(proc.stdout), _decode(proc.stderr)
    text = out if not err else (out + ("\n" if out and not out.endswith("\n") else "") + err)
    return parse_check_output(text, proc.returncode)
