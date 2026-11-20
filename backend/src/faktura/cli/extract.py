"""`faktura extract` локально (план 5.6, P0): настройки -> SQLite (временный файл или --db) -> корпус ->
collect_inputs -> add_inputs(mode="cli") -> прогон inline -> build_output -> код выхода 0|1|2|3.

Прогресс - в stderr: `[ 61/148] done    cigs_flex.pdf  facts=16 issues=1`.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import click

from ..domain.entities import FileReport, RunStatus
from ..domain.errors import AppError
from ..domain.profile import BUILTIN_SOLAR_ALIAS, BUILTIN_SOLAR_ID, ExtractionProfile, builtin_solar_profile

EXIT_OK, EXIT_USAGE, EXIT_PARTIAL, EXIT_CHECK = 0, 1, 2, 3
FORMATS = ("csv", "json", "xlsx")


class UsageProblem(Exception):
    """Ошибка запуска: код 1, ничего не пишется."""


@dataclass
class ExtractOptions:
    in_path: Path
    out_dir: Path
    domain: str = "solar"
    check_script: Path | None = None
    profile: ExtractionProfile = field(default_factory=builtin_solar_profile)
    formats: tuple[str, ...] = ("csv",)
    concurrency: int = 4
    extractor: str | None = None
    extract_url: str | None = None
    raw_dir: Path | None = None
    mock_issues: str | None = None
    gold_dir: Path | None = None
    model: str = "latest"
    parse_url: str | None = None
    retry_delays: tuple[float, ...] = (5.0, 15.0, 30.0)
    no_dedupe: bool = False
    ocr: str = "auto"
    fail_fast: bool = False
    db: Path | None = None
    cache_dir: Path | None = None
    no_cache: bool = False
    no_backend_rules: bool = False


# --- разбор опций ------------------------------------------------------------------------------------

def parse_retry_delays(text: str | None) -> tuple[float, ...]:
    if text is None:
        return (5.0, 15.0, 30.0)
    out: list[float] = []
    for part in str(text).split(","):
        p = part.strip()
        if not p:
            continue
        try:
            v = float(p)
        except ValueError as e:
            raise click.BadParameter(f"ожидаются числа секунд через запятую, например 5,15,30: «{text}»") from e
        if v < 0 or v != v or v == float("inf"):
            raise click.BadParameter(f"задержка не может быть отрицательной: «{p}»")
        out.append(v)
    return tuple(out)


def parse_formats(text: str | None) -> tuple[str, ...]:
    items = [x.strip().lower() for x in (text or "csv").split(",") if x.strip()]
    bad = [x for x in items if x not in FORMATS]
    if bad or not items:
        raise click.BadParameter(f"форматы: {', '.join(FORMATS)}; получено «{text}»")
    if "csv" not in items:
        items.insert(0, "csv")
    return tuple(dict.fromkeys(items))


def load_profile(value: str | None) -> ExtractionProfile:
    v = (value or BUILTIN_SOLAR_ALIAS).strip()
    if v in (BUILTIN_SOLAR_ALIAS, BUILTIN_SOLAR_ID):
        return builtin_solar_profile()
    p = Path(v)
    if not p.is_file():
        raise click.BadParameter(f"профиль: {BUILTIN_SOLAR_ALIAS} или путь к JSON-файлу; файла «{v}» нет")
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        return ExtractionProfile.model_validate(data)
    except (ValueError, UnicodeDecodeError) as e:
        raise click.BadParameter(f"профиль {p.name} не читается: {e}") from e


def _inside(child: Path, parent: Path) -> bool:
    c, p = child.resolve(), parent.resolve()
    return c == p or p in c.parents


# --- выполнение ---------------------------------------------------------------------------------------

def _progress_printer(err: Callable[[str], None]) -> Callable[[dict], None]:
    def show(ev: dict) -> None:
        total = int(ev.get("total") or 0)
        w = len(str(total)) if total else 1
        line = (f"[{int(ev['index']):>{w}}/{total:>{w}}] {ev['status']:<6} {ev['name']}  "
                f"facts={int(ev.get('facts') or 0)} issues={int(ev.get('issues') or 0)}")
        if ev.get("status") == "failed" and ev.get("error"):
            line += f"  error={ev['error'].get('code')}"
        if ev.get("from_cache"):
            line += "  (из кеша)"
        err(line)

    return show


def run_extract(o: ExtractOptions, err: Callable[[str], None], out: Callable[[str], None]) -> int:
    from ..config import Settings
    from ..pdfio.inputs import ZipLimits, collect_inputs
    from ..pipeline import CorpusService, InlineExecutor, InMemoryEventBus, RunService, build_output, build_services
    from ..pipeline.build import make_extractor

    if o.parse_url:
        err(f"--parse-url {o.parse_url}: в волне 1 PDF разбирается локально, параметр игнорируется")
    if o.ocr == "force":
        err("--ocr force: OCR в волне 1 не выполняется (сканы отмечаются needs_ocr)")
    settings = Settings()
    out_dir = Path(o.out_dir)
    kind = o.extractor or ("http" if o.extract_url else "mock")
    cache_dir = None if o.no_cache else (Path(o.cache_dir) if o.cache_dir else settings.cache_dir)
    if cache_dir is not None and _inside(Path(cache_dir), out_dir):
        raise UsageProblem(f"Кеш ответов ML (--cache-dir {cache_dir}) не может лежать внутри --out {out_dir}: "
                           f"выходная папка должна содержать только набор сдачи")
    if o.check_script is not None and not Path(o.check_script).is_file():
        raise UsageProblem(f"Скрипт проверки не найден: {o.check_script}")
    if kind == "raw" and o.raw_dir is not None and not Path(o.raw_dir).is_dir():
        raise UsageProblem(f"Каталог ответов ML не найден: {o.raw_dir}")
    if o.gold_dir is not None and not Path(o.gold_dir).is_dir():
        raise UsageProblem(f"Каталог эталона не найден: {o.gold_dir}")

    work = Path(tempfile.mkdtemp(prefix="faktura_cli_"))
    services = None
    try:
        db_path = Path(o.db).resolve() if o.db else work / "faktura.db"
        if o.db:
            db_path.parent.mkdir(parents=True, exist_ok=True)
        update = dict(db_url=f"sqlite:///{db_path.as_posix()}", storage_backend="local", storage_dir=work / "storage",
                      cache_dir=Path(cache_dir) if cache_dir is not None else None, extractor=kind,
                      extract_url=o.extract_url or settings.extract_url,
                      extract_retry_delays=",".join(f"{d:g}" for d in o.retry_delays) or "0")
        if o.gold_dir is not None:
            update["mock_gold_dir"] = Path(o.gold_dir)
        if o.mock_issues:
            update["mock_issues"] = o.mock_issues
        settings = settings.model_copy(update=update)
        try:
            extractor = make_extractor(settings, kind=kind, url=o.extract_url, raw_dir=o.raw_dir,
                                       retry_delays=o.retry_delays, use_cache=cache_dir is not None)
        except AppError as e:
            raise UsageProblem(e.message) from e

        collected = collect_inputs(Path(o.in_path), limits=ZipLimits.from_settings(settings), work_dir=work / "in")
        rejected_pdf = [r for r in collected.rejected
                        if not (r.code == "unsupported_file" and not r.name.lower().endswith(".pdf"))]
        for r in collected.rejected:
            if r not in rejected_pdf:
                err(f"пропущен {r.rel_path or r.name}: {r.message}")
        if not collected.files and not rejected_pdf:
            raise UsageProblem(f"Во входе нет PDF: {o.in_path}")

        services = build_services(settings, executor=InlineExecutor(), bus=InMemoryEventBus(),
                                  extractor_factory=lambda: extractor)
        services.db.create_all()
        profile = o.profile
        if o.no_backend_rules:
            profile = profile.model_copy(update={"flags": profile.flags.model_copy(update={"backend_rules": False})})
        cs = CorpusService(services)
        ws = f"cli_{uuid.uuid4().hex[:12]}"
        corpus = cs.create(ws, "cli", "faktura extract", o.domain, kind="extract")
        intake = cs.add_inputs(corpus.id, collected.model_copy(update={"rejected": rejected_pdf}), mode="cli",
                               dedupe=not o.no_dedupe)
        rejected_rows: list[FileReport] = []
        for r in intake.rejected:
            name = r.rel_path or r.name
            rejected_rows.append(FileReport(file=name, publication_id=Path(r.name).stem, status="rejected",
                                            error_code=r.code, message=r.message))
            err(f"отклонён {name}: {r.code} - {r.message}")
        n_files = len(intake.added)
        err(f"Вход: {n_files} PDF, отклонено {len(rejected_rows)}; экстрактор: {kind}"
            + (f" ({o.extract_url})" if kind == "http" and o.extract_url else ""))
        rs = RunService(services, profile=profile, retry_delays=o.retry_delays, dedupe=not o.no_dedupe,
                        on_file=_progress_printer(err), say=err, ocr=o.ocr)
        run = rs.start(corpus.id, "full", model_version=o.model or "latest", concurrency=o.concurrency,
                       user_id="cli")
        run = rs.execute(run.id, fail_fast=o.fail_fast)
        res = build_output(services, corpus.id, out_dir, profile=profile, check_script=o.check_script, run=run,
                           extra_files=rejected_rows, input_info={"path": str(o.in_path)}, formats=o.formats)
        code = res.exit_code_hint
        if code == EXIT_OK and run.status != RunStatus.done:
            code = EXIT_PARTIAL
        c = run.counts
        err(f"Готово: файлов {c.total}, разобрано {c.done}, с ошибкой {c.failed}, отменено {c.cancelled}; "
            f"набор сдачи: {out_dir}")
        if res.check is not None:
            ck = res.check
            if ck.crashed:
                err("Проверка сдачи упала:")
                err(ck.raw_output.strip()[-4000:])
            elif ck.ok is False or ck.errors:
                err(f"Проверка сдачи: ошибок {len(ck.errors)}")
                for line in ck.errors[:50]:
                    err(f"  ОШИБКА: {line}")
            else:
                err(f"Проверка сдачи пройдена: ошибок 0, предупреждений {len(ck.warnings)}")
        elif any(p.level == "error" for p in res.problems):
            err("Строгая проверка набора нашла ошибки: см. _meta/errors.log")
        err(f"Код выхода: {code}")
        return code
    finally:
        if services is not None:
            try:
                services.db.dispose()
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(work, ignore_errors=True)
