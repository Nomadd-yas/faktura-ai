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












def test_empty_rag_run_friendly_text():
    """Разбор без данных: дружелюбный текст, карточек нет."""
    from faktura.ask.context import RunCtx
    from faktura.ask.service import answer_question

    out = answer_question(RunCtx("rag", []), "Лучший Answer F1")
    assert out["cards"] == [] and "пока нет данных" in out["text"] and out["suggestions"] == []
    assert RAG_DIR.is_dir()
