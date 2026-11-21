"""Офлайн-тесты сервиса pdf-dataset (без сети и без ключей). Запуск: pytest ml/pdf-dataset/tests -q"""
import sys
import types
from pathlib import Path

import pytest

SVC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SVC))
try:  # [faktura-patch] openai нужен только для вызовов LLM — в тестах подменяем заглушкой
    import openai  # noqa: F401
except ImportError:
    stub = types.ModuleType("openai")
    stub.OpenAI = object
    sys.modules["openai"] = stub

import extract_dataset as ed  # noqa: E402
import pipeline  # noqa: E402
from strip_tail import strip_trailing_sections  # noqa: E402


def _backend_tables():
    src = SVC.parents[1] / "backend" / "src"
    if not (src / "faktura" / "domain" / "tables.py").exists():
        return None
    sys.path.insert(0, str(src))
    from faktura.domain.tables import TABLES
    return TABLES


def test_schema_columns_match_backend():
    tables = _backend_tables()
    if tables is None:
        pytest.skip("backend недоступен")
    for name in ed.TABLES:
        assert tuple(ed.TABLES[name]["properties"]) == tables[name], name


def test_page_markers_and_image_skipped():
    jr = [[{"label": "text", "content": "Intro text"},
           {"label": "image", "content": "![img](x.jpg)"}],
          [{"label": "table", "content": "<table><tr><td>PCE</td></tr></table>"}]]
    md, paged = pipeline.build_paged_markdown(jr, "fallback")
    assert paged
    assert md.index("<!-- page 1 -->") < md.index("Intro text") < md.index("<!-- page 2 -->") < md.index("<table>")
    assert "img" not in md


def test_page_markers_fallback():
    md, paged = pipeline.build_paged_markdown({"weird": 1}, "raw md")
    assert md == "raw md" and not paged


def test_strip_tail_ignores_early_heading():
    body = "# Intro\n\n" + "text line\n" * 100
    text = "## Funding\n\n" + body  # заголовок в самом начале — не режем
    cleaned, info = strip_trailing_sections(text)
    assert info is None and cleaned == text


def test_strip_tail_cuts_late_heading_keeps_markers():
    text = ("<!-- page 1 -->\n\n" + "body\n" * 200 + "<!-- page 2 -->\n\nmore\n" * 5
            + "\n<!-- page 3 -->\n\n## References\n\n[1] A. B.\n" * 1)
    cleaned, info = strip_trailing_sections(text)
    assert info is not None
    assert "<!-- page 1 -->" in cleaned and "[1] A. B." not in cleaned
    assert not cleaned.rstrip().endswith("-->")  # висящий маркер убран


def test_check_dataset_valid_and_invalid():
    ds = {
        "publications": [{"publication_id": "P1", "in_scope": True}],
        "materials": [{"publication_id": "P1", "material_id": "M1", "material_class": "perovskite"}],
        "devices": [{"publication_id": "P1", "device_id": "DV1", "absorber_material_id": "M1",
                     "area_unit": "cm2"}],
        "layers": [{"publication_id": "P1", "layer_id": "L1", "device_id": "DV1", "layer_order": 1,
                    "layer_thickness": "63-68"}],
        "measurements": [{"publication_id": "P1", "measurement_id": "MS1", "device_id": "DV1;DV2"}],
        "results": [{"publication_id": "P1", "result_id": "R1", "device_id": "DV1", "measurement_id": "MS1",
                     "metric_name": "PCE", "source_page": 3}],
        "issues": [{"publication_id": "P1", "issue_id": "I1", "record_ids": "R1",
                    "check_name": "contradiction"}],
    }
    ds["devices"].append({"publication_id": "P1", "device_id": "DV2"})
    assert ed.check_dataset(ds) == []
    ds["materials"][0]["material_class"] = "bogus"
    assert any("material_class" in p for p in ed.check_dataset(ds))


# ---------- [faktura-patch] порядок шагов извлечения (фейковый call_llm) ----------
def _run_extract(monkeypatch, tmp_path, in_scope=True, delays=None):
    import json
    import threading
    import time
    calls, lock = [], threading.Lock()
    delays = delays or {}

    def fake(client, args, messages, schema_name, schema, max_tokens, label, raw_path, report=None,
             retry=True, reasoning_tokens=None):
        step = label
        with lock:
            calls.append({"step": step, "t0": time.time(), "retry": retry, "rt": reasoning_tokens,
                          "mt": max_tokens})
        time.sleep(delays.get(step, 0.02))
        rows = [{"in_scope": in_scope}] if step == "publications" else []
        with lock:
            calls[[c["step"] for c in calls].index(step)]["t1"] = time.time()
        return {step: rows}

    monkeypatch.setattr(ed, "call_llm", fake)
    monkeypatch.setattr(ed, "OpenAI", lambda **kw: object())
    md = tmp_path / "a.md"
    md.write_text("text", encoding="utf-8")
    ed.extract(md, publication_id="P1", out=str(tmp_path / "o.json"), api_key="k")
    return {c["step"]: c for c in calls}, [c["step"] for c in calls]


def test_layers_does_not_block_results(monkeypatch, tmp_path):
    c, order = _run_extract(monkeypatch, tmp_path, delays={"layers": 0.5})
    assert c["results"]["t0"] < c["layers"]["t1"]  # results стартует, пока layers ещё идёт
    assert c["issues"]["t0"] >= c["layers"]["t1"]  # issues ждёт layers
    assert c["devices"]["t0"] >= c["publications"]["t1"]
    assert c["results"]["t0"] >= c["measurements"]["t1"]


def test_out_of_scope_stops(monkeypatch, tmp_path):
    _, order = _run_extract(monkeypatch, tmp_path, in_scope=False)
    assert sorted(order) == ["materials", "publications"]


def test_issues_light_no_retry(monkeypatch, tmp_path):
    c, order = _run_extract(monkeypatch, tmp_path)
    assert order.count("issues") == 1
    assert c["issues"]["retry"] is True and c["issues"]["mt"] == 16384


# ---------- [faktura-patch] область RAG ----------
def test_rag_schemas_match_schema_json():
    import json
    sj = SVC.parents[1] / "data" / "caseA_drive" / "submission_template" / "schema.json"
    if not sj.exists():
        pytest.skip("schema.json недоступен")
    spec = json.loads(sj.read_text(encoding="utf-8"))
    assert list(ed.RAG_TABLES) == list(spec["tables"])
    for name, cols in spec["tables"].items():
        assert list(ed.RAG_TABLES[name]["properties"]) == cols, name
        assert ed.RAG_TABLES[name]["required"] == cols  # strict: все ключи обязательны
    assert ed.RAG_KEYS == spec["keys"]
    res = ed.RAG_TABLES["results"]["properties"]
    assert res["result_origin"]["anyOf"][0]["enum"] == spec["enums"]["results.result_origin"]
    assert res["extraction_status"]["anyOf"][0]["enum"] == spec["enums"]["results.extraction_status"]
    assert res["metric_direction"]["anyOf"][0]["enum"] == spec["recommended_enums"]["results.metric_direction"]


def _rag_fake(calls, lock, pub_in_scope=True):
    import re as _re

    def fake(client, args, messages, schema_name, schema, max_tokens, label, raw_path, report=None,
             retry=True, reasoning_tokens=None):
        with lock:
            calls.append(label)
        if label == "publications":
            return {"publications": [{"in_scope": pub_in_scope}]}
        if label == "configurations":
            return {"configurations": [{"config_id": "C1", "rag_variant": "A"}, {"config_id": "C2", "rag_variant": "B"}]}
        if label == "datasets":
            return {"datasets": [{"dataset_id": "DS1", "dataset_name": "HotpotQA"}]}
        if label == "tasks":
            return {"tasks": [{"task_id": "T1", "task_type": "multi-hop QA"}]}
        if label == "experiments":
            return {"experiments": [{"experiment_id": "E1"}]}
        if label.startswith("results:"):
            user = messages[1]["content"]
            row = {"result_id": "R1", "experiment_id": "E1", "config_id": "C1", "dataset_id": "DS1", "task_id": "T1",
                   "metric_name": "EM", "extraction_status": "extracted"}
            if label == "results:prose":
                return {"results": [dict(row, raw_value="0.2", normalized_value=0.2, source_page=1),
                                    dict(row, result_id="R2", config_id="C9", raw_value="0.9", normalized_value=0.9,
                                         source_page=1, duplicate_of="R7")]}
            n = int(_re.search(r"Таблица: Table (\d)", user).group(1))
            return {"results": [dict(row, raw_value=str(n / 10), normalized_value=n / 10),
                                dict(row, result_id="R2", config_id="C2", raw_value=str(n / 10 + 0.05),
                                     normalized_value=n / 10 + 0.05, duplicate_of="R1")]}
        return {schema_name.replace("table_", ""): []}
    return fake


_RAG_MD = ("<!-- page 1 -->\n\nTable 1: Main results.\n\n<table><tr><td>Method</td><td>EM</td></tr>"
           "<tr><td>A</td><td>.1</td></tr></table>\n\n<!-- page 2 -->\n\nTable 2: Ablation.\n\n"
           "<table><tr><td>Method</td><td>EM</td></tr><tr><td>A</td><td>.2</td></tr></table>\n\n"
           "<!-- page 3 -->\n\nTable 3: Latency.\n\n<table><tr><td>Method</td><td>EM</td></tr>"
           "<tr><td>A</td><td>.3</td></tr></table>\n\nOur method gets 0.2 EM.\n")


def test_rag_chunking_three_tables_and_renumber(monkeypatch, tmp_path):
    import json
    import threading
    calls, lock = [], threading.Lock()
    monkeypatch.setattr(ed, "call_llm", _rag_fake(calls, lock))
    monkeypatch.setattr(ed, "OpenAI", lambda **kw: object())
    monkeypatch.setattr(ed, "RAG_SMALL_TABLE_CELLS", 0)  # без склейки мелких таблиц: вызов на таблицу
    md = tmp_path / "a.md"
    md.write_text(_RAG_MD, encoding="utf-8")
    res = ed.extract(md, publication_id="P1", out=str(tmp_path / "o.json"), api_key="k", domain="rag",
                     meta_path=str(tmp_path / "m.json"))
    rcalls = [c for c in calls if c.startswith("results:")]
    assert len([c for c in rcalls if c != "results:prose"]) == 3 and "results:prose" in rcalls
    # порядок шагов: experiments после publications/configurations/datasets/tasks, issues последним
    assert calls.index("experiments") > max(calls.index(s) for s in ("publications", "configurations", "datasets", "tasks"))
    assert calls[-1] == "issues"
    rows = res["dataset"]["results"]
    assert [r["result_id"] for r in rows] == ["R%03d" % i for i in range(1, len(rows) + 1)] and len(rows) == 8
    by = {r["result_id"]: r for r in rows}
    assert by["R002"]["duplicate_of"] == "R001"          # локальная ссылка переписана на глобальный id
    assert all(r["duplicate_of"] is None or r["duplicate_of"] in by for r in rows)
    assert by["R008"]["config_id"] is None                # prose: config_id C9 вне configurations -> null
    assert by["R008"]["duplicate_of"] is None             # висячая ссылка R7 обнулена
    assert by["R007"]["duplicate_of"] == "R003"           # 0.2 в тексте = значение Table 2 (C1, EM) -> повтор
    assert by["R001"]["source_table"] == "Table 1" and by["R001"]["source_page"] == 1
    assert by["R003"]["source_page"] == 2 and by["R005"]["source_page"] == 3
    assert ed.check_dataset_rag(res["dataset"]) == []
    meta = json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))
    assert meta["domain"] == "rag" and meta["figures_skipped"] is True


def test_rag_split_large_table_keeps_header():
    rows = "".join("<tr><td>m%d</td>%s</tr>" % (i, "<td>1.5</td>" * 10) for i in range(40))
    tbl = "<table><tr><td>Method</td>" + "<td>EM</td>" * 10 + "</tr>" + rows + "</table>"
    chunks = ed.rag_split_table(tbl, max_cells=150)
    assert len(chunks) == 3 and sum(len(c["rows"]) for c in chunks) == 40
    assert all(c["header"] and c["cells"] <= 150 for c in chunks)
    assert [(c["lo"], c["hi"]) for c in chunks][0][0] == 1 and chunks[-1]["hi"] == 40
    assert "m0<" in ed._rag_chunk_html(chunks[0]) and "m0<" not in ed._rag_chunk_html(chunks[1])
    assert ed._rag_chunk_html(chunks[1]).count("<tr") == 1 + len(chunks[1]["rows"])


def test_solar_default_domain_steps_unchanged(monkeypatch, tmp_path):
    _, order = _run_extract(monkeypatch, tmp_path)
    assert sorted(order) == sorted(["publications", "materials", "devices", "layers", "measurements", "results", "issues"])


def test_rag_out_of_scope_only_publications(monkeypatch, tmp_path):
    import threading
    calls, lock = [], threading.Lock()
    monkeypatch.setattr(ed, "call_llm", _rag_fake(calls, lock, pub_in_scope=False))
    monkeypatch.setattr(ed, "OpenAI", lambda **kw: object())
    md = tmp_path / "a.md"
    md.write_text(_RAG_MD, encoding="utf-8")
    res = ed.extract(md, publication_id="P1", out=str(tmp_path / "o.json"), api_key="k", domain="rag")
    ds = res["dataset"]
    assert len(ds["publications"]) == 1 and all(not ds[t] for t in ds if t != "publications")
    assert not any(c in calls for c in ("experiments", "issues")) and not any(c.startswith("results:") for c in calls)


def test_extract_endpoint_rejects_unknown_domain():
    pytest.importorskip("fastapi.testclient")
    pytest.importorskip("multipart")
    from fastapi.testclient import TestClient
    import api
    r = TestClient(api.app).post("/extract", files={"file": ("a.pdf", b"%PDF-1.4")}, data={"domain": "chem"})
    assert r.status_code == 422 and r.json()["code"] == "domain_not_supported"


def test_rag_small_tables_batched_without_reasoning(monkeypatch, tmp_path):
    """[faktura-patch] экономия: мелкие таблицы — одним вызовом, таблицы — без рассуждения, prose — с ним."""
    import re as _re
    import threading
    seen = []

    def fake(client, args, messages, schema_name, schema, max_tokens, label, raw_path, report=None,
             retry=True, reasoning_tokens=None):
        seen.append((label, reasoning_tokens))
        if label == "publications":
            return {"publications": [{"in_scope": True}]}
        if label == "configurations":
            return {"configurations": [{"config_id": "C1"}]}
        if label.startswith("results:batch"):
            tabs = _re.findall(r"=== Таблица: (Table \d); страница PDF \(source_page\) = (\d)", messages[1]["content"])
            return {"results": [{"result_id": f"R{i}", "config_id": "C1", "metric_name": "EM", "raw_value": str(i),
                                 "normalized_value": i, "source_table": t, "extraction_status": "extracted"}
                                for i, (t, _) in enumerate(tabs, start=1)]}
        return {schema_name.replace("table_", ""): []}

    monkeypatch.setattr(ed, "call_llm", fake)
    monkeypatch.setattr(ed, "OpenAI", lambda **kw: object())
    md = tmp_path / "a.md"
    md.write_text(_RAG_MD, encoding="utf-8")
    res = ed.extract(md, publication_id="P1", out=str(tmp_path / "o.json"), api_key="k", domain="rag")
    rcalls = [(l, rt) for l, rt in seen if l.startswith("results:")]
    batch = [x for x in rcalls if x[0].startswith("results:batch")]
    assert len(batch) == 1 and batch[0][1] == 0                    # одна склейка, рассуждение выключено
    assert ("results:prose", None) in rcalls                       # текст — с рассуждением
    pages = {r["source_table"]: r["source_page"] for r in res["dataset"]["results"]}
    assert pages == {"Table 1": 1, "Table 2": 2, "Table 3": 3}     # страницы — по меткам таблиц


def test_solar_normalize_template_conventions():
    st = {
        "materials": [{"material_id": "M1", "material_class": "CIGS"}],
        "devices": [{"device_id": "DV1", "absorber_material_id": "M1", "device_role": "record",
                     "architecture": "n-i-p", "area_definition": "designated illumination", "substrate_type": None},
                    {"device_id": "DV2", "absorber_material_id": "M1", "device_role": None}],
        "layers": [{"device_id": "DV1", "layer_order": 1, "layer_function": "back_contact", "layer_material": "Mo (molybdenum)",
                    "deposition_method": "DC magnetron sputtering"},
                   {"device_id": "DV1", "layer_order": 2, "layer_function": "absorber", "layer_material": "CIGS",
                    "deposition_method": "pulsed electron deposition (PED)"},
                   {"device_id": "DV1", "layer_order": 3, "layer_function": "interlayer", "layer_material": "i-ZnO",
                    "deposition_method": None},
                   {"device_id": "DV1", "layer_order": 4, "layer_function": "ETL", "layer_material": "AZO (Al-doped ZnO)",
                    "deposition_method": None}],
        "measurements": [{"measurement_id": "MS1", "measurement_type": "J-V", "spectrum": None, "light_intensity": None}],
        "results": [{"result_origin": "primary", "source_table": "Table 2", "metric_name": "PCE", "aggregation": None,
                     "source_figure": None}],
    }
    text = ("Cells on soda-lime glass were measured under AM 1.5G, 100 mW/cm2 with a metal mask. "
            "Figure 3. J–V curves of the best cells. Table 2. Photovoltaic parameters of the best devices.")
    ed.solar_normalize(st, text)
    d1, d2 = st["devices"]
    assert (d1["device_role"], d2["device_role"]) == ("target", "target")
    assert d1["architecture"] == "substrate" and d1["area_definition"] == "aperture" and d1["substrate_type"] == "rigid"
    assert [l["layer_function"] for l in st["layers"]][2:] == ["front_contact", "front_contact"]
    assert st["layers"][0]["layer_material"] == "Mo" and st["layers"][3]["layer_material"] == "AZO"
    assert st["layers"][1]["deposition_method"] == "pulsed electron deposition"
    ms = st["measurements"][0]
    assert (ms["spectrum"], ms["light_intensity"], ms["intensity_unit"]) == ("AM1.5G", 100.0, "mW/cm2")
    r = st["results"][0]
    assert (r["aggregation"], r["source_figure"]) == ("champion", "Figure 3")
