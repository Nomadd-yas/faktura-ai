#!/usr/bin/env python3
"""Offline scorer for the RAG domain: submission (7 CSV tables) vs dev gold. stdlib only.

Usage: score_rag.py [GOLD_DIR] SUB_DIR [--pids D001,D003] [--details] [--json out.json]
                    [--no-dups] [--rel 0.005]

GOLD_DIR defaults to data/caseA_drive/dev_gold (relative to the repo root). Self-test: pass the gold dir
as SUB_DIR -> every number must be 100%.

Results are matched per paper by:
  * normalized metric_name (lowercase, punctuation stripped; a metric_k is appended to the name when it is not
    already in it, so "NDCG@5" == name "NDCG" + metric_k 5);
  * value within +-rel (default 0.5%): normalized_value, else metric_value, else raw_value (numeric part);
  * preference for the same source_page (and source_table when both are given); a second pass accepts any page.
Rows without a numeric value (present_not_extractable, absent) are matched on name + raw text + page.
Gold rows are split into "table/text" and "figure" (source_figure non-empty or note mentions digitiz*).
"""
import argparse
import csv
import json
import os
import re
import sys
from bisect import bisect_left, bisect_right
from collections import defaultdict

TABLES = ["publications", "configurations", "datasets", "tasks", "experiments", "results", "issues"]
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_GOLD = os.path.normpath(os.path.join(HERE, "..", "..", "data", "caseA_drive", "dev_gold"))


def load(d):
    out = {}
    for t in TABLES:
        rows = []
        try:
            with open(os.path.join(d, t + ".csv"), newline="", encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    rows.append({(k or "").strip(): (v or "").strip() for k, v in r.items()})
        except FileNotFoundError:
            pass
        out[t] = rows
    return out


def by_pid(rows):
    g = defaultdict(list)
    for r in rows:
        g[r.get("publication_id", "")].append(r)
    return g


def num(s):
    """First numeric value of a cell: '.204' -> 0.204, '63.2±1.1' -> 63.2, '12 %' -> 12.0, text -> None."""
    if s is None:
        return None
    s = str(s).strip().replace("−", "-").replace("–", "-")
    if not s:
        return None
    m = re.match(r"^[<>~≈≥≤+\s]*(-?\d*[.,]?\d+(?:[eE][-+]?\d+)?)", s)
    if not m:
        return None
    t = m.group(1).replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def norm(s):
    return re.sub(r"[\W_]+", "", (s or "").lower())


def name_key(r):
    n = norm(r.get("metric_name"))
    k = num(r.get("metric_k"))
    if k is not None and n:
        ks = str(int(k)) if k == int(k) else str(k)
        if not n.endswith(ks):
            n += ks
    return n


def value_of(r):
    for c in ("normalized_value", "metric_value", "raw_value"):
        v = num(r.get(c))
        if v is not None:
            return v
    return None


def is_figure(r):
    return bool(r.get("source_figure")) or "digitiz" in (r.get("note") or "").lower()


def close(a, b, rel):
    if a == b:
        return True
    return abs(a - b) <= rel * max(abs(a), abs(b))


def page_score(a, b):
    """2: same page and table, 1: same page, 0: otherwise."""
    pa, pb = (a.get("source_page") or ""), (b.get("source_page") or "")
    if pa and pa == pb:
        ta, tb = norm(a.get("source_table")), norm(b.get("source_table"))
        return 2 if (ta and ta == tb) else 1
    return 0


def match_results(gold, sub, rel):
    """-> (pairs [(gold_row, sub_row)], sub_unmatched, gold_unmatched). Greedy two-pass matching."""
    buckets = defaultdict(list)  # name -> sorted [(value, gold_idx)]
    textual = defaultdict(list)  # (name, raw text) -> [gold_idx]
    for i, g in enumerate(gold):
        v = value_of(g)
        if v is None:
            textual[(name_key(g), norm(g.get("raw_value")))].append(i)
        else:
            buckets[name_key(g)].append((v, i))
    for b in buckets.values():
        b.sort()
    keys = {n: [v for v, _ in b] for n, b in buckets.items()}
    used_g = set()
    sub_match = {}

    def candidates(s):
        v = value_of(s)
        n = name_key(s)
        if v is None:
            return [i for i in textual.get((n, norm(s.get("raw_value"))), [])]
        b = buckets.get(n)
        if not b:
            return []
        tol = rel * abs(v) * 1.02 + 1e-9
        lo, hi = bisect_left(keys[n], v - tol), bisect_right(keys[n], v + tol)
        return [i for gv, i in b[lo:hi] if close(gv, v, rel)]

    for pass_no in (1, 2):
        for j, s in enumerate(sub):
            if j in sub_match:
                continue
            best, best_sc = None, None
            for i in candidates(s):
                if i in used_g:
                    continue
                g = gold[i]
                ps = page_score(g, s)
                if pass_no == 1 and ps == 0:
                    continue
                # tie-breakers: same links / fragment / id (ids of two submissions usually differ - soft preference only)
                sc = (ps,) + tuple(int(bool(s.get(c)) and s.get(c) == g.get(c))
                                   for c in ("config_id", "dataset_id", "experiment_id", "task_id", "source_fragment", "result_id"))
                if best_sc is None or sc > best_sc:
                    best, best_sc = i, sc
            if best is not None:
                used_g.add(best)
                sub_match[j] = best
    pairs = [(gold[i], sub[j]) for j, i in sub_match.items()]
    return pairs, [s for j, s in enumerate(sub) if j not in sub_match], \
        [g for i, g in enumerate(gold) if i not in used_g]


def prf(tp, n_sub, n_gold):
    p = tp / n_sub if n_sub else (1.0 if not n_gold else 0.0)
    r = tp / n_gold if n_gold else (1.0 if not n_sub else 0.0)
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def pct(x):
    return "%5.1f%%" % (100 * x)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="+", help="[GOLD_DIR] SUB_DIR")
    ap.add_argument("--pids", help="comma-separated publication ids (default: all gold papers)")
    ap.add_argument("--details", action="store_true", help="print missed gold rows and false positives (first 15 per paper)")
    ap.add_argument("--json", help="write full report as JSON")
    ap.add_argument("--no-dups", action="store_true", help="exclude rows with duplicate_of from both sides")
    ap.add_argument("--rel", type=float, default=0.005, help="relative value tolerance [0.005]")
    a = ap.parse_args()
    if len(a.dirs) == 1:
        gold_dir, sub_dir = DEFAULT_GOLD, a.dirs[0]
    else:
        gold_dir, sub_dir = a.dirs[0], a.dirs[1]
    G, S = load(gold_dir), load(sub_dir)
    pids = [p for p in (a.pids.split(",") if a.pids else sorted(by_pid(G["publications"]))) if p]

    gres, sres = by_pid(G["results"]), by_pid(S["results"])
    report = {"papers": {}, "overall": {}}
    tot = defaultdict(int)
    tab_counts = {t: [0, 0] for t in TABLES}  # table -> [gold, sub]
    print("%-6s %6s %6s %6s %7s %7s %7s | %6s %7s | %6s %6s %6s" %
          ("pid", "gold", "sub", "TP", "P", "R", "F1", "goldT", "recT", "figG", "figTP", "scope"))
    for pid in pids:
        gr = [r for r in gres.get(pid, []) if not (a.no_dups and r.get("duplicate_of"))]
        sr = [r for r in sres.get(pid, []) if not (a.no_dups and r.get("duplicate_of"))]
        pairs, fp, fn = match_results(gr, sr, a.rel)
        tp = len(pairs)
        fig_g = sum(1 for g in gr if is_figure(g))
        fig_tp = sum(1 for g, _ in pairs if is_figure(g))
        tp_nf = tp - fig_tp
        gold_nf = len(gr) - fig_g
        sub_nf = len(sr) - fig_tp  # sub rows that matched a figure-gold row are neutral in the table-only view
        p, r, f = prf(tp, len(sr), len(gr))
        rt = tp_nf / gold_nf if gold_nf else 1.0
        gs = {x["publication_id"]: x for x in G["publications"]}.get(pid)
        ss = {x["publication_id"]: x for x in S["publications"]}.get(pid)
        scope_ok = None
        if gs is not None:
            scope_ok = ss is not None and (ss.get("in_scope", "").lower() == gs.get("in_scope", "").lower())
        print("%-6s %6d %6d %6d %7s %7s %7s | %6d %7s | %6d %6d %6s" %
              (pid, len(gr), len(sr), tp, pct(p), pct(r), pct(f), gold_nf, pct(rt), fig_g, fig_tp,
               "-" if scope_ok is None else ("ok" if scope_ok else "WRONG")))
        report["papers"][pid] = {"gold": len(gr), "sub": len(sr), "tp": tp, "precision": p, "recall": r, "f1": f,
                                 "gold_table_text": gold_nf, "recall_table_text": rt,
                                 "gold_figure": fig_g, "tp_figure": fig_tp, "in_scope_ok": scope_ok,
                                 "sub_table_text_unmatched": len(fp)}
        for k, v in (("gold", len(gr)), ("sub", len(sr)), ("tp", tp), ("gold_nf", gold_nf), ("tp_nf", tp_nf),
                     ("sub_nf", sub_nf), ("fig_g", fig_g), ("fig_tp", fig_tp)):
            tot[k] += v
        if scope_ok is not None:
            tot["scope_n"] += 1
            tot["scope_ok"] += 1 if scope_ok else 0
        for t in TABLES:
            tab_counts[t][0] += sum(1 for x in by_pid(G[t]).get(pid, []))
            tab_counts[t][1] += sum(1 for x in by_pid(S[t]).get(pid, []))
        if a.details:
            for title, rows in (("MISSED gold", fn), ("EXTRA sub", fp)):
                for x in rows[:15]:
                    print("    %s p%-3s %-22s v=%-10s %s %s" % (title, x.get("source_page"), (x.get("metric_name") or "")[:22],
                                                              x.get("raw_value", "")[:10], x.get("source_table") or x.get("source_figure"),
                                                              (x.get("config_id") or "")))
                if len(rows) > 15:
                    print("    ... %s: +%d more" % (title, len(rows) - 15))
    p, r, f = prf(tot["tp"], tot["sub"], tot["gold"])
    pn, rn, fn_ = prf(tot["tp_nf"], tot["sub_nf"], tot["gold_nf"])
    rf = tot["fig_tp"] / tot["fig_g"] if tot["fig_g"] else 1.0
    print("-" * 100)
    print("OVERALL results (micro): gold=%d sub=%d TP=%d  P=%s R=%s F1=%s" % (tot["gold"], tot["sub"], tot["tp"], pct(p), pct(r), pct(f)))
    print("  table/text gold rows (%d): P=%s R=%s F1=%s   [figure-matched sub rows excluded]" % (tot["gold_nf"], pct(pn), pct(rn), pct(fn_)))
    print("  figure gold rows (%d): recall=%s" % (tot["fig_g"], pct(rf)))
    if tot["scope_n"]:
        print("  in_scope accuracy: %d/%d = %s" % (tot["scope_ok"], tot["scope_n"], pct(tot["scope_ok"] / tot["scope_n"])))
    print("  rows per table (gold / sub): " + "  ".join("%s %d/%d" % (t, *tab_counts[t]) for t in TABLES))
    report["overall"] = {"results": {"gold": tot["gold"], "sub": tot["sub"], "tp": tot["tp"], "precision": p, "recall": r, "f1": f},
                         "table_text": {"gold": tot["gold_nf"], "precision": pn, "recall": rn, "f1": fn_},
                         "figure": {"gold": tot["fig_g"], "recall": rf},
                         "in_scope": {"ok": tot["scope_ok"], "n": tot["scope_n"]},
                         "table_counts": {t: {"gold": g, "sub": s} for t, (g, s) in tab_counts.items()}}
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
