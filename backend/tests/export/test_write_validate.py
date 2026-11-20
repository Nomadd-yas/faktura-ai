"""write_submission, strict_validate, run_check_script (REQ-012..REQ-021, К-3)."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

from faktura.domain.entities import CheckReport, ValidationProblem
from faktura.domain.io import read_tables
from faktura.domain.tables import TABLES
from faktura.export import run_check_script, strict_validate, write_submission


# --- write_submission --------------------------------------------------------------------------

def test_writes_exactly_seven_files(make_bundle, tmp_path):
    paths = write_submission([make_bundle()], tmp_path / "out")
    assert [p.name for p in paths] == [f"{t}.csv" for t in TABLES]
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == sorted(f"{t}.csv" for t in TABLES)


def test_creates_out_dir_and_accepts_existing(make_bundle, tmp_path):
    out = tmp_path / "a" / "b"
    write_submission([make_bundle()], out)
    write_submission([make_bundle()], out)  # повторная запись перезаписывает
    assert (out / "results.csv").is_file()


@pytest.mark.parametrize("table", list(TABLES))
def test_bom_header_and_crlf(make_bundle, tmp_path, table):
    write_submission([make_bundle()], tmp_path)
    raw = (tmp_path / f"{table}.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    first = raw[3:].split(b"\r\n", 1)[0].decode("utf-8")
    assert first.split(",") == list(TABLES[table])
    assert raw.endswith(b"\r\n")
    assert b"\n" not in raw.replace(b"\r\n", b"")  # нет одиночных LF


def test_empty_bundles_give_header_only_files(tmp_path):
    paths = write_submission([], tmp_path)
    assert len(paths) == 7
    for t in TABLES:
        assert read_tables(tmp_path)[t] == []
        assert (tmp_path / f"{t}.csv").read_bytes().count(b"\r\n") == 1


def test_quote_minimal(make_bundle, tmp_path):
    write_submission([make_bundle()], tmp_path)
    text = (tmp_path / "publications.csv").read_text(encoding="utf-8-sig")
    assert '"Title, with comma"' in text
    assert "10.1/x" in text and '"10.1/x"' not in text
    assert "Ivanov A.; Petrov B." in text


def test_cells_roundtrip_through_domain_io(make_bundle, tmp_path):
    write_submission([make_bundle()], tmp_path)
    t = read_tables(tmp_path)
    assert t["results"][0]["metric_value"] == "20.1"
    assert t["results"][0]["source_page"] == "3"
    assert t["measurements"][0]["mask_used"] == "true"
    assert t["measurements"][0]["device_id"] == "DV1"
    assert t["issues"][0]["record_ids"] == "R001"


def test_embedded_quotes_newlines_and_unicode(make_bundle, tmp_path):
    b = make_bundle()
    b.results[0].source_fragment = 'a "quoted"\nline, with ﬁ ligature и кириллица'
    write_submission([b], tmp_path)
    rows = read_tables(tmp_path)["results"]
    assert rows[0]["source_fragment"] == 'a "quoted"\nline, with ﬁ ligature и кириллица'
    with open(tmp_path / "results.csv", encoding="utf-8-sig", newline="") as f:  # стандартный csv читает так же
        assert len(list(csv.DictReader(f))) == 2


def test_rows_sorted_by_publication_then_order(make_bundle, tmp_path):
    write_submission([make_bundle("P002"), make_bundle("P001")], tmp_path)
    ids = [r["publication_id"] for r in read_tables(tmp_path)["publications"]]
    assert ids == ["P001", "P002"]
    assert [r["publication_id"] for r in read_tables(tmp_path)["results"]] == ["P001", "P001", "P002", "P002"]


def test_no_extra_columns_from_meta(make_bundle, tmp_path):
    b = make_bundle()
    b.results[0].meta.extra = {"unmapped": "x"}
    write_submission([b], tmp_path)
    header = (tmp_path / "results.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    assert header.split(",") == list(TABLES["results"])


def test_failed_stub_writes_publication_only(tmp_path):
    from faktura.domain.records import PaperBundle

    write_submission([PaperBundle.failed_stub("P009")], tmp_path)
    t = read_tables(tmp_path)
    assert [r["publication_id"] for r in t["publications"]] == ["P009"]
    assert all(t[x] == [] for x in TABLES if x != "publications")


def test_written_mini_gold_has_no_strict_errors(mini_bundles, tmp_path):
    write_submission(mini_bundles, tmp_path)
    assert [p for p in strict_validate(read_tables(tmp_path)) if p.level == "error"] == []


# --- strict_validate ---------------------------------------------------------------------------

def _tables(make_bundle, tables_of):
    return tables_of([make_bundle()])


def _by_level(problems, level):
    return [p for p in problems if p.level == level]


def test_clean_tables_no_problems(make_bundle, tables_of):
    probs = strict_validate(_tables(make_bundle, tables_of))
    assert _by_level(probs, "error") == []
    assert all(isinstance(p, ValidationProblem) for p in probs)


def test_mini_gold_no_errors(mini_gold_dir):
    assert _by_level(strict_validate(read_tables(mini_gold_dir)), "error") == []


def test_vocab_violation_is_error(make_bundle, tables_of):
    t = _tables(make_bundle, tables_of)
    t["materials"][0]["material_class"] = "organic"
    errs = _by_level(strict_validate(t), "error")
    assert any(e.table == "materials" and e.column == "material_class" for e in errs)


@pytest.mark.parametrize("table,col,bad", [("results", "metric_value", "1,5"), ("results", "normalized_value", "~1"),
                                           ("results", "source_page", "p.3"), ("layers", "layer_order", "x"),
                                           ("devices", "active_area", "0.06 cm2"),
                                           ("materials", "bandgap_value", "1.5-1.6")])
def test_non_number_is_error(make_bundle, tables_of, table, col, bad):
    t = _tables(make_bundle, tables_of)
    t[table][0][col] = bad
    errs = _by_level(strict_validate(t), "error")
    assert any(e.table == table and e.column == col and e.row is not None for e in errs)


def test_missing_publication_is_error(make_bundle, tables_of):
    t = _tables(make_bundle, tables_of)
    t["results"][0]["publication_id"] = "NOPE"
    assert any(e.table == "results" for e in _by_level(strict_validate(t), "error"))


@pytest.mark.parametrize("table,col,val", [("devices", "absorber_material_id", "M9"), ("layers", "device_id", "D9"),
                                           ("results", "device_id", "D9"), ("results", "measurement_id", "MS9"),
                                           ("results", "material_id", "M9"), ("results", "duplicate_of", "R9"),
                                           ("issues", "record_ids", "R001;R9"), ("measurements", "device_id", "DV1;D9")])
def test_dangling_reference_is_error(make_bundle, tables_of, table, col, val):
    t = _tables(make_bundle, tables_of)
    t[table][0][col] = val
    errs = _by_level(strict_validate(t), "error")
    assert any(e.table == table and e.column == col for e in errs)


def test_duplicate_result_id_is_error(make_bundle, tables_of):
    t = _tables(make_bundle, tables_of)
    t["results"][1]["result_id"] = "R001"
    assert any("result_id" in (e.column or "") or "R001" in e.message for e in _by_level(strict_validate(t), "error"))


def test_same_result_id_in_different_papers_ok(make_bundle, tables_of):
    t = tables_of([make_bundle("P001"), make_bundle("P002")])
    assert _by_level(strict_validate(t), "error") == []


def test_missing_table_columns_is_error(make_bundle, tables_of):
    t = _tables(make_bundle, tables_of)
    for r in t["results"]:
        del r["note"]
    assert _by_level(strict_validate(t), "error")


def test_empty_tables_dict_is_handled():
    probs = strict_validate({t: [] for t in TABLES})
    assert _by_level(probs, "error") == []


def test_issue_without_page_or_fragment_is_warning_not_error(make_bundle, tables_of):
    t = _tables(make_bundle, tables_of)
    t["issues"][0]["source_page"] = ""
    t["issues"][0]["source_fragment"] = ""
    probs = strict_validate(t)
    warns = [p for p in _by_level(probs, "warning") if p.code == "issue_source_missing"]
    assert len(warns) == 1 and warns[0].table == "issues"
    assert _by_level(probs, "error") == []


def test_issue_with_only_page_missing_is_warning(make_bundle, tables_of):
    t = _tables(make_bundle, tables_of)
    t["issues"][0]["source_page"] = ""
    assert any(p.code == "issue_source_missing" for p in strict_validate(t))


def test_unit_without_value_reported(make_bundle, tables_of):
    t = _tables(make_bundle, tables_of)
    t["layers"][0]["layer_thickness"] = ""
    probs = strict_validate(t)
    assert any(p.table == "layers" and p.column == "thickness_unit" for p in probs)


def test_in_scope_false_with_rows_in_other_tables_reported(make_bundle, tables_of):
    t = _tables(make_bundle, tables_of)
    t["publications"][0]["in_scope"] = "false"
    assert any(p.table in ("publications", "results") for p in strict_validate(t))


def test_row_numbers_are_csv_lines(make_bundle, tables_of):
    t = _tables(make_bundle, tables_of)
    t["results"][1]["metric_value"] = "x"
    e = [p for p in strict_validate(t) if p.level == "error" and p.column == "metric_value"][0]
    assert e.row == 3  # как в check_submission.py: строка 1 - заголовок


# --- run_check_script --------------------------------------------------------------------------

FAKE_OK = '''
import sys
print("таблиц: 7, результатов: 2, ошибок: 0, предупреждений: 1, проверено цитат: 10, не найдено: 2")
print("внимание: results.csv, строка 2: цитата не найдена на с. 3 файла P001.pdf: «abc»")
sys.exit(0)
'''
FAKE_ERR = '''
import sys
print("ОШИБКА: materials.csv, строка 2: material_class = «x» — нет в списке допустимых")
print("ОШИБКА: results.csv, строка 3: metric_value = «y» — не число")
print("внимание: publications.csv: P001 исключена без exclusion_reason")
print("\\nтаблиц: 7, результатов: 2, ошибок: 2, предупреждений: 1")
sys.exit(1)
'''
FAKE_CRASH = '''
raise RuntimeError("boom")
'''


def test_run_ok_parses_counts_and_warnings(fake_script, tmp_path):
    rep = run_check_script(fake_script(FAKE_OK), tmp_path, tmp_path)
    assert isinstance(rep, CheckReport)
    assert rep.ran is True and rep.ok is True and rep.crashed is False
    assert rep.exit_code == 0 and rep.errors == []
    assert len(rep.warnings) == 1 and "цитата не найдена" in rep.warnings[0]
    assert (rep.quotes_checked, rep.quotes_bad) == (10, 2)
    assert "ошибок: 0" in rep.raw_output


def test_run_errors_parsed(fake_script, tmp_path):
    rep = run_check_script(fake_script(FAKE_ERR), tmp_path, tmp_path)
    assert rep.ok is False and rep.crashed is False and rep.exit_code == 1
    assert len(rep.errors) == 2 and "material_class" in rep.errors[0]
    assert len(rep.warnings) == 1


def test_run_crash_traceback_sets_crashed(fake_script, tmp_path):
    rep = run_check_script(fake_script(FAKE_CRASH), tmp_path, tmp_path)
    assert rep.crashed is True and rep.ok is not True
    assert "RuntimeError" in rep.raw_output  # трассировка сохранена
    assert rep.errors == []  # трассировка - не ошибка данных


def test_run_utf8_env_is_forced(fake_script, tmp_path):
    body = 'import os, sys\nprint(os.environ.get("PYTHONUTF8"), os.environ.get("PYTHONIOENCODING"))\nprint("ОШИБКА: кириллица")\nsys.exit(1)\n'
    rep = run_check_script(fake_script(body), tmp_path, tmp_path)
    assert rep.raw_output.startswith("1 utf-8")
    assert rep.errors == ["кириллица"] or "кириллица" in rep.errors[0]


def test_run_passes_csv_and_pdf_dirs_as_arguments(fake_script, tmp_path):
    body = 'import sys\nprint(sys.argv[1]); print(sys.argv[2])\n'
    csv_dir, pdf_dir = tmp_path / "c", tmp_path / "p"
    csv_dir.mkdir(), pdf_dir.mkdir()
    rep = run_check_script(fake_script(body), csv_dir, pdf_dir)
    assert str(csv_dir) in rep.raw_output and str(pdf_dir) in rep.raw_output


def test_run_timeout_is_crashed(fake_script, tmp_path):
    rep = run_check_script(fake_script("import time\ntime.sleep(30)\n"), tmp_path, tmp_path, timeout_s=1)
    assert rep.crashed is True and rep.ok is not True


def test_run_missing_script_is_crashed_not_exception(tmp_path):
    rep = run_check_script(tmp_path / "nope.py", tmp_path, tmp_path)
    assert rep.crashed is True


def test_run_empty_output_without_errors_is_ok_or_crashed_never_silent(fake_script, tmp_path):
    rep = run_check_script(fake_script("pass\n"), tmp_path, tmp_path)
    assert rep.ran is True and (rep.ok is True or rep.crashed is True)


def test_run_real_script_on_mini_gold(check_script, mini_gold_dir, tmp_path):
    rep = run_check_script(check_script, mini_gold_dir, tmp_path)  # пустая папка PDF: цитаты не проверяются
    assert rep.crashed is False and rep.ok is True and rep.errors == []
    assert "результатов: 6" in rep.raw_output or "результатов" in rep.raw_output


def test_run_real_script_detects_error(check_script, make_bundle, tmp_path):
    b = make_bundle()
    b.materials[0].material_class = "organic"
    write_submission([b], tmp_path / "o")
    rep = run_check_script(check_script, tmp_path / "o", tmp_path)
    assert rep.ok is False and rep.crashed is False and any("material_class" in e for e in rep.errors)


def test_run_real_script_missing_file_error(check_script, make_bundle, tmp_path):
    write_submission([make_bundle()], tmp_path / "o")
    (tmp_path / "o" / "issues.csv").unlink()
    rep = run_check_script(check_script, tmp_path / "o", tmp_path)
    assert rep.ok is False and any("issues.csv" in e for e in rep.errors)


def test_run_uses_current_interpreter(fake_script, tmp_path):
    rep = run_check_script(fake_script("import sys\nprint(sys.executable)\n"), tmp_path, tmp_path)
    assert Path(rep.raw_output.strip().splitlines()[0]).resolve() == Path(sys.executable).resolve()
