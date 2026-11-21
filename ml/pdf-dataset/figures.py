"""[faktura-patch] Figure digitization + extraction-state assessment for the RAG pipeline.

Part 1 (digitizer): reads VECTOR charts straight from PDF geometry.  No OCR, no LLM: axes are calibrated
from numeric tick-label text spans, marks come from ``page.get_drawings()``.

    figs = digitize_pdf("paper.pdf")      # -> list[Figure] (plain dicts)

Figure = {page, figure_label, caption, bbox, panels, notes}
panel  = {bbox, title, x_axis, y_axis, series, notes}
axis   = {scale: linear|log, ticks: [(coord, value)], label_text, step, residual}
series = {color, kind: line|scatter|bar|hist, legend_label, points: [(x, y)]}
         (x is a category string for bars under a text-labelled x axis)

Part 2 (state model): ``assess_document`` decides per page and per figure what can be taken directly from
the PDF and how reliably (vector_ok | vector_partial | raster | none) from GENERIC signals only.
Part 3: optional vision fallback (RAG_VISION_MODEL), semantic mapping of series -> results rows by one LLM
call per figure, row generation, merge with table/prose rows.  No per-paper constants anywhere.

Heuristics only: linear/log axes, lines, scatter markers, bars, histograms, legend swatches.  3D plots,
rotated tick labels and broken axes are NOT handled by the vector path.
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import pymupdf

NUM_RE = re.compile(r"^[-+]?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?$|^[-+]?\.\d+$")
CAP_RE = re.compile(r"^\s*(?:Figure|Fig\.?)\s*(\d+)\s*[.:|]?", re.I)


# ---------------------------------------------------------------- utils
def parse_num(s: str):
    s = s.strip().replace("−", "-").replace("–", "-").replace(" ", "")
    mult = 1.0
    if s.endswith("%"):
        s = s[:-1]
    elif s.endswith(("k", "K")):
        s, mult = s[:-1], 1e3
    elif s.endswith("M"):
        s, mult = s[:-1], 1e6
    if not NUM_RE.match(s):
        return None
    try:
        return float(s.replace(",", "")) * mult
    except ValueError:
        return None


def _hexcol(c):
    if c is None:
        return None
    if len(c) == 1:
        c = (c[0],) * 3
    if len(c) == 4:
        k = c[3]
        c = tuple((1 - x) * (1 - k) for x in c[:3])
    return "#%02x%02x%02x" % tuple(int(round(max(0, min(1, v)) * 255)) for v in c[:3])


def _is_white(c):
    return c is None or (len(c) >= 3 and min(c[:3]) > 0.97)


def fit_axis(ticks):
    """ticks [(coord,value)] -> dict(scale,a,b,residual) or None. value=a*coord+b (or 10**)."""
    if len(ticks) < 2:
        return None
    cs = [t[0] for t in ticks]
    best = None
    for scale in ("linear", "log"):
        vs = [t[1] for t in ticks]
        if scale == "log":
            if min(vs) <= 0:
                continue
            vs = [math.log10(v) for v in vs]
        n = len(cs)
        mc, mv = sum(cs) / n, sum(vs) / n
        sxx = sum((c - mc) ** 2 for c in cs)
        if sxx < 1e-9:
            continue
        a = sum((c - mc) * (v - mv) for c, v in zip(cs, vs)) / sxx
        b = mv - a * mc
        span = (max(vs) - min(vs)) or 1.0
        res = max(abs(a * c + b - v) for c, v in zip(cs, vs)) / span
        if abs(a) < 1e-12:
            continue
        # prefer linear unless log is clearly better
        if best is None or res < best["residual"] * 0.5:
            best = {"scale": scale, "a": a, "b": b, "residual": res}
    return best


def _val(ax, c):
    v = ax["a"] * c + ax["b"]
    return 10 ** v if ax["scale"] == "log" else v


# ---------------------------------------------------------------- text
def _spans(page):
    out = []
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            dx, dy = ln["dir"]
            rot = abs(dy) > 0.5
            for s in ln["spans"]:
                t = s["text"].strip()
                if t:
                    out.append({"text": t, "bbox": tuple(s["bbox"]), "rot": rot, "size": s["size"]})
    return out


def _captions(page):
    caps = []
    for b in page.get_text("blocks"):
        m = CAP_RE.match(b[4])
        if m:
            caps.append({"label": "Figure " + m.group(1), "bbox": tuple(b[:4]), "text": " ".join(b[4].split())[:300]})
    return caps


# ---------------------------------------------------------------- tick clusters
def _tick_groups(spans):
    nums = []
    for s in spans:
        if s["rot"]:
            continue
        v = parse_num(s["text"])
        if v is not None and len(s["text"]) <= 9:
            x0, y0, x1, y1 = s["bbox"]
            nums.append({"v": v, "x0": x0, "x1": x1, "y0": y0, "y1": y1, "cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2})
    xrows, ycols = [], []
    byy = defaultdict(list)
    for n in sorted(nums, key=lambda n: n["y1"]):
        for k in byy:
            if abs(k - n["y1"]) < 1.5:
                byy[k].append(n)
                break
        else:
            byy[n["y1"]].append(n)
    for g in byy.values():
        g.sort(key=lambda n: n["cx"])
        if len(g) >= 2:
            xrows.append(g)
    for key in ("x1", "x0"):
        bx = defaultdict(list)
        for n in sorted(nums, key=lambda n: n[key]):
            for k in bx:
                if abs(k - n[key]) < 1.5:
                    bx[k].append(n)
                    break
            else:
                bx[n[key]].append(n)
        for g in bx.values():
            g.sort(key=lambda n: n["cy"])
            if len(g) >= 2 and not any(set(map(id, g)) == set(map(id, o)) for o in ycols):
                ycols.append(g)
    return xrows, ycols


def _split_runs(g, key, maxgap_factor=3.0):
    """split a tick group into monotone runs: break on direction flips / resets and big gaps."""
    if len(g) < 2:
        return [g]
    runs, cur = [], [g[0]]
    sign = 0
    gaps = [g[i + 1][key] - g[i][key] for i in range(len(g) - 1)]
    med = sorted(gaps)[len(gaps) // 2]
    for i in range(len(g) - 1):
        dv = g[i + 1]["v"] - g[i]["v"]
        sg = (dv > 0) - (dv < 0)
        big = med > 0 and gaps[i] > maxgap_factor * med + 15
        if sg == 0 or big or (sign and sg != sign):
            runs.append(cur)
            cur, sign = [g[i + 1]], 0
            continue
        sign = sg
        cur.append(g[i + 1])
    runs.append(cur)
    return runs


def _monotone(g):
    if len(g) < 2:
        return False
    vals = [n["v"] for n in g]
    return all(b > a for a, b in zip(vals, vals[1:])) or all(b < a for a, b in zip(vals, vals[1:]))


# ---------------------------------------------------------------- drawings -> marks
def _marks(page, drawings=None):
    """returns segs (single straight segments), lines (polylines), markers, rects."""
    segs, lines, markers, rects = [], [], [], []
    for d in (drawings if drawings is not None else page.get_drawings()):  # [faktura-patch] shared drawings
        items = d["items"]
        if not items:
            continue
        stroke, fill = d.get("color"), d.get("fill")
        r = d["rect"]
        w, h = r.width, r.height
        kinds = {it[0] for it in items}
        col = _hexcol(fill if not _is_white(fill) else stroke)
        scol = _hexcol(stroke)
        if kinds <= {"re", "qu"}:
            for it in items:
                rr = it[1] if it[0] == "re" else it[1].rect
                if fill is not None and not _is_white(fill):
                    rects.append({"r": (rr.x0, rr.y0, rr.x1, rr.y1), "color": _hexcol(fill)})
                elif fill is None and stroke is not None and 0.4 < max(rr.width, rr.height) < 9:
                    markers.append({"c": ((rr.x0 + rr.x1) / 2, (rr.y0 + rr.y1) / 2), "color": scol,
                                    "size": max(rr.width, rr.height)})
            continue
        if len(items) == 1 and items[0][0] == "l":
            p, q = items[0][1], items[0][2]
            segs.append({"p": (p.x, p.y), "q": (q.x, q.y), "color": scol or col, "w": d.get("width") or 0})
            continue
        if not kinds <= {"l", "c"}:
            continue
        closed = bool(d.get("closePath")) or _close(items)
        small = 0.4 < max(w, h) < 10
        if small and closed and len(items) >= 3:
            if _is_white(fill) and _is_white(stroke):
                continue
            markers.append({"c": ((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2), "color": col, "size": max(w, h)})
            continue
        if fill is not None and not _is_white(fill) and closed:
            continue  # filled polygons (areas, big shapes)
        if not small:
            for sub in _subpaths(items):
                if len(sub) == 1 and sub[0][0] == "l":
                    p, q = sub[0][1], sub[0][2]
                    segs.append({"p": (p.x, p.y), "q": (q.x, q.y), "color": scol or col, "w": d.get("width") or 0})
                else:
                    pts = _verts(sub)
                    if len(pts) >= 3:
                        lines.append({"pts": pts, "color": scol or col, "w": d.get("width") or 0})
    lines += _chain(segs)
    return segs, lines, markers, rects


def _bands(drawings):
    """[faktura-patch] translucent filled polygons (fill_opacity < 0.6) = shaded uncertainty bands around a series."""
    out = []
    for d in drawings:
        items, fill = d.get("items") or [], d.get("fill")
        op = d.get("fill_opacity")
        if fill is None or _is_white(fill) or op is None or not (0 < op < 0.6):
            continue
        if not {it[0] for it in items} <= {"l", "c"} or len(items) < 3:
            continue
        r = d["rect"]
        if max(r.width, r.height) < 10:
            continue
        for sub in _subpaths(items):
            pts = _verts(sub)
            if len(pts) >= 3:
                out.append({"pts": pts, "color": _hexcol(fill), "rect": (r.x0, r.y0, r.x1, r.y1)})
    return out


def _inv(ax, v):
    """data value -> page coordinate (inverse of _val)."""
    if ax["scale"] == "log":
        if v <= 0:
            return None
        v = math.log10(v)
    return (v - ax["b"]) / ax["a"]


def _poly_vspan(pts, x):
    """min/max y where the closed polygon crosses the vertical line at x (None if it does not)."""
    ys = []
    n = len(pts)
    for i in range(n):
        (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
        if abs(x1 - x0) < 1e-9:
            if abs(x - x0) < 0.35:
                ys += [y0, y1]
            continue
        if min(x0, x1) - 0.35 <= x <= max(x0, x1) + 0.35:
            t = min(1.0, max(0.0, (x - x0) / (x1 - x0)))
            ys.append(y0 + t * (y1 - y0))
    return (min(ys), max(ys)) if len(ys) >= 2 else None


def _attach_bands(panel, bb, bands):
    """[faktura-patch] for line/scatter series with a same-colour shaded band: per point [lower, upper] in data units.
    Generic: any figure that draws a confidence/std band as a translucent polygon in the series colour."""
    xfit, yfit = panel["_fit"]
    if not xfit or not bands:
        return
    yspan = bb[3] - bb[1]
    for s in panel["series"]:
        if s.get("kind") not in ("line", "scatter"):
            continue
        mine = [b for b in bands if b["color"] == s["color"] and _in(((b["rect"][0] + b["rect"][2]) / 2,
                                                                       (b["rect"][1] + b["rect"][3]) / 2), bb, 3)]
        if not mine:
            continue
        out, hit = [], 0
        for x, y in s["points"]:
            res = None
            px = _inv(xfit, x) if isinstance(x, (int, float)) else None
            py = _inv(yfit, y) if isinstance(y, (int, float)) else None
            if px is not None and py is not None:
                for b in mine:
                    sp = _poly_vspan(b["pts"], px)
                    if sp and sp[0] - 0.6 <= py <= sp[1] + 0.6 and 0.3 < sp[1] - sp[0] < 0.6 * yspan:
                        lo, hi = sorted((_val(yfit, sp[1]), _val(yfit, sp[0])))
                        res = [lo, hi]
                        break
            out.append(res)
            hit += res is not None
        if hit >= max(2, math.ceil(0.8 * len(out))):
            s["bands"] = out


_BAND_KINDS = [  # (regex inside a sentence about shading/bands/error bars, main aggregation, lower, upper, note)
    (r"(\d{2}(?:\.\d)?)\s*%\s*(?:confidence|c\.?i\b)", "mean", "lower_{0}CI", "upper_{0}CI", "{0}% confidence band endpoint"),
    (r"standard error|\bs\.?e\.?m?\b", "mean", "mean_minus_se", "mean_plus_se", "standard-error band endpoint"),
    (r"standard deviation|\bstd\b|\bs\.d\.", "mean", "mean_minus_std", "mean_plus_std", "standard-deviation band endpoint"),
    (r"\bmin(?:imum)?\b[^.]{0,40}\bmax(?:imum)?\b|\brange\b", None, "min", "max", "min-max band endpoint"),
]


def band_kind(text):
    """[faktura-patch] what the shaded bands mean, from the paper's own wording (caption/body); generic default."""
    for sent in re.split(r"(?<=[.;])\s+", " ".join((text or "").split())):
        if not re.search(r"(?i)shad(?:ed|ing)|\bbands?\b|error bars?|whiskers?|confidence interval", sent):
            continue
        for rx, main, lo, hi, note in _BAND_KINDS:
            m = re.search(rx, sent, re.I)
            if m:
                g = m.groups()
                return {"main": main, "lower": lo.format(*g), "upper": hi.format(*g), "note": note.format(*g)}
    return {"main": None, "lower": "band_lower", "upper": "band_upper", "note": "shaded band endpoint"}


def _close(items):
    try:
        a, b = items[0][1], items[-1][-1]
        return abs(a.x - b.x) < 0.3 and abs(a.y - b.y) < 0.3
    except AttributeError:
        return False


def _subpaths(items):
    """split a path's items into connected runs (a new 'move' breaks the run)."""
    runs, cur = [], []
    for it in items:
        if cur:
            prev = cur[-1][-1]
            if abs(prev.x - it[1].x) > 0.05 or abs(prev.y - it[1].y) > 0.05:
                runs.append(cur)
                cur = []
        cur.append(it)
    if cur:
        runs.append(cur)
    return runs


def _verts(items):
    pts = []
    for it in items:
        if it[0] == "l":
            if not pts:
                pts.append((it[1].x, it[1].y))
            pts.append((it[2].x, it[2].y))
        elif it[0] == "c":
            if not pts:
                pts.append((it[1].x, it[1].y))
            pts.append((it[4].x, it[4].y))
    return pts


def _chain(segs, tol=0.15):
    """join single segments with touching endpoints and same colour into polylines (>=3 vertices)."""
    cell = tol * 4
    by_start = defaultdict(list)
    for i, s in enumerate(segs):
        by_start[(round(s["p"][0] / cell), round(s["p"][1] / cell))].append(i)
    used, out = set(), []
    for i, s in enumerate(segs):
        if i in used:
            continue
        chain = [i]
        used.add(i)
        cur = s
        while True:
            nxt = None
            kx, ky = round(cur["q"][0] / cell), round(cur["q"][1] / cell)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for j in by_start.get((kx + dx, ky + dy), ()):
                        if j not in used and segs[j]["color"] == s["color"] and \
                                abs(segs[j]["p"][0] - cur["q"][0]) < tol and abs(segs[j]["p"][1] - cur["q"][1]) < tol:
                            nxt = j
            if nxt is None:
                break
            used.add(nxt)
            chain.append(nxt)
            cur = segs[nxt]
        if len(chain) >= 2:
            pts = [segs[chain[0]]["p"]] + [segs[j]["q"] for j in chain]
            out.append({"pts": pts, "color": s["color"], "w": s["w"]})
    return out


# ---------------------------------------------------------------- panels
def _regular_unique(runs, key, min_n):
    """keep runs whose ticks fit a linear/log axis (data labels do not); no node reused."""
    good = []
    for r in runs:
        if len(r) < min_n:
            continue
        f = fit_axis([(n[key], n["v"]) for n in r])
        if f and f["residual"] <= 0.01:
            good.append(r)
    good.sort(key=lambda g: -len(g))
    used, out = set(), []
    for r in good:
        ids = {id(n) for n in r}
        if ids & used:
            continue
        used |= ids
        out.append(r)
    return out


def _find_panels(page, spans):
    xrows, ycols = _tick_groups(spans)
    xruns = [r for g in xrows for r in _split_runs(g, "cx") if _monotone(r)]
    ycruns = [r for g in ycols for r in _split_runs(g, "cy") if _monotone(r)]
    ycruns = _regular_unique(ycruns, "cy", 3)
    xruns = _regular_unique(xruns, "cx", 2)
    panels = []
    for yc in ycruns:
        right = max(n["x1"] for n in yc)
        left = min(n["x0"] for n in yc)
        ytop = min(n["cy"] for n in yc)
        ybot = max(n["cy"] for n in yc)
        cand = []
        for xr in xruns:
            top = min(n["y0"] for n in xr)
            xmin = min(n["cx"] for n in xr)
            if top >= ybot - 6 and right - 8 <= xmin <= right + 70 and top - ybot < 450:
                cand.append((top - ybot + 3 * abs(xmin - right - 10), xr))
        xr = min(cand, key=lambda c: c[0])[1] if cand else None
        panels.append({"yc": yc, "xr": xr, "right": right, "left": left, "ytop": ytop, "ybot": ybot})
    return panels


def _snap(nodes, segs, which):
    """snap tick-label centres to real tick marks / gridlines.  Returns list of coords."""
    key = "cy" if which == "y" else "cx"
    out, offs = [], []
    for n in nodes:
        best = None
        for sg in segs:
            (px, py), (qx, qy) = sg["p"], sg["q"]
            if which == "y":
                if abs(py - qy) > 0.2 or abs(py - n["cy"]) > 3.0 or max(px, qx) < n["x1"] - 1:
                    continue
                if min(px, qx) > n["x1"] + 30:
                    continue
                c = py
            else:
                if abs(px - qx) > 0.2 or abs(px - n["cx"]) > 3.0 or min(py, qy) > n["y1"]:
                    continue
                if max(py, qy) < n["y0"] - 14:
                    continue
                c = px
            if best is None or abs(c - n[key]) < abs(best - n[key]):
                best = c
        out.append(best)
        if best is not None:
            offs.append(best - n[key])
    if len(offs) >= max(2, len(nodes) // 2):
        offs.sort()
        med = offs[len(offs) // 2]
        res = [o if o is not None else n[key] + med for o, n in zip(out, nodes)]
        return res
    return None


def _axis_dict(nodes, key, label, fit, coords=None):
    ticks = [((coords[i] if coords else n[key]), n["v"]) for i, n in enumerate(nodes)]
    steps = [abs(b[1] - a[1]) for a, b in zip(ticks, ticks[1:])]
    return {"scale": fit["scale"], "ticks": ticks, "label_text": label,
            "residual": fit["residual"], "step": min(steps) if steps else None}


def _ranges_for(p, page_rect, segs, others):
    """plot-area bbox of the panel."""
    x0 = p["right"] + 1
    ytop, ybot = p["ytop"], p["ybot"]
    if p["xr"] is not None:
        xs = [n["cx"] for n in p["xr"]]
        x1 = max(xs) + (xs[-1] - xs[0]) / max(len(xs) - 1, 1) * 0.5
        y1 = min(n["y0"] for n in p["xr"]) - 0.5
    else:
        x1 = page_rect.width
        y1 = ybot + 6
        for o in others:
            if o is not p and o["left"] > p["right"] and o["ytop"] < ybot and o["ybot"] > ytop:
                x1 = min(x1, o["left"] - 2)
        best = None
        for s in segs:  # category x axis: look for the baseline segment
            if abs(s["p"][1] - s["q"][1]) < 0.3 and abs(s["q"][0] - s["p"][0]) > 40:
                xa, xb = sorted((s["p"][0], s["q"][0]))
                y = s["p"][1]
                if p["right"] - 5 <= xa <= p["right"] + 15 and ybot - 3 <= y <= ybot + 30:
                    if best is None or y > best[1]:
                        best = (xb, y)
        if best:
            x1, y1 = best
    gap = (ybot - ytop) / max(len(p["yc"]) - 1, 1)
    return (x0, ytop - 0.5 * gap - 1, x1, y1 + 1.5)


def _in(pt, bb, m=0.0):
    return bb[0] - m <= pt[0] <= bb[2] + m and bb[1] - m <= pt[1] <= bb[3] + m


def _axis_label(spans, bb, which):
    cands = []
    for s in spans:
        if parse_num(s["text"]) is not None:
            continue
        x0, y0, x1, y1 = s["bbox"]
        if which == "y" and s["rot"] and x1 < bb[0] and x0 > bb[0] - 60 and y1 > bb[1] - 20 and y0 < bb[3] + 20:
            cands.append((bb[0] - x1, s["text"]))
        if which == "x" and not s["rot"] and bb[3] < y0 < bb[3] + 30 and x1 > bb[0] and x0 < bb[2] + 20:
            cands.append((y0 - bb[3], s["text"]))
    if not cands:
        return None
    cands.sort()
    return cands[0][1]


def _dedup(pts, tol=0.05):
    seen, out = set(), []
    for p in pts:
        k = (round(p[0] / tol) if isinstance(p[0], float) else p[0], round(p[1] / tol))
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def _value_label(spans, cx, ytop, y0, y1):
    """numeric text printed on / just above a bar (data label), or None."""
    best = None
    for sp in spans:
        if sp["rot"]:
            continue
        v = parse_num(sp["text"])
        if v is None:
            continue
        x0, a, x1, b = sp["bbox"]
        if x0 - 1 <= cx <= x1 + 1 and (min(y0, y1) - 14 <= (a + b) / 2 <= max(y0, y1) + 1):
            d = abs((a + b) / 2 - ytop)
            if best is None or d < best[0]:
                best = (d, v)
    return best[1] if best else None


def _cat_label(spans, cx, bb):
    best = None
    for s in spans:
        if s["rot"]:
            continue
        x0, y0, x1, y1 = s["bbox"]
        if bb[3] - 1 <= y0 < bb[3] + 25 and x0 - 3 <= cx <= x1 + 3:
            d = abs((x0 + x1) / 2 - cx)
            if best is None or d < best[0]:
                best = (d, s["text"])
    return best[1] if best else round(cx, 1)


def _fill_series(panel, bb, spans, lines, markers, rects, segs=()):
    xfit, yfit = panel["_fit"]
    xspan, yspan = bb[2] - bb[0], bb[3] - bb[1]
    series = []

    # --- legend swatches: short mark + text immediately right
    legend, swatch = {}, set()

    def near_text(x_right, yc, maxgap=14):
        best = None
        for s in spans:
            if s["rot"] or parse_num(s["text"]) is not None:
                continue
            x0, y0, x1, y1 = s["bbox"]
            if 0 <= x0 - x_right <= maxgap and y0 - 2 <= yc <= y1 + 2:
                if best is None or x0 < best[0]:
                    best = (x0, s["text"])
        return best[1] if best else None

    mk_in = [m for m in markers if _in(m["c"], bb, 2)]
    for i, m in enumerate(mk_in):
        t = near_text(m["c"][0] + m["size"] / 2, m["c"][1])
        if t:
            legend.setdefault(m["color"], t)
            swatch.add(("m", i))
    ln_in = [l for l in lines if all(_in(p, bb, 3) for p in l["pts"])]
    for i, l in enumerate(ln_in):
        a, b = l["pts"][0], l["pts"][-1]
        if abs(a[1] - b[1]) < 1 and abs(b[0] - a[0]) < 0.2 * xspan:
            t = near_text(max(a[0], b[0]), a[1])
            if t:
                legend.setdefault(l["color"], t)
                swatch.add(("l", i))

    for sg in segs:  # single-segment line swatches
        (ax, ay), (bx, by) = sg["p"], sg["q"]
        if abs(ay - by) < 1 and 3 < abs(bx - ax) < 0.2 * xspan and _in((ax, ay), bb, 2) and _in((bx, by), bb, 2):
            t = near_text(max(ax, bx), ay)
            if t:
                legend.setdefault(sg["color"], t)

    def conv(pt, catx=None):
        y = _val(yfit, pt[1])
        x = _val(xfit, pt[0]) if xfit else (catx if catx is not None else round(pt[0], 2))
        return (x, y)

    # --- lines
    for i, l in enumerate(ln_in):
        if ("l", i) in swatch:
            continue
        pts = l["pts"]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if (max(xs) - min(xs)) > 0.9 * xspan and (max(ys) - min(ys)) > 0.9 * yspan and pts[0] == pts[-1]:
            continue  # frame
        if (max(xs) - min(xs) < 0.3 or max(ys) - min(ys) < 0.3) and len(pts) <= 3:
            continue  # grid / axis-aligned
        if len(pts) <= 8 and all(abs(a[0] - b[0]) < 0.3 or abs(a[1] - b[1]) < 0.3 for a, b in zip(pts, pts[1:])):
            continue  # frames, brackets, error-bar caps
        cp = _dedup([conv(p) for p in pts])
        if len(cp) >= 3:
            series.append({"color": l["color"], "kind": "line", "legend_label": legend.get(l["color"]), "points": cp})

    # --- bars / hist
    base_y = -yfit["b"] / yfit["a"] if yfit["scale"] == "linear" else None
    bars = []
    for r in rects:
        x0, y0, x1, y1 = r["r"]
        w, h = x1 - x0, y1 - y0
        if w < 1.0 or h < 0.2 or w > 0.8 * xspan or h > 1.05 * yspan:
            continue
        if not _in(((x0 + x1) / 2, (y0 + y1) / 2), bb, 1):
            continue
        bottoms = [bb[3]] + ([base_y] if base_y is not None else [])
        if any(abs(y1 - b) < 1.5 for b in bottoms):
            bars.append((r, y0))
        elif base_y is not None and abs(y0 - base_y) < 1.5:
            bars.append((r, y1))
    if len(bars) >= 2 or (len(bars) == 1 and xfit is None):
        for col in sorted({b[0]["color"] for b in bars}):
            bs = sorted([b for b in bars if b[0]["color"] == col], key=lambda b: b[0]["r"][0])
            widths = [b[0]["r"][2] - b[0]["r"][0] for b in bs]
            gaps = [bs[i + 1][0]["r"][0] - bs[i][0]["r"][2] for i in range(len(bs) - 1)]
            is_hist = len(bs) >= 4 and max(abs(g) for g in gaps) < 0.6 and (max(widths) - min(widths)) < 0.15 * max(widths)
            pts, labels = [], []
            for r, top in bs:
                cx = (r["r"][0] + r["r"][2]) / 2
                pts.append(conv((cx, top), None if xfit else _cat_label(spans, cx, bb)))
                labels.append(_value_label(spans, cx, top, r["r"][1], r["r"][3]))
            series.append({"color": col, "kind": "hist" if is_hist else "bar",
                           "legend_label": legend.get(col), "points": pts, "printed_labels": labels})

    # --- scatter markers
    bym = defaultdict(list)
    for i, m in enumerate(mk_in):
        if ("m", i) not in swatch:
            bym[m["color"]].append(m)
    for col, ms in bym.items():
        pts = _dedup([conv(m["c"]) for m in ms], 0.02)
        pts.sort(key=lambda p: p[0] if isinstance(p[0], (int, float)) else 0)
        series.append({"color": col, "kind": "scatter", "legend_label": legend.get(col), "points": pts})
    panel["series"] = _dedup_series(series)


def _dedup_series(series):
    """[faktura-patch] one data point -> one row. A line drawn with markers comes out as a 'line' and a same-colour
    'scatter' with the same coordinates; some plotters also draw the same polyline twice. Markers that sit on a
    vertex of a same-colour line are dropped (the line keeps the point); identical same-colour lines are kept once."""
    num = [(x, y) for s in series for x, y in s["points"] if isinstance(x, (int, float)) and isinstance(y, (int, float))]
    if not num:
        return series
    xs, ys = [p[0] for p in num], [p[1] for p in num]
    tx, ty = 0.006 * ((max(xs) - min(xs)) or 1.0), 0.006 * ((max(ys) - min(ys)) or 1.0)

    def same(a, b):
        if isinstance(a[0], (int, float)) and isinstance(b[0], (int, float)):
            return abs(a[0] - b[0]) <= tx and abs(a[1] - b[1]) <= ty
        return a[0] == b[0] and abs(a[1] - b[1]) <= ty

    out = []
    for s in series:
        if s["kind"] == "line":
            if any(o["kind"] == "line" and o["color"] == s["color"] and len(o["points"]) == len(s["points"])
                   and all(same(a, b) for a, b in zip(o["points"], s["points"])) for o in out):
                continue
            out.append(s)
    for s in series:
        if s["kind"] == "line":
            continue
        if s["kind"] == "scatter":
            verts = [p for o in out if o["kind"] == "line" and o["color"] == s["color"] for p in o["points"]]
            if verts:
                rest = [p for p in s["points"] if not any(same(p, v) for v in verts)]
                if not rest:
                    if not any(o.get("legend_label") for o in out if o["color"] == s["color"]) and s.get("legend_label"):
                        for o in out:
                            if o["color"] == s["color"] and o["kind"] == "line":
                                o["legend_label"] = s["legend_label"]
                    continue
                s = dict(s, points=rest)
        out.append(s)
    return out


def _group_figures(panels, caps, pno):
    groups = defaultdict(list)
    for p in panels:
        pb = p["bbox"]
        best = None
        for c in caps:
            cb = c["bbox"]
            pen = 0 if min(pb[2], cb[2]) - max(pb[0], cb[0]) > 0 else 400
            if cb[1] >= pb[3] - 5:
                d = cb[1] - pb[3]
            elif cb[3] <= pb[1] + 5:
                d = (pb[1] - cb[3]) + 150
            else:
                d = 1e6
            if best is None or d + pen < best[0]:
                best = (d + pen, c)
        cap = best[1] if best and best[0] < 500 else None
        p.pop("_fit", None)
        groups[cap["label"] if cap else None].append((p, cap))
    figs = []
    for key, items in groups.items():
        ps = [i[0] for i in items]
        bb = (min(p["bbox"][0] for p in ps), min(p["bbox"][1] for p in ps),
              max(p["bbox"][2] for p in ps), max(p["bbox"][3] for p in ps))
        cap = items[0][1]
        figs.append({"page": pno, "figure_label": key, "caption": cap["text"] if cap else None,
                     "bbox": bb, "panels": ps, "notes": []})
    return figs


def _panel_title(spans, bb):
    """[faktura-patch] text line right above the plot area (candidate panel title) or None.
    A row of numeric tick labels right above the plot = a secondary top axis: the first text line above it is that
    axis' label, the title is the next line up."""
    def inx(s):
        return s["bbox"][2] >= bb[0] and s["bbox"][0] <= bb[2] and not s["rot"]
    ticks = [s for s in spans if inx(s) and parse_num(s["text"]) is not None and bb[1] - 14 <= s["bbox"][3] <= bb[1] + 5]
    lines = {}
    for s in spans:
        if not inx(s) or parse_num(s["text"]) is not None:
            continue
        y1 = s["bbox"][3]
        if not (bb[1] - 45 <= y1 <= bb[1] + 2):
            continue
        key = next((k for k in lines if abs(k - y1) < 2.5), y1)
        lines.setdefault(key, []).append(s)
    order = sorted(lines, reverse=True)  # nearest to the plot first
    if len(ticks) >= 2 and order:
        tick_top = min(t["bbox"][1] for t in ticks)
        order = [k for k in order if k <= tick_top + 2.5]
        order = order[1:]  # skip the top-axis label
    order = [k for k in order if k >= bb[1] - (45 if len(ticks) >= 2 else 28)]
    if not order:
        return None
    parts = sorted(lines[order[0]], key=lambda s: s["bbox"][0])
    t = " ".join(s["text"] for s in parts).strip()
    return t[:80] or None


def digitize_page(page, pno=None, drawings=None):
    pno = (page.number + 1) if pno is None else pno
    spans = _spans(page)
    panels_raw = _find_panels(page, spans)
    if not panels_raw:
        return []
    if drawings is None:
        drawings = page.get_drawings()
    segs, lines, markers, rects = _marks(page, drawings)
    bands = _bands(drawings)  # [faktura-patch]
    out = []
    tickable = list(segs)
    for l in lines:
        for a, b in zip(l["pts"], l["pts"][1:]):
            if abs(a[0] - b[0]) < 0.2 or abs(a[1] - b[1]) < 0.2:
                tickable.append({"p": a, "q": b})
    for p in panels_raw:
        yfit = fit_axis([(n["cy"], n["v"]) for n in p["yc"]])
        if not yfit or yfit["residual"] > 0.02:
            continue
        ycoords = _snap(p["yc"], tickable, "y")
        if ycoords:
            f2 = fit_axis([(c, n["v"]) for c, n in zip(ycoords, p["yc"])])
            if f2 and f2["residual"] <= max(0.01, yfit["residual"]):
                yfit = f2
            else:
                ycoords = None
        xfit = fit_axis([(n["cx"], n["v"]) for n in p["xr"]]) if p["xr"] else None
        xcoords = None
        if xfit and p["xr"]:
            xcoords = _snap(p["xr"], tickable, "x")
            if xcoords:
                f2 = fit_axis([(c, n["v"]) for c, n in zip(xcoords, p["xr"])])
                if f2 and f2["residual"] <= max(0.01, xfit["residual"]):
                    xfit = f2
                else:
                    xcoords = None
        notes = []
        if xfit and xfit["residual"] > 0.02:
            xfit = None
            notes.append("x tick fit rejected")
        bb = _ranges_for(p, page.rect, segs, panels_raw)
        if bb[2] - bb[0] < 30 or bb[3] - bb[1] < 20:
            continue
        panel = {"bbox": tuple(round(v, 2) for v in bb), "notes": notes, "x_axis": None, "series": [],
                 "title": _panel_title(spans, bb),
                 "y_axis": _axis_dict(p["yc"], "cy", _axis_label(spans, bb, "y"), yfit, ycoords), "_fit": (xfit, yfit)}
        if xfit:
            panel["x_axis"] = _axis_dict(p["xr"], "cx", _axis_label(spans, bb, "x"), xfit, xcoords)
        _fill_series(panel, bb, spans, lines, markers, rects, segs)
        if panel["series"]:
            _attach_bands(panel, bb, bands)
            out.append(panel)
        else:
            panel.pop("_fit", None)
    # [faktura-patch] a "title" that is really an axis label (of this or a neighbouring panel), a caption line or a
    # long sentence is not a panel title
    axis_txt = {_norm_title(a.get("label_text")) for p in out for a in (p.get("x_axis"), p.get("y_axis")) if a}
    for p in out:
        t = p.get("title")
        if t and (_norm_title(t) in axis_txt or len(t.split()) > 8 or t.rstrip().endswith(".")
                  or t.lstrip()[:1] in ")]," or re.match(r"(?i)\s*(table|figure|fig\.)\s*\d", t)):
            p["title"] = None
    return _group_figures(out, _captions(page), pno)


def _norm_title(s):
    return re.sub(r"[\W_]+", "", (s or "").lower())


def digitize_pdf(pdf_path, pages=None):
    """-> list[Figure]. Figure['page'] is 1-based."""
    doc = pymupdf.open(pdf_path)
    figs = []
    for i, page in enumerate(doc):
        if pages is not None and (i + 1) not in pages:
            continue
        try:
            figs += digitize_page(page, i + 1)
        except Exception as e:  # keep the pipeline robust
            print(f"[vector_digitize] page {i + 1}: {e!r}", file=sys.stderr)
    return figs


# =====================================================================================================
# [faktura-patch] PART 2 - state assessment (generic signals, no per-paper constants)
# =====================================================================================================
VECTOR_OK = 0.75              # confidence >= -> vector_ok
VECTOR_PARTIAL = 0.40         # confidence >= -> vector_partial; below -> none (low_confidence)
VISION_PREFERRED_BELOW = 0.60  # vector_partial below this prefers the vision model when one is configured
RASTER_FRAC = 0.50            # image area share of the figure region -> raster
MIXED_FRAC = 0.25             # image share that makes a vector figure "mixed" (never vector_ok)
MIN_VECTOR_MARKS = 8          # drawings in the region needed to call it vector graphics
POOR_TEXT_CHARS = 200
POOR_TEXT_BAD = 0.03
CHARTISH_RX = re.compile(
    r"\b(accuracy|recall|precision|f1|scores?|performance|results?|comparison|compare[sd]?|versus|vs|curves?|"
    r"rates?|ablation|latency|cost|loss|trends?|distribution|histogram|improvement|benchmark|evaluation|ratio)\b", re.I)
MD_MARK_RX = re.compile(r"<!-- page (\d+) -->")


def _env_flag(name, default):
    v = os.environ.get(name)
    return default if v is None or v.strip() == "" else v.strip()


def _union_area(rects, clip=None):
    """exact union area of axis-aligned rects (x0,y0,x1,y1) by coordinate compression; clip optional."""
    rs = []
    for r in rects[:80]:
        x0, y0, x1, y1 = r
        if clip is not None:
            x0, y0, x1, y1 = max(x0, clip[0]), max(y0, clip[1]), min(x1, clip[2]), min(y1, clip[3])
        if x1 > x0 and y1 > y0:
            rs.append((x0, y0, x1, y1))
    if not rs:
        return 0.0
    xs = sorted({r[0] for r in rs} | {r[2] for r in rs})
    area = 0.0
    for a, b in zip(xs, xs[1:]):
        iv = sorted((r[1], r[3]) for r in rs if r[0] <= a and r[2] >= b)
        cur_end, tot = None, 0.0
        for y0, y1 in iv:
            if cur_end is None or y0 > cur_end:
                tot += y1 - y0
                cur_end = y1
            elif y1 > cur_end:
                tot += y1 - cur_end
                cur_end = y1
        area += tot * (b - a)
    return area


def _image_rects(page):
    out = []
    try:
        for im in page.get_image_info():
            x0, y0, x1, y1 = im["bbox"]
            if (x1 - x0) * (y1 - y0) >= 900:
                out.append((x0, y0, x1, y1))
    except Exception:  # noqa: BLE001
        pass
    return out


def _md_pages(md):
    out, cur = {}, 1
    for part in re.split(r"(<!-- page \d+ -->)", md or ""):
        m = MD_MARK_RX.match(part)
        if m:
            cur = int(m.group(1))
        else:
            out[cur] = out.get(cur, "") + part
    return out


def page_state(page, md_text=None, drawings=None, img_rects=None):
    """per-page facts: text layer quality, vector path count, image area share, OCR text presence."""
    text = page.get_text()
    chars = len(text.strip())
    bad = text.count("�") + 6 * len(re.findall(r"\(cid:\d+\)", text))
    ratio = bad / max(len(text), 1)
    if chars < 20:
        layer = "none"
    elif chars < POOR_TEXT_CHARS or ratio > POOR_TEXT_BAD:
        layer = "poor"
    else:
        layer = "ok"
    if drawings is None:
        drawings = page.get_drawings()
    if img_rects is None:
        img_rects = _image_rects(page)
    prect = page.rect
    parea = max(prect.width * prect.height, 1.0)
    return {"page": page.number + 1, "text_layer": layer, "chars": chars, "bad_glyph_ratio": round(ratio, 4),
            "vector_paths": len(drawings), "raster_frac": round(min(1.0, _union_area(img_rects) / parea), 3),
            "ocr_used": True, "ocr_chars": len((md_text or "").strip()) if md_text is not None else None}


def _elements(page, drawings, img_rects):
    """figure-ish elements as (kind, x0, y0, x1, y1): images and vector drawings (page rules / backgrounds skipped)."""
    pw, ph = page.rect.width, page.rect.height
    els = []
    for r in img_rects:
        if (r[2] - r[0]) * (r[3] - r[1]) < 0.85 * pw * ph:
            els.append(("img",) + tuple(r))
    for d in drawings:
        r = d["rect"]
        w, h = r.width, r.height
        if (w > 0.92 * pw and h < 3) or (w < 0.5 and h < 0.5) or w * h > 0.85 * pw * ph:
            continue
        els.append(("vec", r.x0, r.y0, r.x1, r.y1))
    return els


def _figure_region(page, cap_bbox, els, max_h=600.0):
    """bbox of the graphics (images + vector marks) adjacent to a caption block, or None.
    Looks above the caption first, then below; grows over contiguous elements (gap <= 22pt)."""
    cx0, cy0, cx1, cy1 = cap_bbox

    def hov(e, x0, x1):
        return min(e[3], x1) - max(e[1], x0) > 0

    best = None
    for side in ("above", "below"):
        if side == "above":
            seeds = [e for e in els if cy0 - 45 <= e[4] <= cy0 + 4 and hov(e, cx0 - 5, cx1 + 5)]
        else:
            seeds = [e for e in els if cy1 - 4 <= e[2] <= cy1 + 45 and hov(e, cx0 - 5, cx1 + 5)]
        if not seeds:
            continue
        reg = [min(e[1] for e in seeds), min(e[2] for e in seeds), max(e[3] for e in seeds), max(e[4] for e in seeds)]
        gap = min(abs(e[4] - cy0) if side == "above" else abs(e[2] - cy1) for e in seeds)
        for _ in range(8):
            changed = False
            for e in els:
                if (side == "above" and e[4] > cy0 + 4) or (side == "below" and e[2] < cy1 - 4):
                    continue
                if e[2] < reg[1] - 22 or e[4] > reg[3] + 22 or e[1] > reg[2] + 40 or e[3] < reg[0] - 40:
                    continue
                n = [min(reg[0], e[1]), min(reg[1], e[2]), max(reg[2], e[3]), max(reg[3], e[4])]
                if n[3] - n[1] > max_h:
                    continue
                if n != reg:
                    reg, changed = n, True
            if not changed:
                break
        if best is None or gap < best[0]:
            best = (gap, reg)
    if best is None:
        return None
    reg = best[1]
    area = max((reg[2] - reg[0]) * (reg[3] - reg[1]), 1.0)
    imgs = [(e[1], e[2], e[3], e[4]) for e in els if e[0] == "img"]
    n_vec = sum(1 for e in els if e[0] == "vec" and e[1] >= reg[0] - 1 and e[3] <= reg[2] + 1
                and e[2] >= reg[1] - 1 and e[4] <= reg[3] + 1)
    return {"bbox": tuple(round(v, 1) for v in reg), "raster_frac": round(min(1.0, _union_area(imgs, reg) / area), 3),
            "n_vec": n_vec, "area": round(area, 1)}


def _axis_quality(a):
    if not a:
        return 0.0
    n = len(a.get("ticks") or [])
    res = a.get("residual")
    res = 0.0 if res is None else res
    return min(1.0, n / 4.0) * max(0.0, 1.0 - res / 0.01)


def _axis_bounds(a):
    vs = [t[1] for t in (a.get("ticks") or [])]
    if not vs:
        return None
    lo, hi = min(vs), max(vs)
    if a.get("scale") == "log" and lo > 0:
        lo, hi = math.log10(lo), math.log10(hi)
    return lo, hi


def _inside(v, a, bounds, margin=0.25):
    if bounds is None or not isinstance(v, (int, float)):
        return True
    if a.get("scale") == "log":
        if v <= 0:
            return False
        v = math.log10(v)
    lo, hi = bounds
    r = (hi - lo) or 1.0
    return lo - margin * r <= v <= hi + margin * r


def panel_signals(p):
    """generic confidence signals of one digitized panel (all in 0..1) and their weighted sum."""
    ya, xa = p.get("y_axis"), p.get("x_axis")
    series = p.get("series") or []
    pts = [(x, y) for s in series for x, y in s["points"]]
    yq = _axis_quality(ya)
    if xa:
        xq = _axis_quality(xa)
    else:
        cats = [x for x, _ in pts if isinstance(x, str)]
        xq = 0.7 * (len(cats) / len(pts)) if pts else 0.0
    nmax = max([len(s["points"]) for s in series] or [0])
    series_q = 1.0 if nmax >= 2 else (0.6 if nmax == 1 else 0.0)
    legend_q = 1.0 if len(series) <= 1 else sum(1 for s in series if s.get("legend_label")) / len(series)
    yb = _axis_bounds(ya) if ya else None
    xb = _axis_bounds(xa) if xa else None
    ins = [(_inside(y, ya, yb) if ya else True) and (_inside(x, xa, xb) if xa else True) for x, y in pts]
    inside_q = sum(ins) / len(ins) if ins else 0.0
    axes = 0.6 * yq + 0.4 * xq
    conf = 0.40 * axes + 0.25 * series_q + 0.15 * legend_q + 0.20 * inside_q
    return {"confidence": round(conf, 3), "y_axis_q": round(yq, 3), "x_axis_q": round(xq, 3),
            "series_q": series_q, "legend_q": round(legend_q, 3), "inside_q": round(inside_q, 3),
            "points": len(pts), "y_ticks": len((ya or {}).get("ticks") or []),
            "x_ticks": len((xa or {}).get("ticks") or []), "series": len(series)}


def _expected_panels(caption):
    letters = set(re.findall(r"\(([a-h])\)", caption or ""))
    return len(letters) if len(letters) >= 2 else 1


def classify_figure(fig, region=None):
    """digitizer Figure (+ geometric region stats) -> (state, confidence, signals, reason)."""
    panels = [p for p in fig.get("panels", []) if p.get("series")]
    npts = sum(len(s["points"]) for p in panels for s in p["series"])
    rf = (region or {}).get("raster_frac", 0.0)
    if not panels or npts == 0:
        return "none", 0.0, {}, "no_series"
    sigs = [panel_signals(p) for p in panels]
    w = [max(1, s["points"]) for s in sigs]
    conf = sum(s["confidence"] * wi for s, wi in zip(sigs, w)) / sum(w)
    reasons = []
    exp = _expected_panels(fig.get("caption"))
    if exp >= 2 and len(panels) < exp:
        conf *= len(panels) / exp
        reasons.append(f"panels_found {len(panels)} < caption_panels {exp}")
    signals = {"panels": len(panels), "points": npts, "raster_frac": rf, "panel_signals": sigs}
    if rf >= RASTER_FRAC and npts < 5:
        return "raster", round(conf, 3), signals, "image_dominates_region"
    if rf >= MIXED_FRAC:
        conf = min(conf, VECTOR_OK - 0.01)
        reasons.append("mixed_vector_and_raster")
    conf = round(conf, 3)
    if conf >= VECTOR_OK:
        return "vector_ok", conf, signals, "; ".join(reasons) or "calibrated_axes_and_series"
    if conf >= VECTOR_PARTIAL:
        weak = [k for k in ("y_axis_q", "x_axis_q", "series_q", "legend_q", "inside_q")
                if min(s[k] for s in sigs) < 0.6]
        if weak:
            reasons.append("weak: " + ",".join(weak))
        return "vector_partial", conf, signals, "; ".join(reasons) or "partial"
    return "none", conf, signals, "low_confidence"


def assess_document(pdf, md=None, *, pages=None):
    """Per page and per figure: what can be taken directly from the PDF, and how reliably.

    -> {"pages": [page_state...], "figures": [record...], "seconds": float}
    record = {label, page, caption, region, state: vector_ok|vector_partial|raster|none, confidence, signals,
              reason, points, fig (digitizer Figure | None), method, rows}
    Figures are found from captions ("Figure N"/"Fig. N" blocks) with graphics next to them, plus uncaptioned
    digitized panels (label None).  Nothing here depends on a particular paper."""
    t0 = time.time()
    doc = pymupdf.open(str(pdf))
    mdp = _md_pages(md) if md else None
    out_pages, recs = [], []
    for i, page in enumerate(doc):
        pno = i + 1
        if pages is not None and pno not in pages:
            continue
        try:
            drawings = page.get_drawings()
            imgs = _image_rects(page)
            pst = page_state(page, mdp.get(pno, "") if mdp is not None else None, drawings, imgs)
        except Exception as e:  # noqa: BLE001
            out_pages.append({"page": pno, "error": f"{type(e).__name__}: {e}"[:160]})
            continue
        out_pages.append(pst)
        try:
            figs = digitize_page(page, pno, drawings)
        except Exception as e:  # noqa: BLE001
            figs = []
            pst["digitize_error"] = f"{type(e).__name__}: {e}"[:160]
        caps = _captions(page)
        els = None
        taken = set()
        for fg in figs:
            if els is None:
                els = _elements(page, drawings, imgs)
            cap = next((c for c in caps if c["label"] == fg["figure_label"]), None)
            region = _figure_region(page, cap["bbox"], els) if cap else None
            if region is None:
                b = fg["bbox"]
                region = {"bbox": (b[0] - 60, b[1] - 20, b[2] + 20, b[3] + 45), "raster_frac": 0.0, "n_vec": 0, "area": 0}
            state, conf, sig, reason = classify_figure(fg, region)
            if state in ("vector_partial", "none") and pst["text_layer"] != "ok":
                reason += f"; page text layer {pst['text_layer']}"
            if fg["figure_label"] is None:
                reason = f"no_caption; {reason}"
            recs.append({"label": fg["figure_label"], "page": pno, "caption": fg.get("caption"),
                         "region": [round(v, 1) for v in region["bbox"]], "state": state, "confidence": conf,
                         "signals": sig, "reason": reason, "points": sig.get("points", 0), "fig": fg,
                         "method": None, "rows": 0})
            if fg["figure_label"]:
                taken.add(fg["figure_label"])
        for cap in caps:
            if cap["label"] in taken:
                continue
            if els is None:
                els = _elements(page, drawings, imgs)
            region = _figure_region(page, cap["bbox"], els)
            if region is None or region["area"] < 2500:
                continue  # text mention of "Figure N", not a figure
            if region["raster_frac"] >= RASTER_FRAC:
                state, reason = "raster", "image_covers_figure_region"
            elif region["n_vec"] >= MIN_VECTOR_MARKS:
                state, reason = "none", "vector_graphics_without_calibratable_axes"
            else:
                state, reason = "none", "no_data_graphics"
            if pst["text_layer"] != "ok" and state != "raster":
                reason += f"; page text layer {pst['text_layer']}"
            taken.add(cap["label"])
            recs.append({"label": cap["label"], "page": pno, "caption": cap["text"],
                         "region": [round(v, 1) for v in region["bbox"]], "state": state, "confidence": 0.0,
                         "signals": {"raster_frac": region["raster_frac"], "vector_marks": region["n_vec"]},
                         "reason": reason, "points": 0, "fig": None, "method": None, "rows": 0})
    if any((r.get("fig") or {}).get("panels") and any(s.get("bands") for pn in r["fig"]["panels"] for s in pn["series"])
           for r in recs):  # [faktura-patch] meaning of shaded bands, from the paper's own text
        try:
            bk = band_kind(" ".join(pg.get_text() for pg in doc))
        except Exception:  # noqa: BLE001
            bk = band_kind("")
        for r in recs:
            r["band_kind"] = bk
    return {"pages": out_pages, "figures": recs, "seconds": round(time.time() - t0, 2)}


def fallback_eligible(rec):
    """can a vision model reasonably help with this figure?"""
    st = rec["state"]
    if st in ("raster", "vector_partial"):
        return True
    return st == "none" and (rec["reason"].startswith("low_confidence")
                             or rec["reason"].startswith("vector_graphics_without_calibratable_axes"))


def chartish(rec):
    return bool(CHARTISH_RX.search(rec.get("caption") or ""))


# =====================================================================================================
# PART 3a - vision fallback (optional, RAG_VISION_MODEL)
# =====================================================================================================
VISION_PROMPT = """You are digitizing ONE scientific figure from a paper. Caption: {caption}

Decide whether the picture is a data chart: line/scatter/bar plot with numeric axes or labelled bars, or a heatmap /
annotated matrix whose cells show numbers. If it is not (diagram, photo, architecture sketch, schematic), answer
{{"is_chart": false, "panels": []}}.
Otherwise estimate the plotted values from the picture using the printed axis tick labels. Return ONLY JSON:
{{"is_chart": true, "panels": [{{"title": str|null,
  "x_axis": {{"label": str|null, "scale": "linear"|"log"|"categorical", "min": number|null, "max": number|null}},
  "y_axis": {{"label": str|null, "scale": "linear"|"log", "min": number|null, "max": number|null}},
  "series": [{{"label": str|null (legend text), "kind": "line"|"scatter"|"bar"|"heatmap", "points": [[x, y], ...]}}]}}]}}
Rules: x is a string for categorical axes (the category label), otherwise a number. One entry per visible data
point/bar/vertex, at most 60 points per series. Values are approximate; do NOT invent points you cannot see.
Heatmap / annotated matrix: kind "heatmap", one series per ROW (label = the row label, prefixed by the y-axis name if
any, e.g. "Lower Threshold=0.3"), points [[column label as string, cell value]] using the number PRINTED in the cell
(skip empty cells); x_axis scale "categorical". Bars with printed value labels: use the printed numbers."""


def render_region_png(pdf, page_no, bbox, dpi=170, max_side=2000):
    doc = pymupdf.open(str(pdf))
    page = doc[page_no - 1]
    r = pymupdf.Rect(*bbox) & page.rect
    if r.is_empty:
        r = page.rect
    scale = dpi / 72.0
    longest = max(r.width, r.height) * scale
    if longest > max_side:
        dpi = dpi * max_side / longest
    return page.get_pixmap(clip=r, dpi=max(int(dpi), 72)).tobytes("png")


def _loose_json(raw):
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    a = s.find("{")
    if a < 0:
        raise ValueError("no JSON object")
    try:  # [faktura-patch] first complete object; models sometimes append a second object or text after it
        return json.JSONDecoder().raw_decode(s[a:])[0]
    except json.JSONDecodeError:
        b = s.rfind("}")
        if b <= a:
            raise
        return json.loads(s[a:b + 1])


def _fnum(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and math.isfinite(v):
        return float(v)
    if isinstance(v, str):
        return parse_num(v)
    return None


def panels_from_vision(js):
    """vision JSON -> digitizer-shaped panels (axes without ticks; points approximate)."""
    panels = []

    def ax(a):
        a = a if isinstance(a, dict) else {}
        return {"scale": a.get("scale") or "linear", "ticks": [], "label_text": a.get("label"),
                "residual": None, "step": None, "range": [_fnum(a.get("min")), _fnum(a.get("max"))]}

    for p in (js or {}).get("panels") or []:
        if not isinstance(p, dict):
            continue
        series = []
        for s in p.get("series") or []:
            if not isinstance(s, dict):
                continue
            pts = []
            for pt in (s.get("points") or [])[:60]:
                if not (isinstance(pt, (list, tuple)) and len(pt) == 2):
                    continue
                y = _fnum(pt[1])
                x = pt[0] if isinstance(pt[0], str) and _fnum(pt[0]) is None else _fnum(pt[0])
                if x is None or y is None:
                    continue
                pts.append((x, y))
            if pts:
                kind = s.get("kind") if s.get("kind") in ("line", "scatter", "bar", "heatmap") else "line"
                series.append({"color": None, "kind": kind, "legend_label": s.get("label"), "points": pts})
        if series:
            panels.append({"bbox": None, "title": p.get("title"), "x_axis": ax(p.get("x_axis")),
                           "y_axis": ax(p.get("y_axis")), "series": series, "notes": ["vision"]})
    return panels


def vision_digitize(client, model, png, caption, label=None):
    """-> (panels, info). Raises on transport/parse errors (caller treats as 'vision unavailable')."""
    b64 = base64.b64encode(png).decode("ascii")
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": VISION_PROMPT.format(caption=(caption or label or "")[:400])},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}]
    # [faktura-patch] provider is always pinned (RAG_VISION_PROVIDER, no fallbacks): an unpinned request may be routed
    # to a provider many times more expensive than the listed price
    provs = [x.strip() for x in os.environ.get("RAG_VISION_PROVIDER", "").split(",") if x.strip()]
    if not provs:
        raise RuntimeError("RAG_VISION_PROVIDER is not set: vision is not called without a pinned provider")
    for attempt in (1, 2):  # [faktura-patch] one retry: cheap vision models sometimes emit malformed JSON
        resp = client.chat.completions.create(model=model, messages=msgs, temperature=0.0, max_tokens=8000,
                                              response_format={"type": "json_object"},
                                              extra_body={"provider": {"order": provs, "allow_fallbacks": False}})
        try:
            js = _loose_json(resp.choices[0].message.content or "")
            break
        except ValueError:
            if attempt == 2:
                raise
    if not js.get("is_chart", True):
        return [], {"is_chart": False}
    return panels_from_vision(js), {"is_chart": True}


def run_fallbacks(asm, pdf, client=None, vision_model=None, *, workers=4, max_calls=None, vision_fn=None):
    """Decide the extraction method per figure and run the vision fallback where needed.
    Sets rec['method'] (vector|vision|none), rec['use_panels'], rec['reason'], rec['issue'] (bool).
    vector_ok -> vector.  vector_partial -> vector when confidence >= VISION_PREFERRED_BELOW or no vision;
    RAG_FIGURE_PARTIAL=drop turns the no-vision case into 'none'.  raster / uncalibrated -> vision if configured
    and working, otherwise 'none' + figure_only issue.  Vision failures behave as 'vision unavailable'."""
    t0 = time.time()
    if vision_model is None:
        vision_model = os.environ.get("RAG_VISION_MODEL", "").strip() or None
    use_vision = bool(vision_model and (client is not None or vision_fn is not None))
    partial_policy = _env_flag("RAG_FIGURE_PARTIAL", "keep")
    if max_calls is None:
        max_calls = int(_env_flag("RAG_FIGURE_VISION_MAX", "8"))
    todo = []
    for rec in asm["figures"]:
        rec.setdefault("issue", False)
        st = rec["state"]
        if not rec.get("label"):
            rec["method"], rec["reason"] = "none", rec["reason"] if rec["reason"].startswith("no_caption") else "no_caption"
            continue
        if st == "vector_ok":
            rec["method"], rec["use_panels"] = "vector", rec["fig"]["panels"]
        elif st == "vector_partial" and (not use_vision or rec["confidence"] >= VISION_PREFERRED_BELOW):
            if partial_policy == "drop" and not use_vision:
                rec["method"] = "none"
                rec["reason"] += "; dropped (RAG_FIGURE_PARTIAL=drop, no vision model)"
                rec["issue"] = True
            else:
                rec["method"], rec["use_panels"] = "vector", rec["fig"]["panels"]
        elif fallback_eligible(rec):
            if use_vision and len(todo) < max_calls:
                todo.append(rec)
            elif st == "vector_partial":
                rec["method"], rec["use_panels"] = "vector", rec["fig"]["panels"]  # nothing better is available
            else:
                rec["method"] = "none"
                rec["reason"] += "; vision call budget exhausted" if use_vision else "; no vision model configured"
                rec["issue"] = chartish(rec) or rec["reason"].startswith("low_confidence")
        else:
            rec["method"] = "none"

    def go(rec):
        try:
            png = render_region_png(pdf, rec["page"], rec["region"]) if vision_fn is None else b""
            if vision_fn is not None:
                panels, info = vision_fn(rec)
            else:
                panels, info = vision_digitize(client, vision_model, png, rec.get("caption"), rec.get("label"))
            return rec, panels, None
        except Exception as e:  # noqa: BLE001 - any vision failure == vision unavailable
            return rec, None, f"{type(e).__name__}: {e}"[:200]

    if todo:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, 4))) as pool:
            results = list(pool.map(go, todo))
        for rec, panels, err in results:
            keep_vec = rec["state"] == "vector_partial"
            if err is not None or not panels:
                why = f"vision error: {err}" if err is not None else "vision: no data chart found"
                if err is not None:
                    rec["vision_error"] = err
                if keep_vec:
                    rec["method"], rec["use_panels"] = "vector", rec["fig"]["panels"]
                    rec["reason"] += f"; {why}, kept vector points"
                else:
                    rec["method"], rec["reason"] = "none", f"{rec['reason']}; {why}"
                    rec["issue"] = err is not None
            else:
                rec["method"], rec["use_panels"] = "vision", panels
                rec["reason"] += "; digitized by vision model"
    asm["vision"] = {"model": vision_model if use_vision else None, "calls": len(todo),
                     "seconds": round(time.time() - t0, 2)}
    return asm


# =====================================================================================================
# PART 3b - semantic mapping (1 LLM text call per figure with data) and row generation
# =====================================================================================================
_ENUM_AXIS_MEANING = ["metric_k", "hyperparameter", "training_size", "step", "category", "none"]
_ENUM_DIR = ["higher_is_better", "lower_is_better", "target"]


def _S(enum=None):
    base = {"type": "string"}
    if enum:
        base["enum"] = enum
    return {"anyOf": [base, {"type": "null"}]}


def _obj(props):
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


MAPPING_SCHEMA = _obj({"series": {"type": "array", "items": _obj({
    "series_id": {"type": "string"},
    "is_result": {"type": "boolean"},
    "skip_reason": _S(),
    "series_name": _S(),
    "metric_name": _S(),
    "metric_variant": _S(),
    "metric_direction": _S(_ENUM_DIR),
    "metric_group": _S(),
    "metric_unit": _S(),
    "metric_axis": _S(["y", "x"]),
    "other_axis_meaning": _S(_ENUM_AXIS_MEANING),
    "other_axis_name": _S(),
    "config_id": _S(),
    "dataset_id": _S(),
    "task_id": _S(),
    "experiment_id": _S(),
    "category_map": {"type": "array", "items": _obj({"category": {"type": "string"}, "config_id": _S(),
                                                     "dataset_id": _S()})},
})}})

MAPPING_SYSTEM = """You map chart series of a RAG paper onto rows of a results table. You never output data points -
only per-series semantics; the program turns every digitized point into a row. Answer with JSON only, in English.
Keys of the schema are fixed. Ids (config_id, dataset_id, task_id, experiment_id) MUST come from the 'already
extracted' context or be null - never invent an id; a wrong id is worse than null."""

MAPPING_TASK = """Figure {label} (page {page}). Caption: {caption}

Figure data (JSON; each series has a series_id, legend label, colour, kind, number of points, x/y ranges and a few
sample points; axis labels and scales; panel titles are only candidates taken from the text above the plot):
{figure}

Already extracted from this paper (JSON): {context}

For EVERY series_id return one entry:
- is_result: true if the series shows measured results of the paper's systems/configurations (a curve, bars, points,
  or a horizontal line of a NAMED baseline/configuration). false for decoration or non-results: confidence bands,
  grid, y=x diagonals, ideal/threshold markers, histograms of raw data counts that are not a metric, annotations,
  legend swatches. Give skip_reason when false.
- metric_name: the metric ITSELF as the paper names it (axis label / caption / legend), WITHOUT dataset/benchmark,
  model or system names (they belong to dataset_id / config_id: axis 'QuaLITY Accuracy (%)' -> 'Accuracy'),
  WITHOUT units or '(%)', WITHOUT '@k' (put k to metric_k via other_axis_meaning). Keep standard abbreviations (EM,
  F1, NDCG, MRR, ROUGE-L). Derived quantities: '<metric> relative improvement' / '<metric> delta' as the paper says. metric_variant: how it is defined if stated (e.g. 'macro', 'LLM judge'),
  else null. metric_direction: higher_is_better / lower_is_better / target / null. metric_group: free text such as
  'answer quality', 'retrieval quality', 'efficiency'. metric_unit: '%' when the axis is in percent, 'fraction' for
  0-1 scores, otherwise the printed unit (ms, tokens...) or null.
- metric_axis: which axis carries the metric ('y' normally; 'x' for horizontal charts).
- other_axis_meaning: what the other axis is: metric_k (cut-off k of the metric such as Recall@k), hyperparameter
  (a parameter being swept, e.g. top_k of the retriever, chunk size, threshold, number of trajectories),
  training_size (training budget/data size), step (epoch/iteration/time), category (categorical x: configurations,
  datasets, models, question types), none (reference line or a single point). other_axis_name: its name as printed.
- config_id / dataset_id / task_id / experiment_id: from the context by meaning of the legend label, panel title and
  caption; null when the series covers several configurations or nothing matches.
- category_map: only for category axes; one item per category label of the series: category (exactly as given),
  config_id and dataset_id from the context or null. Otherwise [].
- series_name: short label for notes (legend text or a description)."""


def _sig(v, n=3):
    if v is None:
        return None
    if v == 0:
        return "0"
    dec = max(0, n - 1 - int(math.floor(math.log10(abs(v)))))
    return f"{v:.{min(dec, 8)}f}"


def _sigf(v, n=4):
    s = _sig(v, n)
    return float(s) if s is not None else None


def _sample(points, n=6):
    if len(points) <= n:
        return list(points)
    idx = sorted({round(i * (len(points) - 1) / (n - 1)) for i in range(n)})
    return [points[i] for i in idx]


def _rng(vals):
    nums = [v for v in vals if isinstance(v, (int, float))]
    return [_sigf(min(nums)), _sigf(max(nums))] if nums else None


def _axis_info(a):
    if not a:
        return None
    rng = None
    if a.get("ticks"):
        vs = [t[1] for t in a["ticks"]]
        rng = [_sigf(min(vs)), _sigf(max(vs))]
    elif a.get("range"):
        rng = list(a["range"])
    return {"label": a.get("label_text"), "scale": a.get("scale"), "range": rng}


def build_figure_context(rec):
    """compact description of a figure's data for the mapping prompt (never the full point list)."""
    out = {"panels": []}
    for pi, p in enumerate(rec["use_panels"]):
        ser = []
        for si, s in enumerate(p["series"]):
            pts = s["points"]
            xs = [x for x, _ in pts]
            ys = [y for _, y in pts]
            cats = [x for x in xs if isinstance(x, str)]
            item = {"series_id": f"p{pi}s{si}", "legend_label": s.get("legend_label"), "color": s.get("color"),
                    "kind": s.get("kind"), "n_points": len(pts), "y_range": _rng(ys),
                    "samples": [[(x if isinstance(x, str) else _sigf(x)), _sigf(y)] for x, y in _sample(pts)]}
            if cats:
                item["categories"] = list(dict.fromkeys(cats))[:20]
            else:
                item["x_range"] = _rng(xs)
            ser.append(item)
        out["panels"].append({"panel": pi, "title": p.get("title"), "x_axis": _axis_info(p.get("x_axis")),
                              "y_axis": _axis_info(p.get("y_axis")), "series": ser})
    return out


def map_figure(rec, context_json, llm):
    """one LLM call -> {series_id: mapping entry}.  llm(messages, schema_name, schema, label) -> dict."""
    task = MAPPING_TASK.format(label=rec["label"], page=rec["page"], caption=(rec.get("caption") or "")[:300],
                               figure=json.dumps(build_figure_context(rec), ensure_ascii=False, separators=(",", ":")),
                               context=context_json)
    res = llm([{"role": "system", "content": MAPPING_SYSTEM}, {"role": "user", "content": task}],
              "figure_mapping", MAPPING_SCHEMA, f"figure:{rec['label']}")
    out = {}
    for m in (res or {}).get("series", []) if isinstance(res, dict) else []:
        if isinstance(m, dict) and isinstance(m.get("series_id"), str):
            out[m["series_id"]] = m
    return out


def _norm_name(s):
    return re.sub(r"[\W_]+", "", (s or "").lower())


def _fmt_value(v, axis):
    """printed precision: ~1/200 of the axis tick step for linear axes, 4 significant digits otherwise."""
    step = (axis or {}).get("step")
    if step and (axis or {}).get("scale") != "log":
        dec = max(0, min(6, math.ceil(-math.log10(step / 200.0))))
        return f"{v:.{dec}f}"
    return _sig(v, 4)


def _tol(axis):
    step = (axis or {}).get("step")
    if step and (axis or {}).get("scale") != "log":
        return step / 100.0
    return None


def caption_fragment(caption, label):
    """short verbatim start of the caption (the gold uses 'Figure 3: Comparison of WMV'-style fragments)."""
    if not caption:
        return label
    t = " ".join(caption.split())
    if len(t) <= 48:
        return t
    cut = t[:48]
    return cut[:cut.rfind(" ")] if " " in cut[20:] else cut


def generate_rows(rec, mapping, ids, pub_id, *, max_rows=12000, start=1):
    """digitized series + semantic mapping -> RAG_RESULT rows (temporary ids 'fig<N>').
    ids: {'configs','datasets','tasks','experiments'} sets of valid ids.  Returns (rows, stats)."""
    vision = rec["method"] == "vision"
    rows, skipped, truncated = [], 0, False
    multi = len(rec["use_panels"]) > 1
    frag = caption_fragment(rec.get("caption"), rec["label"])
    for pi, p in enumerate(rec["use_panels"]):
        ya, xa = p.get("y_axis"), p.get("x_axis")
        for si, s in enumerate(p["series"]):
            m = mapping.get(f"p{pi}s{si}")
            if not m or not m.get("is_result"):
                skipped += 1
                continue
            axis = m.get("metric_axis") or "y"
            maxis, oaxis = (ya, xa) if axis == "y" else (xa, ya)
            name = m.get("metric_name") or (maxis or {}).get("label_text")
            if not name:
                skipped += 1
                continue
            # [faktura-patch] cut-off printed on the axis label / caption ("NDCG@5") but dropped from the name -> metric_k
            name_k = None
            if "@" not in name:
                for src in ((maxis or {}).get("label_text"), rec.get("caption")):
                    mk = re.search(r"([A-Za-z][\w\-]*)\s*@\s*(\d+)", src or "")
                    if mk and _norm_name(mk.group(1)) and _norm_name(mk.group(1)) in _norm_name(name):
                        name_k = int(mk.group(2))
                        break
            pts = [(x, y) if axis == "y" else (y, x) for x, y in s["points"]]  # (other, metric)
            pts = [(o, v) for o, v in pts if isinstance(v, (int, float))]
            if not pts:
                continue
            tol = None if vision else _tol(maxis)
            vals = [v for _, v in pts]
            constant = s.get("kind") == "line" and len(pts) >= 2 and (max(vals) - min(vals)) <= (tol or 1e-9 * (abs(vals[0]) + 1))
            if constant:
                pts = pts[:1]
            meaning = m.get("other_axis_meaning") or "none"
            oname = m.get("other_axis_name") or (oaxis or {}).get("label_text")
            cmap = {_norm_name(c["category"]): c for c in m.get("category_map") or []}
            unit = m.get("metric_unit")
            sname = m.get("series_name") or s.get("legend_label") or f"series {si + 1}"
            fig_name = (rec["label"] or "Figure") + (f" {p['title']}" if multi and p.get("title") else "")
            for pj, (o, v) in enumerate(pts, start=1):
                if len(rows) >= max_rows:
                    truncated = True
                    break
                cfg, ds = m.get("config_id"), m.get("dataset_id")
                variant = m.get("metric_variant")
                k, xnote = name_k, ""
                if constant:
                    xnote = " Constant reference line over the whole x range."
                elif isinstance(o, str):
                    c = cmap.get(_norm_name(o))
                    if c:
                        cfg = c.get("config_id") or cfg
                        ds = c.get("dataset_id") or ds
                    xnote = f" Category: {o}."
                elif meaning == "metric_k":
                    k = int(round(o)) if abs(o - round(o)) <= 0.1 + 0.02 * abs(o) else None
                    xnote = f" x={_sig(o, 4)}{' (' + oname + ')' if oname else ''}."
                elif meaning != "none":
                    xs = _sig(o, 4)
                    if meaning == "hyperparameter" and oname:
                        variant = "; ".join(x for x in (variant, f"{oname}={xs}") if x)
                    xnote = f" x={xs}{' (' + oname + ')' if oname else ''}."
                tolt = f"{tol:g}" if tol else None
                if vision:
                    note = ("approximate, digitized from raster image by vision model; "
                            f"series '{sname}', point {pj}.{xnote}")
                else:
                    note = ("Digitized from original PDF vector geometry calibrated to printed axis ticks; "
                            f"series '{sname}', point {pj}; approximate, tolerance "
                            f"{(tolt + (' ' + unit if unit else '')) if tolt else 'about 0.5% of value'}.{xnote}")
                    if rec["state"] != "vector_ok":
                        note += f" Partially digitized figure (confidence {rec['confidence']:.2f})."
                sv = _sig(v, 3) if vision else _fmt_value(v, maxis)
                fv = float(sv)
                band = None
                if s.get("bands") and axis == "y" and not constant and not vision and pj - 1 < len(s["bands"]):
                    band = s["bands"][pj - 1]
                bk = rec.get("band_kind") or band_kind("")
                rows.append({
                    "publication_id": pub_id, "result_id": f"fig{start + len(rows)}",
                    "experiment_id": m.get("experiment_id") if m.get("experiment_id") in ids["experiments"] else None,
                    "config_id": cfg if cfg in ids["configs"] else None,
                    "dataset_id": ds if ds in ids["datasets"] else None,
                    "task_id": m.get("task_id") if m.get("task_id") in ids["tasks"] else None,
                    "metric_group": m.get("metric_group"), "metric_name": name, "metric_variant": variant,
                    "metric_direction": m.get("metric_direction"), "metric_k": k,
                    "metric_value": fv, "metric_unit": unit, "baseline_value": None, "aggregation": None,
                    "sample_size": None, "raw_value": "~" + sv, "raw_unit": "%" if unit == "%" else None,
                    "normalized_value": fv, "normalized_unit": unit, "result_origin": "primary",
                    "cited_publication_id": None, "source_page": rec["page"], "source_section": None,
                    "source_table": None, "source_figure": fig_name[:80], "source_fragment": frag,
                    "extraction_status": "extracted", "duplicate_of": None, "note": note,
                    "_tol": tol, "_v": v,
                })
                if (not constant and meaning in ("hyperparameter", "training_size")
                        and isinstance(o, (int, float))):  # [faktura-patch] swept setting -> own configuration later
                    rows[-1]["_sweep"] = {"name": oname or meaning, "value": _sig(o, 4), "raw": o,
                                          "base": rows[-1]["config_id"], "series": sname, "figure": rec["label"]}
                if band:  # [faktura-patch] shaded band around the series -> two more rows (lower / upper endpoint)
                    rows[-1]["aggregation"] = bk["main"]
                    base = rows[-1]
                    for agg, bv in ((bk["lower"], band[0]), (bk["upper"], band[1])):
                        if len(rows) >= max_rows:
                            truncated = True
                            break
                        bsv = _fmt_value(bv, maxis)
                        rows.append(dict(base,
                                         result_id=f"fig{start + len(rows)}", aggregation=agg,
                                         metric_value=float(bsv), normalized_value=float(bsv), raw_value="~" + bsv,
                                         note=note + f" {bk['note'][0].upper() + bk['note'][1:]}.", _v=bv))
            if truncated:
                break
        if truncated:
            break
    return rows, {"skipped_series": skipped, "truncated": truncated}


def map_and_generate(asm, pub_id, context_json, ids, llm, *, workers=4, max_rows=None, max_rows_total=None):
    """Semantic mapping (parallel, <=4 calls) + row generation for every figure that has data.
    -> (rows, issues, stats).  Per-figure failures become state/issues."""
    t0 = time.time()
    max_rows = max_rows or int(_env_flag("RAG_FIGURE_MAX_ROWS", "12000"))
    max_total = max_rows_total or int(_env_flag("RAG_FIGURE_MAX_ROWS_TOTAL", "40000"))
    work = [r for r in asm["figures"] if r.get("method") in ("vector", "vision") and r.get("use_panels")]

    def go(rec):
        try:
            return rec, map_figure(rec, context_json, llm), None
        except Exception as e:  # noqa: BLE001
            return rec, None, f"{type(e).__name__}: {e}"[:200]

    rows = []
    if work:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, 4))) as pool:
            res = list(pool.map(go, work))
        for rec, mapping, err in res:  # deterministic order
            if err is not None:
                rec["method"], rec["reason"], rec["issue"] = "none", f"{rec['reason']}; mapping failed: {err}", True
                continue
            room = max(0, max_total - len(rows))
            r, st = generate_rows(rec, mapping, ids, pub_id, max_rows=min(max_rows, room), start=len(rows) + 1)
            rec["rows"] = len(r)
            rec["mapping"] = {"series": sum(len(p["series"]) for p in rec["use_panels"]),
                              "result_series": sum(1 for m in mapping.values() if m.get("is_result")), **st}
            if st["truncated"]:
                rec["reason"] += f"; rows truncated at {len(r)}"
            if not r:
                rec["reason"] += "; no result series after mapping"
            rows.extend(r)
    return rows, figure_issues(asm, pub_id), {"figures_mapped": len(work), "rows": len(rows),
                                              "seconds": round(time.time() - t0, 2)}


def figure_issues(asm, pub_id, limit=6):
    """figure_only issues for figures whose numbers could not be taken (never silently lose a data figure)."""
    out = []
    for rec in asm["figures"]:
        if not rec.get("issue") or not rec.get("label"):
            continue
        why = {"raster": "it is a raster image", "vector_partial": "its vector digitization is unreliable",
               "none": "its axes could not be calibrated"}.get(rec["state"], "it could not be digitized")
        out.append({"publication_id": pub_id, "issue_id": None, "record_ids": None, "check_name": "figure_only",
                    "description": (f"{rec['label']} on page {rec['page']} reports numeric results only as a plot; "
                                    f"{why} and no values could be extracted from it ({rec['reason'][:110]}), so "
                                    f"its values are not in the dataset."),
                    "source_page": rec["page"], "source_fragment": caption_fragment(rec.get("caption"), rec["label"])})
    return out[:limit]


# =====================================================================================================
# PART 3c - merge with table/prose rows, meta
# =====================================================================================================
def _rnum(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.match(r"^[<>~≈≥≤+\s]*(-?\d*[.,]?\d+)", str(v).replace("−", "-"))
    return float(m.group(1).replace(",", ".")) if m else None


def _vclose(a, b, tol):
    if a is None or b is None:
        return False
    t = max(tol or 0.0, 0.005 * max(abs(a), abs(b)), 1e-9)
    return abs(a - b) <= t


def merge_figure_rows(rows, fig_rows):
    """Append figure rows after table/prose rows.  A figure point equal to an existing table/prose row (same
    metric name and k, compatible config/dataset, value within tolerance) is kept but linked with
    duplicate_of -> the table/prose row (as the gold does: the figure row repeats a printed number).
    Figure rows with no config are linked only when exactly one candidate matches.  Returns the number linked."""
    idx = defaultdict(list)
    by_id = {r["result_id"]: r for r in rows}
    for r in rows:
        v = _rnum(r.get("normalized_value") if r.get("normalized_value") is not None else r.get("raw_value"))
        if v is None or not r.get("metric_name") or r.get("source_figure"):
            continue
        idx[(_norm_name(r["metric_name"]), _rnum(r.get("metric_k")))].append((r, v))
    linked = 0
    for fr in fig_rows:
        v, tol = fr.pop("_v", None), fr.pop("_tol", None)
        if v is None:
            continue
        cands = []
        for r, rv in idx.get((_norm_name(fr["metric_name"]), _rnum(fr.get("metric_k"))), ()):
            if not (_vclose(v, rv, tol) or _vclose(v * 100, rv, tol and tol * 100) or _vclose(v, rv * 100, tol)):
                continue
            if fr.get("config_id") and r.get("config_id") and fr["config_id"] != r["config_id"]:
                continue
            if fr.get("dataset_id") and r.get("dataset_id") and fr["dataset_id"] != r["dataset_id"]:
                continue
            cands.append(r)
        if fr.get("config_id"):
            cands = [r for r in cands if r.get("config_id") == fr["config_id"]]
        elif len(cands) != 1:
            cands = []
        if cands:
            base, seen = cands[0], set()
            while base.get("duplicate_of") and base["duplicate_of"] in by_id and base["result_id"] not in seen:
                seen.add(base["result_id"])
                base = by_id[base["duplicate_of"]]
            fr["duplicate_of"] = base["result_id"]
            linked += 1
    rows.extend(fig_rows)
    return linked


def extraction_state_meta(asm, extra=None):
    """meta.extraction_state: pages + figures (+ counts).  JSON-safe, no digitizer internals."""
    figs, counts = [], defaultdict(int)
    for r in asm["figures"]:
        f = {"label": r.get("label"), "page": r["page"], "state": r["state"], "method": r.get("method"),
             "confidence": r["confidence"], "points": r["points"], "rows": r.get("rows", 0),
             "reason": r["reason"], "region": r["region"]}
        if r.get("vision_error"):
            f["vision_error"] = r["vision_error"]
        figs.append(f)
        counts["state_" + r["state"]] += 1
        counts["method_" + str(r.get("method"))] += 1
    pages = asm["pages"]
    out = {"pages": pages, "figures": figs,
           "counts": {"pages": len(pages), "figures": len(figs), **dict(counts),
                      "pages_text_poor_or_none": sum(1 for p in pages if p.get("text_layer") in ("poor", "none")),
                      "pages_raster_heavy": sum(1 for p in pages if (p.get("raster_frac") or 0) >= 0.5),
                      "rows": sum(r.get("rows", 0) for r in asm["figures"])},
           "vision": asm.get("vision"), "assess_seconds": asm.get("seconds")}
    if extra:
        out.update(extra)
    return out


if __name__ == "__main__":
    for f in digitize_pdf(sys.argv[1]):
        print(f["page"], f["figure_label"], [(s["kind"], len(s["points"])) for p in f["panels"] for s in p["series"]])
