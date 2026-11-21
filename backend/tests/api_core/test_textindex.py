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


def test_pids_filter_and_k(services, cid):
    q = "perovskite solar cells efficiency"
    with services.db.uow() as uow:
        both = search_text(uow, cid, q, k=30)
        only = search_text(uow, cid, q, pids=["D002"], k=30)
        none = search_text(uow, cid, "perovskite", pids=[])
        unknown = search_text(uow, cid, "perovskite", pids=["D999"])
        small = search_text(uow, cid, q, k=2)
    assert {r["pid"] for r in both} == {"D001", "D002"}
    assert only and {r["pid"] for r in only} == {"D002"}
    assert none == [] and unknown == [] and len(small) == 2


def test_read_page_whole_page_and_total(services, cid, pdfs):
    pages = pdf_page_texts(pdfs["D001"])
    with services.db.uow() as uow:
        got = read_page(uow, cid, "D001", 3)
        missing = read_page(uow, cid, "D001", len(pages) + 1)
        other = read_page(uow, cid, "D999", 1)
        by_run = read_page(uow, "run_" + cid[4:], "D001", 3)
    assert got["pid"] == "D001" and got["page"] == 3 and got["pages"] == len(pages)
    assert got["text"] == clean_text(pages[2]) and "NiCl2 is 0.01 M" in got["text"]
    assert missing is None and other is None
    assert by_run == got  # идентификатор прогона run_* принимается


@pytest.mark.parametrize("q", ['"', "'", '"unbalanced', "a AND OR NOT b", "NEAR(a b)", "x* y^ z:", "(((", "a-b -c +d",
                               "SELECT * FROM text_chunks; --", "'); DROP TABLE text_chunks; --", "col:val {a b}",
                               "éè 中文", "%_%", "\\", "   ", "the of and", "", "abc\x00def"])
def test_special_characters_do_not_crash(services, cid, q):
    with services.db.uow() as uow:
        res = search_text(uow, cid, q)
        assert isinstance(res, list)
        assert _count(uow, S.text_chunks) > 0


def test_empty_query_and_unknown_corpus(services, cid):
    with services.db.uow() as uow:
        assert search_text(uow, cid, "") == []
        assert search_text(uow, "crp_nope", "perovskite") == []
        assert read_page(uow, "crp_nope", "D001", 1) is None


def test_reindex_replaces_chunks(services, cid):
    with services.db.uow() as uow:
        paper = uow.conn.execute(sa.select(S.papers.c.id).where(
            S.papers.c.corpus_id == cid, S.papers.c.publication_id == "D001")).scalar()

        def mine() -> int:
            return uow.conn.execute(sa.select(sa.func.count()).select_from(S.text_chunks).where(
                S.text_chunks.c.paper_id == paper)).scalar()

        before = mine()
        n = index_paper(uow, cid, paper, "D001", ["Zorblax quantum widget calibration.", "", "Second page about gizmos."])
        assert n == 2 and before > 2 and mine() == 2
        assert search_text(uow, cid, "bivalent metal chlorides", pids=["D001"]) == []
        hit = search_text(uow, cid, "zorblax calibration")
        assert [(h["pid"], h["page"]) for h in hit] == [("D001", 1)]
        assert read_page(uow, cid, "D001", 3)["pages"] == 3 and read_page(uow, cid, "D001", 2)["text"] == ""
        # повторная замена не плодит строк и не оставляет следов в FTS
        assert index_paper(uow, cid, paper, "D001", ["Only gizmos now."]) == 1
        assert search_text(uow, cid, "zorblax") == []
        assert [h["page"] for h in search_text(uow, cid, "gizmos", pids=["D001"])] == [1]


def test_rebuild_cli(services, cid, monkeypatch):
    from faktura.cli.main import cli
    from faktura.textindex import sources

    _clear(services)
    s = services.settings
    monkeypatch.setattr(sources, "_STORES", {})  # ленивая индексация не должна подменять CLI
    monkeypatch.setenv("FAKTURA_DB_URL", s.db_url)
    args = ["textindex", "rebuild", "--run", "run_" + cid[4:], "--db", s.db_url, "--storage-dir", str(s.storage_dir)]
    r = CliRunner().invoke(cli, args, standalone_mode=False)
    assert r.exception is None, r.output
    assert "статей 2" in r.output
    with services.db.uow() as uow:
        res = search_text(uow, cid, PHRASE)
        assert res and res[0]["pid"] == "D001"
    bad = CliRunner().invoke(cli, ["textindex", "rebuild", "--run", "crp_nope", "--db", s.db_url, "--storage-dir",
                                   str(s.storage_dir)], standalone_mode=False)
    assert bad.return_value == 1
    with services.db.uow() as uow:  # повторный rebuild заменяет, а не дублирует
        n = _count(uow, S.text_chunks)
    assert CliRunner().invoke(cli, args, standalone_mode=False).exception is None
    with services.db.uow() as uow:
        assert _count(uow, S.text_chunks) == n


def test_lazy_index_on_first_search(services, cid):
    _clear(services)
    t0 = time.monotonic()
    with services.db.uow() as uow:
        res = search_text(uow, cid, PHRASE)
    assert res and res[0]["pid"] == "D001"
    assert time.monotonic() - t0 < 10


def test_search_is_fast(services, cid):
    with services.db.uow() as uow:
        search_text(uow, cid, "perovskite")
        t0 = time.perf_counter()
        for _ in range(5):
            search_text(uow, cid, "efficiency of perovskite solar cells with additive")
        assert (time.perf_counter() - t0) / 5 < 0.2


def test_deleted_paper_not_found(services, cid):
    with services.db.uow() as uow:
        pid = uow.conn.execute(sa.select(S.papers.c.id).where(
            S.papers.c.corpus_id == cid, S.papers.c.publication_id == "D002")).scalar()
        uow.papers.update(pid, deleted_at=uow.now())
    with services.db.uow() as uow:
        assert {r["pid"] for r in search_text(uow, cid, "perovskite", k=30)} == {"D001"}
        assert read_page(uow, cid, "D002", 1) is None


def test_purge_removes_chunks(services, cid):
    with services.db.uow() as uow:
        assert _count(uow, S.text_chunks) > 0
        uow.corpora.soft_delete(cid)
        uow.conn.execute(S.corpora.update().where(S.corpora.c.id == cid).values(
            deleted_at=uow.now() - timedelta(days=400)))
        assert uow.corpora.purge_expired() == 1
        assert _count(uow, S.text_chunks) == 0 and _count(uow, S.text_pages) == 0


# --- чанкинг и источники текста ------------------------------------------------------------------

def test_clean_text_ligatures_hyphens_whitespace():
    t = "The eﬃcient ﬁlm exhib-\nits  high ﬂux.\n\n\nNew para\r\nline  two <!-- page 3 -->"
    assert clean_text(t) == "The efficient film exhibits high flux.\n\nNew para line two"


def test_chunking_bounds_overlap_and_no_empties():
    sent = "Perovskite solar cells reach high efficiency under standard illumination. "
    text = "\n\n".join(sent * 8 for _ in range(6))
    chunks = chunk_page(text)
    assert len(chunks) > 3 and all(c.strip() for c in chunks)
    assert all(len(c) <= 1200 + 150 for c in chunks)
    for a, b in zip(chunks, chunks[1:]):  # перекрытие: начало следующего чанка уже было в конце предыдущего
        assert b[:30] in a
    assert chunk_page("") == [] and chunk_page("   \n ") == []
    assert chunk_page("short") == ["short"]
    assert all(len(c) <= 1200 for c in chunk_page("x" * 5000))


def test_chunks_never_mix_pages(services, cid):
    with services.db.uow() as uow:
        rows = uow.conn.execute(sa.select(S.text_chunks.c.paper_id, S.text_chunks.c.page, S.text_chunks.c.text)).all()
        pages = {(r.paper_id, r.page): r.text.replace("\n", " ") for r in uow.conn.execute(sa.select(
            S.text_pages.c.paper_id, S.text_pages.c.page, S.text_pages.c.text))}
    assert rows
    for r in rows:
        body = " ".join(r.text.split())
        page = " ".join(pages[(r.paper_id, r.page)].split())
        assert body in page or (body[:50] in page and body[-50:] in page)


def test_merge_pages_uses_ocr_only_for_short_text_layer():
    pdf = ["x" * 80, "", "tiny", "y" * 60]
    ocr = ["ocr0", "OCR text of page two <table><tr><td>cell</td></tr></table>", "ocr page three is long enough?", ""]
    got = merge_pages(pdf, ocr)
    assert got[0] == pdf[0] and got[3] == pdf[3]
    assert got[1].startswith("OCR text of page two") and "<td>" not in got[1]
    assert got[2] == "ocr page three is long enough?"
    assert merge_pages(pdf, None) == pdf


def test_index_pdf_scanned_page_uses_ocr(services, tmp_path, cid):
    import fitz

    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((72, 100), "This text layer page has plenty of selectable words about photovoltaic modules and more.")
    doc.new_page()  # скан без текстового слоя
    path = tmp_path / "scan.pdf"
    doc.save(str(path))
    doc.close()
    with services.db.uow() as uow:
        paper = uow.papers.get(uow.conn.execute(sa.select(S.papers.c.id).where(
            S.papers.c.corpus_id == cid, S.papers.c.publication_id == "D002")).scalar())
    n = index_pdf(services.db, paper, path, ["", "Recognized by OCR: hysteresis index was 0.12 for the champion device."])
    assert n == 2
    with services.db.uow() as uow:
        got = read_page(uow, cid, "D002", 2)
        hit = search_text(uow, cid, "hysteresis champion", pids=["D002"])
    assert got["pages"] == 2 and "hysteresis" in got["text"]
    assert [h["page"] for h in hit] == [2]


def test_ml_page_texts_parsing():
    from faktura.domain.registry import get_domain
    from faktura.ml.generic import parse_generic_response, pdf_dataset_to_generic
    from faktura.ml.response import page_texts

    assert page_texts({"page_texts": ["a", "", "b"]}) == ["a", "", "b"]
    assert page_texts({"page_texts": ["", " "]}) is None and page_texts({"page_texts": "x"}) is None
    assert page_texts(None) is None
    body = {"ok": True, "dataset": {"publications": [{"title": "T"}]}, "meta": {"page_texts": ["p1", "p2"]}}
    raw = pdf_dataset_to_generic(body, "D001", "rag")
    assert raw["page_texts"] == ["p1", "p2"]
    out = parse_generic_response(raw, "D001", get_domain("rag"))
    assert out.page_texts == ["p1", "p2"] and out.raw["page_texts"] == ["p1", "p2"]
    assert parse_generic_response({"tables": {"publications": [{}]}}, "D001", get_domain("rag")).page_texts is None
