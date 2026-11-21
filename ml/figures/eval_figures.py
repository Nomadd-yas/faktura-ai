"""Evaluate vector_digitize against dev gold figure rows.

    python eval_figures.py [--papers D014 D003 D008] [--rel 0.02] [--abs-step-div 100]

Gold rows: results.csv rows with non-empty source_figure.  A gold row is matched when
a digitized value (y or x) on the same page is within max(rel*|gold|, axis_step/div).
"""
from __future__ import annotations

import argparse
import bisect
import csv
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from vector_digitize import digitize_pdf  # noqa: E402

ROOT = HERE.parents[1]
DATA = ROOT / "data" / "caseA_drive"


def parse_gold(s):
    s = s.strip().replace("−", "-").replace("≈", "").replace("~", "").replace("%", "").replace(",", "")
    m = re.search(r"-?\d*\.?\d+(?:[eE]-?\d+)?", s)
    return float(m.group(0)) if m else None


def load_gold(paper):
    rows = []
    with open(DATA / "dev_gold" / "results.csv", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["publication_id"] == paper and r["source_figure"]:
                v = parse_gold(r["raw_value"])
                try:
                    pg = int(r["source_page"])
                except ValueError:
                    continue
                if v is not None:
                    rows.append({"page": pg, "v": v, "fig": r["source_figure"], "metric": r["metric_name"]})
    return rows


def collect(figs):
    """page -> sorted list of (value, tol_step, figure_label)"""
    pts = defaultdict(list)
    n = 0
    for f in figs:
        for p in f["panels"]:
            for s in p["series"]:
                for lv in s.get("printed_labels") or []:
                    if lv is not None:
                        pts[f["page"]].append((lv, 0, f["figure_label"]))
                for x, y in s["points"]:
                    n += 1
                    pts[f["page"]].append((y, (p["y_axis"]["step"] or 0), f["figure_label"]))
                    if isinstance(x, (int, float)) and p["x_axis"]:
                        pts[f["page"]].append((x, (p["x_axis"]["step"] or 0), f["figure_label"]))
    for k in pts:
        pts[k].sort(key=lambda t: t[0])
    return pts, n


def match(gold, pts, rel, div):
    hit, used = [], set()
    for g in gold:
        arr = pts.get(g["page"], [])
        vals = [a[0] for a in arr]
        tol0 = rel * abs(g["v"])
        i = bisect.bisect_left(vals, g["v"] - max(tol0, 1e-12) - 1e9 * 0)  # coarse start
        ok = False
        lo = bisect.bisect_left(vals, g["v"] - 1e6)
        # scan neighbours around g["v"]
        j = bisect.bisect_left(vals, g["v"])
        for k in range(max(0, j - 50), min(len(arr), j + 50)):
            v, step, _ = arr[k]
            if abs(v - g["v"]) <= max(tol0, step / div):
                ok = True
                used.add((g["page"], k))
                break
        hit.append(ok)
    return hit, used


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--papers", nargs="*", default=["D014", "D003", "D008"])
    ap.add_argument("--rel", type=float, default=0.02)
    ap.add_argument("--abs-step-div", type=float, default=100.0)
    ap.add_argument("--examples", type=int, default=5)
    a = ap.parse_args()
    for paper in a.papers:
        gold = load_gold(paper)
        t = time.time()
        figs = digitize_pdf(str(DATA / "dev" / f"{paper}.pdf"))
        dt = time.time() - t
        pts, n = collect(figs)
        hit, used = match(gold, pts, a.rel, a.abs_step_div)
        total_vals = sum(len(v) for v in pts.values())
        print(f"\n=== {paper}: gold fig rows {len(gold)}, digitized points {n} (values {total_vals}), "
              f"recall {sum(hit)}/{len(gold)} = {sum(hit) / max(len(gold), 1):.1%}, "
              f"precision proxy {len(used)}/{total_vals} = {len(used) / max(total_vals, 1):.1%}, {dt:.1f}s")
        byf = defaultdict(lambda: [0, 0])
        for g, h in zip(gold, hit):
            byf[g["fig"]][0] += 1
            byf[g["fig"]][1] += h
        for fg, (tot, ok) in sorted(byf.items(), key=lambda kv: -kv[1][0])[:10]:
            print(f"   {fg:32s} {ok:5d}/{tot:<5d} {ok / tot:6.1%}")
        print("   digitized figures:", Counter(f["figure_label"] for f in figs).most_common(12))
        miss = [g for g, h in zip(gold, hit) if not h]
        for g in miss[:a.examples]:
            print("   MISS", g)


if __name__ == "__main__":
    main()
