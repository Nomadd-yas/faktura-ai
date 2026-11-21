"""Digitize VECTOR charts in scientific PDFs straight from PDF geometry.

No OCR, no LLM: axes are calibrated from numeric tick-label text spans, marks
come from ``page.get_drawings()``.

    figs = digitize_pdf("paper.pdf")      # -> list[Figure] (plain dicts)

Figure = {page, figure_label, caption, bbox, panels, notes}
panel  = {bbox, x_axis, y_axis, series, notes}
axis   = {scale: linear|log, ticks: [(coord, value)], label_text, step, residual}
series = {color, kind: line|scatter|bar|hist, legend_label, points: [(x, y)]}
         (x is a category string for bars under a text-labelled x axis)

Heuristics only: linear/log axes, lines, scatter markers, bars, histograms,
legend swatches.  Raster figures, 3D plots, rotated tick labels, broken axes
are NOT handled.
"""
from __future__ import annotations

import math
import re
import sys
from collections import defaultdict

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
def _marks(page):
    """returns segs (single straight segments), lines (polylines), markers, rects."""
    segs, lines, markers, rects = [], [], [], []
    for d in page.get_drawings():
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
    panel["series"] = series


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


def digitize_page(page, pno=None):
    pno = (page.number + 1) if pno is None else pno
    spans = _spans(page)
    panels_raw = _find_panels(page, spans)
    if not panels_raw:
        return []
    segs, lines, markers, rects = _marks(page)
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
                 "y_axis": _axis_dict(p["yc"], "cy", _axis_label(spans, bb, "y"), yfit, ycoords), "_fit": (xfit, yfit)}
        if xfit:
            panel["x_axis"] = _axis_dict(p["xr"], "cx", _axis_label(spans, bb, "x"), xfit, xcoords)
        _fill_series(panel, bb, spans, lines, markers, rects, segs)
        if panel["series"]:
            out.append(panel)
        else:
            panel.pop("_fit", None)
    return _group_figures(out, _captions(page), pno)


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


if __name__ == "__main__":
    for f in digitize_pdf(sys.argv[1]):
        print(f["page"], f["figure_label"], [(s["kind"], len(s["points"])) for p in f["panels"] for s in p["series"]])
