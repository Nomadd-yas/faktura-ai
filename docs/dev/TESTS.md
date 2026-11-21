# Тесты бэкенда

Каталог: `backend/tests`. Запуск из `backend/`.

## Запуск

```
uv run --python 3.12 --all-extras python -m pytest -q            # по умолчанию, без slow и perf
uv run --python 3.12 --all-extras python -m pytest -q -m "not perf"   # включая slow (полный прогон dev, check_submission.py)
uv run --python 3.12 --all-extras python -m pytest -q -m slow         # только slow
uv run --python 3.12 --all-extras python -m pytest -q -m perf         # замеры времени (допуск x3 к порогу спеки)
uv run --python 3.12 --all-extras python -m pytest -q tests/api_core  # один каталог
```

По умолчанию (`addopts` в `pyproject.toml`) исключены маркеры `perf` и `slow`. Ключ `-m` в командной строке заменяет
значение из `addopts`. Для защиты от зависаний добавьте `--with pytest-timeout` и `--timeout 600`.

Маркеры: `data` (нужны данные кейса, иначе тест пропускается), `slow` (> 10 с: прогон всего dev, реальный
`check_submission.py`), `perf`, `pg` (нужен `FAKTURA_TEST_PG_URL`), `s3` (нужен `FAKTURA_TEST_S3_URL`).

## Окружение

| Переменная | Назначение |
|---|---|
| `FAKTURA_CASE_DIR` | корень данных кейса (по умолчанию `data/caseB_kejs5` рядом с `backend/`); нужен `dev_gold/`. Без данных тесты `data` пропускаются |
| `FAKTURA_TEST_PG_URL` | URL PostgreSQL для тестов с маркером `pg` |
| `FAKTURA_TEST_S3_URL` | URL S3-совместимого хранилища для тестов с маркером `s3` |
| `FAKTURA_*` | остальные настройки приложения (`faktura.config.Settings`); тесты собирают `Settings` явно |

## Что покрывает каждый каталог

| Каталог | Покрытие |
|---|---|
| `shared` | доменные сущности, таблицы, разбор/запись ячеек CSV, публичные интерфейсы модулей |
| `norm` | нормализация значений и единиц, словарь, регрессия на эталоне |
| `adapters` | PDF (`pdfio`: разбор, цитаты, ввод/ZIP), ML-адаптеры и кэш, мок-сервер |
| `checks` | правила проверок, слияние замечаний, эталон замечаний dev |
| `storage` | БД, репозитории, файловые хранилища (PG и S3 - по маркерам), перф |
| `pipeline` | обработка статьи, прогоны, CLI (`extract`, `eval`, `users`), сборка выходной папки, roundtrip на dev (slow) |
| `export` | submission, человекочитаемый выгруз, валидация, оценка (`evaluation`), приёмка на данных |
| `passport` | паспорт прогона |
| `api_base` | фабрика приложения, вход, пользователи, middleware, ошибки, сериализаторы, заглушки |
| `api_core` | run-API: `/runs`, `/papers/{domain}/{pid}`, `/results`, `/issues`, v2-правки, SSE, управление прогонами |
| `api_rag` | область RAG через API (прогон, таблицы, PDF, submission) |
| `e2e` | CLI roundtrip на 3 dev-PDF (slow), вход и 401/404 API, диагностика сканов |

Старые каталоги `api_corpus`, `api_papers`, `api_review` (дизайн «факты/корпуса», `/facts`, `/corpora`) удалены:
их маршруты больше не подключены (`ROUTER_MODULES`), живой контракт - `docs/api/CORE_API.md`.
