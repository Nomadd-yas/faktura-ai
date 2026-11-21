"""create_app: ленивое подключение роутеров по faktura.api.ROUTER_MODULES, ленивая сборка Services, нет циклов."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import faktura.api as api_pkg
from faktura.api import API_PREFIX

SRC = Path(__file__).resolve().parents[2] / "src"


def _write_pkg(root: Path, name: str, init_source: str) -> None:
    pkg = root / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(textwrap.dedent(init_source), encoding="utf-8")


@pytest.fixture
def fake_pkgs(tmp_path, monkeypatch):
    """Каталог с поддельными пакетами-роутерами, добавленный в sys.path."""
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path
    for name in list(sys.modules):
        if name.startswith("fake_api_"):
            del sys.modules[name]


class TestCreateApp:
    def test_returns_fastapi(self, settings, services):
        from faktura.api.base import create_app

        assert isinstance(create_app(settings, services), FastAPI)

    def test_all_routes_under_api_prefix(self, app):
        paths = [r.path for r in app.routes if isinstance(r, APIRoute)]
        assert paths, "нет ни одного маршрута"
        assert all(p.startswith(API_PREFIX + "/") for p in paths), [p for p in paths if not p.startswith(API_PREFIX)]

    @pytest.mark.parametrize("method,path", [
        ("GET", "/health"), ("POST", "/auth/login"), ("POST", "/auth/refresh"), ("POST", "/auth/logout"),
        ("GET", "/auth/me"), ("GET", "/me"), ("PATCH", "/me"), ("GET", "/collections"),
        ("GET", "/admin/users"), ("POST", "/admin/users"), ("PATCH", "/admin/users/{user_id}"),
    ])
    def test_base_routes_registered(self, app, method, path):
        import re

        norm = lambda p: re.sub(r"\{[^}]+\}", "{}", p)
        have = {(m, norm(r.path)) for r in app.routes if isinstance(r, APIRoute) for m in r.methods}
        assert (method, norm(API_PREFIX + path)) in have, f"нет маршрута {method} {path}"

    def test_two_apps_are_independent(self, settings, services, admin_creds, h):
        from faktura.api.base import create_app

        a, b = create_app(settings, services), create_app(settings, services)
        assert a is not b
        ca, cb = TestClient(a), TestClient(b)
        for _ in range(5):
            h.login(ca, admin_creds[0], "wrong-password")
        # лимитер входа не общий между экземплярами приложения
        assert h.login(cb, *admin_creds).status_code == 200

    def test_none_settings_uses_get_settings(self, monkeypatch, settings, services):
        from faktura.api.base import create_app
        from faktura.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setattr("faktura.config.get_settings", lambda: settings)
        app = create_app(None, services)
        assert TestClient(app).get(f"{API_PREFIX}/health").status_code == 200


class TestLazyServices:
    def test_given_services_are_used_and_build_not_called(self, monkeypatch, settings, services):
        import faktura.pipeline as pipeline
        from faktura.api.base import create_app

        def boom(*a, **kw):
            raise AssertionError("build_services не должен вызываться, если Services переданы")

        monkeypatch.setattr(pipeline, "build_services", boom)
        app = create_app(settings, services)
        assert TestClient(app).get(f"{API_PREFIX}/health").status_code == 200

    def test_services_none_calls_build_services_lazily(self, monkeypatch, settings, services):
        import faktura.pipeline as pipeline
        from faktura.api.base import create_app

        calls = []

        def fake_build(s):
            calls.append(s)
            return services

        monkeypatch.setattr(pipeline, "build_services", fake_build, raising=True)
        app = create_app(settings, None)
        r = TestClient(app).get(f"{API_PREFIX}/health")
        assert r.status_code == 200
        assert len(calls) == 1 and calls[0] is settings
        # повторные запросы не пересобирают
        TestClient(app).get(f"{API_PREFIX}/health")
        assert len(calls) == 1


class TestRouterModules:
    def test_missing_package_is_skipped_with_warning(self, monkeypatch, settings, services, caplog):
        from faktura.api.base import create_app

        monkeypatch.setattr(api_pkg, "ROUTER_MODULES", ("faktura.api.does_not_exist_xyz",))
        with caplog.at_level(logging.WARNING):
            app = create_app(settings, services)
        assert any("does_not_exist_xyz" in rec.getMessage() for rec in caplog.records)
        assert TestClient(app).get(f"{API_PREFIX}/health").status_code == 200

    def test_present_package_routers_are_mounted_under_prefix(self, monkeypatch, fake_pkgs, settings, services, h):
        from faktura.api.base import create_app

        _write_pkg(fake_pkgs, "fake_api_ok", """
            from fastapi import APIRouter
            r = APIRouter()
            @r.get("/ping-fake")
            def ping():
                return {"pong": True}
            ROUTERS = [r]
        """)
        monkeypatch.setattr(api_pkg, "ROUTER_MODULES", ("fake_api_ok",))
        app = create_app(settings, services)
        resp = TestClient(app).get(f"{API_PREFIX}/ping-fake")
        assert resp.status_code == 200 and resp.json() == {"pong": True}
        assert resp.headers.get("X-Request-Id")  # middleware действует и на чужие роутеры

    def test_router_error_format_is_unified(self, monkeypatch, fake_pkgs, settings, services, h):
        from faktura.api.base import create_app

        _write_pkg(fake_pkgs, "fake_api_err", """
            from fastapi import APIRouter
            from faktura.domain.errors import AppError
            r = APIRouter()
            @r.get("/boom-fake")
            def boom():
                raise AppError("conflict", "Прогон уже идёт")
            ROUTERS = [r]
        """)
        monkeypatch.setattr(api_pkg, "ROUTER_MODULES", ("fake_api_err",))
        resp = TestClient(create_app(settings, services)).get(f"{API_PREFIX}/boom-fake")
        err = h.assert_error(resp, 409, "conflict")
        assert err["message"] == "Прогон уже идёт"

    def test_multiple_packages_missing_and_present_mixed(self, monkeypatch, fake_pkgs, settings, services):
        from faktura.api.base import create_app

        _write_pkg(fake_pkgs, "fake_api_mix", """
            from fastapi import APIRouter
            r = APIRouter()
            @r.get("/mix-fake")
            def mix():
                return {}
            ROUTERS = [r]
        """)
        monkeypatch.setattr(api_pkg, "ROUTER_MODULES", ("fake_api_absent_1", "fake_api_mix", "fake_api_absent_2"))
        app = create_app(settings, services)
        assert TestClient(app).get(f"{API_PREFIX}/mix-fake").status_code == 200

    def test_broken_import_in_existing_package_fails_startup(self, monkeypatch, fake_pkgs, settings, services):
        from faktura.api.base import create_app

        _write_pkg(fake_pkgs, "fake_api_broken", "raise ImportError('boom: сломанный пакет')\n")
        monkeypatch.setattr(api_pkg, "ROUTER_MODULES", ("fake_api_broken",))
        with pytest.raises(ImportError):
            create_app(settings, services)

    def test_syntax_error_in_existing_package_fails_startup(self, monkeypatch, fake_pkgs, settings, services):
        from faktura.api.base import create_app

        _write_pkg(fake_pkgs, "fake_api_syntax", "def broken(:\n")
        monkeypatch.setattr(api_pkg, "ROUTER_MODULES", ("fake_api_syntax",))
        with pytest.raises(SyntaxError):
            create_app(settings, services)

    def test_missing_dependency_inside_package_is_not_swallowed(self, monkeypatch, fake_pkgs, settings, services):
        """ModuleNotFoundError ДРУГОГО модуля (не самого пакета) роняет запуск, а не пропускается."""
        from faktura.api.base import create_app

        _write_pkg(fake_pkgs, "fake_api_dep", "import module_that_does_not_exist_qwerty\nROUTERS = []\n")
        monkeypatch.setattr(api_pkg, "ROUTER_MODULES", ("fake_api_dep",))
        with pytest.raises(ModuleNotFoundError) as e:
            create_app(settings, services)
        assert "module_that_does_not_exist_qwerty" in str(e.value)

    def test_package_without_routers_attribute_fails(self, monkeypatch, fake_pkgs, settings, services):
        from faktura.api.base import create_app

        _write_pkg(fake_pkgs, "fake_api_norouters", "X = 1\n")
        monkeypatch.setattr(api_pkg, "ROUTER_MODULES", ("fake_api_norouters",))
        with pytest.raises((AttributeError, ImportError, RuntimeError)):
            create_app(settings, services)

    def test_real_packages_listed(self):
        assert api_pkg.ROUTER_MODULES == ("faktura.api.corpus", "faktura.api.papers", "faktura.api.review")


class TestNoCircularImports:
    def _run(self, code: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "PYTHONUTF8": "1"}
        # чистый процесс: проверяем sys.modules после импорта
        prelude = "import sys; sys.path.insert(0, " + repr(str(SRC)) + ")\n"
        return subprocess.run([sys.executable, "-c", prelude + code], capture_output=True, text=True,
                              env=env, timeout=60)

    def test_importing_base_does_not_import_router_packages(self):
        r = self._run(
            "import sys, faktura.api.base, faktura.api.base.deps, faktura.api.base.serializers, faktura.api.base.auth\n"
            "bad = [m for m in ('faktura.api.corpus','faktura.api.papers','faktura.api.review') if m in sys.modules]\n"
            "assert not bad, bad\n")
        assert r.returncode == 0, r.stderr

    @pytest.mark.parametrize("pkg", ["corpus", "papers", "review"])
    def test_router_package_imports_alone(self, pkg):
        """Пакет роутеров импортируется сам по себе (импортирует только api.base, а не наоборот)."""
        r = self._run(f"import faktura.api.{pkg}\n")
        assert r.returncode == 0, r.stderr

    def test_create_app_import_chain_clean(self):
        r = self._run("from faktura.api.base import create_app\nassert callable(create_app)\n")
        assert r.returncode == 0, r.stderr
