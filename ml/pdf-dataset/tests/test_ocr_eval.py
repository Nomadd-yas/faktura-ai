"""[faktura-patch] Метрики harness ml/eval/ocr_eval.py: нормализация, числа, CER. Без vLLM."""
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))
import ocr_eval as ev  # noqa: E402


def _dp(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def test_levenshtein_matches_dp():
    rnd = random.Random(1)
    for _ in range(300):
        a = "".join(rnd.choice("abc ") for _ in range(rnd.randint(0, 40)))
        b = "".join(rnd.choice("abc ") for _ in range(rnd.randint(0, 40)))
        assert ev.levenshtein(a, b) == _dp(a, b), (a, b)
    assert ev.levenshtein("x" * 70, "x" * 69 + "y") == 1  # длиннее машинного слова


def test_cer_basic():
    assert ev.cer("hello world", "hello world") == 0.0
    assert ev.cer("abcd", "abxd") == pytest.approx(0.25)
    assert ev.cer("", "") == 0.0 and ev.cer("", "x") == 1.0


def test_normalize_markup_and_unicode():
    assert ev.normalize("<table><tr><td>GEMINI<br>1.0 Pro</td><td>12.5</td></tr></table>") == "gemini 1.0 pro 12.5"
    assert ev.normalize("## Title with **bold** and `code`") == "title with bold and code"
    assert ev.normalize("$V_{\\mathrm{OC}}$ = 1.05 V") == "voc = 1.05 v"
    assert ev.normalize("ﬁnal efﬁciency") == "final efficiency"
    assert ev.normalize("ｆｕｌｌ　width") == "full width"
    assert ev.normalize("recom-\nmended") == "recommended"
    assert ev.normalize("−3.5 – x") == "-3.5 - x"


def test_cer_ignores_markup():
    ref = "Efficiency of 12.5 % was reached"
    hyp = "## Efficiency of **12.5** % was reached"
    assert ev.cer(ref, hyp) == 0.0


def test_numbers():
    assert ev.numbers("values 12.5 and 1,234 and 0.5") == ["12.5", "1234", "0.5"]
    assert ev.numbers("−3.5") == ["-3.5"]
    assert ev.numbers("<td>12.5</td><td>7</td>") == ["12.5", "7"]
    assert ev.numbers("Q8 and v2.0") == []  # цифры внутри слов — не числа


def test_multiset_pr():
    r = ev.multiset_pr(["1", "1", "2", "3"], ["1", "2", "2", "4"])
    assert r["match"] == 2 and r["p"] == 0.5 and r["r"] == 0.5
    assert ev.multiset_pr([], [])["r"] == 1.0
    assert ev.multiset_pr(["1"], [])["r"] == 0.0


def test_count_merged():
    ref = ev.words("GEMINI 1.0 Pro and GPT 4 text")
    assert ev.count_merged(ref, ["gemini1", "pro", "text"]) == 1          # gemini+1 склеены
    assert ev.count_merged(ref, ["pro", "text", "and"]) == 0
    assert ev.count_merged(["gemini", "pro", "text"], ["geminipro", "text"]) == 1


def test_table_metrics_with_text_layer_words():
    # слова: (x0,y0,x1,y1,text,block,line,wordno); страница 1000x1000 pt -> bbox 0..1000 == pt
    pw = [(100, 100, 130, 110, "1.5", 0, 0, 0), (200, 100, 230, 110, "2.5", 0, 0, 1),
          (100, 150, 130, 160, "3.5", 0, 1, 0), (900, 900, 930, 910, "99", 1, 0, 0)]
    blocks = [{"label": "table", "bbox": [50, 50, 300, 300],
               "content": "<table><tr><td>1.5</td><td>2.5</td></tr><tr><td>3.0</td></tr></table>"},
              {"label": "text", "bbox": [0, 0, 10, 10], "content": "ignored 7"}]
    m = ev.table_metrics(blocks, pw, 1000, 1000)
    assert m["tables"] == 1
    assert m["num"]["ref"] == 3 and m["num"]["match"] == 2 and m["num"]["hyp"] == 3  # 99 вне bbox; 3.0 != 3.5
    assert m["num"]["r"] == pytest.approx(2 / 3, abs=1e-3)


def test_words_in_rect_reading_order():
    pw = [(10, 50, 20, 60, "b", 0, 1, 0), (10, 10, 20, 20, "a", 0, 0, 0)]
    assert ev.words_in_rect(pw, (0, 0, 100, 100)) == ["a", "b"]


def test_block_px():
    assert ev.block_px([100, 500, 1000, 1000], 600, 800) == (60.0, 400.0, 600.0, 800.0)
