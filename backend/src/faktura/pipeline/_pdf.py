"""Доступ к PDF из конвейера: общий замок PyMuPDF и материализация файла из FileStore.

PyMuPDF не потокобезопасен, а прогон обрабатывает статьи в нескольких потоках. Все вызовы разбора, поиска
якорей и проверки PDF из конвейера идут под `PDF_LOCK`; медленная часть (запрос к ML-1) - вне замка.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Iterator

from ..domain.errors import AppError
from ..domain.ports import FileStore

PDF_LOCK = threading.RLock()


@contextlib.contextmanager
def local_pdf(store: FileStore, key: str | None, name: str = "paper.pdf") -> Iterator[Path]:
    """Путь к PDF на диске: локальное хранилище отдаёт путь напрямую, иначе файл копируется во временный каталог."""
    if not key:
        raise AppError("not_found", "У файла нет сохранённого PDF")
    local = getattr(store, "local_path", None)
    if callable(local):
        try:
            p = Path(local(key))
        except Exception:  # noqa: BLE001
            p = None
        if p is not None and p.is_file():
            yield p
            return
    tmp = tempfile.mkdtemp(prefix="faktura_pdf_")
    try:
        dest = Path(tmp) / (os.path.basename(name) or "paper.pdf")
        try:
            store.copy_to(key, dest)
        except (OSError, KeyError) as e:
            raise AppError("not_found", f"PDF не найден в хранилище: {key}") from e
        yield dest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
