"""(data) Roundtrip REQ-120 и вехи P0/P1 на данных кейса: dev + dev_gold + реальный check_submission.py."""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import pytest

from faktura.domain.entities import CorpusFileStatus, FactFilter, RunStatus
from faktura.domain.tables import CHECK_CATALOG, EVAL_METRICS, TABLES

pytestmark = pytest.mark.data

TABLE_FILES = sorted(f"{t}.csv" for t in TABLES)


def read(d: Path, table: str) -> list[dict]:
    with open(d / f"{table}.csv", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _walk(obj, key):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            yield from _walk(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, key)


def issue_keys(rows: list[dict]) -> Counter:
    return Counter((r["publication_id"], r["check_name"],
                    frozenset(x for x in (r["record_ids"] or "").split(";") if x)) for r in rows)


@pytest.fixture(scope="module")
def dev_out(tmp_path_factory, dev_dir, dev_gold_dir, check_script):
    """Один прогон extract --in dev на моке для нескольких тестов (CLI с реальным валидатором)."""
    from click.testing import CliRunner
    from faktura.cli.main import cli
    out = tmp_path_factory.mktemp("dev_out")
    cache = tmp_path_factory.mktemp("cache")
    r = CliRunner().invoke(cli, ["extract", "--domain", "solar", "--in", str(dev_dir), "--out", str(out),
                                 "--check", str(check_script), "--extractor", "mock", "--gold-dir", str(dev_gold_dir),
                                 "--cache-dir", str(cache)], catch_exceptions=True)
    assert not isinstance(r.exception, NotImplementedError), f"заглушка: {r.exception!r}"
    return out, r


def test_dev_roundtrip_exit_0_and_layout(dev_out):
    out, r = dev_out
    assert r.exit_code == 0, r.output
    assert sorted(p.name for p in out.glob("*.csv")) == TABLE_FILES
    assert (out / "_meta").is_dir()


def test_dev_validator_clean(dev_out):
    out, _ = dev_out
    report = (out / "_meta" / "check_report.txt").read_text(encoding="utf-8")
    assert "Ошибок: 0" in report or "0 ошибок" in report.lower() or "ошибок не найдено" in report.lower()


def test_dev_eval_perfect_on_11_metrics(dev_out, dev_gold_dir, tmp_path):
    from click.testing import CliRunner
    from faktura.cli.main import cli
    out, _ = dev_out
    rep = tmp_path / "rep.json"
    r = CliRunner().invoke(cli, ["eval", "--gold", str(dev_gold_dir), "--pred", str(out), "--json", str(rep)])
    assert r.exit_code == 0, r.output
    data = json.loads(rep.read_text(encoding="utf-8"))
    assert set(data["per_metric"]) == set(EVAL_METRICS)
    vals = [v for k in ("precision", "recall") for v in _walk(data["per_metric"], k) if v is not None]
    assert vals and all(v == 1 for v in vals)


def test_dev_row_counts_equal_gold(dev_out, dev_gold_dir):
    out, _ = dev_out
    for t in ("publications", "materials", "devices", "layers", "measurements", "results"):
        assert len(read(out, t)) == len(read(dev_gold_dir, t)), t
    assert len(read(out, "results")) == 659


def test_dev_spread_values_all_present(dev_out, dev_gold_dir):
    out, _ = dev_out
    gold = {(r["publication_id"], r["result_id"]): r["spread_value"] for r in read(dev_gold_dir, "results")}
    got = {(r["publication_id"], r["result_id"]): r["spread_value"] for r in read(out, "results")}
    assert sum(1 for v in got.values() if v) == sum(1 for v in gold.values() if v) == 49
    for k, v in gold.items():
        if v:
            assert float(got[k]) == pytest.approx(float(v))


def test_dev_passthrough_rows_preserved(dev_out, dev_gold_dir):
    out, _ = dev_out
    facts_ui = json.loads((out / "_meta" / "facts_ui.json").read_text(encoding="utf-8"))
    assert sum(1 for v in _walk(facts_ui, "norm_status") if v == "passthrough") == 4


def test_dev_files_csv_all_done(dev_out):
    out, _ = dev_out
    with open(out / "_meta" / "files.csv", encoding="utf-8-sig", newline="") as f:
        st = list(csv.DictReader(f))
    assert len(st) == 21 and {r["status"] for r in st} == {"done"}


# --- замечания: roundtrip ключей ------------------------------------------------------------------------------------

def _extract(tmp_path, dev_dir, dev_gold_dir, *extra):
    from click.testing import CliRunner
    from faktura.cli.main import cli
    out = tmp_path / "o"
    r = CliRunner().invoke(cli, ["extract", "--in", str(dev_dir), "--out", str(out), "--extractor", "mock",
                                 "--gold-dir", str(dev_gold_dir), "--cache-dir", str(tmp_path / "c"), *extra])
    assert not isinstance(r.exception, NotImplementedError), f"заглушка: {r.exception!r}"
    assert r.exit_code == 0, r.output
    return out


def test_issues_roundtrip_without_backend_rules_exact(tmp_path, dev_dir, dev_gold_dir):
    out = _extract(tmp_path, dev_dir, dev_gold_dir, "--no-backend-rules")
    gold = issue_keys(read(dev_gold_dir, "issues"))
    assert sum(gold.values()) == 53
    assert issue_keys(read(out, "issues")) == gold


def test_issues_with_rules_recall_one_and_no_duplicate_keys(tmp_path, dev_dir, dev_gold_dir):
    out = _extract(tmp_path, dev_dir, dev_gold_dir)
    gold, got = issue_keys(read(dev_gold_dir, "issues")), issue_keys(read(out, "issues"))
    assert all(got[k] >= n for k, n in gold.items()), "recall эталонных ключей должен быть 1"
    # в статье нет двух записей с одним ключом, кроме тех, что уже двоятся в эталоне
    for k, n in got.items():
        assert n <= max(1, gold.get(k, 0)), k
    extras = got - gold
    assert all(c[1] in CHECK_CATALOG for c in extras)


def test_issue_ids_in_out_are_sequential_per_paper(tmp_path, dev_dir, dev_gold_dir):
    out = _extract(tmp_path, dev_dir, dev_gold_dir)
    per: dict[str, list[str]] = {}
    for r in read(out, "issues"):
        per.setdefault(r["publication_id"], []).append(r["issue_id"])
    for pid, ids in per.items():
        assert ids == [f"I{i}" for i in range(1, len(ids) + 1)], pid


# --- extract-url на mock_server == extractor mock ---------------------------------------------------------------------

def test_extract_url_on_mock_server_equals_mock(tmp_path, monkeypatch, dev_dir, dev_gold_dir, settings):
    import httpx
    from click.testing import CliRunner
    from fastapi.testclient import TestClient
    from faktura.cli.main import cli
    from faktura.ml.mock_server import create_mock_app

    settings.mock_gold_dir = dev_gold_dir
    tc = TestClient(create_mock_app(settings))

    def handler(req: httpx.Request) -> httpx.Response:
        r = tc.request(req.method, req.url.path, content=req.content,
                       headers={k: v for k, v in req.headers.items() if k.lower() != "host"})
        return httpx.Response(r.status_code, content=r.content, headers={"content-type": r.headers["content-type"]})

    orig = httpx.Client.__init__
    monkeypatch.setattr(httpx.Client, "__init__",
                        lambda self, *a, **kw: orig(self, *a, **{**kw, "transport": httpx.MockTransport(handler)}))
    a = tmp_path / "a"
    b = tmp_path / "b"
    ra = CliRunner().invoke(cli, ["extract", "--in", str(dev_dir), "--out", str(a), "--extract-url", "http://ml.test",
                                  "--cache-dir", str(tmp_path / "c1"), "--retry-delays", "0"])
    rb = CliRunner().invoke(cli, ["extract", "--in", str(dev_dir), "--out", str(b), "--extractor", "mock",
                                  "--gold-dir", str(dev_gold_dir), "--cache-dir", str(tmp_path / "c2")])
    assert ra.exit_code == rb.exit_code == 0, (ra.output, rb.output)
    for t in TABLES:
        assert read(a, t) == read(b, t), t


# --- test: 90 PDF, мок ------------------------------------------------------------------------------------------------------

@pytest.mark.slow
def test_extract_test_dir_90_rows(tmp_path, test_dir, dev_gold_dir, check_script):
    from click.testing import CliRunner
    from faktura.cli.main import cli
    out = tmp_path / "t"
    r = CliRunner().invoke(cli, ["extract", "--in", str(test_dir), "--out", str(out), "--check", str(check_script),
                                 "--extractor", "mock", "--gold-dir", str(dev_gold_dir),
                                 "--cache-dir", str(tmp_path / "c")])
    assert not isinstance(r.exception, NotImplementedError), f"заглушка: {r.exception!r}"
    assert r.exit_code == 0, r.output
    assert len(read(out, "publications")) == 90
    assert sorted(p.name for p in out.glob("*.csv")) == TABLE_FILES


# --- P1: прогон full по dev через RunService ------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def dev_env(tmp_path_factory, dev_dir, dev_gold_dir):
    """Модульная фикстура без подмены часов: мок не ждёт, пауз нет."""
    from faktura.config import Settings
    from faktura.ml.adapters import MockGoldExtractor
    from faktura.pdfio.inputs import CollectResult, InputFile
    from faktura.pipeline import CorpusService, InlineExecutor, InMemoryEventBus, RunService, build_services
    from faktura.storage import seed_admin

    tmp = tmp_path_factory.mktemp("p1")
    s = Settings(_env_file=None, case_dir=dev_dir.parent, db_url=f"sqlite:///{(tmp / 'db.sqlite').as_posix()}",
                 storage_dir=tmp / "st", cache_dir=tmp / "cache", jwt_secret="x" * 40, admin_email="a@example.com",
                 admin_password="admin-password-1", cookie_secure=False, mock_gold_dir=dev_gold_dir)
    inner = MockGoldExtractor(dev_gold_dir, "all")
    svc = build_services(s, extractor_factory=lambda: inner, executor=InlineExecutor(), bus=InMemoryEventBus())
    svc.db.create_all()
    seed_admin(svc.db, s)
    with svc.db.uow() as uow:
        admin = uow.users.get_by_email("a@example.com")
    c = CorpusService(svc).create(admin.workspace_id, admin.id, "dev", "solar")
    files = [InputFile(name=p.name, rel_path=p.name, publication_id=p.stem, path=p) for p in sorted(dev_dir.glob("*.pdf"))]
    CorpusService(svc).add_inputs(c.id, CollectResult(files=files), mode="api")
    rs = RunService(svc)
    run = rs.start(c.id, "full", user_id=admin.id)
    rs.submit(run.id)
    return svc, c, run


def test_p1_full_run_21_done(dev_env):
    svc, c, run = dev_env
    with svc.db.uow() as uow:
        files = uow.files.list(c.id)
        final = uow.runs.get(run.id)
    assert len(files) == 21 and {f.status for f in files} == {CorpusFileStatus.done}
    assert final.status == RunStatus.done and final.counts.done == 21


def test_p1_publication_only_papers(dev_env):
    svc, c, _ = dev_env
    with svc.db.uow() as uow:
        for f in uow.files.list(c.id):
            if f.publication_id in ("D006", "D016"):
                b = uow.papers.load_bundle(f.paper_id)
                assert b.results == b.devices == b.layers == b.materials == b.measurements == []


def test_p1_facts_equal_gold_result_rows(dev_env):
    svc, c, _ = dev_env
    with svc.db.uow() as uow:
        total = 0
        for f in uow.files.list(c.id):
            rows = uow.facts.query(FactFilter(paper_ids=[f.paper_id]), [], 5000, None).data
            total += sum(1 for r in rows if r.fact.target["entity"] == "results")
    assert total == 659


def test_p1_quote_found_filled_and_anchors_in_fractions(dev_env):
    svc, c, _ = dev_env
    with svc.db.uow() as uow:
        f = next(x for x in uow.files.list(c.id) if x.publication_id == "D001")
        rows = uow.facts.query(FactFilter(paper_ids=[f.paper_id]), [], 5000, None).data
    with_quote = [r.fact for r in rows if r.fact.source_fragment]
    assert with_quote and all(x.quote_found is not None for x in with_quote)
    assert sum(1 for x in with_quote if x.quote_found) / len(with_quote) > 0.5  # цитаты эталона лежат в реальных PDF
