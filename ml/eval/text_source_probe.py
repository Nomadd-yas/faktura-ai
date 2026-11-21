#!/usr/bin/env python3
"""[faktura-patch] R6: офлайн-проба TEXT_SOURCE=auto без OCR и LLM.

Для каждого PDF печатает по страницам решение (pdf|ocr + причина), число HTML-таблиц, собранных из текстового слоя,
и для pdf-страниц — сколько эталонных значений результатов (gold results.csv, строки не из рисунков) этой страницы
присутствует в markdown, собранном из текстового слоя (strict: напечатанная строка raw_value; loose: число в любом
масштабе, pipeline._has_value). OCR-markdown локально нет — сравнение с OCR делается на сервере.

Usage: python ml/eval/text_source_probe.py D004 D005 --domain solar   (id или путь к PDF; --mode auto|pdf)
"""
import argparse
import csv
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ml" / "pdf-dataset"))
try:
    import openai  # noqa: F401
except ImportError:
    stub = types.ModuleType("openai")
    stub.OpenAI = object
    sys.modules["openai"] = stub
import pipeline  # noqa: E402

CASES = {"solar": "caseB_kejs5", "rag": "caseA_drive"}


def _norm(s):
    return re.sub(r"\s+", "", s.replace("−", "-"))


def _num(s):
    try:
        return float(str(s).replace(",", ".").replace("−", "-"))
    except ValueError:
        return None


def gold_by_page(domain, pid):
    path = ROOT / "data" / CASES[domain] / "dev_gold" / "results.csv"
    out = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if r["publication_id"] != pid:
                continue
            if (r.get("source_figure") or "").strip() and not (r.get("source_table") or "").strip():
                continue  # значение из рисунка
            try:
                pg = int(r["source_page"])
            except (KeyError, ValueError):
                continue
            out.setdefault(pg, []).append(r)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("papers", nargs="+")
    ap.add_argument("--domain", choices=list(CASES), required=True)
    ap.add_argument("--mode", default="auto", choices=["auto", "pdf"])
    a = ap.parse_args()
    for p in a.papers:
        pdf = Path(p) if p.lower().endswith(".pdf") else ROOT / "data" / CASES[a.domain] / "dev" / f"{p}.pdf"
        pid = pdf.stem
        infos = pipeline.analyze_text_source(pdf, a.mode)
        gold = gold_by_page(a.domain, pid)
        n_ocr = sum(1 for i in infos if i["source"] == "ocr")
        print(f"\n=== {a.domain} {pid}: {len(infos)} pages, pdf={len(infos) - n_ocr}, ocr={n_ocr}, "
              f"tables(pdf)={sum(len(i['tables']) for i in infos)}")
        tot_s = tot_l = tot_n = 0
        for inf in infos:
            pg = inf["page"]
            line = f"p{pg:>2} {inf['source']:<3} tables={len(inf['tables'])} {inf['reason']}"
            rows = gold.get(pg, [])
            if inf["source"] == "pdf" and rows:
                md = pipeline.build_pdf_page_markdown(inf)
                flat = _norm(md)
                strict = loose = 0
                for r in rows:
                    raw = (r.get("raw_value") or r.get("metric_value") or "").strip()
                    if raw and re.search(r"(?<![\d.])" + re.escape(_norm(raw)) + r"(?![\d])", flat):
                        strict += 1
                    v = _num(r.get("metric_value"))
                    if v is not None and pipeline._has_value(md, v, leading_zero=(a.domain == "rag")):
                        loose += 1
                tot_s, tot_l, tot_n = tot_s + strict, tot_l + loose, tot_n + len(rows)
                line += f"  gold={len(rows)} strict={strict} loose={loose}"
            elif rows:
                line += f"  gold={len(rows)} (ocr page)"
            print(line)
        if tot_n:
            print(f"--> pdf pages: gold {tot_n}, strict {tot_s} ({tot_s / tot_n:.0%}), loose {tot_l} ({tot_l / tot_n:.0%})")


if __name__ == "__main__":
    main()
