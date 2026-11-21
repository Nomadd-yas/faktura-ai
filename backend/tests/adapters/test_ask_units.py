"""«Вопросы по разбору»: проверка плана, правиловый планировщик, фильтр текста автора, адрес ML (без БД и ML)."""
from __future__ import annotations

from types import SimpleNamespace

from faktura.ask.client import ask_base_url
from faktura.ask.plan import normalize_plan, plan_schema
from faktura.ask.rules import keyword_plan
from faktura.ask.service import grounded

DESC = {"domain": "rag", "pids": ["D001", "D014"], "metrics": [{"name": "Answer F1", "n": 5, "papers": 2},
                                                               {"name": "Recall@3", "n": 2, "papers": 1},
                                                               {"name": "Recall@6", "n": 2, "papers": 1}],
        "datasets": ["HotpotQA", "NQ"]}


def test_normalize_plan_drops_foreign_codes_metrics_and_clamps():
    p = normalize_plan({"intent": "best", "pids": ["d001", "D999"], "metrics": ["answer f1", "Nope"], "limit": 9999,
                        "min_value": float("nan"), "table": "evil", "sort": "weird", "layers": ["SnO2"] * 20}, DESC)
    assert p["pids"] == ["D001"] and p["missing_pids"] == ["D999"]
    assert p["metrics"] == ["Answer F1"] and p["unknown_metric"] == "Nope"
    assert p["limit"] == 50 and p["min_value"] is None and p["table"] == "" and p["sort"] == "best"
    assert len(p["layers"]) == 6
    assert normalize_plan("junk", DESC)["intent"] == "unknown"
    assert normalize_plan({"intent": "drop table"}, DESC)["intent"] == "unknown"
    assert normalize_plan({"metrics": ["recall"]}, DESC)["metrics"] == ["Recall@3", "Recall@6"]  # семейство метрик


def test_plan_schema_is_strict():
    s = plan_schema()
    assert s["additionalProperties"] is False and set(s["required"]) == set(s["properties"])
    assert "unknown_metric" not in s["properties"] and "missing_pids" not in s["properties"]


def test_keyword_plan_rag():
    p = keyword_plan("Лучший Answer F1 на HotpotQA", DESC)
    assert p["intent"] == "best" and p["metrics"] == ["Answer F1"] and p["rag_dataset"] == "HotpotQA"
    assert keyword_plan("Какие датасеты", DESC)["rag_list"] == "datasets"
    q = normalize_plan(keyword_plan("лучший recall@5", DESC), DESC)
    assert q["metrics"] == [] and q["unknown_metric"] == "recall@5"
    assert keyword_plan("Замечания у D014", DESC)["pids"] == ["D014"]


def test_keyword_plan_solar():
    d = {"domain": "solar", "pids": ["D001"], "metrics": [{"name": "PCE", "n": 1, "papers": 1}, {"name": "Voc", "n": 1, "papers": 1}]}
    p = keyword_plan("КПД перовскитов выше 20 % со слоем SnO2", d)
    assert p["intent"] == "results" and p["metrics"] == ["PCE"] and p["material_class"] == ["perovskite"]
    assert p["min_value"] == 20.0 and "sno2" in p["layers"]
    assert keyword_plan("Voc ниже 900 мВ", d)["max_value"] == 0.9
    assert keyword_plan("что значит extraction_status", d)["intent"] == "schema"
    assert keyword_plan("расскажи анекдот", d)["intent"] == "unknown"


def test_grounded_rejects_invented_numbers_and_cites():
    draft, facts = "Лучший КПД: 25,3 % у D001 [1].", {"rows": [{"value": "25,3 %"}]}
    assert grounded("КПД 25,3 % у D001 [1].", draft, facts, "лучший КПД")
    assert not grounded("КПД 25,4 % у D001 [1].", draft, facts, "q")
    assert not grounded("КПД 25,3 % у D001 [2].", draft, facts, "q")
    assert not grounded("", draft, facts, "q") and not grounded("я" * 1000, draft, facts, "q")


def test_ask_base_url():
    s = SimpleNamespace(ask_url=None, ask_mode="auto", extractor="http", extract_url="http://ml:8080/extract")
    assert ask_base_url(s) == "http://ml:8080"
    s.extract_url = "http://gw/ml/extract/"
    assert ask_base_url(s) == "http://gw"
    s.ask_url = "http://ask:9000"
    assert ask_base_url(s) == "http://ask:9000"
    s.ask_mode = "off"
    assert ask_base_url(s) is None
    assert ask_base_url(SimpleNamespace(ask_url=None, ask_mode="auto", extractor="mock", extract_url="http://x/extract")) is None
