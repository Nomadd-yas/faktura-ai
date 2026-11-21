import math
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vector_digitize import digitize_pdf  # noqa: E402

X0, X1, Y0, Y1 = 100.0, 400.0, 400.0, 150.0  # plot area in page coords (y down)


def xm(v):
    return X0 + v * (X1 - X0) / 10


def _axes(page, xticks, yticks, ymap):
    page.draw_line((X0, Y0), (X1, Y0), width=0.8)
    page.draw_line((X0, Y0), (X0, Y1), width=0.8)
    for v in xticks:
        page.draw_line((xm(v), Y0), (xm(v), Y0 + 4), width=0.5)
        page.insert_text((xm(v) - pymupdf.get_text_length(f"{v:g}", fontsize=8) / 2, Y0 + 15), f"{v:g}", fontsize=8)
    for v in yticks:
        y = ymap(v)
        page.draw_line((X0 - 4, y), (X0, y), width=0.5)
        page.insert_text((X0 - 30, y + 3), f"{v:g}", fontsize=8)
    page.insert_text((200, 460), "Figure 1. Synthetic chart.", fontsize=9)


def _digitize(tmp_path, draw):
    doc = pymupdf.open()
    draw(doc.new_page(width=500, height=500))
    p = tmp_path / "t.pdf"
    doc.save(str(p))
    figs = digitize_pdf(str(p))
    assert len(figs) == 1 and figs[0]["figure_label"] == "Figure 1"
    assert len(figs[0]["panels"]) == 1
    return figs[0]["panels"][0]


def test_polyline_linear(tmp_path):
    def ym(v):
        return Y0 - v * (Y0 - Y1) / 100

    data = [(1, 10), (3, 35), (5, 50), (8, 80), (10, 95)]

    def draw(page):
        _axes(page, [0, 2, 4, 6, 8, 10], [0, 20, 40, 60, 80, 100], ym)
        page.draw_polyline([(xm(x), ym(y)) for x, y in data], color=(1, 0, 0), width=1.2)

    panel = _digitize(tmp_path, draw)
    lines = [s for s in panel["series"] if s["kind"] == "line"]
    assert len(lines) == 1 and len(lines[0]["points"]) == 5
    for (x, y), (ex, ey) in zip(lines[0]["points"], data):
        assert abs(x - ex) <= 0.1 and abs(y - ey) <= 1.0
    assert panel["x_axis"]["scale"] == "linear"


def test_bars(tmp_path):
    def ym(v):
        return Y0 - v * (Y0 - Y1) / 50

    bars = [(1, 12.0), (3, 30.0), (5, 22.5), (7, 45.0)]

    def draw(page):
        _axes(page, [0, 2, 4, 6, 8, 10], [0, 10, 20, 30, 40, 50], ym)
        for x, h in bars:
            page.draw_rect(pymupdf.Rect(xm(x) - 10, ym(h), xm(x) + 10, Y0), color=None, fill=(0, 0.4, 0.8))

    panel = _digitize(tmp_path, draw)
    b = [s for s in panel["series"] if s["kind"] == "bar"]
    assert len(b) == 1 and len(b[0]["points"]) == 4
    for (x, y), (ex, ey) in zip(b[0]["points"], bars):
        assert abs(y - ey) <= 0.5 and abs(x - ex) <= 0.1


def test_scatter_log_y(tmp_path):
    def ym(v):
        return Y0 - math.log10(v) * (Y0 - Y1) / 3

    pts = [(1, 2.0), (4, 30.0), (6, 150.0), (9, 700.0)]

    def draw(page):
        _axes(page, [0, 2, 4, 6, 8, 10], [1, 10, 100, 1000], ym)
        for x, y in pts:
            page.draw_circle((xm(x), ym(y)), 2.5, color=(0, 0.5, 0), fill=(0, 0.5, 0))

    panel = _digitize(tmp_path, draw)
    sc = [s for s in panel["series"] if s["kind"] == "scatter"]
    assert len(sc) == 1 and panel["y_axis"]["scale"] == "log"
    for (x, y), (ex, ey) in zip(sc[0]["points"], pts):
        assert abs(y - ey) <= 0.01 * ey and abs(x - ex) <= 0.1


def test_legend_label(tmp_path):
    def ym(v):
        return Y0 - v * (Y0 - Y1) / 100

    def draw(page):
        _axes(page, [0, 2, 4, 6, 8, 10], [0, 20, 40, 60, 80, 100], ym)
        page.draw_polyline([(xm(x), ym(y)) for x, y in [(1, 10), (4, 40), (9, 70)]], color=(0, 0, 1), width=1.2)
        page.draw_line((250, 170), (270, 170), color=(0, 0, 1), width=1.2)
        page.insert_text((274, 173), "Ours", fontsize=8)

    panel = _digitize(tmp_path, draw)
    lines = [s for s in panel["series"] if s["kind"] == "line"]
    assert len(lines) == 1 and lines[0]["legend_label"] == "Ours"
