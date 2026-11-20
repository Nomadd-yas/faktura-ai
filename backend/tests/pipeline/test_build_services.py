"""build_services: единственное место выбора реализаций портов (план 5.6)."""
from __future__ import annotations

import pytest

from faktura.domain.ports import EvalAdapter, EventBus, Executor, ExtractAdapter, FileStore, Normalizer, ParseAdapter
from faktura.domain.services import Services
from faktura.pipeline import InlineExecutor, InMemoryEventBus, build_services


def test_default_wiring_satisfies_ports(settings):
    s = build_services(settings)
    assert isinstance(s, Services)
    assert s.settings is settings
    assert isinstance(s.store, FileStore)
    assert isinstance(s.parser, ParseAdapter)
    assert isinstance(s.normalizer, Normalizer)
    assert isinstance(s.bus, EventBus)
    assert isinstance(s.executor, Executor)
    assert isinstance(s.evaluator, EvalAdapter)
    assert isinstance(s.extractor_factory(), ExtractAdapter)


def test_mock_extractor_selected_by_settings(settings):
    settings.extractor = "mock"
    assert build_services(settings).extractor_factory().name == "mock"


def test_http_extractor_selected_when_configured(settings):
    settings.extractor = "http"
    settings.extract_url = "http://ml.invalid:8081"
    assert build_services(settings).extractor_factory().name == "http"


def test_http_without_url_is_config_error(settings):
    settings.extractor = "http"
    settings.extract_url = None
    with pytest.raises(Exception) as ei:
        build_services(settings).extractor_factory()
    assert not isinstance(ei.value, NotImplementedError)


def test_overrides_replace_fields(settings):
    ex, bus, marker = InlineExecutor(), InMemoryEventBus(), object()
    s = build_services(settings, executor=ex, bus=bus, extractor_factory=lambda: marker)
    assert s.executor is ex
    assert s.bus is bus
    assert s.extractor_factory() is marker


def test_unknown_override_rejected(settings):
    with pytest.raises((TypeError, ValueError)):
        build_services(settings, no_such_field=1)


def test_db_usable_after_create_all(settings):
    s = build_services(settings)
    s.db.create_all()
    with s.db.uow() as uow:
        assert uow.corpora.list("nobody") == []


def test_storage_dir_from_settings(settings):
    s = build_services(settings)
    s.store.put_bytes("k/x.bin", b"1")
    assert (settings.storage_dir / "k" / "x.bin").exists()


def test_cache_dir_from_settings_not_default_user_dir(settings):
    # кеш ML лежит там, куда указывает settings, а не в профиле пользователя
    s = build_services(settings)
    assert s.settings.cache_dir == settings.cache_dir
