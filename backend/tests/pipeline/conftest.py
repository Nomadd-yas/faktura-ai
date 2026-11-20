"""Фикстуры модуля pipeline: сервисы на SQLite и моке, шпионы ML, поддельные часы, синтетические бандлы.

Все тесты модуля идут против заглушек: до реализации они падают на NotImplementedError, не на импорте.
Импортируются только публичные имена, которые уже есть в пакете.
"""
from __future__ import annotations

import json
import re
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import pytest
from click.testing import CliRunner

from faktura.domain.errors import ExtractError
from faktura.domain.extraction import ExtractOutcome, ExtractRequest, MlHealth
from faktura.domain.ports import LayerFunction, NormResult, ParsedNumber
from faktura.domain.records import (Device, Layer, Material, Measurement, PaperBundle, Publication, Result)

# --------------------------------------------------------------------------------------------
# Нормализатор-пустышка: детерминированная таблица замен, реализует протокол Normalizer.
# Изолирует apply_normalization от модуля norm (ML-2): проверяется оркестровка шагов 1-8.
# --------------------------------------------------------------------------------------------

_METRICS = {"V_oc": "Voc", "Voc": "Voc", "V oc": "Voc", "PCE": "PCE", "FF": "FF", "Jsc": "Jsc", "J_sc": "Jsc",
            "Rs": "Rs", "T80": "T80"}
_LAYER_FUNC = {
    "buffer": LayerFunction(value="ETL", note=None),
    "ETL": LayerFunction(value="ETL"),
    "Ag grid": LayerFunction(value="front_contact", note=None),
    "anti-reflection coating": LayerFunction(value="interlayer", note="исходно: anti-reflection coating"),
    "AR": LayerFunction(value="interlayer", note="исходно: AR"),
}


class FakeNormalizer:
    """Только протокол Normalizer; считает вызовы, чтобы тесты видели, что бэк идёт через протокол."""
    version = "fake-1"
    dictionary_version = "fake-dict-1"

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def _rec(self, name: str, *a) -> None:
        self.calls.append((name, a))

    def parse_number(self, text: str) -> ParsedNumber:
        self._rec("parse_number", text)
        m = re.match(r"^\s*([-+]?\d+(?:[.,]\d+)?)\s*(?:±\s*(\d+(?:[.,]\d+)?))?\s*(.*)$", text or "")
        if not m:
            return ParsedNumber(ok=False)
        spread = Decimal(m.group(2).replace(",", ".")) if m.group(2) else None
        return ParsedNumber(ok=True, value=Decimal(m.group(1).replace(",", ".")), spread=spread,
                            rest=m.group(3) or None)

    def normalize(self, metric_name, raw_value, raw_unit, header_unit=None) -> NormResult:
        self._rec("normalize", metric_name, raw_value, raw_unit)
        if raw_value is None or raw_value == "":
            return NormResult(norm_status="not_applicable")
        p = self.parse_number(raw_value)
        if not p.ok:
            return NormResult(norm_status="failed", issue="unit_inconsistency")
        val, unit = p.value, raw_unit
        status = "ok"
        if metric_name == "Voc" and raw_unit == "mV":
            val, unit, status = val / Decimal(1000), "V", "converted"
        if metric_name == "FF" and raw_unit == "fraction":
            val, unit, status = val * Decimal(100), "%", "converted"
        # ± в самом значении («18.4 ± 0.5») -> разброс
        return NormResult(normalized_value=val, normalized_unit=unit, norm_status=status, spread_value=p.spread)

    def normalize_field(self, table, column, raw_value, raw_unit) -> NormResult:
        self._rec("normalize_field", table, column, raw_value, raw_unit)
        if raw_value in (None, ""):
            return NormResult(norm_status="not_applicable")
        p = self.parse_number(str(raw_value))
        if not p.ok:
            return NormResult(norm_status="failed")
        return NormResult(normalized_value=p.value, normalized_unit=raw_unit, norm_status="ok")

    def match_metric(self, text: str) -> str | None:
        self._rec("match_metric", text)
        return _METRICS.get(text)

    def canon_material(self, text: str) -> str | None:
        self._rec("canon_material", text)
        return {"MAPbI3": "CH3NH3PbI3", "CsPbI2Br": "CsPbI2Br"}.get(text)

    def layer_function(self, text: str, position: int | None = None) -> LayerFunction:
        self._rec("layer_function", text, position)
        return _LAYER_FUNC.get(text, LayerFunction(value=None, note=None))

    def normalize_encapsulation(self, value) -> tuple[bool | None, str | None]:
        self._rec("normalize_encapsulation", value)
        if value in (None, ""):
            return None, None
        low = str(value).lower()
        if low in ("false", "unencapsulated", "no"):
            return False, None
        if low in ("true", "encapsulated", "yes"):
            return True, None
        return None, "не распознано"

    def normalize_spectrum(self, text):
        self._rec("normalize_spectrum", text)
        if text is None:
            return None
        return {"AM 1.5G": "AM1.5G", "AM1.5G": "AM1.5G", "AM1.5": "AM1.5G"}.get(text, text)

    def format_display(self, value, unit, *, digits=None) -> str:
        return f"{value} {unit or ''}".strip()

    def preview(self, collection, param, raw):
        raise NotImplementedError

    def dictionary(self):
        raise NotImplementedError

    def called(self, name: str) -> list[tuple]:
        return [a for n, a in self.calls if n == name]


@pytest.fixture
def fake_norm() -> FakeNormalizer:
    return FakeNormalizer()


@pytest.fixture
def profile():
    from faktura.domain.profile import builtin_solar_profile
    return builtin_solar_profile()


# --------------------------------------------------------------------------------------------
# Синтетический bundle для apply_normalization и build_facts
# --------------------------------------------------------------------------------------------

def make_bundle(pid: str = "S001") -> PaperBundle:
    pub = Publication(publication_id=pid, title="Synthetic", year=2023, DOI="10.1/syn", authors=["A B."],
                      in_scope=True)
    b = PaperBundle(publication=pub)
    b.materials = [Material(publication_id=pid, material_id="M1", material_name_raw="MAPbI3", formula_normalized=None,
                            bandgap_value="1.55", bandgap_unit="eV"),
                   Material(publication_id=pid, material_id="M2", material_name_raw="no gap", bandgap_value=None,
                            bandgap_unit="eV")]
    b.devices = [Device(publication_id=pid, device_id="DV1", absorber_material_id="M1", active_area="0.16",
                        area_unit="cm2", encapsulation="unencapsulated"),
                 Device(publication_id=pid, device_id="DV2", absorber_material_id="M1", active_area=None,
                        area_unit="cm2")]
    b.layers = [
        Layer(publication_id=pid, layer_id="L1", device_id="DV1", layer_order=1, layer_function="buffer",
              layer_material="CdS", layer_thickness="50", thickness_unit="nm"),
        Layer(publication_id=pid, layer_id="L2", device_id="DV1", layer_order=2, layer_function="Ag grid",
              layer_material="Ag", layer_thickness="100", thickness_unit="nm"),
        Layer(publication_id=pid, layer_id="L3", device_id="DV1", layer_order=3,
              layer_function="anti-reflection coating", layer_material="MgF2", layer_thickness="100",
              thickness_unit="nm"),
        Layer(publication_id=pid, layer_id="L4", device_id="DV1", layer_order=4, layer_function="absorber",
              layer_material="MAPbI3", layer_thickness=None, thickness_unit="nm"),
    ]
    b.measurements = [Measurement(publication_id=pid, measurement_id="MS1", device_id=["DV1"],
                                  measurement_type="J-V", spectrum="AM 1.5G", light_intensity="100",
                                  intensity_unit="mW/cm2")]
    b.results = [
        Result(publication_id=pid, result_id="R1", device_id="DV1", measurement_id="MS1", material_id="M1",
               metric_name="V_oc", raw_value="1197", raw_unit="mV", extraction_status="extracted", source_page=5,
               source_fragment="Voc 1197 mV"),
        Result(publication_id=pid, result_id="R2", device_id="DV1", measurement_id="MS1", material_id="M1",
               metric_name="PCE", raw_value="12.89", raw_unit="%", spread_value="0.4", extraction_status="extracted",
               source_page=5, source_fragment="PCE 12.89 %"),
        Result(publication_id=pid, result_id="R3", device_id="DV1", measurement_id="MS1", material_id="M1",
               metric_name="PCE", raw_value="18.4 ± 0.5", raw_unit="%", spread_value=None,
               extraction_status="extracted", source_page=6, source_fragment="18.4 ± 0.5 %"),
        Result(publication_id=pid, result_id="R4", device_id="DV1", measurement_id="MS1", metric_name="Jsc",
               raw_value=None, raw_unit="mA/cm2", extraction_status="present_not_extractable", source_page=6,
               source_fragment="Jsc (graph)"),
        Result(publication_id=pid, result_id="R5", device_id="DV1", measurement_id="MS1", metric_name="FF",
               raw_value="abc", raw_unit="%", extraction_status="extracted", source_page=6,
               source_fragment="FF abc"),
    ]
    return b


@pytest.fixture
def synth_bundle() -> Callable[..., PaperBundle]:
    return make_bundle


# --------------------------------------------------------------------------------------------
# ParseAdapter-пустышка
# --------------------------------------------------------------------------------------------

class FakeParser:
    def __init__(self, size=(600.0, 800.0), rects_for: dict[str, list] | None = None) -> None:
        self.size = size
        self.find_calls: list[tuple[int, str]] = []
        self.rects_for = rects_for or {}

    def parse(self, pdf_path):
        from faktura.domain.extraction import ParsedDocument, ParsedPage
        return ParsedDocument(page_count=1, pages=[ParsedPage(n=1, width_pt=self.size[0], height_pt=self.size[1])],
                              text_by_page=["text"])

    def render_page(self, pdf_path, n, scale=1.5, fmt="webp"):
        return b""

    def find_rects(self, pdf_path, page, needle):
        from faktura.domain.records import Rect
        self.find_calls.append((page, needle))
        return self.rects_for.get(needle, [Rect(x=0.1, y=0.2, w=0.3, h=0.05)])

    def page_size(self, pdf_path, page):
        return self.size


@pytest.fixture
def fake_parser() -> FakeParser:
    return FakeParser()


# --------------------------------------------------------------------------------------------
# Часы и sleep
# --------------------------------------------------------------------------------------------

class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000_000.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.now += s

    def advance(self, s: float) -> None:
        self.now += s


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    """Подменяет time.time/monotonic/sleep глобально: sleep двигает часы и не ждёт."""
    c = FakeClock()
    monkeypatch.setattr(time, "time", c.time)
    monkeypatch.setattr(time, "monotonic", c.time)
    monkeypatch.setattr(time, "sleep", c.sleep)
    return c


# --------------------------------------------------------------------------------------------
# Шпион ML-1 над любым ExtractAdapter
# --------------------------------------------------------------------------------------------

class SpyExtractor:
    """Оборачивает внутренний экстрактор. fail_on: publication_id -> код ExtractError.
    health_seq: очередь ответов health ('ok'|'down'|'degraded'); после исчерпания остаётся последний.
    Пока последний ответ health = down, extract бросает ml_unavailable (как реальный контейнер).
    down_all: ML недоступен всегда."""
    name = "mock"

    def __init__(self, inner, health_seq: list[str] | None = None) -> None:
        self.inner = inner
        self.calls: list[str] = []
        self.health_calls = 0
        self.fail_on: dict[str, str] = {}
        self.health_seq = list(health_seq) if health_seq else None
        self.state = self.health_seq[0] if self.health_seq else "ok"
        self.down_all = False

    def _is_down(self) -> bool:
        return self.down_all or self.state == "down"

    def extract(self, req: ExtractRequest) -> ExtractOutcome:
        self.calls.append(req.publication_id)
        if self._is_down():
            raise ExtractError("ml_unavailable", retryable=True)
        code = self.fail_on.get(req.publication_id)
        if code:
            raise ExtractError(code, retryable=code == "ml_unavailable")
        return self.inner.extract(req)

    def health(self) -> MlHealth:
        self.health_calls += 1
        if self.down_all:
            return MlHealth(status="down", retry_after_s=1)
        if self.health_seq is not None:
            self.state = self.health_seq.pop(0) if len(self.health_seq) > 1 else self.health_seq[0]
            if self.state == "down":
                return MlHealth(status="down", retry_after_s=1)
            return MlHealth(status=self.state, model_version="mock-1")
        return self.inner.health()


# --------------------------------------------------------------------------------------------
# Сервисы на SQLite и локальном хранилище с моком на mini_gold (D001, D003)
# --------------------------------------------------------------------------------------------

def collect_dir(directory: Path, names: list[str] | None = None):
    """CollectResult из папки без участия pdfio.collect_inputs (модуль pdfio проверяется отдельно)."""
    from faktura.pdfio.inputs import CollectResult, InputFile
    files = []
    for p in sorted(Path(directory).glob("*.pdf")):
        if names is None or p.name in names:
            files.append(InputFile(name=p.name, rel_path=p.name, publication_id=p.stem, path=p, source="dir"))
    return CollectResult(files=files)


@pytest.fixture
def collect():
    return collect_dir


class Env:
    """Контекст теста: сервисы, рабочее пространство, пользователь, шпион ML."""

    def __init__(self, services, spy, workspace_id: str, user_id: str) -> None:
        self.services = services
        self.spy = spy
        self.workspace_id = workspace_id
        self.user_id = user_id

    def uow(self):
        return self.services.db.uow()

    def new_corpus(self, name="C", **kw):
        from faktura.pipeline import CorpusService
        return CorpusService(self.services).create(self.workspace_id, self.user_id, name, "solar", **kw)

    def add_dir(self, directory: Path, corpus_id: str, **kw):
        from faktura.pipeline import CorpusService
        return CorpusService(self.services).add_inputs(corpus_id, collect_dir(directory), mode=kw.pop("mode", "api"),
                                                       **kw)

    def run_service(self):
        from faktura.pipeline import RunService
        return RunService(self.services)

    def full_run(self, corpus_id: str, *, kind="full", **kw):
        rs = self.run_service()
        run = rs.start(corpus_id, kind, user_id=self.user_id, **kw)
        return rs.execute(run.id)


@pytest.fixture
def make_env(settings, mini_gold_dir, clock):
    """Фабрика Env. overrides уходят в build_services; mock_gold_dir по умолчанию - mini_gold (D001, D003)."""
    from faktura.ml.adapters import MockGoldExtractor
    from faktura.pipeline import InlineExecutor, InMemoryEventBus, build_services
    from faktura.storage import seed_admin

    def _make(*, gold_dir: Path | None = None, health_seq=None, extractor=None, real_extractor=False,
              **overrides) -> Env:
        """real_extractor=True: экстрактор строит сам build_services по settings (кеш, http на http_ml)."""
        settings.mock_gold_dir = gold_dir or mini_gold_dir
        inner = extractor or MockGoldExtractor(settings.mock_gold_dir, "all")
        spy = SpyExtractor(inner, health_seq)
        base = dict(executor=InlineExecutor(), bus=InMemoryEventBus())
        if not real_extractor:
            base["extractor_factory"] = lambda: spy
        base.update(overrides)
        services = build_services(settings, **base)
        services.db.create_all()
        seed_admin(services.db, settings)
        with services.db.uow() as uow:
            admin = uow.users.get_by_email(settings.admin_email)
        return Env(services, spy, admin.workspace_id, admin.id)

    return _make


@pytest.fixture
def pdf_dir(tmp_path, make_pdf):
    """Папка с D001.pdf и D003.pdf (есть в mini_gold)."""
    d = tmp_path / "pdfs"
    make_pdf(["Table 1 Jsc 15.28 mA/cm2 Voc 1.197 V"], name="D001.pdf", directory=d)
    make_pdf(["Yttrium-doped Sn3O4 text"], name="D003.pdf", directory=d)
    return d


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------

class CliResult:
    def __init__(self, r) -> None:
        self.r = r
        self.exit_code = r.exit_code
        self.output = r.output
        self.stderr = getattr(r, "stderr", "") if getattr(r, "stderr_bytes", None) is not None else ""
        self.exception = r.exception

    def __repr__(self) -> str:
        return f"<exit={self.exit_code} out={self.output[-600:]!r} exc={self.exception!r}>"


@pytest.fixture
def run_cli(monkeypatch, tmp_path):
    """run_cli(*args) -> CliResult. Не даёт заглушке сойти за «код 1»: NotImplementedError - всегда сбой теста.
    Окружение чистое: ни FAKTURA_* из оболочки, ни глобальный кеш пользователя (cache_dir -> tmp)."""
    from faktura.cli.main import cli

    for k in list(__import__("os").environ):
        if k.startswith("FAKTURA_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FAKTURA_CACHE_DIR", str(tmp_path / "default_cache"))

    def _run(*args: str, input: str | None = None, env: dict | None = None) -> CliResult:
        try:
            runner = CliRunner(mix_stderr=False)
        except TypeError:  # click >= 8.2: stderr всегда отдельно
            runner = CliRunner()
        r = runner.invoke(cli, [str(a) for a in args], input=input, env=env or {}, catch_exceptions=True)
        assert not isinstance(r.exception, NotImplementedError), f"заглушка: {r.exception!r}"
        res = CliResult(r)
        try:
            res.stderr = r.stderr
        except ValueError:
            res.stderr = r.output
        return res

    return _run


@pytest.fixture
def fake_validator(tmp_path):
    """Фабрика поддельных check_submission.py. kind: ok | errors | crash."""

    def _make(kind: str = "ok") -> Path:
        p = tmp_path / f"check_{kind}.py"
        body = {
            "ok": "import sys\nprint('Проверка пройдена. Ошибок: 0')\nsys.exit(0)\n",
            "errors": "import sys\nprint('ОШИБКА: results.csv: поле metric_value не число')\nprint('Ошибок: 1')\nsys.exit(1)\n",
            "crash": "raise RuntimeError('boom')\n",
        }[kind]
        p.write_text(body, encoding="utf-8")
        return p

    return _make


# --------------------------------------------------------------------------------------------
# HTTP-шпион ML-1 для CLI --extractor http (httpx.Client получает MockTransport)
# --------------------------------------------------------------------------------------------

class HttpMlSpy:
    def __init__(self, example: dict) -> None:
        self.example = example
        self.extract_calls: list[str] = []
        self.health_calls = 0
        self.health_status: list[str] = ["ok"]
        self.state = "ok"  # последний ответ /health; пока "down", /ml/extract отвечает 503
        self.fail_on: set[str] = set()

    def handler(self, request):
        import httpx
        path = request.url.path
        if path.endswith("/health"):
            self.health_calls += 1
            st = self.health_status.pop(0) if len(self.health_status) > 1 else self.health_status[0]
            self.state = st
            code = 200 if st != "down" else 503
            return httpx.Response(code, json={"status": st, "model_version": "spy-1"})
        if path.endswith("/ml/extract"):
            m = re.search(rb'name="publication_id"\r\n\r\n([^\r]+)', request.content)
            pid = m.group(1).decode() if m else "UNKNOWN"
            self.extract_calls.append(pid)
            if self.state == "down":
                return httpx.Response(503, json={"error": "down"})
            if pid in self.fail_on:
                return httpx.Response(502, json={"error": "boom"})
            data = json.loads(json.dumps(self.example))
            data["publication"]["publication_id"] = pid
            return httpx.Response(200, json=data)
        return httpx.Response(404)


@pytest.fixture
def http_ml(monkeypatch, example_7_0_1) -> HttpMlSpy:
    import httpx

    spy = HttpMlSpy(example_7_0_1)
    orig = httpx.Client.__init__

    def patched(self, *a, **kw):
        kw["transport"] = httpx.MockTransport(spy.handler)
        orig(self, *a, **kw)

    monkeypatch.setattr(httpx.Client, "__init__", patched)
    return spy


_PROGRESS_RE = re.compile(r"^\[\s*\d+/\s*\d+\]\s+(done|failed|skipped)\s+\S+\.pdf\s+facts=\d+\s+issues=\d+", re.M)


@pytest.fixture
def progress_re():
    return _PROGRESS_RE


class FakeInner:
    """Экстрактор на синтетических бандлах: FakeInner(doi=..., title=..., year=..., in_scope=True).
    doi/title могут быть функцией publication_id."""
    name = "mock"

    def __init__(self, doi=None, title=None, year=2023, in_scope=True, authors=("Ivanov A.",)) -> None:
        self.doi, self.title, self.year, self.in_scope, self.authors = doi, title, year, in_scope, list(authors)
        self.calls: list[str] = []

    def _v(self, x, pid):
        return x(pid) if callable(x) else x

    def extract(self, req):
        self.calls.append(req.publication_id)
        b = make_bundle(req.publication_id)
        b.publication.DOI = self._v(self.doi, req.publication_id)
        b.publication.title = self._v(self.title, req.publication_id)
        b.publication.year = self.year
        b.publication.authors = list(self.authors)
        b.publication.in_scope = self.in_scope
        return ExtractOutcome(bundle=b, model_version="fake-m1")

    def health(self):
        return MlHealth(status="ok", model_version="fake-m1")


@pytest.fixture
def fake_inner():
    return FakeInner


class EventLog:
    """Собирает события шины в фоновом потоке. start() до прогона, stop() после."""

    def __init__(self, bus, workspace_id: str, last_event_id: str | None = None) -> None:
        import threading
        self.items: list[tuple[str, str, dict]] = []
        self.sub = bus.subscribe(workspace_id, last_event_id)
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self) -> None:
        try:
            for ev in self.sub:
                self.items.append(ev)
        except Exception:
            pass

    def stop(self) -> "EventLog":
        self._t.join(timeout=0.3)
        self.sub.close()
        self._t.join(timeout=1.0)
        return self

    def names(self) -> list[str]:
        return [e[1] for e in self.items]

    def of(self, name: str) -> list[dict]:
        return [e[2] for e in self.items if e[1] == name]


@pytest.fixture
def event_log():
    return EventLog
