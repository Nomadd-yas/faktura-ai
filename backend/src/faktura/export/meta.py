"""Служебные файлы _meta/, манифест, ZIP (план 5.4; spec/13 §13.6; REQ-002, REQ-009).

В корне выходной папки лежат только 7 CSV сдачи; всё служебное - в `_meta/` (скрипт проверки её не читает):
`manifest.json`, `passport.json`, `passport.md`, `files.csv`, `facts_ui.json`, `errors.log`, `check_report.txt`.
"""
from __future__ import annotations

import csv
import io
import json
import os
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from pydantic import BaseModel

from .. import __version__
from ..domain.entities import CheckReport, FileReport

__all__ = ["write_meta", "build_manifest", "zip_dir", "check_summary", "check_status", "to_jsonable", "META_DIR",
           "META_FILES"]

META_DIR = "_meta"
META_FILES = ("manifest.json", "passport.json", "passport.md", "files.csv", "facts_ui.json", "errors.log",
              "check_report.txt")


def _iso(dt: datetime | date) -> str:
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return dt.isoformat()


def to_jsonable(obj: Any) -> Any:
    """Приводит значение к типам JSON: модели pydantic, Decimal, даты, пути, перечисления, множества."""
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        return obj if obj == obj and obj not in (float("inf"), float("-inf")) else None
    if isinstance(obj, Decimal):
        if not obj.is_finite():
            return None
        return int(obj) if obj == obj.to_integral_value() and abs(obj) < 10 ** 15 else float(obj)
    if isinstance(obj, Enum):
        return to_jsonable(obj.value)
    if isinstance(obj, (datetime, date)):
        return _iso(obj)
    if isinstance(obj, Path):
        return obj.as_posix()
    if isinstance(obj, BaseModel):
        return to_jsonable(obj.model_dump(mode="python"))
    if isinstance(obj, Mapping):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        items = [to_jsonable(v) for v in obj]
        return sorted(items, key=str) if isinstance(obj, (set, frozenset)) else items
    return str(obj)


def _dump_json(obj: Any) -> bytes:
    return (json.dumps(to_jsonable(obj), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def check_status(report: CheckReport | None) -> str | None:
    """Итог проверки для `X-Check-Status`, `Export.check.status`, `Run.output.check_status` (api_contract §3.4,
    §3.11): ok | warnings | failed; None - проверка не запускалась. Падение скрипта = failed (REQ-004)."""
    if report is None or (not report.ran and not report.crashed):
        return None
    if report.crashed or report.errors or report.ok is False:
        return "failed"
    return "warnings" if report.warnings else "ok"


def check_summary(report: CheckReport | None) -> dict[str, Any] | None:
    """Блок `check` манифеста (spec/13 §13.6) и `Export.check` (без поля `script` - его знает вызывающий)."""
    if report is None:
        return None
    return {"status": check_status(report), "errors": len(report.errors), "warnings": len(report.warnings),
            "quotes_checked": report.quotes_checked, "quotes_not_found": report.quotes_bad,
            "crashed": report.crashed, "exit_code": report.exit_code}


def build_manifest(*, version: str | None = None, started_at: datetime | str | None = None,
                   finished_at: datetime | str | None = None, domain: str | None = None, split: str | None = None,
                   profile: Mapping[str, Any] | Any | None = None, extract_model: str | None = None,
                   parse_model: str | None = None, norm_version: str | None = None,
                   rules_version: str | None = None, dictionary_version: str | None = None,
                   model_version: str | None = None, from_cache: bool | None = None,
                   versions: Mapping[str, Any] | None = None, input: Mapping[str, Any] | None = None,
                   result: Mapping[str, Any] | None = None, check: CheckReport | Mapping[str, Any] | None = None,
                   **extra: Any) -> dict:
    """Поля spec/13 §13.6 + model_version, from_cache, versions. Непереданные поля не пишутся (кроме tool и
    version); `extract_model` по умолчанию = `model_version`; `check` можно передать CheckReport. Результат -
    только типы JSON. Профиль можно передать моделью ExtractionProfile -> {id, version}."""
    m: dict[str, Any] = {"tool": "faktura", "version": version or __version__}
    if profile is not None and not isinstance(profile, Mapping):
        profile = {"id": getattr(profile, "profile_id", None), "version": getattr(profile, "version", None)}
    if isinstance(check, CheckReport):
        check = check_summary(check)
    fields = dict(started_at=started_at, finished_at=finished_at, domain=domain, split=split, profile=profile,
                  extract_model=extract_model or model_version, parse_model=parse_model, norm_version=norm_version,
                  rules_version=rules_version, dictionary_version=dictionary_version, model_version=model_version,
                  from_cache=from_cache, versions=versions, input=input, result=result, check=check)
    for k, v in {**fields, **extra}.items():
        if v is not None:
            m[k] = to_jsonable(v)
    return m


def _files_csv(rows: list[FileReport]) -> bytes:
    cols = list(FileReport.model_fields)
    buf = io.StringIO(newline="")
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(cols)
    for r in rows:
        d = r.model_dump() if isinstance(r, BaseModel) else dict(r)
        out = []
        for c in cols:
            v = d.get(c)
            out.append("" if v is None else ("true" if v is True else "false" if v is False else str(v)))
        w.writerow(out)
    return buf.getvalue().encode("utf-8-sig")


def _write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_meta(out_dir: Path, *, manifest: dict, files_rows: list[FileReport], facts_ui: Any, errors: list[str],
               passport: dict, passport_md: str, check_report: CheckReport | None) -> None:
    """_meta/: manifest.json, passport.json, passport.md, files.csv, facts_ui.json, errors.log, check_report.txt.
    CSV сдачи в корне не трогаются. Без `check_report` файл check_report.txt не пишется (старый удаляется)."""
    meta = Path(out_dir) / META_DIR
    meta.mkdir(parents=True, exist_ok=True)
    _write(meta / "manifest.json", _dump_json(manifest))
    _write(meta / "passport.json", _dump_json(passport))
    _write(meta / "passport.md", passport_md.encode("utf-8"))
    _write(meta / "files.csv", _files_csv(list(files_rows)))
    _write(meta / "facts_ui.json", _dump_json(facts_ui if facts_ui is not None else {}))
    lines = [str(e).replace("\r\n", " ").replace("\n", " ") for e in errors]
    _write(meta / "errors.log", ("\n".join(lines) + "\n").encode("utf-8") if lines else b"")
    cr = meta / "check_report.txt"
    if check_report is not None:
        _write(cr, check_report.raw_output.encode("utf-8"))
    elif cr.exists():
        cr.unlink()


def zip_dir(dir: Path, *, include: Callable[[Path], bool] | None = None) -> bytes:
    """ZIP папки: имена относительные, через '/', в отсортированном порядке. `include(rel_path)` получает путь
    относительно `dir` (например, `Path("_meta/manifest.json")`). Нет папки - FileNotFoundError."""
    root = Path(dir)
    if not root.exists():
        raise FileNotFoundError(f"нет папки {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"{root} - не папка")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(root.rglob("*"), key=lambda x: x.relative_to(root).as_posix()):
            if not p.is_file():
                continue
            rel = p.relative_to(root)
            if p.name.endswith(".tmp") and p.name.startswith("."):
                continue
            if include is not None and not include(rel):
                continue
            z.write(p, rel.as_posix())
    return buf.getvalue()
