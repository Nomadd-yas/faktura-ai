"""Сдача: выборка, санитайзер, CSV, _meta, валидаторы, выгрузки для людей (план 5.4).

Конвейер сдачи (его собирает `pipeline.build_output`):
    select_for_submission -> sanitize -> number_issues -> write_submission -> strict_validate
    -> run_check_script -> write_meta (+ zip_dir для API).
"""
from __future__ import annotations

from .csv_submission import SUBMISSION_FILES, table_bytes, write_submission, write_tables
from .human import (FLAT_FORMATS, PREVIEW_LIMIT, RELATIONAL_FORMATS, content_type, export_flat, export_relational,
                    file_extension, flat_head, preview)
from .meta import META_DIR, META_FILES, build_manifest, check_status, check_summary, to_jsonable, write_meta, zip_dir
from .sanitize import SanitizeResult, number_issues, parse_number_text, sanitize
from .rows import paper_submission_tables
from .select import SelectResult, manual_fact_problem, materialize_manual_fact, select_for_submission
from .validate import parse_check_output, parse_rag_check_output, run_check_script, run_rag_check, strict_validate

__all__ = [
    "paper_submission_tables",
    "write_submission", "write_tables", "table_bytes", "SUBMISSION_FILES",
    "export_flat", "export_relational", "preview", "flat_head", "content_type", "file_extension",
    "FLAT_FORMATS", "RELATIONAL_FORMATS", "PREVIEW_LIMIT",
    "build_manifest", "write_meta", "zip_dir", "check_status", "check_summary", "to_jsonable", "META_DIR",
    "META_FILES",
    "SanitizeResult", "number_issues", "sanitize", "parse_number_text",
    "SelectResult", "materialize_manual_fact", "manual_fact_problem", "select_for_submission",
    "run_check_script", "strict_validate", "parse_check_output", "run_rag_check", "parse_rag_check_output",
]
