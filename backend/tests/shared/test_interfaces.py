"""Каркас: все публичные интерфейсы модулей импортируются и имеют заявленные сигнатуры.

Тела - NotImplementedError до реализации модулями; эти тесты защищают от случайного изменения контрактов.
"""
from __future__ import annotations

import importlib
import inspect

import pytest

INTERFACES = {
    "faktura.norm": ["NORM_VERSION", "DICTIONARY_VERSION", "LocalNormalizer", "FakturaNormAdapter", "get_normalizer"],
    "faktura.pdfio": ["inspect_pdf", "collect_inputs", "safe_unzip", "dedupe_names", "LocalParseAdapter",
                      "bbox_to_rects", "validator_norm", "PypdfQuoteIndex", "title_key", "CollectResult",
                      "InputFile", "Rejected", "ZipLimits", "NameDedupeResult"],
    "faktura.ml": ["parse_extract_response", "MockGoldExtractor", "HttpExtractor", "RawDirExtractor", "MlCache",
                   "CachedExtractor", "resolve_model_version", "build_extractor"],
    "faktura.ml.mock_server": ["app", "create_mock_app"],
    "faktura.checks": ["RULES_VERSION", "BACKEND_RULES", "run_backend_rules", "check_ml_issues", "merge_issues",
                       "assign_keys", "link_duplicates", "comparability_key", "paper_quality", "paper_coverage",
                       "check_paper", "CheckOutcome"],
    "faktura.export": ["select_for_submission", "materialize_manual_fact", "sanitize", "number_issues",
                       "write_submission", "strict_validate", "run_check_script", "write_meta", "build_manifest",
                       "zip_dir", "export_flat", "export_relational", "preview"],
    "faktura.passport": ["build_passport", "passport_markdown"],
    "faktura.evaluation": ["evaluate", "evaluate_issues", "accuracy_response", "LocalEval", "FakturaEvalAdapter"],
    "faktura.storage": ["Database", "UnitOfWork", "LocalFileStore", "S3FileStore", "build_file_store",
                        "seed_admin", "encode_cursor", "decode_cursor"],
    "faktura.pipeline": ["build_services", "apply_normalization", "build_facts", "detect_semantic_duplicate",
                         "PaperProcessor", "CorpusService", "RunService", "build_output", "recheck_paper",
                         "transfer_review", "InlineExecutor", "ThreadExecutor", "InMemoryEventBus"],
    "faktura.cli.main": ["cli"],
    "faktura.api.base": ["create_app"],
    "faktura.api.base.deps": ["get_services", "get_uow", "current_user", "admin_user", "page_params",
                              "page_response", "mutation_response", "stub_response", "idempotent"],
    "faktura.api.base.serializers": ["user_out", "corpus_out", "corpus_file_out", "run_out", "paper_out",
                                     "fact_out", "anchor_out", "issue_out", "history_out", "export_out"],
    "faktura.api.base.auth": ["issue_tokens", "decode_access", "LoginLimiter", "RateLimiter"],
    "faktura.api.corpus": ["ROUTERS"],
    "faktura.api.papers": ["ROUTERS"],
    "faktura.api.review": ["ROUTERS"],
}


@pytest.mark.parametrize("module,names", list(INTERFACES.items()))
def test_interface_names(module, names):
    mod = importlib.import_module(module)
    missing = [n for n in names if not hasattr(mod, n)]
    assert not missing, f"{module}: нет {missing}"


def test_router_modules_have_routers():
    from faktura.api import ROUTER_MODULES

    for name in ROUTER_MODULES:
        assert isinstance(importlib.import_module(name).ROUTERS, list)


def test_extract_adapters_have_protocol_methods():
    from faktura.domain.ports import ExtractAdapter
    from faktura.ml import CachedExtractor, HttpExtractor, MockGoldExtractor, RawDirExtractor

    for cls in (MockGoldExtractor, HttpExtractor, RawDirExtractor, CachedExtractor):
        assert callable(getattr(cls, "extract")) and callable(getattr(cls, "health"))
    assert hasattr(ExtractAdapter, "extract")


def test_normalizer_implements_protocol_methods():
    from faktura.domain.ports import Normalizer
    from faktura.norm import FakturaNormAdapter, LocalNormalizer

    proto = [n for n, v in vars(Normalizer).items() if inspect.isfunction(v) and not n.startswith("_")]
    assert len(proto) == 11
    for cls in (LocalNormalizer, FakturaNormAdapter):
        assert [n for n in proto if not callable(getattr(cls, n, None))] == []


def test_cli_commands():
    from faktura.cli.main import cli

    assert {"extract", "import-raw", "eval", "check", "users", "serve"} <= set(cli.commands)
    extract_opts = {p.name for p in cli.commands["extract"].params}
    for opt in ("domain", "in_path", "out_dir", "check_script", "profile", "formats", "concurrency", "extractor",
                "extract_url", "raw_dir", "mock_issues", "gold_dir", "model", "parse_url", "retry_delays",
                "no_dedupe", "ocr", "fail_fast", "db", "cache_dir", "no_cache", "no_backend_rules", "server"):
        assert opt in extract_opts, opt

