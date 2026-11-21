"""[faktura-patch] Offline tests of figures.py (state assessment, fallbacks, mapping -> rows, merge) and its integration
into the RAG pipeline. No network, no keys: LLM and vision are fakes, PDFs are synthetic."""
import json
import re
import sys
import threading
import types
from pathlib import Path

import pymupdf
import pytest

SVC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SVC))
try:
    import openai  # noqa: F401
except ImportError:
    stub = types.ModuleType("openai")
    stub.OpenAI = object
    sys.modules["openai"] = stub

import extract_dataset as ed  # noqa: E402
import figures as F  # noqa: E402

X0, X1, Y0, Y1 = 100.0, 400.0, 400.0, 150.0
DATA = [(1, 10), (3, 35), (5, 50), (8, 80), (10, 95)]  # x = k, y = percent


def xm(v):
    return X0 + v * (X1 - X0) / 10


def ym(v):
    return Y0 - v * (Y0 - Y1) / 100


def _vector_chart(page, caption="Figure 1. Recall (%) of retrievers versus k."):
    page.draw_line((X0, Y0), (X1, Y0), width=0.8)
    page.draw_line((X0, Y0), (X0, Y1), width=0.8)
    for v in (0, 2, 4, 6, 8, 10):
        page.draw_line((xm(v), Y0), (xm(v), Y0 + 4), width=0.5)
        page.insert_text((xm(v) - 3, Y0 + 15), f"{v:g}", fontsize=8)
    for v in (0, 20, 40, 60, 80, 100):
        page.draw_line((X0 - 4, ym(v)), (X0, ym(v)), width=0.5)
        page.insert_text((X0 - 30, ym(v) + 3), f"{v:g}", fontsize=8)
    page.draw_polyline([(xm(x), ym(y)) for x, y in DATA], color=(1, 0, 0), width=1.2)
    page.draw_line((300, 170), (320, 170), color=(1, 0, 0), width=1.2)
    page.insert_text((325, 173), "Hybrid", fontsize=8)
    page.insert_text((150, 460), caption, fontsize=9)


def _raster_chart(page, caption="Figure 2. Accuracy of systems on three datasets."):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 200), False)
    pix.set_rect(pix.irect, (200, 220, 240))
    page.insert_image(pymupdf.Rect(100, 120, 400, 320), pixmap=pix)
    page.insert_text((150, 345), caption, fontsize=9)


def _pdf(tmp_path, pages=("vector", "raster")):
    doc = pymupdf.open()
    for kind in pages:
        pg = doc.new_page(width=500, height=500)
        for i in range(6):
            pg.insert_text((50, 30 + 12 * i), "Some body text of the page that reads like a paragraph. " * 2, fontsize=7)
        (_vector_chart if kind == "vector" else _raster_chart)(pg)
    p = tmp_path / "t.pdf"
    doc.save(str(p))
    return p


# ------------------------------------------------------------------ state classification
def test_state_vector_vs_raster(tmp_path):
    asm = F.assess_document(_pdf(tmp_path), md="<!-- page 1 -->\nbody\n<!-- page 2 -->\nbody")
    by = {r["label"]: r for r in asm["figures"]}
    v, r = by["Figure 1"], by["Figure 2"]
    assert v["state"] == "vector_ok" and v["confidence"] >= F.VECTOR_OK and v["points"] == 5
    assert r["state"] == "raster" and r["points"] == 0 and r["fig"] is None
    p1, p2 = asm["pages"]
    assert p1["text_layer"] == "ok" and p1["vector_paths"] > 10 and p1["raster_frac"] == 0
    assert p2["raster_frac"] > 0.2 and p2["ocr_used"] is True and p2["ocr_chars"] is not None
    meta = F.extraction_state_meta(asm)
    json.dumps(meta)  # JSON-safe
    assert meta["counts"]["state_vector_ok"] == 1 and meta["counts"]["state_raster"] == 1


def test_scanned_page_text_layer_none(tmp_path):
    doc = pymupdf.open()
    doc.new_page(width=300, height=300)
    p = tmp_path / "blank.pdf"
    doc.save(str(p))
    asm = F.assess_document(p)
    assert asm["pages"][0]["text_layer"] == "none" and asm["figures"] == []


# ------------------------------------------------------------------ fallbacks
def test_no_vision_gives_figure_only_issue(tmp_path, monkeypatch):
    monkeypatch.delenv("RAG_VISION_MODEL", raising=False)
    pdf = _pdf(tmp_path)
    asm = F.assess_document(pdf)
    F.run_fallbacks(asm, pdf, client=None, vision_model=None)
    by = {r["label"]: r for r in asm["figures"]}
    assert by["Figure 1"]["method"] == "vector" and by["Figure 2"]["method"] == "none"
    issues = F.figure_issues(asm, "P1")
    assert len(issues) == 1 and issues[0]["check_name"] == "figure_only"
    assert "Figure 2" in issues[0]["description"] and issues[0]["source_page"] == 2


def test_vision_error_behaves_as_unavailable(tmp_path):
    pdf = _pdf(tmp_path)
    asm = F.assess_document(pdf)

    def boom(rec):
        raise RuntimeError("503")
    F.run_fallbacks(asm, pdf, vision_model="vm", vision_fn=boom)
    r = next(x for x in asm["figures"] if x["label"] == "Figure 2")
    assert r["method"] == "none" and "vision error" in r["reason"] and r["issue"] is True


def test_vision_fallback_rows_are_marked_approximate(tmp_path):
    pdf = _pdf(tmp_path)
    asm = F.assess_document(pdf)
    js = {"is_chart": True, "panels": [{"title": None, "x_axis": {"label": "k", "scale": "linear", "min": 1, "max": 10},
                                        "y_axis": {"label": "Accuracy (%)", "scale": "linear", "min": 0, "max": 100},
                                        "series": [{"label": "A", "kind": "line", "points": [[1, 40.0], [2, 55.5]]}]}]}
    F.run_fallbacks(asm, pdf, vision_model="vm", vision_fn=lambda rec: (F.panels_from_vision(js), {}))
    r = next(x for x in asm["figures"] if x["label"] == "Figure 2")
    assert r["method"] == "vision"
    rows, _, _ = F.map_and_generate(asm, "P1", "{}", _ids(), _fake_mapper(), workers=2)
    vr = [x for x in rows if x["source_figure"] == "Figure 2"]
    assert len(vr) == 2 and all(x["note"].startswith("approximate, digitized from raster image by vision model")
                                for x in vr)
    assert vr[0]["metric_k"] == 1 and vr[1]["raw_value"] == "~55.5"


# ------------------------------------------------------------------ mapping -> rows
def _ids():
    return {"configs": {"C1", "C2"}, "datasets": {"DS1"}, "tasks": {"T1"}, "experiments": {"E1"}}


def _fake_mapper(config="C1"):
    def llm(messages, schema_name, schema, label):
        assert schema_name == "figure_mapping" and "Figure data" in messages[1]["content"]
        ser = re.findall(r'"series_id":"(p\d+s\d+)"', messages[1]["content"])
        return {"series": [{"series_id": s, "is_result": True, "skip_reason": None, "series_name": None,
                            "metric_name": "Recall", "metric_variant": None, "metric_direction": "higher_is_better",
                            "metric_group": "retrieval quality", "metric_unit": "%", "metric_axis": "y",
                            "other_axis_meaning": "metric_k", "other_axis_name": "k", "config_id": config,
                            "dataset_id": "DSX", "task_id": None, "experiment_id": "E1", "category_map": []}
                           for s in ser]}
    return llm


def test_rows_from_mapping_k_from_x(tmp_path):
    pdf = _pdf(tmp_path, pages=("vector",))
    asm = F.assess_document(pdf)
    F.run_fallbacks(asm, pdf)
    rows, issues, st = F.map_and_generate(asm, "P1", "{}", _ids(), _fake_mapper())
    assert len(rows) == 5 and issues == [] and st["figures_mapped"] == 1
    assert [r["metric_k"] for r in rows] == [1, 3, 5, 8, 10]
    for r, (x, y) in zip(rows, DATA):
        assert abs(r["metric_value"] - y) <= 1.0 and r["raw_value"].startswith("~")
        assert r["source_figure"] == "Figure 1" and r["source_page"] == 1
        assert r["config_id"] == "C1" and r["dataset_id"] is None        # id outside the context -> null
        assert r["experiment_id"] == "E1" and r["raw_unit"] == "%" and r["metric_unit"] == "%"
        assert r["note"].startswith("Digitized from original PDF vector geometry") and "tolerance" in r["note"]
        assert r["source_fragment"].startswith("Figure 1.")
    assert asm["figures"][0]["rows"] == 5


def test_non_result_series_skipped_and_cap(tmp_path):
    pdf = _pdf(tmp_path, pages=("vector",))
    asm = F.assess_document(pdf)
    F.run_fallbacks(asm, pdf)

    def llm(messages, *a):
        return {"series": [{"series_id": "p0s0", "is_result": False, "skip_reason": "grid", "series_name": None,
                            "metric_name": None, "metric_variant": None, "metric_direction": None,
                            "metric_group": None, "metric_unit": None, "metric_axis": None,
                            "other_axis_meaning": None, "other_axis_name": None, "config_id": None,
                            "dataset_id": None, "task_id": None, "experiment_id": None, "category_map": []}]}
    rows, _, _ = F.map_and_generate(asm, "P1", "{}", _ids(), llm)
    assert rows == [] and "no result series" in asm["figures"][0]["reason"]
    asm2 = F.assess_document(pdf)
    F.run_fallbacks(asm2, pdf)
    rows, _, _ = F.map_and_generate(asm2, "P1", "{}", _ids(), _fake_mapper(), max_rows=3)
    assert len(rows) == 3 and "truncated" in asm2["figures"][0]["reason"]


def test_mapping_failure_becomes_issue(tmp_path):
    pdf = _pdf(tmp_path, pages=("vector",))
    asm = F.assess_document(pdf)
    F.run_fallbacks(asm, pdf)

    def llm(*a):
        raise RuntimeError("llm down")
    rows, issues, _ = F.map_and_generate(asm, "P1", "{}", _ids(), llm)
    assert rows == [] and issues and issues[0]["check_name"] == "figure_only"


# ------------------------------------------------------------------ merge
def test_merge_links_duplicates_to_table_rows():
    table = [{"result_id": "t1", "config_id": "C1", "dataset_id": "DS1", "metric_name": "Recall", "metric_k": 5,
              "normalized_value": 50.0, "raw_value": "50.0", "source_table": "Table 1"},
             {"result_id": "t2", "config_id": "C1", "dataset_id": "DS1", "metric_name": "Recall", "metric_k": 8,
              "normalized_value": 80.0, "raw_value": "80", "duplicate_of": "t1"}]

    def fr(i, k, v, cfg="C1", tol=0.1):
        return {"result_id": f"fig{i}", "config_id": cfg, "dataset_id": None, "metric_name": "recall",
                "metric_k": k, "_v": v, "_tol": tol, "duplicate_of": None, "source_figure": "Figure 1"}
    figs = [fr(1, 5, 50.05), fr(2, 8, 80.0), fr(3, 3, 35.0), fr(4, 5, 50.0, cfg="C2"), fr(5, 5, 50.0, cfg=None)]
    n = F.merge_figure_rows(table, figs)
    by = {r["result_id"]: r for r in table}
    assert by["fig1"]["duplicate_of"] == "t1"
    assert by["fig2"]["duplicate_of"] == "t1"          # follows the chain to the primary row
    assert by["fig3"]["duplicate_of"] is None          # not printed anywhere
    assert by["fig4"]["duplicate_of"] is None          # other config
    assert by["fig5"]["duplicate_of"] == "t1"          # no config, single unambiguous candidate
    assert n == 3 and "_v" not in by["fig1"] and len(table) == 7


# ------------------------------------------------------------------ pipeline integration
def _rag_fake(calls, lock):
    mapper = _fake_mapper()

    def fake(client, args, messages, schema_name, schema, max_tokens, label, raw_path, report=None,
             retry=True, reasoning_tokens=None):
        with lock:
            calls.append(label)
        if label == "publications":
            return {"publications": [{"in_scope": True}]}
        if label == "configurations":
            return {"configurations": [{"config_id": "C1", "rag_variant": "Hybrid"}]}
        if label == "datasets":
            return {"datasets": [{"dataset_id": "DS1", "dataset_name": "NQ"}]}
        if label == "tasks":
            return {"tasks": [{"task_id": "T1", "task_type": "retrieval"}]}
        if label == "experiments":
            return {"experiments": [{"experiment_id": "E1"}]}
        if label.startswith("results:"):
            if label == "results:prose":
                return {"results": []}
            return {"results": [{"result_id": "R1", "config_id": "C1", "dataset_id": "DS1", "metric_name": "Recall",
                                 "metric_k": 5, "raw_value": "50.0", "normalized_value": 50.0,
                                 "extraction_status": "extracted"}]}
        if label.startswith("figure:"):
            assert schema_name == "figure_mapping"
            return mapper(messages, schema_name, schema, label)
        return {schema_name.replace("table_", ""): []}
    return fake


def test_pipeline_integration_state_rows_and_issue(monkeypatch, tmp_path):
    monkeypatch.delenv("RAG_VISION_MODEL", raising=False)
    monkeypatch.delenv("RAG_FIGURES", raising=False)
    calls, lock = [], threading.Lock()
    monkeypatch.setattr(ed, "call_llm", _rag_fake(calls, lock))
    monkeypatch.setattr(ed, "OpenAI", lambda **kw: object())
    pdf = _pdf(tmp_path)
    md = tmp_path / "a.md"
    md.write_text("<!-- page 1 -->\n\nTable 1: Recall.\n\n<table><tr><td>Method</td><td>Recall@5</td></tr>"
                  "<tr><td>Hybrid</td><td>50.0</td></tr></table>\n\n<!-- page 2 -->\n\nbody\n", encoding="utf-8")
    res = ed.extract(md, publication_id="P1", out=str(tmp_path / "o.json"), api_key="k", domain="rag",
                     meta_path=str(tmp_path / "m.json"), pdf_path=pdf)
    ds = res["dataset"]
    rows = ds["results"]
    figrows = [r for r in rows if r.get("source_figure")]
    assert len(figrows) == 5 and [r["result_id"] for r in rows] == ["R%03d" % i for i in range(1, len(rows) + 1)]
    table_row = next(r for r in rows if r.get("source_table") == "Table 1")
    k5 = next(r for r in figrows if r["metric_k"] == 5)
    assert k5["duplicate_of"] == table_row["result_id"]        # printed number repeated in the figure
    assert sum(1 for r in figrows if r.get("duplicate_of")) == 1
    assert sum(1 for c in calls if c.startswith("figure:")) == 1   # one mapping call, only for the data figure
    fo = [i for i in ds["issues"] if i["check_name"] == "figure_only"]
    assert len(fo) == 1 and "Figure 2" in fo[0]["description"]
    assert ed.check_dataset_rag(ds) == []
    meta = json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))
    st = meta["extraction_state"]
    assert meta["figures_skipped"] is False and len(st["pages"]) == 2
    by = {f["label"]: f for f in st["figures"]}
    assert by["Figure 1"]["method"] == "vector" and by["Figure 1"]["rows"] == 5
    assert by["Figure 2"]["method"] == "none" and by["Figure 2"]["state"] == "raster"
    assert "figures_assess" in meta["rag"]["timings"] and "figures_map" in meta["rag"]["timings"]


def test_figures_can_be_switched_off(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_FIGURES", "0")
    calls, lock = [], threading.Lock()
    monkeypatch.setattr(ed, "call_llm", _rag_fake(calls, lock))
    monkeypatch.setattr(ed, "OpenAI", lambda **kw: object())
    md = tmp_path / "a.md"
    md.write_text("<!-- page 1 -->\n\ntext\n", encoding="utf-8")
    res = ed.extract(md, publication_id="P1", out=str(tmp_path / "o.json"), api_key="k", domain="rag",
                     pdf_path=_pdf(tmp_path))
    assert not any(c.startswith("figure:") for c in calls)
    assert all(not r.get("source_figure") for r in res["dataset"]["results"])


# ------------------------------------------------------------------ no per-paper hardcoding
def test_no_publication_ids_in_figure_code():
    pat = re.compile(r"\b[DP]0\d\d\b")
    src = (SVC / "figures.py").read_text(encoding="utf-8")
    assert not pat.findall(src)
    full = (SVC / "extract_dataset.py").read_text(encoding="utf-8")
    rag = full[full.index("DOMAINS = (") : full.index("\ndef extract(")]
    assert not pat.findall(rag)
    for needle in ("page == ", "pno == ", "page_no == "):  # no per-page special cases in the state model
        assert needle not in src


# ---------------------------------------------------------------- [faktura-patch] shaded bands, panel titles
def test_band_kind_from_paper_wording():
    import figures as F
    k = F.band_kind("Accuracy vs tokens. Shaded areas indicate the 95% confidence interval over 8 runs.")
    assert (k["main"], k["lower"], k["upper"]) == ("mean", "lower_95CI", "upper_95CI")
    k = F.band_kind("Error bars show one standard deviation.")
    assert k["lower"] == "mean_minus_std"
    assert F.band_kind("No uncertainty is described here.")["lower"] == "band_lower"


def test_poly_vspan_and_inverse_axis():
    import figures as F
    poly = [(0, 10), (10, 8), (10, 2), (0, 4)]  # band between y=4..10 at x=0 and 2..8 at x=10
    assert F._poly_vspan(poly, 5) == pytest.approx((3.0, 9.0))
    assert F._poly_vspan(poly, 20) is None
    ax = {"scale": "log", "a": 0.01, "b": 0.0}
    assert F._inv(ax, F._val(ax, 123.0)) == pytest.approx(123.0)


def test_band_rows_generated():
    import figures as F
    rec = {"label": "Figure 1", "page": 3, "caption": "Figure 1: Acc.", "state": "vector_ok", "confidence": 0.9,
           "method": None, "band_kind": F.band_kind("Shaded: 95% confidence interval."),
           "use_panels": [{"title": None, "x_axis": {"step": 10, "scale": "linear", "label_text": "x"},
                           "y_axis": {"step": 10, "scale": "linear", "label_text": "Accuracy"},
                           "series": [{"points": [(1.0, 50.0), (2.0, 55.0)], "legend_label": "A",
                                       "bands": [[48.0, 52.0], [53.0, 57.0]]}]}]}
    m = {"p0s0": {"is_result": True, "metric_axis": "y", "metric_name": "Accuracy"}}
    rows, _ = F.generate_rows(rec, m, {"configs": set(), "datasets": set(), "tasks": set(), "experiments": set()}, "X")
    assert [r["aggregation"] for r in rows] == ["mean", "lower_95CI", "upper_95CI"] * 2
    assert [r["metric_value"] for r in rows[:3]] == [50.0, 48.0, 52.0]
    assert len({r["result_id"] for r in rows}) == 6


def test_metric_names_normalized_generic():
    import extract_dataset as E
    ds = [{"dataset_id": "DS1", "dataset_name": "QuALITY"}, {"dataset_id": "DS2", "dataset_name": "2WikiMultiHopQA (2Wiki)"}]
    rows = [{"metric_name": n, "dataset_id": None, "metric_k": None}
            for n in ("QuaLITY Accuracy (%)", "F1 on 2Wiki", "NDCG@5", "QuALITY", "F1-Score")]
    E.rag_normalize_metric_names(rows, ds)
    assert [r["metric_name"] for r in rows] == ["Accuracy", "F1", "NDCG@5", "QuALITY", "F1-Score"]
    assert [r["dataset_id"] for r in rows] == ["DS1", "DS2", None, None, None]
    assert rows[2]["metric_k"] == 5


def test_figure_rows_take_k_from_axis_label():
    import figures as F
    rec = {"label": "Figure 2", "page": 1, "caption": "Figure 2: results.", "state": "vector_ok", "confidence": 0.9,
           "method": None, "use_panels": [{"title": None, "x_axis": {"step": 1, "scale": "linear", "label_text": "t"},
                                           "y_axis": {"step": 0.1, "scale": "linear", "label_text": "NDCG@5"},
                                           "series": [{"points": [(1.0, 0.5), (2.0, 0.6)], "legend_label": "A"}]}]}
    m = {"p0s0": {"is_result": True, "metric_axis": "y", "metric_name": "NDCG", "other_axis_meaning": "hyperparameter"}}
    rows, _ = F.generate_rows(rec, m, {"configs": set(), "datasets": set(), "tasks": set(), "experiments": set()}, "X")
    assert {r["metric_k"] for r in rows} == {5}
