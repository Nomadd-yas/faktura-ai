"""pdfio.validator_norm, PypdfQuoteIndex, title_key (REQ-067, REQ-074)."""
from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest

from faktura.domain.ports import QuoteChecker
from faktura.pdfio import PypdfQuoteIndex, title_key, validator_norm


def _load_validator(script: Path):
    spec = importlib.util.spec_from_file_location("check_submission_ref", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------- validator_norm ----------------
@pytest.mark.parametrize("raw,expected", [
    ("", ""),
    ("ABC def", "abcdef"),
    ("PCE = 5.25 %", "pce5.25%"),
    ("ﬁll factor", "fillfactor"),          # лигатура fi
    ("ﬂux ﬀ", "fluxff"),              # fl, ff
    ("a−b", "ab"),                        # минус и тире уходят после замены на '-'
    ("a–b", "ab"),
    ("  tabs\tand\nnewlines ", "tabsandnewlines"),
    ("0,71", "071"),                           # запятая не сохраняется, точка сохраняется
    ("---", ""),
    ("Кириллица 12", "12"),
])
def test_validator_norm_cases(raw, expected):
    assert validator_norm(raw) == expected


def test_validator_norm_idempotent():
    s = "The ﬁrst − value: 15.28 mA/cm2 (±0.3)%"
    assert validator_norm(validator_norm(s)) == validator_norm(s)


@pytest.mark.data
def test_validator_norm_matches_script(check_script):
    ref = _load_validator(check_script).norm
    samples = ["ﬁ ﬂ ﬀ − – x", "Table 1, FF 0.71%", "J-sc = 24.89 mA cm-2", "", "αβγ 1.5eV", "a  b"]
    for s in samples:
        assert validator_norm(s) == ref(s)


# ---------------- PypdfQuoteIndex ----------------
def test_quote_index_implements_protocol(make_pdf):
    assert isinstance(PypdfQuoteIndex(make_pdf(["abc"])), QuoteChecker)


def test_quote_found_on_page(make_pdf):
    idx = PypdfQuoteIndex(make_pdf(["alpha beta", "the champion PCE = 5.25% here"]))
    assert idx.check(2, "champion PCE = 5.25%") is True


def test_quote_found_ignoring_case_and_spaces(make_pdf):
    idx = PypdfQuoteIndex(make_pdf(["The Champion  PCE"]))
    assert idx.check(1, "the champion pce") is True


def test_quote_wrong_page_is_false(make_pdf):
    idx = PypdfQuoteIndex(make_pdf(["alpha", "unique phrase here"]))
    assert idx.check(1, "unique phrase here") is False


def test_quote_not_found(make_pdf):
    assert PypdfQuoteIndex(make_pdf(["alpha"])).check(1, "completely absent") is False


@pytest.mark.parametrize("page", [0, 3, -1])
def test_quote_page_out_of_range_is_false(make_pdf, page):
    assert PypdfQuoteIndex(make_pdf(["alpha", "beta"])).check(page, "alpha") is False


def test_quote_empty_after_norm_is_not_found(make_pdf):
    idx = PypdfQuoteIndex(make_pdf(["alpha"]))
    assert idx.check(1, "---") is False
    assert idx.check(1, "  − – ") is False


@pytest.mark.parametrize("page,frag", [(None, "alpha"), (1, None)])
def test_quote_not_checkable_returns_none(make_pdf, page, frag):
    assert PypdfQuoteIndex(make_pdf(["alpha"])).check(page, frag) is None


def test_quote_table_reference_not_checked(make_pdf):
    # check_submission.py пропускает цитаты, начинающиеся с Table/TABLE/Supplementary
    idx = PypdfQuoteIndex(make_pdf(["alpha"]))
    assert idx.check(1, "Table 4, B4 (record)") is None


def test_quote_page_texts_cached(make_pdf, monkeypatch):
    p = make_pdf(["alpha beta"])
    idx = PypdfQuoteIndex(p)
    assert idx.check(1, "alpha") is True
    p.unlink()  # второй вызов не должен перечитывать файл
    assert idx.check(1, "beta") is True


def test_quote_unreadable_pdf_does_not_crash(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 broken")
    res = PypdfQuoteIndex(bad).check(1, "alpha")
    assert res in (False, None)


def test_quote_page_is_one_based(make_pdf):
    idx = PypdfQuoteIndex(make_pdf(["first", "second"]))
    assert idx.check(1, "first") is True and idx.check(2, "second") is True


@pytest.mark.data
@pytest.mark.slow
def test_quote_index_matches_script_on_dev(case_dir, check_script):
    """251 проверяемая цитата в results+issues dev_gold: не найдена ровно одна (D003), как в check_submission.py."""
    import re

    ref = _load_validator(check_script)
    gold, dev = case_dir / "dev_gold", case_dir / "dev"
    indexes: dict[str, PypdfQuoteIndex] = {}
    checked = missing = 0
    missing_pubs: list[str] = []
    for table in ("results", "issues"):
        with open(gold / f"{table}.csv", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                frag, page = (row.get("source_fragment") or "").strip(), (row.get("source_page") or "").strip()
                if not frag or not page or re.match(r"^(Table|TABLE|Supplementary)", frag):
                    continue
                pid = row["publication_id"]
                pdf = dev / f"{pid}.pdf"
                if not pdf.exists():
                    continue
                try:
                    n = int(float(page))
                except ValueError:
                    continue
                idx = indexes.setdefault(pid, PypdfQuoteIndex(pdf))
                res = idx.check(n, frag)
                assert res is not None
                checked += 1
                if not res:
                    missing += 1
                    missing_pubs.append(pid)
    assert checked == 251
    assert missing == 1 and missing_pubs == ["D003"]
    assert ref.norm("x") == validator_norm("x")


# ---------------- title_key ----------------
def test_title_key_basic():
    k = title_key("Improved Photovoltaic Performance of CsPbI2Br", 2021, "Qiao H.W.")
    assert isinstance(k, str) and k


def test_title_key_ignores_case_punctuation_spaces():
    a = title_key("Improved Photovoltaic Performance: CsPbI2Br!", 2021, "Qiao H.W.")
    b = title_key("  improved   photovoltaic performance - cspbi2br ", 2021, "QIAO h.w")
    assert a == b


def test_title_key_differs_by_year():
    assert title_key("Same title", 2020, "Ivanov") != title_key("Same title", 2021, "Ivanov")


def test_title_key_differs_by_title():
    assert title_key("Title one", 2020, "Ivanov") != title_key("Title two", 2020, "Ivanov")


def test_title_key_differs_by_author():
    assert title_key("Same title", 2020, "Ivanov") != title_key("Same title", 2020, "Petrov")


@pytest.mark.parametrize("title,year,author", [
    ("Title", None, "Ivanov"),
    ("Title", 2020, None),
    ("Title", 2020, ""),
    ("Title", 2020, "   "),
    (None, 2020, "Ivanov"),
    ("", 2020, "Ivanov"),
])
def test_title_key_missing_parts_give_none(title, year, author):
    assert title_key(title, year, author) is None


def test_title_key_deterministic():
    assert title_key("T", 2020, "A") == title_key("T", 2020, "A")
