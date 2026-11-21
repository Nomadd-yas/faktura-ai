"""CLI faktura (план 5.6).

Коды выхода extract: 0 - успех; 1 - ошибка аргументов или запуска (ничего не записано); 2 - частичный сбой
(есть failed файлы), набор записан; 3 - валидатор сдачи нашёл ошибки или упал.
Ошибки разбора аргументов click (по умолчанию код 2) приводятся к коду 1.
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from .extract import (CHECK_DEFAULT, EXIT_CHECK, EXIT_OK, EXIT_PARTIAL, EXIT_USAGE, ExtractOptions, UsageProblem, load_profile,
                      parse_formats, parse_retry_delays, run_extract)


def _err(msg: str) -> None:
    click.echo(msg, err=True)


def _out(msg: str) -> None:
    click.echo(msg)


def _safe_streams() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


class FakturaGroup(click.Group):
    """Группа команд: ошибки аргументов -> код 1 (а не 2, как у click); возвращаемое число - код выхода."""

    def main(self, args=None, prog_name=None, complete_var=None, standalone_mode=True, **extra):  # type: ignore[override]
        if not standalone_mode:
            return super().main(args, prog_name, complete_var, standalone_mode=False, **extra)
        _safe_streams()
        try:
            rv = super().main(args, prog_name, complete_var, standalone_mode=False, **extra)
        except click.exceptions.Abort:
            _err("Прервано")
            sys.exit(EXIT_USAGE)
        except click.ClickException as e:
            e.show()
            sys.exit(EXIT_USAGE)
        except KeyboardInterrupt:
            _err("Прервано")
            sys.exit(EXIT_USAGE)
        sys.exit(rv if isinstance(rv, int) else 0)


def _retry_cb(ctx, param, value):
    return parse_retry_delays(value)


def _format_cb(ctx, param, value):
    return parse_formats(value)


@click.group(cls=FakturaGroup)
@click.version_option(package_name="faktura", prog_name="faktura")
def cli() -> None:
    """Faktura: извлечение данных из научных PDF в 7 связанных CSV."""


@cli.command("extract")
@click.option("--domain", default="solar", show_default=True, type=click.Choice(["solar"]))
@click.option("--in", "in_path", required=True, type=click.Path(exists=True, path_type=Path),
              help="Папка | ZIP | .txt со списком")
@click.option("--out", "out_dir", required=True, type=click.Path(path_type=Path))
@click.option("--check", "check_script", is_flag=False, flag_value=CHECK_DEFAULT, default=None,
              type=click.Path(path_type=Path),
              help="Проверить набор check_submission.py: --check - скрипт по умолчанию, --check PATH - свой")
@click.option("--profile", default="tz-solar-v1", show_default=True, help="tz-solar-v1 | ./profile.json")
@click.option("--format", "formats", default="csv", show_default=True, callback=_format_cb,
              help="csv[,json,xlsx]; json и xlsx пишутся в _meta/")
@click.option("--concurrency", type=click.IntRange(min=1), default=4, show_default=True)
@click.option("--extractor", type=click.Choice(["mock", "http", "raw"]), default=None,
              help="По умолчанию http при --extract-url, иначе mock")
@click.option("--extract-url", default=None, help="URL контейнера ML-1")
@click.option("--raw-dir", type=click.Path(path_type=Path), default=None, help="Каталог ответов ML-1 (raw)")
@click.option("--mock-issues", type=click.Choice(["all", "ml_only"]), default=None)
@click.option("--gold-dir", type=click.Path(path_type=Path), default=None, help="Эталон для мока ML-1")
@click.option("--model", default="latest", show_default=True)
@click.option("--parse-url", default=None, help="Принимается, в волне 1 игнорируется с предупреждением")
@click.option("--retry-delays", default="5,15,30", show_default=True, callback=_retry_cb,
              help="Ожидание недоступного ML-1, секунды через запятую")
@click.option("--no-dedupe", is_flag=True, help="Не отмечать дубли (тот же файл, DOI, название)")
@click.option("--ocr", type=click.Choice(["auto", "off", "force"]), default="auto", show_default=True)
@click.option("--fail-fast", is_flag=True, help="Остановиться на первом упавшем файле")
@click.option("--db", type=click.Path(path_type=Path), default=None, help="SQLite-файл (по умолчанию временный)")
@click.option("--cache-dir", type=click.Path(path_type=Path), default=None,
              help="Кеш ответов ML-1 (вне --out)")
@click.option("--no-cache", is_flag=True)
@click.option("--no-backend-rules", is_flag=True, help="Без правил бэка: замечания только от модели")
@click.option("--server", default=None, help="URL API (удалённый режим, P2-R)")
@click.option("--api-key", default=None, hidden=True)
def extract(**kw) -> int:
    """Извлечь данные из PDF и записать 7 CSV сдачи."""
    if kw.pop("api_key", None) is not None:
        from .remote import API_KEY_MESSAGE
        _err(API_KEY_MESSAGE)
        return EXIT_USAGE
    try:
        profile = load_profile(kw.pop("profile"))
    except click.BadParameter as e:
        _err(f"Ошибка: --profile: {e.message}")
        return EXIT_USAGE
    server = kw.pop("server")
    if kw.get("check_script") is not None and str(kw["check_script"]) == CHECK_DEFAULT:
        from ..config import Settings

        default = Settings().check_script
        if default is None:
            _err("Ошибка: --check без пути: скрипт check_submission.py по умолчанию не найден "
                 "(задайте FAKTURA_CHECK_SCRIPT или --check PATH)")
            return EXIT_USAGE
        kw["check_script"] = Path(default)
    opts = ExtractOptions(profile=profile, **kw)
    try:
        if server:
            from .remote import RemoteError, run_remote
            try:
                return run_remote(opts, server, _err)
            except RemoteError as e:
                _err(f"Ошибка: {e}")
                return EXIT_USAGE
        return run_extract(opts, _err, _out)
    except UsageProblem as e:
        _err(f"Ошибка: {e}")
        return EXIT_USAGE


@cli.command("import-raw")
@click.option("--in", "in_path", required=True, type=click.Path(exists=True, path_type=Path),
              help="PDF статей (папка | ZIP)")
@click.option("--raw-dir", required=True, type=click.Path(path_type=Path), help="<publication_id>.json ответов ML-1")
@click.option("--out", "out_dir", required=True, type=click.Path(path_type=Path))
@click.option("--check", "check_script", type=click.Path(path_type=Path))
@click.option("--profile", default="tz-solar-v1", show_default=True)
@click.option("--db", type=click.Path(path_type=Path), default=None)
def import_raw(in_path, raw_dir, out_dir, check_script, profile, db) -> int:
    """= extract --extractor raw."""
    try:
        prof = load_profile(profile)
    except click.BadParameter as e:
        _err(f"Ошибка: --profile: {e.message}")
        return EXIT_USAGE
    opts = ExtractOptions(in_path=in_path, out_dir=out_dir, check_script=check_script, profile=prof,
                          extractor="raw", raw_dir=raw_dir, db=db, no_cache=True)
    try:
        return run_extract(opts, _err, _out)
    except UsageProblem as e:
        _err(f"Ошибка: {e}")
        return EXIT_USAGE


@cli.command("eval")
@click.option("--gold", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--pred", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--issues", is_flag=True, help="Ещё и замечания: recall/precision по ключу")
@click.option("--json", "json_path", type=click.Path(path_type=Path), default=None)
def eval_cmd(gold: Path, pred: Path, issues: bool, json_path: Path | None) -> int:
    """Оценка с dev_gold по 11 метрикам ТЗ."""
    import json

    from ..config import Settings
    from ..evaluation import evaluate_issues, report_markdown
    from ..export import to_jsonable
    from ..pipeline.build import _default_evaluator

    for d, what in ((gold, "--gold"), (pred, "--pred")):
        if not (d / "results.csv").is_file() or not (d / "publications.csv").is_file():
            _err(f"Ошибка: {what} {d}: нет CSV сдачи (publications.csv, results.csv)")
            return EXIT_USAGE
    try:
        evaluator = _default_evaluator(Settings())
        report = evaluator.evaluate(gold, pred)
    except Exception as e:  # noqa: BLE001
        _err(f"Ошибка оценки: {type(e).__name__}: {e}")
        return EXIT_USAGE
    iss = evaluate_issues(gold, pred) if issues else None
    _out(report_markdown(report, iss).rstrip())
    if json_path is not None:
        data = report.model_dump(mode="json")
        if iss is not None:
            data["issues"] = iss.model_dump(mode="json")
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(to_jsonable(data), ensure_ascii=False, indent=2), encoding="utf-8")
        _err(f"Отчёт: {json_path}")
    return EXIT_OK


@cli.command("check")
@click.argument("csv_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("pdf_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--script", type=click.Path(path_type=Path), default=None)
def check(csv_dir: Path, pdf_dir: Path, script: Path | None) -> int:
    """Запустить check_submission.py."""
    from ..config import Settings
    from ..export import run_check_script

    settings = Settings()
    script = script or settings.check_script
    if script is None:
        _err("Ошибка: не задан скрипт проверки: --script или FAKTURA_CHECK_SCRIPT")
        return EXIT_USAGE
    rep = run_check_script(Path(script), csv_dir, pdf_dir, timeout_s=settings.check_timeout_s)
    if rep.raw_output:
        _out(rep.raw_output.rstrip())
    if rep.crashed:
        _err("Проверка упала или не запустилась")
        return EXIT_CHECK
    return EXIT_OK if rep.ok and not rep.errors else EXIT_CHECK


# --- пользователи ---------------------------------------------------------------------------------------

def _db(url: str | None):
    from ..config import Settings
    from ..storage import Database

    if url and "://" not in url:
        url = f"sqlite:///{Path(url).resolve().as_posix()}"
    db = Database(url or Settings().db_url)
    db.create_all()
    return db


def _user_or_fail(uow, email: str):
    u = uow.users.get_by_email(email)
    if u is None:
        raise UsageProblem(f"Пользователь {email} не найден")
    return u


def _users_run(fn) -> int:
    from ..domain.errors import AppError

    try:
        fn()
        return EXIT_OK
    except UsageProblem as e:
        _err(f"Ошибка: {e}")
    except AppError as e:
        _err(f"Ошибка: {e.message}")
    except Exception as e:  # noqa: BLE001 - например, нарушение уникальности
        _err(f"Ошибка: {type(e).__name__}")
    return EXIT_USAGE


@cli.group("users", cls=click.Group)
def users() -> None:
    """Управление пользователями."""


@users.command("create")
@click.argument("email_arg", metavar="[EMAIL]", required=False)
@click.option("--email", "email_opt", default=None, help="Почта пользователя")
@click.option("--name", default=None, help="Имя (по умолчанию - часть почты до @)")
@click.option("--password", default=None, help="Пароль (или --generate)")
@click.option("--generate", is_flag=True, help="Сгенерировать пароль и показать один раз")
@click.option("--admin", is_flag=True)
@click.option("--db", default=None, help="URL БД или путь к SQLite-файлу (по умолчанию FAKTURA_DB_URL)")
def users_create(email_arg: str | None, email_opt: str | None, name: str | None, password: str | None,
                 generate: bool, admin: bool, db: str | None) -> int:
    from ..domain.security import generate_password, hash_password
    from ..storage import DEFAULT_WORKSPACE_ID

    def go() -> None:
        email = (email_opt or email_arg or "").strip()
        if not email:
            raise UsageProblem("Укажите почту: --email")
        if password is None and not generate:
            raise UsageProblem("Укажите пароль: --password или --generate")
        if password is not None and generate:
            raise UsageProblem("--password и --generate вместе не используются")
        pw = password if password is not None else generate_password()
        h = hash_password(pw)
        d = _db(db)
        try:
            with d.uow() as uow:
                if uow.users.get_by_email(email) is not None:
                    raise UsageProblem(f"Пользователь {email} уже есть")
                uow.workspaces.ensure(DEFAULT_WORKSPACE_ID)
                uow.users.create(workspace_id=DEFAULT_WORKSPACE_ID, email=email, is_admin=bool(admin),
                                 disabled=False, password_hash=h,
                                 name=(name or "").strip() or email.split("@", 1)[0], role="", team="")
        finally:
            d.dispose()
        _err(f"Создан пользователь {email.strip().lower()}" + (" (администратор)" if admin else ""))
        if generate:
            _out(pw)

    return _users_run(go)


def _set_disabled(email: str, db: str | None, disabled: bool) -> int:
    def go() -> None:
        d = _db(db)
        try:
            with d.uow() as uow:
                u = _user_or_fail(uow, email)
                uow.users.update(u.id, disabled=disabled)
                if disabled:
                    try:
                        uow.sessions.revoke_all_for_user(u.id)
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            d.dispose()
        _err(f"{email}: {'отключён' if disabled else 'включён'}")

    return _users_run(go)


@users.command("disable")
@click.argument("email")
@click.option("--db", default=None)
def users_disable(email: str, db: str | None) -> int:
    return _set_disabled(email, db, True)


@users.command("enable")
@click.argument("email")
@click.option("--db", default=None)
def users_enable(email: str, db: str | None) -> int:
    return _set_disabled(email, db, False)


@users.command("reset-password")
@click.argument("email")
@click.option("--password", default=None)
@click.option("--db", default=None)
def users_reset_password(email: str, password: str | None, db: str | None) -> int:
    from ..domain.security import generate_password, hash_password

    def go() -> None:
        pw = password if password is not None else generate_password()
        h = hash_password(pw)
        d = _db(db)
        try:
            with d.uow() as uow:
                u = _user_or_fail(uow, email)
                uow.users.update(u.id, password_hash=h)
                try:
                    uow.sessions.revoke_all_for_user(u.id)
                except Exception:  # noqa: BLE001
                    pass
        finally:
            d.dispose()
        _err(f"{email}: пароль изменён")
        if password is None:
            _out(pw)

    return _users_run(go)


@users.command("list")
@click.option("--db", default=None)
def users_list(db: str | None) -> int:
    def go() -> None:
        d = _db(db)
        try:
            with d.uow() as uow:
                rows = []
                for w in uow.workspaces.list():
                    rows.extend(uow.users.list(w.id))
        finally:
            d.dispose()
        if not rows:
            _err("Пользователей нет")
        for u in rows:
            role = "admin" if u.is_admin else "user"
            state = "disabled" if u.disabled else "active"
            _out(f"{u.email}\t{role}\t{state}\t{u.workspace_id}")

    return _users_run(go)


@cli.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, type=click.IntRange(1, 65535), show_default=True)
def serve(host: str, port: int) -> int:
    """uvicorn, фабрика faktura.api.base.app:create_app (ленивый импорт)."""
    import uvicorn

    uvicorn.run("faktura.api.base.app:create_app", factory=True, host=host, port=port)
    return EXIT_OK


__all__ = ["cli", "EXIT_OK", "EXIT_USAGE", "EXIT_PARTIAL", "EXIT_CHECK"]
