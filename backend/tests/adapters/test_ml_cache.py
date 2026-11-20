"""ml.RawDirExtractor, MlCache, CachedExtractor, resolve_model_version (REQ-066, REQ-072)."""
from __future__ import annotations

import json

import pytest

from faktura.domain.errors import ExtractError
from faktura.domain.ports import ExtractAdapter
from faktura.ml import CachedExtractor, MlCache, RawDirExtractor, resolve_model_version

SHA = "a" * 64


# ---------------- RawDirExtractor ----------------
def test_raw_dir_reads_publication_json(tmp_path, example_7_0_1, extract_req):
    (tmp_path / "D021.json").write_text(json.dumps(example_7_0_1), encoding="utf-8")
    ext = RawDirExtractor(tmp_path)
    assert isinstance(ext, ExtractAdapter) and ext.name == "raw"
    out = ext.extract(extract_req("D021"))
    assert out.bundle.publication_id == "D021" and len(out.bundle.results) == 3
    assert out.model_version == "extract-0.3"


def test_raw_dir_health_always_ok(tmp_path):
    assert RawDirExtractor(tmp_path / "does-not-exist").health().status == "ok"


def test_raw_dir_missing_file(tmp_path, extract_req):
    with pytest.raises(ExtractError):
        RawDirExtractor(tmp_path).extract(extract_req("D404"))


def test_raw_dir_invalid_json(tmp_path, extract_req):
    (tmp_path / "D021.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ExtractError) as e:
        RawDirExtractor(tmp_path).extract(extract_req("D021"))
    assert e.value.code == "ml_schema_mismatch"


def test_raw_dir_invalid_envelope(tmp_path, extract_req):
    (tmp_path / "D021.json").write_text(json.dumps({"model_version": "x"}), encoding="utf-8")
    with pytest.raises(ExtractError) as e:
        RawDirExtractor(tmp_path).extract(extract_req("D021"))
    assert e.value.code == "ml_schema_mismatch"


def test_raw_dir_utf8_bom_tolerated(tmp_path, example_7_0_1, extract_req):
    (tmp_path / "D021.json").write_bytes(b"\xef\xbb\xbf" + json.dumps(example_7_0_1).encode("utf-8"))
    assert RawDirExtractor(tmp_path).extract(extract_req("D021")).bundle.publication_id == "D021"


# ---------------- MlCache ----------------
@pytest.fixture(params=["path", "store"])
def cache(request, tmp_path, memory_store):
    return MlCache(tmp_path / "cache") if request.param == "path" else MlCache(memory_store)


def test_cache_miss_returns_none(cache):
    assert cache.get(SHA, "v1") is None
    assert cache.last_version(SHA) is None


def test_cache_put_get_roundtrip(cache, example_7_0_1):
    cache.put(SHA, "v1", example_7_0_1)
    assert cache.get(SHA, "v1") == example_7_0_1


def test_cache_key_is_sha_and_version(cache, example_7_0_1):
    cache.put(SHA, "v1", example_7_0_1)
    assert cache.get(SHA, "v2") is None
    assert cache.get("b" * 64, "v1") is None


def test_cache_last_version_tracks_latest_put(cache, example_7_0_1):
    cache.put(SHA, "v1", example_7_0_1)
    assert cache.last_version(SHA) == "v1"
    cache.put(SHA, "v2", dict(example_7_0_1, model_version="v2"))
    assert cache.last_version(SHA) == "v2"
    assert cache.get(SHA, "v1") == example_7_0_1  # старая версия сохраняется


def test_cache_last_is_per_sha(cache, example_7_0_1):
    cache.put(SHA, "v1", example_7_0_1)
    cache.put("c" * 64, "v9", example_7_0_1)
    assert cache.last_version(SHA) == "v1"


def test_cache_overwrite_same_key(cache, example_7_0_1):
    cache.put(SHA, "v1", {"a": 1})
    cache.put(SHA, "v1", example_7_0_1)
    assert cache.get(SHA, "v1") == example_7_0_1


def test_cache_layout_on_disk(tmp_path, example_7_0_1):
    MlCache(tmp_path).put(SHA, "extract-0.3", example_7_0_1)
    assert (tmp_path / SHA / "extract-0.3.json").is_file()
    assert (tmp_path / SHA / "_last.json").is_file()


def test_cache_layout_in_store(memory_store, example_7_0_1):
    MlCache(memory_store).put(SHA, "extract-0.3", example_7_0_1)
    keys = list(memory_store.data)
    assert any(k.endswith(f"{SHA}/extract-0.3.json") for k in keys)
    assert any(k.endswith(f"{SHA}/_last.json") for k in keys)


def test_cache_unicode_roundtrip(cache):
    raw = {"publication": {"title": "Перовскитный элемент, КПД 23 %"}}
    cache.put(SHA, "v1", raw)
    assert cache.get(SHA, "v1") == raw


def test_cache_corrupted_entry_is_miss_or_error(tmp_path):
    c = MlCache(tmp_path)
    (tmp_path / SHA).mkdir()
    (tmp_path / SHA / "v1.json").write_text("{broken", encoding="utf-8")
    try:
        assert c.get(SHA, "v1") is None
    except (ExtractError, ValueError):
        pass


# ---------------- CachedExtractor ----------------
def test_cached_name_and_protocol(fake_extractor, cache):
    c = CachedExtractor(fake_extractor, cache)
    assert isinstance(c, ExtractAdapter) and c.name == fake_extractor.name


def test_first_call_hits_inner_and_fills_cache(fake_extractor, cache, extract_req):
    c = CachedExtractor(fake_extractor, cache)
    out = c.extract(extract_req(sha=SHA))
    assert len(fake_extractor.calls) == 1 and out.from_cache is False
    assert cache.get(SHA, "extract-0.3") is not None


def test_second_call_same_sha_and_version_skips_inner(fake_extractor, cache, extract_req):
    c = CachedExtractor(fake_extractor, cache)
    first = c.extract(extract_req(sha=SHA))
    second = c.extract(extract_req(sha=SHA))
    assert len(fake_extractor.calls) == 1
    assert second.from_cache is True
    assert second.bundle.model_dump() == first.bundle.model_dump()
    assert second.model_version == "extract-0.3"


def test_other_version_calls_inner_again(fake_extractor, cache, extract_req):
    c = CachedExtractor(fake_extractor, cache)
    c.extract(extract_req(sha=SHA, version="extract-0.3"))
    fake_extractor.version = "extract-0.4"
    c.extract(extract_req(sha=SHA, version="extract-0.4"))
    assert len(fake_extractor.calls) == 2


def test_other_sha_calls_inner_again(fake_extractor, cache, extract_req):
    c = CachedExtractor(fake_extractor, cache)
    c.extract(extract_req(sha="a" * 64))
    c.extract(extract_req(sha="b" * 64))
    assert len(fake_extractor.calls) == 2


def test_offline_uses_last_json(fake_extractor, cache, extract_req):
    c = CachedExtractor(fake_extractor, cache)
    c.extract(extract_req(sha=SHA, version="latest"))  # latest -> extract-0.3 через health
    fake_extractor.health_status = "down"
    fake_extractor.fail = ExtractError("ml_unavailable", retryable=True)
    calls_before = len(fake_extractor.calls)
    out = c.extract(extract_req(sha=SHA, version="latest"))
    assert out.from_cache is True and out.bundle.publication_id == "D021"
    assert len(fake_extractor.calls) == calls_before  # к ML не ходили


def test_offline_without_cache_raises_unavailable(fake_extractor, cache, extract_req):
    fake_extractor.health_status = "down"
    fake_extractor.fail = ExtractError("ml_unavailable", retryable=True)
    c = CachedExtractor(fake_extractor, cache)
    with pytest.raises(ExtractError) as e:
        c.extract(extract_req(sha=SHA, version="latest"))
    assert e.value.code == "ml_unavailable" and e.value.retryable is True


def test_inner_error_not_cached(fake_extractor, cache, extract_req):
    fake_extractor.fail = ExtractError("pdf_broken")
    c = CachedExtractor(fake_extractor, cache)
    with pytest.raises(ExtractError):
        c.extract(extract_req(sha=SHA))
    assert cache.get(SHA, "extract-0.3") is None and cache.last_version(SHA) is None


def test_non_retryable_error_not_masked_by_cache(fake_extractor, cache, extract_req):
    c = CachedExtractor(fake_extractor, cache)
    c.extract(extract_req(sha=SHA, version="extract-0.3"))
    fake_extractor.version = "extract-0.5"
    fake_extractor.fail = ExtractError("pdf_encrypted")
    with pytest.raises(ExtractError) as e:
        c.extract(extract_req(sha=SHA, version="extract-0.5"))
    assert e.value.code == "pdf_encrypted"


def test_cached_health_delegates(fake_extractor, cache):
    c = CachedExtractor(fake_extractor, cache)
    assert c.health().model_version == "extract-0.3"
    fake_extractor.health_status = "down"
    assert c.health().status == "down"


# ---------------- resolve_model_version ----------------
def test_resolve_latest_uses_health(fake_extractor):
    fake_extractor.version = "extract-0.9"
    assert resolve_model_version("latest", fake_extractor) == "extract-0.9"
    assert fake_extractor.health_calls == 1


def test_resolve_latest_when_down_is_none(fake_extractor):
    fake_extractor.health_status = "down"
    assert resolve_model_version("latest", fake_extractor) is None


def test_resolve_explicit_version_returned(fake_extractor):
    assert resolve_model_version("extract-0.3", fake_extractor) == "extract-0.3"


def test_resolve_with_mock(mini_gold_dir):
    from faktura.ml import MockGoldExtractor

    m = MockGoldExtractor(mini_gold_dir)
    assert resolve_model_version("latest", m) == m.health().model_version
