"""Полнотекстовый индекс статей прогона (docs/design/TEXT_INDEX.md): поиск со страницами, фильтр, страница целиком,
безопасность запроса, переиндексация, CLI rebuild, ленивая индексация, OCR для страниц без текстового слоя."""
from __future__ import annotations

import time
from datetime import timedelta

import pytest
import sqlalchemy as sa
from click.testing import CliRunner

from faktura.storage import schema as S
from faktura.textindex import chunk_page, clean_text, index_paper, read_page, search_text
from faktura.textindex.sources import index_pdf, merge_pages, pdf_page_texts

PHRASE = "NiCl2 additive bivalent metal chlorides CsPbI2Br perovskite films"


def _pages_with(pdf, needle: str) -> list[int]:
    return [i + 1 for i, t in enumerate(pdf_page_texts(pdf)) if needle.lower() in clean_text(t).lower()]


def _count(uow, table) -> int:
    return uow.conn.execute(sa.select(sa.func.count()).select_from(table)).scalar()


def _clear(services) -> None:
    with services.db.uow() as uow:
        uow.conn.execute(S.text_chunks.delete())
        uow.conn.execute(S.text_pages.delete())


@pytest.fixture
def cid(run_two) -> str:
    return "crp_" + run_two["id"].removeprefix("run_")


def test_search_finds_phrase_on_right_page(services, cid, pdfs):
    expect = _pages_with(pdfs["D001"], "bivalent metal chlorides on CsPbI2Br")
    assert expect
    with services.db.uow() as uow:
        res = search_text(uow, cid, PHRASE, k=5)
    assert res and res[0]["pid"] == "D001" and res[0]["page"] in expect
    assert set(res[0]) == {"chunk_id", "pid", "page", "text", "score"}
    assert res[0]["score"] > 0 and "bivalent" in res[0]["text"]
    assert [r["score"] for r in res] == sorted((r["score"] for r in res), reverse=True)
    assert len(res) <= 5






















# --- чанкинг и источники текста ------------------------------------------------------------------











