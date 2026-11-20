#!/usr/bin/env python3
"""Offline scorer: submission (7 CSV tables) vs dev gold. stdlib only.

Usage: score.py GOLD_DIR SUB_DIR [--pids D001,D003] [--split tune|holdout]
                [--json out.json] [--details] [--strict]
"""
import argparse
import csv
import json
import re
import sys
from collections import defaultdict

TABLES = ["publications", "materials", "devices", "layers", "measurements", "results", "issues"]
SCORED = {"PCE", "Voc", "Jsc", "FF", "Rs", "Rsh", "HI", "Jsc_EQE", "stabilized_PCE", "T80", "retained_PCE"}
TUNE = {"D%03d" % i for i in range(1, 15)}
HOLDOUT = {"D%03d" % i for i in range(15, 23)}


# ---------------------------------------------------------------- io / helpers
def load(d):
    out = {}
    for t in TABLES:
        rows = []
        try:
            with open("%s/%s.csv" % (d, t), newline="", encoding="utf-8-sig") as f:
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
    if s is None:
        return None
    s = str(s).strip().replace("%", "").replace("−", "-")
    if not s:
        return None
    if re.fullmatch(r"-?\d+,\d+", s):
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def close(a, b, rel=0.005):
    if a == 0 or b == 0:
        return abs(a - b) <= 1e-9
    return abs(a - b) <= rel * max(abs(a), abs(b))


def eq(a, b):
    """generic field equality: both empty, numeric-close, or case-insens text."""
    a = (a or "").strip()
    b = (b or "").strip()
    if not a and not b:
        return True
    if not a or not b:
        return False
    x, y = num(a), num(b)
    if x is not None and y is not None:
        return close(x, y, 1e-6)
    return a.lower() == b.lower()


def norm(s):
    return re.sub(r"[\W_]+", "", (s or "").lower())


def toks(s):
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def jacc(a, b):
    A, B = toks(a), toks(b)
    if not A and not B:
        return 1.0
    return len(A & B) / len(A | B)


def val(r):
    v = num(r.get("metric_value"))
    if v is None:
        v = num(r.get("raw_value"))
    return v


class Acc:
    """key -> [hit, total] (floats allowed)"""
    def __init__(self):
        self.d = defaultdict(lambda: [0.0, 0])

    def add(self, k, hit, tot=1):
        self.d[k][0] += hit
        self.d[k][1] += tot


def merge_acc(dst, src):
    for k, (h, t) in src.items():
        dst[k][0] += h
        dst[k][1] += t


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f}


# ---------------------------------------------------------------- results
RES_ENUM_FIELDS = ["aggregation", "result_origin", "extraction_status", "source_page",
                   "cited_publication_id", "spread_value", "n_devices", "metric_unit"]


def match_results(G, S):
    """returns list of (gi, si) pairs for scored rows; G,S full lists of rows"""
    cands = []
    gi_all = [i for i, r in enumerate(G) if r["metric_name"] in SCORED]
    si_all = [i for i, r in enumerate(S) if r["metric_name"] in SCORED]
    sbym = defaultdict(list)
    for j in si_all:
        sbym[S[j]["metric_name"]].append(j)
    for i in gi_all:
        g = G[i]
        gv = val(g)
        for j in sbym.get(g["metric_name"], []):
            s = S[j]
            sv = val(s)
            if gv is not None:
                if sv is None or not close(gv, sv):
                    continue
            else:
                if sv is not None:
                    continue
                if not (eq(g["extraction_status"], s["extraction_status"]) and eq(g["source_page"], s["source_page"])):
                    continue
            sc = (eq(g["source_page"], s["source_page"]),
                  eq(g["aggregation"], s["aggregation"]),
                  bool(g["duplicate_of"]) == bool(s["duplicate_of"]),
                  eq(g["source_table"], s["source_table"]) and eq(g["source_figure"], s["source_figure"]),
                  eq(g["metric_unit"], s["metric_unit"]),
                  eq(g["result_origin"], s["result_origin"]))
            cands.append((tuple(-int(x) for x in sc), abs(i - j), i, j))
    cands.sort()
    ug, us, pairs = set(), set(), []
    for _, _, i, j in cands:
        if i in ug or j in us:
            continue
        ug.add(i)
        us.add(j)
        pairs.append((i, j))
    return pairs, gi_all, si_all


def majority_map(votes):
    m = {}
    by = defaultdict(lambda: defaultdict(int))
    for (s, g), c in votes.items():
        by[s][g] += c
    for s, d in by.items():
        m[s] = max(sorted(d), key=lambda g: d[g])
    return m


# ---------------------------------------------------------------- per paper
def score_paper(pid, G, S, details):
    """G,S: dict table -> rows for that paper"""
    R = {"res": {"tp": 0, "fp": 0, "fn": 0}, "per_metric": defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0}),
         "acc": Acc(), "mat": {"tp": 0, "fp": 0, "fn": 0}, "iss": {"tp": 0, "fp": 0, "fn": 0}}
    acc = R["acc"]
    gres, sres = G["results"], S["results"]
    pairs, gi_all, si_all = match_results(gres, sres)
    mg = {i for i, _ in pairs}
    ms = {j for _, j in pairs}
    R["res"] = {"tp": len(pairs), "fp": len(si_all) - len(pairs), "fn": len(gi_all) - len(pairs)}
    for i in gi_all:
        m = R["per_metric"][gres[i]["metric_name"]]
        m["tp" if i in mg else "fn"] += 1
    for j in si_all:
        if j not in ms:
            R["per_metric"][sres[j]["metric_name"]]["fp"] += 1

    # field accuracy on matched
    for i, j in pairs:
        g, s = gres[i], sres[j]
        for f in RES_ENUM_FIELDS:
            acc.add("res." + f, eq(g[f], s[f]))
        acc.add("res.source_table_nonempty", bool(g["source_table"]) == bool(s["source_table"]))
        acc.add("res.source_figure_nonempty", bool(g["source_figure"]) == bool(s["source_figure"]))
        acc.add("res.duplicate_of_emptiness", bool(g["duplicate_of"]) == bool(s["duplicate_of"]))

    # linkage
    dv, mv = defaultdict(int), defaultdict(int)
    for i, j in pairs:
        g, s = gres[i], sres[j]
        if g["device_id"]:
            dv[(s["device_id"], g["device_id"])] += 1
        if g["measurement_id"]:
            mv[(s["measurement_id"], g["measurement_id"])] += 1
    dmap, mmap = majority_map(dv), majority_map(mv)
    for i, j in pairs:
        g, s = gres[i], sres[j]
        if g["device_id"]:
            acc.add("link.device", bool(s["device_id"]) and dmap.get(s["device_id"]) == g["device_id"])
        if g["measurement_id"]:
            acc.add("link.measurement", bool(s["measurement_id"]) and mmap.get(s["measurement_id"]) == g["measurement_id"])

    gdev = {r["device_id"]: r for r in G["devices"]}
    sdev = {r["device_id"]: r for r in S["devices"]}
    for sd, gd in dmap.items():
        if sd in sdev and gd in gdev:
            for f in ["device_role", "device_type", "architecture", "substrate_type", "area_definition",
                      "active_area", "encapsulation"]:
                acc.add("dev." + f, eq(gdev[gd][f], sdev[sd][f]))
    gms = {r["measurement_id"]: r for r in G["measurements"]}
    sms = {r["measurement_id"]: r for r in S["measurements"]}
    for sm, gm in mmap.items():
        if sm in sms and gm in gms:
            for f in ["measurement_type", "scan_direction", "spectrum", "light_intensity", "stability_protocol"]:
                acc.add("meas." + f, eq(gms[gm][f], sms[sm][f]))

    # layers
    def layers_of(rows, d):
        ls = [r for r in rows if r["device_id"] == d]
        return sorted(ls, key=lambda r: (num(r["layer_order"]) if num(r["layer_order"]) is not None else 1e9))
    for sd, gd in dmap.items():
        if sd not in sdev or gd not in gdev:
            continue
        gl, sl = layers_of(G["layers"], gd), layers_of(S["layers"], sd)
        acc.add("layers.count_equal", len(gl) == len(sl))
        for k, g in enumerate(gl):
            s = sl[k] if k < len(sl) else None
            acc.add("layers.function", bool(s) and eq(g["layer_function"], s["layer_function"]))
            acc.add("layers.material_norm", bool(s) and norm(g["layer_material"]) == norm(s["layer_material"]))
            acc.add("layers.material_jaccard", jacc(g["layer_material"], s["layer_material"]) if s else 0.0)
            acc.add("layers.thickness", bool(s) and eq(g["layer_thickness"], s["layer_thickness"]))
            acc.add("layers.deposition", bool(s) and norm(g["deposition_method"]) == norm(s["deposition_method"]))

    # materials
    def mkey(r):
        return (r["material_class"].lower(), norm(r["formula_normalized"]))
    pool = defaultdict(list)
    for r in S["materials"]:
        pool[mkey(r)].append(r)
    for g in G["materials"]:
        lst = pool.get(mkey(g))
        if lst:
            s = lst.pop(0)
            R["mat"]["tp"] += 1
            acc.add("mat.dimensionality", eq(g["dimensionality"], s["dimensionality"]))
            acc.add("mat.bandgap_value", eq(g["bandgap_value"], s["bandgap_value"]))
            acc.add("mat.bandgap_method", eq(g["bandgap_method"], s["bandgap_method"]))
        else:
            R["mat"]["fn"] += 1
    R["mat"]["fp"] = sum(len(v) for v in pool.values())

    # publications
    gp = G["publications"][0] if G["publications"] else None
    sp = S["publications"][0] if S["publications"] else None
    if gp:
        acc.add("pub.in_scope", bool(sp) and eq(gp["in_scope"], sp["in_scope"]))
        acc.add("pub.DOI", bool(sp) and eq(gp["DOI"], sp["DOI"]))
        acc.add("pub.year", bool(sp) and eq(gp["year"], sp["year"]))
        acc.add("pub.title", bool(sp) and norm(gp["title"]) == norm(sp["title"]))

    # issues
    s2g = {sres[j]["result_id"]: gres[i]["result_id"] for i, j in pairs}
    gis, sis = G["issues"], S["issues"]
    used = set()
    matched = 0

    def ids(r):
        return {x.strip() for x in r["record_ids"].split(";") if x.strip()}
    for mode in (0, 1):
        for s in sis:
            if id(s) in used:
                continue
            for gi in gis:
                if id(gi) in used:
                    continue
                if mode == 0:
                    ok = bool(ids(gi) & {s2g.get(x) for x in ids(s)})
                else:
                    ok = bool(s["source_page"]) and eq(gi["check_name"], s["check_name"]) and eq(gi["source_page"], s["source_page"])
                if ok:
                    used.add(id(s))
                    used.add(id(gi))
                    matched += 1
                    break
    R["iss"] = {"tp": matched, "fp": len(sis) - matched, "fn": len(gis) - matched}

    R["rows"] = {t: [len(G[t]), len(S[t])] for t in TABLES}
    if details:
        R["fn_rows"] = [row_info(gres[i]) for i in gi_all if i not in mg]
        R["fp_rows"] = [row_info(sres[j]) for j in si_all if j not in ms]
    return R


def row_info(r):
    return {"metric": r["metric_name"], "value": r["metric_value"] or r["raw_value"], "unit": r["metric_unit"],
            "agg": r["aggregation"], "page": r["source_page"], "device": r["device_id"],
            "fragment": r["source_fragment"][:80]}


# ---------------------------------------------------------------- aggregate / output
def acc_json(d):
    return {k: {"acc": (h / t if t else None), "n": t} for k, (h, t) in sorted(d.items())}


def finalize(papers):
    tot = {"res": {"tp": 0, "fp": 0, "fn": 0}, "mat": {"tp": 0, "fp": 0, "fn": 0}, "iss": {"tp": 0, "fp": 0, "fn": 0}}
    pm = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    acc = defaultdict(lambda: [0.0, 0])
    rows = {t: [0, 0] for t in TABLES}
    for R in papers.values():
        for k in tot:
            for q in tot[k]:
                tot[k][q] += R[k][q]
        for m, d in R["per_metric"].items():
            for q in d:
                pm[m][q] += d[q]
        merge_acc(acc, R["acc"].d)
        for t in TABLES:
            for x in (0, 1):
                rows[t][x] += R["rows"][t][x]
    return {"results": prf(**tot["res"]),
            "per_metric": {m: prf(**d) for m, d in sorted(pm.items())},
            "accuracy": acc_json(acc),
            "materials": prf(**tot["mat"]), "issues": prf(**tot["iss"]),
            "rows": rows}


def per_paper_json(R):
    return {"results": prf(**R["res"]), "materials": prf(**R["mat"]), "issues": prf(**R["iss"]),
            "per_metric": {m: prf(**d) for m, d in sorted(R["per_metric"].items())},
            "accuracy": acc_json(R["acc"].d),
            "rows": R["rows"]}


def pct(x):
    return "  -  " if x is None else "%5.1f" % (100 * x)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gold")
    ap.add_argument("sub")
    ap.add_argument("--pids")
    ap.add_argument("--split", choices=["tune", "holdout"])
    ap.add_argument("--json")
    ap.add_argument("--details", action="store_true")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()

    gold, sub = load(a.gold), load(a.sub)
    gb = {t: by_pid(gold[t]) for t in TABLES}
    sb = {t: by_pid(sub[t]) for t in TABLES}
    pids = sorted(gb["publications"])
    if a.pids:
        want = {x.strip() for x in a.pids.split(",")}
        pids = [p for p in pids if p in want]
    if a.split:
        pool = TUNE if a.split == "tune" else HOLDOUT
        pids = [p for p in pids if p in pool]
    if not a.strict:
        pids = [p for p in pids if p in sb["publications"]]
    if not pids:
        print("no papers to score")
        return 1

    papers = {}
    for p in pids:
        G = {t: gb[t].get(p, []) for t in TABLES}
        S = {t: sb[t].get(p, []) for t in TABLES}
        papers[p] = score_paper(p, G, S, a.details)

    tot = finalize(papers)
    print("Papers: %d (%s)  | scored metrics only: %s" % (len(pids), ",".join(pids), ",".join(sorted(SCORED))))
    r = tot["results"]
    print("\nRESULTS  P=%s R=%s F1=%s  TP=%d FP=%d FN=%d" % (pct(r["precision"]), pct(r["recall"]), pct(r["f1"]), r["tp"], r["fp"], r["fn"]))
    print("per paper:")
    for p in pids:
        x = prf(**papers[p]["res"])
        print("  %s  P=%s R=%s F1=%s  TP=%d FP=%d FN=%d" % (p, pct(x["precision"]), pct(x["recall"]), pct(x["f1"]), x["tp"], x["fp"], x["fn"]))
    print("per metric:")
    for m, x in tot["per_metric"].items():
        print("  %-15s P=%s R=%s F1=%s  TP=%d FP=%d FN=%d" % (m, pct(x["precision"]), pct(x["recall"]), pct(x["f1"]), x["tp"], x["fp"], x["fn"]))
    print("\nACCURACY (%, n):")
    groups = defaultdict(list)
    for k, v in tot["accuracy"].items():
        groups[k.split(".")[0]].append((k, v))
    for g, items in groups.items():
        print(" [%s] " % g + "  ".join("%s=%s(%d)" % (k.split(".", 1)[1], pct(v["acc"]).strip(), v["n"]) for k, v in items))
    m, i = tot["materials"], tot["issues"]
    print("\nMATERIALS P=%s R=%s TP=%d FP=%d FN=%d" % (pct(m["precision"]), pct(m["recall"]), m["tp"], m["fp"], m["fn"]))
    print("ISSUES    P=%s R=%s TP=%d FP=%d FN=%d" % (pct(i["precision"]), pct(i["recall"]), i["tp"], i["fp"], i["fn"]))
    print("ROWS gold/sub: " + "  ".join("%s=%d/%d" % (t, tot["rows"][t][0], tot["rows"][t][1]) for t in TABLES))

    if a.details:
        for p in pids:
            print("\n=== %s ===" % p)
            for lab, key in (("FN (missed gold)", "fn_rows"), ("FP (extra sub)", "fp_rows")):
                print(" %s: %d" % (lab, len(papers[p][key])))
                for x in papers[p][key]:
                    print("   %-14s %-10s %-8s p%-3s %-8s | %s" % (x["metric"], x["value"], x["agg"], x["page"], x["device"], x["fragment"]))

    if a.json:
        js = {"overall": tot, "papers": {p: per_paper_json(papers[p]) for p in pids}}
        if a.details:
            for p in pids:
                js["papers"][p]["fn_rows"] = papers[p]["fn_rows"]
                js["papers"][p]["fp_rows"] = papers[p]["fp_rows"]
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(js, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
