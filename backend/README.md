# Faktura backend

Бэкенд проекта PDF-Analitics (кейс ВИНИТИ, область 1: тонкоплёночные солнечные элементы). Извлекает данные из научных PDF
в 7 связанных CSV с номером страницы и дословной цитатой к каждому значению.

Состояние: **каркас**. Структура пакетов, общие типы (`faktura.domain`), конфигурация и тестовая инфраструктура готовы;
публичные интерфейсы модулей описаны заглушками (`NotImplementedError`). Архитектура и порядок работ:
`docs/wave1/02_plan.md`; контракт API для фронтенда: `docs/wave1/api_contract.md`; спека: `docs/spec_src/`.

## Установка

```
cd backend
python -m pip install -e ".[dev]"          # + extras: pg (PostgreSQL), s3 (MinIO), img (webp-рендер)
python -m pytest -q
```

Без установки пакета тесты тоже работают (`pythonpath = ["src"]` в `pyproject.toml`).

## Главная команда (после реализации модулей)

```
faktura extract --domain solar --in ../data/caseB_kejs5/test --out ./out \
    --check ../data/caseB_kejs5/tools/check_submission.py
```

Результат: ровно 7 CSV в `out/`, служебные файлы в `out/_meta/`, `check_submission.py` без ошибок. Работает без
PostgreSQL, MinIO и сети: с моком ML-1 (`--extractor mock`, по умолчанию) или с контейнером ML-1 (`--extract-url`).
Коды выхода: 0 успех; 1 ошибка аргументов; 2 частичный сбой; 3 валидатор сдачи нашёл ошибки.

Остальные команды: `faktura import-raw`, `faktura eval --gold --pred`, `faktura check`, `faktura users ...`,
`faktura serve`.

## Структура

```
src/faktura/
  config.py            настройки (FAKTURA_*), get_settings()
  domain/              shared: tables, records, io, extraction, profile, entities, ids, errors, security, ports, services
  norm/                нормализация (модуль norm)
  pdfio/  ml/          PDF и адаптеры ML-1 (модуль adapters)
  checks/              правила и замечания (модуль checks)
  export/ passport/ evaluation/   сдача, паспорт, оценка (модуль export)
  storage/             БД и файлы (модуль storage)
  pipeline/ cli/       конвейер, прогоны, CLI (модуль pipeline)
  api/base|corpus|papers|review/  REST API (модули api_*), query/, review/
tests/
  conftest.py fixtures/ shared/   общие фикстуры и тесты каркаса
  <id модуля>/                    тесты модуля (norm, adapters, checks, export, storage, pipeline, api_*)
```

Правило владения: модуль правит только свои пути; общие файлы (`pyproject.toml`, `config.py`, `domain/`,
`api/__init__.py`, `tests/conftest.py`, `tests/fixtures/`, `tests/shared/`) правит координатор. Ошибку в shared модуль
оформляет `xfail(strict=True, reason="shared_bug: ...")` и описывает в отчёте.

## Тесты и маркеры

`data` (нужны данные кейса: `data/caseB_kejs5`, либо `FAKTURA_CASE_DIR`), `slow`, `perf` (по умолчанию исключён, `-m perf`),
`pg` (нужен `FAKTURA_TEST_PG_URL`), `s3` (нужен `FAKTURA_TEST_S3_URL`). Без данных/сервисов такие тесты пропускаются.

Фикстуры (`tests/conftest.py`): `settings`, `case_dir`, `dev_dir`, `dev_gold_dir`, `test_dir`, `check_script`,
`tmp_db_url`, `make_pdf(pages=[...])`, `example_7_0_1`, `example_broken`, `mini_gold_dir`, `gold_bundles`.
`tests/fixtures/`: `ml_extract_example.json` (пример spec/07 §7.0.1), `ml_extract_broken.json`, `mini_gold/` (D001, D003).

## Docker

`docker-compose.yml`: `postgres:16`, `minio`, `ml-mock` (`python -m faktura.ml.mock_server`), `api`.
Переменные окружения - `.env.example`.

## Принятые решения каркаса

- Числовые, целые и булевы поля записей допускают `str` (нераспознанный текст ячейки), чистит их `export.sanitize`
  (см. docstring `domain/records.py`). Причина: эталон содержит `layer_thickness = "63–68"`, а roundtrip не должен терять ячейки.
- `AddedBy` в домене: `auto|manual`; в API (spec/03) это `model|user`, сопоставление - в сериализаторах `api_base`.
- Запасного (rule-based) экстрактора нет (решение Б-1): извлечение только через `ExtractAdapter` (мок на dev_gold, HTTP к ML-1, raw-каталог).
