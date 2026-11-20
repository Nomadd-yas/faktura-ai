"""Этап B, (perf): 100 строк таблицы фактов при 100 тыс. фактов - до 300 мс; overview - до 100 мс. Допуск x3.

Запуск: pytest -m perf tests/storage/test_p1_perf.py (по умолчанию исключён addopts).
"""
from __future__ import annotations

import time
from decimal import Decimal

import pytest

from faktura.domain.entities import FactFilter, PaperStats, ReviewStatus, SortKey

pytestmark = [pytest.mark.perf, pytest.mark.slow]

TOLERANCE = 3
N_PAPERS, PER_PAPER = 100, 1000


@pytest.fixture(scope="module")
def big(tmp_path_factory):
    from faktura.domain.entities import FactDraft
    from faktura.domain.records import PaperBundle, Publication, Result, RecordMeta
    from faktura.storage import Database

    url = f"sqlite:///{(tmp_path_factory.mktemp('perf') / 'perf.db').as_posix()}"
    db = Database(url)
    db.create_all()
    with db.uow() as u:
        corpus = u.corpora.create(workspace_id="ws_perf", name="Perf")
    names = ["PCE", "Voc", "Jsc", "FF"]
    for k in range(N_PAPERS):
        pid = f"P{k:04d}"
        with db.uow() as u:
            p = u.papers.create(workspace_id="ws_perf", corpus_id=corpus.id, publication_id=pid, title=f"T{k}",
                                year=2000 + k % 25)
            results, drafts = [], []
            for i in range(PER_PAPER):
                rid = f"R{i:04d}"
                v = Decimal((k * 7 + i) % 3000) / Decimal(100)
                results.append(Result(publication_id=pid, result_id=rid, metric_name=names[i % 4],
                                      normalized_value=v, meta=RecordMeta(ml_order=i)))
                drafts.append(FactDraft(ref=rid, target={"entity": "results", "id": rid, "field": "metric_value"},
                                        param="metric_value", metric_name=names[i % 4], normalized_value=v,
                                        confidence=(i % 100) / 100, source_page=i % 20 + 1))
            bundle = PaperBundle(publication=Publication(publication_id=pid), results=results)
            u.papers.save_processed(p.id, bundle, drafts, [], [], stats=PaperStats(facts_count=PER_PAPER),
                                    model_version="m", versions={})
    return db


def _best_of(fn, n=3):
    times = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t)
    return min(times)


def test_table_100_rows_first_page(big):
    def run():
        with big.uow() as u:
            page = u.facts.query(FactFilter(workspace_id="ws_perf"), [SortKey(field="normalized_value", dir="desc")],
                                 100, None)
            assert len(page.data) == 100

    assert _best_of(run) <= 0.300 * TOLERANCE


def test_table_100_rows_deep_page_keyset(big):
    with big.uow() as u:
        flt, sort = FactFilter(workspace_id="ws_perf"), [SortKey(field="normalized_value")]
        cursor = None
        for _ in range(500):  # 50 000 строк вглубь
            cursor = u.facts.query(flt, sort, 100, cursor).next_cursor

    def run():
        with big.uow() as u:
            assert len(u.facts.query(flt, sort, 100, cursor).data) == 100

    assert _best_of(run) <= 0.300 * TOLERANCE


def test_table_with_filters(big):
    flt = FactFilter(workspace_id="ws_perf", metric_names=["PCE"], value_min=Decimal("5"),
                     review_status=[ReviewStatus.new])

    def run():
        with big.uow() as u:
            assert len(u.facts.query(flt, [SortKey(field="confidence", dir="desc"), SortKey(field="metric_name")],
                                     100, None).data) == 100

    assert _best_of(run) <= 0.300 * TOLERANCE


def test_facets_on_100k(big):
    def run():
        with big.uow() as u:
            u.facts.facets(FactFilter(workspace_id="ws_perf"), ["metric_name", "review_status"])

    assert _best_of(run) <= 0.300 * TOLERANCE


def test_overview_100ms(big):
    def run():
        with big.uow() as u:
            ov = u.stats.overview("ws_perf")
            assert ov["totals"]["facts"] == N_PAPERS * PER_PAPER

    assert _best_of(run) <= 0.100 * TOLERANCE


def test_overview_does_not_scan_facts(big):
    """Время overview не зависит от числа фактов: сравнение с пустой БД в пределах x3 порога."""
    from faktura.storage import Database
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as d:
        small = Database(f"sqlite:///{pathlib.Path(d, 's.db').as_posix()}")
        small.create_all()
        with small.uow() as u:
            u.corpora.create(workspace_id="ws_perf", name="Perf")

        def run_small():
            with small.uow() as u:
                u.stats.overview("ws_perf")

        def run_big():
            with big.uow() as u:
                u.stats.overview("ws_perf")

        try:
            assert _best_of(run_big, 5) <= max(_best_of(run_small, 5) * 10, 0.1)
        finally:
            small.dispose()  # Windows: открытый пулом файл БД мешает удалить временный каталог
