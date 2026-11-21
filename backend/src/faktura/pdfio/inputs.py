"""Проверка PDF и сбор входа: папка, ZIP, .txt (план 5.2; REQ-010, REQ-072, REQ-073, REQ-079)."""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal

from pydantic import BaseModel, Field

from ..domain.errors import AppError, PdfError

PDF_SIGNATURE = b"%PDF-"
# сигнатура ищется в начале файла: спецификация PDF допускает мусор перед заголовком
_SIGNATURE_WINDOW = 1024
_CHUNK = 1024 * 1024
# служебные файлы, которые молча пропускаются при обходе папок и архивов
_JUNK_NAMES = {"thumbs.db", "desktop.ini", "__macosx"}


class PdfInfo(BaseModel):
    sha256: str
    size_bytes: int
    page_count: int


class InputFile(BaseModel):
    name: str                 # имя файла, например "D001.pdf"
    rel_path: str             # путь внутри входа (для подпапок и ZIP)
    publication_id: str       # имя файла без расширения
    path: Path
    source: Literal["dir", "zip", "file", "list"] = "dir"


class Rejected(BaseModel):
    name: str
    code: str  # unsupported_file | validation_error | zip_bomb | zip_too_large | duplicate_name ...
    message: str
    rel_path: str | None = None  # путь внутри входа (подпапка, ZIP); в API rejected[].name = rel_path (У-12)


class CollectResult(BaseModel):
    files: list[InputFile] = Field(default_factory=list)
    rejected: list[Rejected] = Field(default_factory=list)


class ZipLimits(BaseModel):
    max_zip_mb: int = 2048
    max_unpacked_gb: int = 2
    max_files: int = 500
    max_depth: int = 10

    @classmethod
    def from_settings(cls, settings) -> "ZipLimits":
        return cls(max_zip_mb=settings.max_zip_mb, max_unpacked_gb=settings.zip_max_unpacked_gb,
                   max_files=settings.zip_max_files, max_depth=settings.zip_max_depth)


class NameDedupeResult(BaseModel):
    files: list[InputFile] = Field(default_factory=list)
    rejected: list[Rejected] = Field(default_factory=list)  # code = duplicate_name


# ---------------------------------------------------------------------------------------------
# inspect_pdf
# ---------------------------------------------------------------------------------------------

def _silence_mupdf() -> None:
    try:
        import fitz

        fitz.TOOLS.mupdf_display_errors(False)
        fitz.TOOLS.mupdf_display_warnings(False)
    except Exception:  # noqa: BLE001 - только подавление шума в stderr
        pass


def has_pdf_signature(head: bytes) -> bool:
    return PDF_SIGNATURE in head[:_SIGNATURE_WINDOW]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def inspect_pdf(path: Path, *, max_mb: int = 50, max_pages: int = 300) -> PdfInfo:
    """PdfError: unsupported_file | file_too_large | too_many_pages | pdf_encrypted | pdf_broken.

    Порядок: размер (до разбора) -> сигнатура %PDF- -> открытие PyMuPDF -> пароль -> число страниц.
    Скан без текстового слоя принимается: needs_ocr решает разбор (LocalParseAdapter).
    """
    path = Path(path)
    if not path.is_file():
        raise PdfError("unsupported_file", f"Файл не найден: {path.name}")
    size = path.stat().st_size
    if size > max_mb * 1024 * 1024:
        raise PdfError("file_too_large", f"Файл {path.name} больше {max_mb} МБ")
    with open(path, "rb") as f:
        head = f.read(_SIGNATURE_WINDOW)
    if not has_pdf_signature(head):
        raise PdfError("unsupported_file", f"{path.name}: не PDF (нет сигнатуры %PDF-)")

    import fitz

    _silence_mupdf()
    try:
        doc = fitz.open(str(path), filetype="pdf")
    except Exception as e:  # noqa: BLE001 - любой сбой разбора = битый PDF
        raise PdfError("pdf_broken", f"{path.name}: PDF повреждён ({type(e).__name__})") from e
    try:
        if doc.needs_pass:
            raise PdfError("pdf_encrypted", f"{path.name}: PDF защищён паролем")
        try:
            pages = doc.page_count
        except Exception as e:  # noqa: BLE001
            raise PdfError("pdf_broken", f"{path.name}: не удаётся прочитать страницы") from e
        if pages < 1:
            raise PdfError("pdf_broken", f"{path.name}: в PDF нет страниц")
        if pages > max_pages:
            raise PdfError("too_many_pages", f"{path.name}: {pages} страниц, допустимо не больше {max_pages}")
        try:  # первая страница должна загружаться: иначе файл фактически битый
            doc.load_page(0)
        except Exception as e:  # noqa: BLE001
            raise PdfError("pdf_broken", f"{path.name}: первая страница не читается") from e
    finally:
        doc.close()
    return PdfInfo(sha256=sha256_file(path), size_bytes=size, page_count=pages)


# ---------------------------------------------------------------------------------------------
# collect_inputs
# ---------------------------------------------------------------------------------------------

def _is_junk(part: str) -> bool:
    return part.startswith(".") or part.casefold() in _JUNK_NAMES


def _input_file(path: Path, rel_path: str, source: str) -> InputFile:
    name = PurePosixPath(rel_path).name
    return InputFile(name=name, rel_path=rel_path, publication_id=Path(name).stem, path=path, source=source)


def collect_inputs(src: Path, *, limits: ZipLimits | None = None, work_dir: Path | None = None) -> CollectResult:
    """Папка (рекурсивно) | ZIP | один PDF | .txt (список URL - волна 2 -> rejected validation_error).

    Скрытые файлы, `__MACOSX`, Thumbs.db молча пропускаются; прочие не-PDF -> rejected unsupported_file.
    ZIP распаковывается в `work_dir` (по умолчанию - новый временный каталог) через `safe_unzip`.
    Нарушение лимитов архива (`zip_bomb`, `zip_too_large`) или не-ZIP -> rejected для всего архива.
    Проверка содержимого PDF - `inspect_pdf`, коллизии имён - `dedupe_names` (их вызывает конвейер).
    """
    src = Path(src)
    if not src.exists():
        raise AppError("validation_error", f"Путь не найден: {src}", field="in")
    result = CollectResult()
    if src.is_dir():
        entries = []
        for root, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if not _is_junk(d)]
            for fn in files:
                if _is_junk(fn):
                    continue
                p = Path(root) / fn
                entries.append((p.relative_to(src).as_posix(), p))
        for rel, p in sorted(entries, key=lambda e: e[0]):
            if p.suffix.lower() == ".pdf":
                result.files.append(_input_file(p, rel, "dir"))
            else:
                result.rejected.append(Rejected(name=p.name, code="unsupported_file", rel_path=rel,
                                                message=f"{rel}: принимаются только PDF"))
        return result

    suffix = src.suffix.lower()
    if suffix == ".pdf":
        result.files.append(_input_file(src, src.name, "file"))
        return result
    if suffix == ".zip":
        dest = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="faktura_zip_"))
        try:
            paths, skipped = _unzip(src, dest, limits or ZipLimits())
        except AppError as e:
            result.rejected.append(Rejected(name=src.name, code=e.code, message=e.message))
            return result
        result.rejected.extend(skipped)
        for p in paths:
            result.files.append(_input_file(p, p.relative_to(dest).as_posix(), "zip"))
        return result
    if suffix == ".txt":
        result.rejected.append(Rejected(
            name=src.name, code="validation_error",
            message="Загрузка по списку ссылок (.txt) появится во второй волне; передайте папку или ZIP с PDF"))
        return result
    result.rejected.append(Rejected(name=src.name, code="unsupported_file",
                                    message=f"{src.name}: ожидается папка, ZIP или PDF"))
    return result


# ---------------------------------------------------------------------------------------------
# safe_unzip
# ---------------------------------------------------------------------------------------------

def _decode_member_name(info: zipfile.ZipInfo) -> str:
    """Имена без флага UTF-8 zipfile читает как cp437; русские архивы Windows обычно в cp866."""
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    for enc in ("utf-8", "cp866"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return name


def _safe_parts(name: str) -> list[str] | None:
    """Части относительного пути или None, если путь опасный (абсолютный, с '..', с диском)."""
    n = name.replace("\\", "/")
    if n.startswith("/") or (len(n) > 1 and n[1] == ":"):
        return None
    parts = [p for p in n.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or ":" in p for p in parts):
        return None
    return parts


def _stream_size(stream: BinaryIO) -> int | None:
    try:
        pos = stream.tell()
        stream.seek(0, io.SEEK_END)
        size = stream.tell()
        stream.seek(pos)
        return size
    except (OSError, AttributeError, ValueError):
        return None


def safe_unzip(zip_path_or_stream: Path | BinaryIO, dest: Path, limits: ZipLimits) -> list[Path]:
    """Защита: zip-slip, бомба, глубина, __MACOSX, скрытые файлы, сигнатура %PDF-.

    Возвращает пути извлечённых PDF (порядок архива). Опасные пути, вложенность глубже `max_depth`,
    не-PDF и файлы без сигнатуры пропускаются. Превышение размера архива, числа файлов или объёма
    распаковки -> AppError `zip_too_large` / `zip_bomb`; частично распакованное удаляется.
    """
    paths, _ = _unzip(zip_path_or_stream, Path(dest), limits)
    return paths


def _unzip(zip_path_or_stream: Path | BinaryIO, dest: Path, limits: ZipLimits) -> tuple[list[Path], list[Rejected]]:
    max_zip = limits.max_zip_mb * 1024 * 1024
    max_unpacked = limits.max_unpacked_gb * 1024 ** 3
    if isinstance(zip_path_or_stream, (str, os.PathLike)):
        zp = Path(zip_path_or_stream)
        if not zp.is_file():
            raise AppError("validation_error", f"Архив не найден: {zp.name}")
        size = zp.stat().st_size
        label = zp.name
    else:
        size = _stream_size(zip_path_or_stream)
        label = "archive"
    if size is not None and size > max_zip:
        raise AppError("zip_too_large", f"Архив {label} больше {limits.max_zip_mb} МБ")
    try:
        zf = zipfile.ZipFile(zip_path_or_stream)
    except (zipfile.BadZipFile, OSError, ValueError) as e:
        raise AppError("validation_error", f"{label}: не ZIP-архив или архив повреждён") from e

    skipped: list[Rejected] = []
    extracted: list[Path] = []
    created: list[Path] = []
    with zf:
        members = [i for i in zf.infolist() if not i.is_dir()]
        if len(members) > limits.max_files:
            raise AppError("zip_too_large", f"В архиве {len(members)} файлов, допустимо не больше {limits.max_files}")
        declared = sum(max(i.file_size, 0) for i in members)
        if declared > max_unpacked:
            raise AppError("zip_bomb", f"Архив распаковывается больше чем в {limits.max_unpacked_gb} ГБ")

        dest.mkdir(parents=True, exist_ok=True)
        root = dest.resolve()
        seen: set[str] = set()
        written = 0
        try:
            for info in members:
                name = _decode_member_name(info)
                parts = _safe_parts(name)
                if parts is None:
                    skipped.append(Rejected(name=PurePosixPath(name.replace("\\", "/")).name or name,
                                            code="validation_error", message=f"{name}: небезопасный путь в архиве",
                                            rel_path=name))
                    continue
                if any(_is_junk(p) for p in parts):
                    continue
                fname = parts[-1]
                if len(parts) - 1 > limits.max_depth:
                    skipped.append(Rejected(name=fname, code="validation_error",
                                            message=f"{name}: вложенность глубже {limits.max_depth}", rel_path=name))
                    continue
                if not fname.lower().endswith(".pdf"):
                    skipped.append(Rejected(name=fname, code="unsupported_file",
                                            message=f"{name}: принимаются только PDF", rel_path=name))
                    continue
                rel = "/".join(parts)
                if rel.casefold() in seen:
                    continue
                seen.add(rel.casefold())
                target = root.joinpath(*parts)
                try:
                    target.resolve().relative_to(root)
                except ValueError:
                    skipped.append(Rejected(name=fname, code="validation_error",
                                            message=f"{name}: путь выходит за каталог распаковки", rel_path=name))
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                ok_signature = True
                created.append(target)
                with zf.open(info) as src_f, open(target, "wb") as out:
                    first = True
                    while True:
                        chunk = src_f.read(_CHUNK)
                        if not chunk:
                            break
                        if first:
                            first = False
                            if not has_pdf_signature(chunk):
                                ok_signature = False
                                break
                        written += len(chunk)
                        if written > max_unpacked:
                            raise AppError("zip_bomb",
                                           f"Архив распаковывается больше чем в {limits.max_unpacked_gb} ГБ")
                        out.write(chunk)
                if first or not ok_signature:  # пустой файл или нет %PDF-
                    target.unlink(missing_ok=True)
                    created.remove(target)
                    skipped.append(Rejected(name=fname, code="unsupported_file",
                                            message=f"{name}: не PDF (нет сигнатуры %PDF-)", rel_path=name))
                    continue
                extracted.append(target)
        except AppError:
            for p in created:
                Path(p).unlink(missing_ok=True)
            raise
        except (zipfile.BadZipFile, OSError, EOFError, NotImplementedError, RuntimeError) as e:
            for p in created:
                Path(p).unlink(missing_ok=True)
            raise AppError("validation_error", f"{label}: архив повреждён ({type(e).__name__})") from e
    return extracted, skipped


# ---------------------------------------------------------------------------------------------
# dedupe_names
# ---------------------------------------------------------------------------------------------

def _name_key(name: str) -> str:
    """Ключ коллизии: publication_id без учёта регистра (на Windows P001.pdf и p001.PDF - один файл)."""
    return Path(PurePosixPath(name.replace("\\", "/")).name).stem.casefold()


def dedupe_names(files: list[InputFile], *, existing_names: set[str]) -> NameDedupeResult:
    """Только duplicate_name; дубли по sha решает pipeline. Первый файл с именем остаётся, остальные - rejected."""
    taken = {_name_key(n) for n in existing_names}
    out = NameDedupeResult()
    first_by_key: dict[str, str] = {}
    for f in files:
        key = _name_key(f.name)
        if key in taken:
            where = first_by_key.get(key)
            msg = (f"{f.rel_path}: файл с именем {f.name} уже есть ({where})" if where
                   else f"{f.rel_path}: файл с именем {f.name} уже есть в корпусе")
            out.rejected.append(Rejected(name=f.name, code="duplicate_name", message=msg, rel_path=f.rel_path))
            continue
        taken.add(key)
        first_by_key[key] = f.rel_path
        out.files.append(f)
    return out


def cleanup_dir(path: Path) -> None:
    """Удалить временный каталог распаковки (ошибки игнорируются)."""
    shutil.rmtree(path, ignore_errors=True)
