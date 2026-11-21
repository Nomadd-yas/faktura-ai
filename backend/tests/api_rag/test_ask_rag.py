"""«Вопросы по разбору» (CORE_API §4i), область rag: лучшее значение метрики среди сопоставимых, лимит строк, списки."""
from __future__ import annotations

import shutil

import pytest

from .conftest import API, RAG_DIR, upload


@pytest.fixture
def big_run(client, auth, tmp_path, rag_dir):
    """D014 (28 649 строк results) и D007: разбор для проверки лимита строк."""
    client.app.state.faktura.ask_client = None
    paths = []
    for pid in ("D014", "D007"):
        shutil.copy(rag_dir / "dev" / f"{pid}.pdf", tmp_path / f"{pid}.pdf")
        paths.append(tmp_path / f"{pid}.pdf")
    r = upload(client, auth, paths)
    assert r.status_code == 202, r.text
    return r.json()


def ask(client, auth, rid, question, **kw):
    r = client.post(f"{API}/runs/{rid}/ask", json={"question": question, **kw}, headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


def _num(s: str) -> float:
    return float(s.replace(",", ".").split()[0])


def test_best_answer_f1_across_configs_is_capped_and_comparable(client, auth, big_run, gold):
    rid = big_run["id"]
    body = ask(client, auth, rid, "Лучший Answer F1")
    card = body["cards"][0]
    assert card["kind"] == "ragTable" and card["cols"][:2] == ["Статья", "Значение"]
    assert 1 <= len(card["rows"]) <= 50 and card["rows"][0]["best"] is True
    top = card["rows"][0]
    # лучшая строка - максимум значения среди строк карточки (в таблице одни сопоставимые условия)
    ref = {(r["publication_id"], r["result_id"]): r for r in gold["results"]["D014"] + gold["results"]["D007"]}
    assert all(ref[(r["pid"], r["rid"])]["metric_name"] == "Answer F1" for r in card["rows"])
    assert len({(ref[(r["pid"], r["rid"])]["dataset_id"], ref[(r["pid"], r["rid"])]["metric_variant"]) for r in card["rows"]
                if r["pid"] == "D014"}) == 1
    vals = [_num(r["cells"][1]) for r in card["rows"]]
    assert vals[0] == max(vals)  # Answer F1: выше лучше
    assert body["cites"][0]["domain"] == "rag" and body["cites"][0]["rid"] == top["rid"]
    assert "Answer F1" in body["text"] and "[1]" in body["text"]
    assert body["meta"]["intent"] == "best"


def test_results_list_is_capped_with_message(client, auth, big_run):
    body = ask(client, auth, big_run["id"], "Покажи значения Answer F1")
    card = body["cards"][0]
    assert len(card["rows"]) == 50 and body["meta"]["capped"]["shown"] == 50 and body["meta"]["capped"]["total"] > 50
    assert "Показаны первые 50" in body["text"]
    assert len(body["cites"]) == 50


def test_dataset_filter_and_lists(client, auth, big_run):
    rid = big_run["id"]
    ds = ask(client, auth, rid, "Какие датасеты")
    assert ds["cards"][0]["kind"] == "ragTable" and ds["cards"][0]["cols"] == ["Название", "Статей", "Какие"]
    name = ds["cards"][0]["rows"][0]["cells"][0]
    f = ask(client, auth, rid, f"Лучший Answer F1 на {name}")
    assert f["cards"] and all(r["cells"][3] == name for r in f["cards"][0]["rows"])
    models = ask(client, auth, rid, "Какие генераторы")
    assert models["cards"][0]["kind"] == "ragTable" and models["cards"][0]["title"].lower().startswith("генератор")


def test_issues_paper_schema_suggestions(client, auth, big_run):
    rid = big_run["id"]
    iss = ask(client, auth, rid, "Замечания у D014")
    assert iss["cards"][0]["kind"] == "issues" and iss["cards"][0]["domain"] == "rag"
    assert all(i["pid"] == "D014" for i in iss["cards"][0]["items"])
    paper = ask(client, auth, rid, "Расскажи про D007")
    assert paper["cards"][0] == {"kind": "ragPaper", "pid": "D007"}
    sch = ask(client, auth, rid, "Что в таблице configurations")
    assert sch["cards"][0]["kind"] == "schema" and sch["cards"][0]["table"] == "configurations"
    tr = [c for c in sch["cards"] if c["kind"] == "tableRows"]
    assert tr and tr[0]["domain"] == "rag"
    s = client.get(f"{API}/runs/{rid}/ask/suggestions", headers=auth).json()
    assert "Какие датасеты" in s and any(x.startswith("Лучший ") for x in s) and "Что в таблице configurations" in s


def test_rag_rejected_row_excluded(client, auth, big_run):
    rid = big_run["id"]
    first = ask(client, auth, rid, "Лучший Answer F1")["cards"][0]["rows"][0]
    r = client.patch(f"{API}/runs/{rid}/papers/rag/{first['pid']}/results/{first['rid']}/review",
                     json={"status": "rejected"}, headers=auth)
    assert r.status_code == 200, r.text
    again = ask(client, auth, rid, "Лучший Answer F1")["cards"][0]["rows"]
    assert all(x["rid"] != first["rid"] or x["pid"] != first["pid"] for x in again)


def test_empty_rag_run_friendly_text():
    """Разбор без данных: дружелюбный текст, карточек нет."""
    from faktura.ask.context import RunCtx
    from faktura.ask.service import answer_question

    out = answer_question(RunCtx("rag", []), "Лучший Answer F1")
    assert out["cards"] == [] and "пока нет данных" in out["text"] and out["suggestions"] == []
    assert RAG_DIR.is_dir()
