"""faktura extract: коды выхода 0/1/2/3, 7 CSV, кеш, дубли, недоступный ML (план 5.6, P0). Без данных кейса."""
from __future__ import annotations

import csv
import json
import re
import shutil
import zipfile
from pathlib import Path

import pytest

from faktura.domain.tables import TABLES

TABLE_FILES = sorted(f"{t}.csv" for t in TABLES)


def rows(out: Path, table: str) -> list[dict]:
    with open(out / f"{table}.csv", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def files_csv(out: Path) -> dict[str, dict]:
    with open(out / "_meta" / "files.csv", encoding="utf-8-sig", newline="") as f:
        return {r["file"]: r for r in csv.DictReader(f)}


@pytest.fixture
def base(clock, run_cli, pdf_dir, mini_gold_dir, tmp_path):
    """extract на моке mini_gold; base(*extra, out=...) -> CliResult."""
    def _run(*extra, src=None, out=None, mock=True, env=None):
        out = out or tmp_path / "out"
        args = ["extract", "--domain", "solar", "--in", src or pdf_dir, "--out", out, "--retry-delays", "1,1,1"]
        if mock:
            args += ["--extractor", "mock", "--gold-dir", mini_gold_dir]
        return run_cli(*args, *extra, env=env)
    _run.out = tmp_path / "out"
    return _run


# --- успешный прогон ---------------------------------------------------------------------------------------------

def test_ok_exit_0_seven_csv_and_meta(base):
    r = base()
    assert r.exit_code == 0, r
    assert sorted(p.name for p in base.out.glob("*.csv")) == TABLE_FILES
    assert (base.out / "_meta").is_dir()
    assert [x["publication_id"] for x in rows(base.out, "publications")] == ["D001", "D003"]


def test_progress_lines_in_stderr(base, progress_re):
    r = base()
    found = progress_re.findall(r.stderr)
    assert len(found) == 2 and set(found) == {"done"}
    assert re.search(r"\[\s*2/\s*2\]\s+done\s+D00[13]\.pdf\s+facts=\d+\s+issues=\d+", r.stderr)


def test_zip_input(base, pdf_dir, tmp_path):
    z = tmp_path / "in.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for p in pdf_dir.glob("*.pdf"):
            zf.write(p, p.name)
    r = base(src=z)
    assert r.exit_code == 0, r
    assert [x["publication_id"] for x in rows(base.out, "publications")] == ["D001", "D003"]


def test_default_extractor_is_mock_without_url(clock, run_cli, pdf_dir, tmp_path, mini_gold_dir):
    r = run_cli("extract", "--in", pdf_dir, "--out", tmp_path / "o", "--gold-dir", mini_gold_dir)
    assert r.exit_code == 0, r
    assert len(rows(tmp_path / "o", "publications")) == 2


def test_extra_options_accepted(base):
    r = base("--no-backend-rules", "--mock-issues", "ml_only", "--ocr", "off", "--concurrency", "2",
             "--profile", "tz-solar-v1", "--model", "latest", "--format", "csv")
    assert r.exit_code == 0, r


def test_parse_url_warns_and_is_ignored(base):
    r = base("--parse-url", "http://parser.invalid")
    assert r.exit_code == 0 and re.search(r"(?i)parse|игнор", r.stderr)


def test_db_option_writes_sqlite_file(base, tmp_path):
    db = tmp_path / "work.db"
    r = base("--db", db)
    assert r.exit_code == 0 and db.is_file() and db.stat().st_size > 0


def test_no_backend_rules_fewer_or_equal_issues(base, tmp_path, mini_gold_dir):
    base()
    with_rules = len(rows(base.out, "issues"))
    r = base("--no-backend-rules", out=tmp_path / "out2")
    assert r.exit_code == 0
    gold_n = len(rows(mini_gold_dir, "issues"))
    assert len(rows(tmp_path / "out2", "issues")) == gold_n <= with_rules


# --- проверка сдачи и код 3 -------------------------------------------------------------------------------------------

def test_check_ok(base, fake_validator):
    assert base("--check", fake_validator("ok")).exit_code == 0


def test_check_errors_exit_3_csv_still_written(base, fake_validator):
    r = base("--check", fake_validator("errors"))
    assert r.exit_code == 3, r
    assert sorted(p.name for p in base.out.glob("*.csv")) == TABLE_FILES
    assert "metric_value" in (base.out / "_meta" / "check_report.txt").read_text(encoding="utf-8")


def test_check_crash_exit_3(base, fake_validator):
    r = base("--check", fake_validator("crash"))
    assert r.exit_code == 3 and "boom" in (r.output + r.stderr)


def test_check_missing_script_exit_3_or_1(base, tmp_path):
    r = base("--check", tmp_path / "nope.py")
    assert r.exit_code in (1, 3)


def test_check_errors_beats_partial_failure(base, fake_validator, pdf_dir):
    (pdf_dir / "BROKEN.pdf").write_bytes(b"%PDF-1.4\ngarbage")
    assert base("--check", fake_validator("errors")).exit_code == 3


# --- неверные аргументы -> 1 -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("extra", [
    ["--extractor", "nope"], ["--domain", "rag"], ["--concurrency", "0"], ["--retry-delays", "a,b"],
    ["--retry-delays", "-1"], ["--profile", "./no_such_profile.json"], ["--format", "xml"],
    ["--ocr", "maybe"], ["--mock-issues", "some"],
])
def test_bad_option_exit_1(base, extra):
    r = base(*extra)
    assert r.exit_code == 1, r


def test_missing_required_in_exit_1(clock, run_cli, tmp_path):
    assert run_cli("extract", "--out", tmp_path / "o").exit_code == 1


def test_missing_required_out_exit_1(clock, run_cli, pdf_dir):
    assert run_cli("extract", "--in", pdf_dir).exit_code == 1


def test_input_path_not_exists_exit_1(clock, run_cli, tmp_path):
    assert run_cli("extract", "--in", tmp_path / "nope", "--out", tmp_path / "o").exit_code == 1


def test_input_without_pdfs_exit_1(clock, run_cli, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert run_cli("extract", "--in", empty, "--out", tmp_path / "o").exit_code == 1


def test_http_without_url_exit_1(base):
    assert base("--extractor", "http", mock=False).exit_code == 1


def test_raw_without_raw_dir_exit_1(base):
    assert base("--extractor", "raw", mock=False).exit_code == 1


def test_cache_dir_inside_out_exit_1(base, tmp_path):
    out = tmp_path / "out"
    r = base("--cache-dir", out / "cache", out=out)
    assert r.exit_code == 1 and re.search(r"(?i)cache|кеш", r.output + r.stderr)
    assert not list(out.glob("*.csv")), "при ошибке запуска ничего не пишем"


def test_cache_dir_equal_to_out_exit_1(base, tmp_path):
    out = tmp_path / "out"
    assert base("--cache-dir", out, out=out).exit_code == 1


def test_cache_dir_outside_out_ok(base, tmp_path):
    assert base("--cache-dir", tmp_path / "elsewhere").exit_code == 0


def test_api_key_option_exit_1(base):
    r = base("--api-key", "k")
    assert r.exit_code == 1


# --- файлы, не принятые к разбору: строка publications только с id, код 2 (REQ-024) ----------------------------------

def test_broken_pdf_stub_row_exit_2(base, pdf_dir):
    (pdf_dir / "BROKEN.pdf").write_bytes(b"%PDF-1.4\nnot a pdf")
    r = base()
    assert r.exit_code == 2, r
    pubs = {x["publication_id"]: x for x in rows(base.out, "publications")}
    assert set(pubs) == {"D001", "D003", "BROKEN"}
    assert all(v == "" for k, v in pubs["BROKEN"].items() if k != "publication_id")
    assert files_csv(base.out)["BROKEN.pdf"]["status"] == "failed"
    assert "BROKEN" in (base.out / "_meta" / "errors.log").read_text(encoding="utf-8")


def test_encrypted_pdf_exit_2(base, pdf_dir):
    import fitz
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "x")
    doc.save(pdf_dir / "ENC.pdf", encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="p", owner_pw="o")
    r = base()
    assert r.exit_code == 2
    assert files_csv(base.out)["ENC.pdf"]["error_code"] == "pdf_encrypted"
    assert "ENC" in [x["publication_id"] for x in rows(base.out, "publications")]


def test_too_many_pages_exit_2(base, pdf_dir, make_pdf):
    make_pdf(["a", "b", "c"], name="BIG.pdf", directory=pdf_dir)
    r = base(env={"FAKTURA_MAX_PAGES": "2"})
    assert r.exit_code == 2
    assert files_csv(base.out)["BIG.pdf"]["error_code"] == "too_many_pages"


def test_oversize_pdf_exit_2(base, pdf_dir, make_pdf):
    make_pdf(["a"], name="HUGE.pdf", directory=pdf_dir)
    r = base(env={"FAKTURA_MAX_FILE_MB": "0"})
    assert r.exit_code == 2
    assert files_csv(base.out)["HUGE.pdf"]["error_code"] == "file_too_large"


def test_duplicate_name_in_subfolders_exit_2(base, pdf_dir, make_pdf):
    make_pdf(["other"], name="D001.pdf", directory=pdf_dir / "sub")
    r = base()
    assert r.exit_code == 2
    assert len([x for x in rows(base.out, "publications") if x["publication_id"] == "D001"]) == 1
    assert any(v["status"] == "rejected" for v in files_csv(base.out).values())


def test_failed_file_does_not_hide_good_ones(base, pdf_dir):
    (pdf_dir / "BROKEN.pdf").write_bytes(b"junk")
    base()
    assert len(rows(base.out, "results")) == 47 + 21


# --- ML по http: кеш, дубли, пауза (шпионский транспорт httpx) -----------------------------------------------------------

@pytest.fixture
def http_base(clock, run_cli, http_ml, tmp_path, make_pdf):
    """Четыре разных PDF P1..P4 (разное содержимое) и вызов extract --extractor http."""
    src = tmp_path / "hsrc"
    for i in range(1, 5):
        make_pdf([f"unique text {i}"], name=f"P{i}.pdf", directory=src)

    def _run(*extra, out=None, delays="1,1,1"):
        out = out or tmp_path / "hout"
        return run_cli("extract", "--in", src, "--out", out, "--extractor", "http", "--extract-url", "http://ml.test",
                       "--retry-delays", delays, *extra)

    _run.src, _run.out, _run.spy = src, tmp_path / "hout", http_ml
    return _run


def test_http_extract_ok(http_base):
    r = http_base()
    assert r.exit_code == 0, r
    assert sorted(http_base.spy.extract_calls) == ["P1", "P2", "P3", "P4"]
    assert len(rows(http_base.out, "publications")) == 4


def test_extract_url_alone_selects_http(clock, run_cli, http_ml, pdf_dir, tmp_path):
    r = run_cli("extract", "--in", pdf_dir, "--out", tmp_path / "o", "--extract-url", "http://ml.test",
                "--retry-delays", "1")
    assert r.exit_code == 0, r
    assert sorted(http_ml.extract_calls) == ["D001", "D003"]


def test_second_run_served_from_cache(http_base, tmp_path):
    http_base()
    n = len(http_base.spy.extract_calls)
    r = http_base(out=tmp_path / "hout2")
    assert r.exit_code == 0 and len(http_base.spy.extract_calls) == n
    assert rows(tmp_path / "hout2", "publications") == rows(http_base.out, "publications")


def test_no_cache_flag_calls_ml_again(http_base, tmp_path):
    http_base()
    n = len(http_base.spy.extract_calls)
    http_base("--no-cache", out=tmp_path / "hout2")
    assert len(http_base.spy.extract_calls) == 2 * n


def test_explicit_cache_dir_is_used(http_base, tmp_path):
    cache = tmp_path / "mycache"
    http_base("--cache-dir", cache)
    assert cache.is_dir() and any(cache.rglob("*.json"))


def test_resume_after_failure_calls_ml_only_for_failed(http_base, tmp_path):
    spy = http_base.spy
    spy.fail_on = {"P3"}  # падает 3-й файл
    r1 = http_base()
    assert r1.exit_code == 2
    assert files_csv(http_base.out)["P3.pdf"]["status"] == "failed"
    spy.fail_on = set()
    before = len(spy.extract_calls)
    r2 = http_base(out=tmp_path / "hout2")
    assert r2.exit_code == 0, r2
    assert spy.extract_calls[before:] == ["P3"]
    assert len(rows(tmp_path / "hout2", "publications")) == 4


def test_fail_fast_stops_early(http_base):
    http_base.spy.fail_on = {"P1"}
    r = http_base("--fail-fast", "--concurrency", "1")
    assert r.exit_code == 2
    assert "P4" not in http_base.spy.extract_calls


def test_same_content_two_files_ml_called_once(clock, run_cli, http_ml, tmp_path, make_pdf):
    src = tmp_path / "same"
    first = make_pdf(["identical"], name="A1.pdf", directory=src)
    shutil.copy(first, src / "A2.pdf")
    r = run_cli("extract", "--in", src, "--out", tmp_path / "o", "--extractor", "http", "--extract-url", "http://ml.test",
                "--retry-delays", "1")
    assert r.exit_code == 0, r
    assert len(http_ml.extract_calls) == 1
    pubs = rows(tmp_path / "o", "publications")
    assert [p["publication_id"] for p in pubs] == ["A1", "A2"]
    assert pubs[0]["title"] == pubs[1]["title"] != ""
    st = files_csv(tmp_path / "o")
    assert st["A2.pdf"]["dup_kind"] == "file" and st["A2.pdf"]["dup_of"]


def test_same_doi_marked_doi_dup_in_files_csv(http_base):
    # ответ шпиона у всех статей с одним DOI: вторая и далее - смысловые дубли
    r = http_base()
    st = files_csv(http_base.out)
    assert r.exit_code == 0
    assert st["P1.pdf"]["dup_kind"] == ""
    assert {st[f"P{i}.pdf"]["dup_kind"] for i in (2, 3, 4)} == {"doi"}
    assert all(st[f"P{i}.pdf"]["dup_of"] for i in (2, 3, 4))
    assert len(rows(http_base.out, "publications")) == 4  # на CSV не влияет


def test_no_dedupe_removes_marks(http_base):
    http_base("--no-dedupe")
    st = files_csv(http_base.out)
    assert {r["dup_kind"] for r in st.values()} == {""} and {r["dup_of"] for r in st.values()} == {""}


# --- недоступный ML (REQ-065) --------------------------------------------------------------------------------------------------

def test_ml_down_down_ok_all_done(http_base, clock):
    http_base.spy.health_status = ["down", "down", "ok"]
    http_base.spy.state = "down"
    r = http_base(delays="5,15,30")
    assert r.exit_code == 0, r
    assert {v["status"] for v in files_csv(http_base.out).values()} == {"done"}
    assert re.search(r"(?i)ml|пауз|недоступ|unavailable", r.stderr)
    assert sum(clock.slept) >= 5


def test_ml_always_down_failed_exit_2_set_written(http_base, clock):
    http_base.spy.health_status = ["down"]
    http_base.spy.state = "down"
    r = http_base(delays="1,2")
    assert r.exit_code == 2, r
    st = files_csv(http_base.out)
    assert {v["status"] for v in st.values()} == {"failed"} and {v["error_code"] for v in st.values()} == {"ml_unavailable"}
    assert sorted(p.name for p in http_base.out.glob("*.csv")) == TABLE_FILES
    assert len(rows(http_base.out, "publications")) == 4  # заглушки с publication_id
    assert sum(clock.slept) >= 3


def test_ml_down_but_cached_files_still_done(http_base, clock, tmp_path):
    http_base()  # прогрев кеша
    http_base.spy.health_status = ["down"]
    http_base.spy.state = "down"
    r = http_base(out=tmp_path / "hout2", delays="1")
    assert r.exit_code == 0, r
    assert {v["status"] for v in files_csv(tmp_path / "hout2").values()} == {"done"}


def test_retry_delays_zero_means_no_waiting(http_base, clock):
    http_base.spy.health_status = ["down"]
    http_base.spy.state = "down"
    r = http_base(delays="0")
    assert r.exit_code == 2 and sum(clock.slept) == 0


# --- import-raw / поведение до записи ------------------------------------------------------------------------------------------------

def test_import_raw_equals_extract_raw(clock, run_cli, pdf_dir, tmp_path, example_7_0_1):
    raw = tmp_path / "raw"
    raw.mkdir()
    for pid in ("D001", "D003"):
        d = json.loads(json.dumps(example_7_0_1))
        d["publication"]["publication_id"] = pid
        (raw / f"{pid}.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    a = run_cli("import-raw", "--in", pdf_dir, "--raw-dir", raw, "--out", tmp_path / "a")
    b = run_cli("extract", "--in", pdf_dir, "--out", tmp_path / "b", "--extractor", "raw", "--raw-dir", raw)
    assert a.exit_code == b.exit_code == 0, (a, b)
    for t in TABLES:
        assert (tmp_path / "a" / f"{t}.csv").read_bytes() == (tmp_path / "b" / f"{t}.csv").read_bytes()


def test_import_raw_missing_json_failed_exit_2(clock, run_cli, pdf_dir, tmp_path, example_7_0_1):
    raw = tmp_path / "raw"
    raw.mkdir()
    d = json.loads(json.dumps(example_7_0_1))
    d["publication"]["publication_id"] = "D001"
    (raw / "D001.json").write_text(json.dumps(d), encoding="utf-8")
    r = run_cli("import-raw", "--in", pdf_dir, "--raw-dir", raw, "--out", tmp_path / "o")
    assert r.exit_code == 2
    assert files_csv(tmp_path / "o")["D003.pdf"]["status"] == "failed"


def test_import_raw_bad_json_failed_not_crash(clock, run_cli, pdf_dir, tmp_path, example_broken):
    raw = tmp_path / "raw"
    raw.mkdir()
    for pid in ("D001", "D003"):
        (raw / f"{pid}.json").write_text("{not json", encoding="utf-8")
    r = run_cli("import-raw", "--in", pdf_dir, "--raw-dir", raw, "--out", tmp_path / "o")
    assert r.exit_code == 2 and len(rows(tmp_path / "o", "publications")) == 2
