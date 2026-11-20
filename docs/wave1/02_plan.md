# Волна 1. Архитектура и план бэкенда (отдел 2), редакция 2

Основа — `docs/wave1/01_spec_analysis.md` (требования REQ-001…REQ-130, решения К-1…К-21, умолчания В-1…В-21) и решение пользователя Б-1: **запасного экстрактора без LLM нет**. Извлечение идёт только через адаптер к контейнеру ML-1 (`POST /ml/extract`, spec/07 §7.0.1) и через мок на `dev_gold`. Адаптер настраивается (URL, таймауты, ретраи), чтобы подключить реальный контейнер без правок конвейера. Контракт API для фронтенда — `docs/wave1/api_contract.md` (версия 1.1).

Ссылки на спеку: `BACKEND §N` — `docs/spec_src/BACKEND_SPEC.md`; `spec/NN §N.M` — `docs/spec_src/spec/*.md`.

Редакция 2 устраняет замечания критика. Что и где исправлено — в разделе 11.

---

## 1. Рамки волны 1

| Приоритет | Что входит | Требования |
|---|---|---|
| **P0** (сдача) | CLI `faktura extract`, конвейер на моке и HTTP-адаптере ML-1, нормализация, проверки бэка, санитайзер, экспорт 7 CSV, `_meta/`, паспорт, запуск `check_submission.py`, кеш ответов ML, регрессия с `dev_gold` | REQ-001…REQ-069, REQ-112, REQ-114, REQ-116, REQ-117, REQ-120 |
| **P1** (API для фронта) | хранение (SQLite/Postgres, диск/S3), соглашения API, вход, корпус, импорт, прогоны, SSE, сводка, профили, статья, факты, таблица фактов, замечания, журнал и отмена, health, экспорт и паспорт через API | REQ-070…REQ-111, REQ-113, REQ-115 |
| **P2-R** | P2, который реализуется в волне 1 после P1: точность (`/checks/accuracy`) и загрузка эталона из каталога (`POST /admin/gold {dir}`), чтение словаря (`GET /dictionary`), удалённый режим CLI | REQ-118 и часть REQ-121 |
| **P2** | сравнение, граф, обзор данных, поиск, проекты, чаты, правка словаря, форматы выгрузки для людей. До реализации — заглушки с верной формой ответа и заголовком `X-Faktura-Stub: 1` | REQ-121…REQ-125 |
| Не делаем | rule-based экстрактор (Б-1), OCR (REQ-129), RAG (REQ-130, только задел модели), импорт по URL, tus и `server_path` (В-16, ответ 400), `/ml/parse` (В-12), ключи API (`/me/api-keys` — позже) | — |

**Главный критерий приёмки волны:**
```
faktura extract --domain solar --in <case>/test --out ./out --check <case>/tools/check_submission.py
```
Результат: ровно 7 CSV в `out/`, служебные файлы в `out/_meta/`, 0 ошибок валидатора. Работает без Postgres, MinIO и сети: с моком или с подключённым контейнером ML-1 (`--extract-url`).

---

## 2. Стек

| Слой | Выбор | Почему |
|---|---|---|
| Язык | Python 3.12 | Окружение; PyMuPDF и pypdf уже стоят |
| API | FastAPI + uvicorn; SSE через `StreamingResponse` | Спека — REST/JSON + SSE; pydantic-схемы дают валидацию и OpenAPI |
| Модели данных | pydantic 2; числа — `Decimal` | REQ-046: арифметика без артефактов float |
| БД | SQLAlchemy 2 (ORM + Core). SQLite (WAL) — для CLI и тестов; PostgreSQL ≥ 14 (`psycopg[binary]`) — для сервера | REQ-080, REQ-081, Коорд. п.5 |
| Файлы | `FileStore`: `LocalFileStore` (диск) и `S3FileStore` (boto3, MinIO) | BACKEND §8.2 |
| PDF | PyMuPDF (страницы, рендер, `search_for`); pypdf (проверка цитат той же логикой, что у валидатора) | REQ-060, REQ-063 |
| HTTP-клиент ML-1 | httpx | Ретраи, таймауты, `MockTransport` в тестах |
| CLI | click | |
| Безопасность | PyJWT (HS256), argon2-cffi (argon2id) | BACKEND §2.1, REQ-096 |
| Экспорт для людей | openpyxl | spec/04 §4.16 |
| Тесты | pytest, FastAPI `TestClient`, без внешних сервисов. Postgres — по `FAKTURA_TEST_PG_URL`, MinIO — по `FAKTURA_TEST_S3_URL`, иначе такие тесты пропускаются | REQ-128 |
| Упаковка | `pyproject.toml` (src-layout, entry point `faktura`), Dockerfile, `docker-compose.yml` (postgres, minio, api, ml-mock) | REQ-011, REQ-084 |

Зависимости: `fastapi`, `uvicorn[standard]`, `python-multipart`, `pydantic>=2`, `pydantic-settings`, `sqlalchemy>=2`, `httpx`, `pymupdf`, `pypdf`, `click`, `pyjwt`, `argon2-cffi`, `openpyxl`, `platformdirs`. Extras: `pg = [psycopg[binary]]`, `s3 = [boto3]`, `img = [pillow]` (рендер в webp, без него — png), `dev = [pytest]`.

---

## 3. Архитектура

### 3.1. Слои и поток данных

```
            ┌──────────────────────────── API (FastAPI, /api/v1) ────────────────────────────┐
            │ api.base: приложение, deps, ошибки, вход, пользователи, health, сериализаторы, P2 │
            │ api.corpus: корпус, файлы, прогоны, SSE, /extract, экспорт, профили, паспорт      │
            │ api.papers: статьи, страницы, факты (чтение), таблица фактов, язык запросов, точность │
            │ api.review: правки фактов, ручные факты, замечания, журнал, отмена               │
            └───────────────────────────────┬─────────────────────────────────────────────────┘
                                            │                        CLI (pipeline.cli): extract, eval, check,
                                            ▼                        import-raw, users, serve
  ┌───────────────────────────────────── pipeline ──────────────────────────────────────────────┐
  │ CorpusService (приём, sha, смысловые дубли)   RunService (inline/фон, пауза ML, отмена, события) │
  │ PaperProcessor: parse → extract(кеш) → scope → apply_normalization → build_facts → link dups   │
  │                 → check_paper(merge) → quote_found → stats → save (1 транзакция)               │
  │ build_output: корпус → выборка → санитайзер → I1… → 7 CSV → _meta → PDF в папку → валидатор    │
  └──┬──────────────┬───────────────┬────────────────┬─────────────────┬─────────────────────────┘
     ▼              ▼               ▼                ▼                 ▼
  adapters        norm           checks           export            storage ──► SQLite / Postgres
  pdfio: приём,   faktura.norm   правила §5.2,    выборка, санитайзер, UoW, репозитории,   FileStore ──► диск / MinIO
  разбор, цитаты  (Decimal,      слияние, ключи,  7 CSV, _meta, ZIP,   keyset, фасеты,
  ml: мок, HTTP,  словари)       качество,        паспорт, валидатор,  агрегаты корпуса
  raw, кеш                       покрытие         оценка (§14.7)
                         ▲
                         └── faktura.domain (shared): типы, порты, чтение 7 CSV → PaperBundle, хеширование паролей
```

Принципы:
1. **Порты и адаптеры.** Интеграционные точки с ML описаны протоколами в `faktura/domain/ports.py`: `ExtractAdapter` (ML-1), `ParseAdapter` и `Normalizer` (ML-2), `EvalAdapter` (ML-2, `faktura-eval`), а также `QuoteChecker`, `FileStore`, `EventBus`, `Executor`. Конвейер работает **только** через протоколы из `Services` и не импортирует реализации. Реализация выбирается конфигурацией в `pipeline.build_services`.
2. **Канонический формат статьи — `PaperBundle`**: строки 7 таблиц и служебные метаданные записи. Его порождает адаптер ML; нормализация и проверки его меняют; storage хранит; export читает. Формат §7.0.1 разбирается один раз, в `ml`. Чтение 7 CSV в `PaperBundle` — общий код `faktura.domain.io`, один для мока, эталона, оценки и тестов.
3. **Один код для CLI и API.** Сборка выходной папки — одна функция `pipeline.build_output`. Её вызывают `faktura extract`, `POST /exports {format: submission}` и `POST /extract` (→ `GET /runs/{id}/output`).
4. **Детерминизм.** Одинаковый вход даёт одинаковые CSV: порядок по `publication_id`, затем `ml_order`; ключи замечаний детерминированы (К-6).
5. **Санитайзер — последний слой перед записью.** При любом ответе ML валидатор даёт 0 ошибок (REQ-018).
6. **Внешние сервисы необязательны.** Юнит-тесты и CLI работают на SQLite и локальном диске.
7. **Нет циклических импортов.** Пакеты `faktura.api.corpus|papers|review` импортируют только `faktura.api.base`. Приложение подключает их роутеры лениво, по списку из общего `faktura/api/__init__.py` (раздел 4.5).

### 3.2. Структура каталога `backend/`

```
backend/
  pyproject.toml  README.md  .env.example  Dockerfile  docker-compose.yml       ← shared
  src/faktura/
    __init__.py  config.py                                                       ← shared
    domain/   (tables, records, io, extraction, profile, entities, ids,
               errors, security, ports, services)                                ← shared
    api/__init__.py   (ROUTER_MODULES)                                           ← shared
    norm/     (+ data/*.json)                                                    ← norm
    pdfio/  ml/                                                                  ← adapters
    checks/                                                                      ← checks
    export/  passport/  evaluation/                                              ← export
    storage/                                                                     ← storage
    pipeline/  cli/  __main__.py                                                 ← pipeline
    api/base/                                                                    ← api_base
    api/corpus/                                                                  ← api_corpus
    api/papers/  query/                                                          ← api_papers
    api/review/  review/                                                         ← api_review
  tests/
    conftest.py  fixtures/  shared/                                              ← shared
    norm/ adapters/ checks/ export/ storage/ pipeline/
    api_base/ api_corpus/ api_papers/ api_review/                                ← каталог тестов = id модуля
```

---

## 4. Общие файлы (shared_paths): каркас

Каркас пишет координатор **до** модулей. Модули shared не правят.

**Порядок исправления shared.** Если модуль находит ошибку в shared, он:
1. пишет у себя тест, который её показывает, с `@pytest.mark.xfail(reason="shared_bug: <что>", strict=True)`;
2. обходит ошибку локально, не меняя публичного интерфейса;
3. указывает ошибку в итоговом отчёте модуля.

Координатор правит shared, снимает `xfail` и прогоняет `tests/shared` и тесты затронутых модулей. Новый общий тип модуль временно заводит у себя (`<module>/_types.py`), а координатор переносит его в `domain` на интеграции.

**Тесты каркаса** (`tests/shared/`, их тоже пишет координатор) проверяются раньше модулей:
- `TABLES`, `VOCAB`, `NUMERIC_STRICT` 1:1 с `check_submission.py`. Скрипт разбирается через `ast` и `ast.literal_eval` без выполнения, путь берётся из `FAKTURA_CASE_DIR`. Без данных тест пропускается (маркер `data`).
- `domain.io`: все 7 CSV `dev_gold` читаются в `PaperBundle` и обратно через `tables_from_bundles` дают те же ячейки (кроме 4 ячеек `encapsulation`, К-7). Ячейки сравниваются как строки после разбора: `Decimal` без экспоненты, `true/false`, списки.
- `CHECK_CATALOG` содержит все `check_name`, которые есть в `dev_gold/issues.csv` (21 имя).
- `builtin_solar_profile()` содержит все 11 `EVAL_METRICS`, правила с порогами 3 % и 5 %; `issue_key` детерминирован.
- `security`: хеш argon2id проверяется; пароль из 7 символов отклоняется.

### 4.1. `pyproject.toml`
- src-layout, пакет `faktura`, версия `0.1.0`, entry point `faktura = "faktura.cli.main:cli"`.
- `[tool.pytest.ini_options]`: `pythonpath = ["src"]`, `testpaths = ["tests"]`, `addopts = ["--import-mode=importlib", "-m", "not perf"]`.
- Маркеры: `data` (нужны данные кейса), `slow` (больше 10 с), `perf` (замер времени; по умолчанию исключён, запуск `-m perf`, допуск ×3 к порогу спеки), `pg` (нужен `FAKTURA_TEST_PG_URL`), `s3` (нужен `FAKTURA_TEST_S3_URL`).

### 4.2. `faktura/config.py`
`Settings(BaseSettings)`, префикс `FAKTURA_`, `get_settings()` с `lru_cache`, поддержка `.env`.

| Поле | По умолчанию | Назначение |
|---|---|---|
| `case_dir: Path \| None` | `None` → `../data/caseB_kejs5` от `backend/`, если каталог есть | корень кейса (К-10) |
| `db_url` | `sqlite:///./faktura.db` | БД API. CLI по умолчанию берёт временный файл |
| `storage_backend`, `storage_dir` | `local`, `./storage` | |
| `s3_endpoint`, `s3_bucket`, `s3_access_key`, `s3_secret_key`, `s3_region` | — | MinIO/S3 |
| `extractor` | `mock` | `mock \| http \| raw` |
| `extract_url` | `None` | URL контейнера ML-1 |
| `extract_timeout_s` | `330` | `EXTRACT_TIMEOUT_S + 30` |
| `extract_concurrency` | `2` | семафор на контейнер |
| `extract_retry_delays` | `30,120,600` | воркер API. CLI переопределяет на `5,15,30` |
| `ml_pause_poll_s`, `ml_pause_max_s` | `30`, `1800` | пауза прогона при недоступном ML (раздел 5.6) |
| `cache_dir` | `platformdirs.user_cache_dir("faktura")/ml_raw` | кеш сырых ответов ML для CLI; никогда не внутри `--out` |
| `mock_issues` | `all` | `all \| ml_only` (К-15) |
| `mock_gold_dir` | `case_dir/dev_gold` | |
| `bbox_origin` | `top-left` | `top-left \| bottom-left` (К-2) |
| `autolink_duplicates` | `auto` | `auto` — включено для `http/raw`, выключено для `mock` (В-13) |
| `norm_impl` | `local` | `local \| faktura_norm` (К-13) |
| `eval_impl` | `local` | `local \| faktura_eval` |
| `jwt_secret` | обязателен для API | |
| `access_ttl_s`, `refresh_ttl_days`, `refresh_short_ttl_h` | `900`, `30`, `12` | BACKEND §2.1 |
| `refresh_in_body`, `cookie_secure` | `false`, `true` | |
| `admin_email`, `admin_password`, `allow_signup` | —, —, `false` | BACKEND §2.2 |
| `cors_origins` | `["http://localhost:5173"]` | BACKEND §8.5 |
| `max_file_mb`, `max_pages`, `max_upload_files`, `max_upload_mb`, `max_zip_mb` | `50`, `300`, `200`, `1024`, `2048` | multipart — до 1 ГБ; ZIP — до 2 ГБ (spec/13 §13.4) |
| `zip_max_unpacked_gb`, `zip_max_files`, `zip_max_depth` | `10`, `20000`, `10` | защита распаковки |
| `run_concurrency` | `4` | |
| `export_sync_limit` | `50000` | порог асинхронности для форматов для людей |
| `check_script` | `case_dir/tools/check_submission.py`, если он есть | `--check` |
| `check_timeout_s` | `900` | |
| `sse_ping_s`, `sse_buffer` | `25`, `1000` | |
| `sse_max_events`, `sse_idle_timeout_s` | `None`, `None` | только для тестов: поток SSE закрывается сам |

### 4.3. `faktura/domain/` — общие типы и код

**`tables.py`** — константы формата сдачи, 1:1 с `check_submission.py`:
- `TABLES: dict[str, tuple[str, ...]]` — 7 таблиц, колонки в порядке шаблона;
- `VOCAB: dict[tuple[str, str], frozenset[str]]`, с учётом регистра;
- `NUMERIC_STRICT` (поля, которые проверяет скрипт) и `NUMERIC_LOOSE` (`year`, `n_devices`, `spread_value`, `light_intensity`, `layer_thickness`, `time_point`: скрипт их не проверяет, но там тоже только числа). `scan_rate`, `temperature`, `annealing_*` — строки с единицей, как в `dev_gold`;
- `BOOL_FIELDS`: `publications.in_scope`, `devices.encapsulation`, `measurements.mask_used`, `measurements.certified`;
- `LIST_FIELDS` с разделителем: `publications.authors` → `"; "`; `materials.additives`, `measurements.device_id`, `issues.record_ids` → `";"`;
- `UNIT_FOR_VALUE`: пары «значение → единица» (`bandgap_value→bandgap_unit`, `layer_thickness→thickness_unit`, `active_area→area_unit`) для правила REQ-037;
- `EVAL_METRICS` — 11 `metric_name` (spec/14 §14.7);
- `TARGET_UNITS` — целевые единицы стандартных метрик (spec/06 §6.1);
- `CHECK_CATALOG: dict[str, CheckInfo]` — каталог spec/05 §5.2. Поля: `severity`, `ui_level`, `plugin`, `title` (по-русски), `computed_by` (`backend|ml|both`), `scope` (`record|paper`), `backend_default_enabled` (bool), `exported` (bool).

**`records.py`** — pydantic-модели строк. Имена полей совпадают с колонками шаблона; числа — `Decimal | None`, списки — `list[str]`, булевы — `bool | None`.
- `RecordMeta`: `ml_order: int`, `confidence: float | None`, `page: int | None`, `bbox: tuple[float, float, float, float] | None` (пункты PDF, как прислал ML), `review_status: ReviewStatus`, `corrected_value: Decimal | None`, `corrected_raw: str | None`, `added_by: AddedBy`, `quote_found: bool | None`, `norm_status: NormStatus | None`, `norm_issue: str | None`, `norm_trace: list[TraceStep]`, `notes_backend: list[str]`, `extra: dict` (поля вне шаблона → `unmapped_values`), `linked_by_backend: bool`.
- `Publication`, `Material`, `Device`, `Layer`, `Measurement`, `Result`, `IssueRecord`. У каждой модели есть поле `meta: RecordMeta`, в CSV оно не выгружается.
- `IssueRecord` дополнительно: `key` (внутренний `I-…`, К-6), `severity`, `computed_by: "backend"|"ml"`, `merged: bool`, `confidence`, `status: IssueStatus`, `numbers: dict`, `text` (= `description`).
- `PaperBundle`: `publication`, `materials`, `devices`, `layers`, `measurements`, `results`, `issues`; методы `by_id(table, id)`, `iter_tables()`, `effective_value(result)` (`corrected_value ?? normalized_value`), `failed_stub(publication_id)` — пакет упавшего файла (К-14).

**`io.py`** — общее чтение и запись ячеек (раньше было в `export`, теперь его используют `ml`, `checks`, `storage`, `export` и тесты):
```python
def parse_cell(table: str, column: str, text: str) -> Decimal | bool | list[str] | str | None   # '' → None; NUMERIC → Decimal; BOOL → bool (unencapsulated → False по К-7); LIST → list
def format_cell(table: str, column: str, value) -> str          # Decimal без экспоненты и лишних нулей, true/false, списки через разделитель LIST_FIELDS
def read_tables(dir: Path) -> dict[str, list[dict[str, str]]]   # 7 CSV, utf-8-sig, порядок строк сохраняется
def bundles_from_tables(tables) -> list[PaperBundle]            # ml_order = порядок строки в файле; исходный текст непонятного bool — в meta.notes_backend
def tables_from_bundles(bundles) -> dict[str, list[dict[str, str]]]   # для записи CSV (запись в файл — export.write_submission)
```

**`extraction.py`** — вход и выход адаптера извлечения:
- `ExtractRequest(publication_id, pdf_path, sha256, domain="solar", fields: list[ProfileField] | None, document: ParsedDocument | None, model_version: str = "latest", request_id)`;
- `ExtractOutcome(bundle: PaperBundle, model_version: str, warnings: list[str], raw: dict, from_cache: bool)`;
- `MlHealth(status: "ok"|"degraded"|"down", model_version: str | None, retry_after_s: int | None)`.

**`profile.py`** — `ProfileField`, `RuleConfig(check_name, enabled, params)`, `ExtractionProfile(profile_id, version, name, collection, fields, rules, builtin, flags)`. Флаги: `export_absent=False`, `ml_issue_min_confidence=0.5`, `backend_rules=True` (выключатель всех правил бэка — для строгого roundtrip). Функция `builtin_solar_profile()` (id `prf_solar_tz_v1`, псевдоним `tz-solar-v1`): поля по шаблону и 11 `metric_name` с единицами и синонимами; правила из `CHECK_CATALOG` с `backend_default_enabled`; пороги `jv_consistency.threshold_pct = 3`, `eqe_consistency.threshold_pct = 5`; `comparability` выключено (раздел 5.3). `param_to_metric(param) -> str | None` и обратная функция.

**`entities.py`** — сущности хранения и API (все модули обмениваются ими, а не своими типами):
- перечисления: `CorpusFileStatus` (`pending, downloading, queued, processing, done, failed, skipped_duplicate, needs_ocr, cancelled`), `RunKind`, `RunStatus` (`queued, running, done, done_with_errors, cancelling, cancelled, failed`), `PaperStatus` (`queued, processing, done, failed`), `ReviewStatus` (`new, accepted, rejected`), `ExtractionStatus`, `IssueStatus` (`open, fixed, ok`), `AddedBy` (`auto, manual`), `SourceKind`, `DupKind` (`file, doi, title`), `NormStatus`;
- записи: `User`, `CurrentUser(id, workspace_id, email, is_admin)`, `Corpus` (с `kind: "regular"|"manual"|"extract"`), `CorpusFile`, `Run` (со `counts`, `paused`, `pause_reason`, `output: RunOutput | None`), `RunOutput(available, url, csv_files, check_status, built_at)`, `Paper`, `PaperStats(quality, coverage, open_issues, top_level, facts_count)`, `Quality`, `Coverage`, `Fact`, `Anchor`, `Issue`, `Action`;
- черновики конвейера: `FactDraft` (факт без id БД: `target{entity,id,field}`, `param`, `metric_name`, `row_id`, `row_label`, `raw_*`, `normalized_*`, `extraction_status`, `confidence`, `source_*`, `anchor_ref`, `duplicate_of_ref`, `added_by`, `model_version`), `AnchorDraft` (`ref`, `kind`, `page`, `rects`, `text`, `fact_refs`);
- запросы и страницы: `FactFilter`, `SortKey(field, dir)`, `Page[T](data, total, next_cursor)`, `FactRow` (строка `/facts/table`), `IssuesDiff(opened, closed, changed)`, `TransferResult`;
- выход конвейера: `FileReport` (строка `_meta/files.csv`), `OutputResult`, `CheckReport`.

**`ids.py`** — `new_id(prefix)` (12 символов base32), `stable_id(prefix, *parts)` (sha256 → base32, для `iss_` и `anc_`), `sha8(obj)`, `issue_key(check_name, record_ids, params)` по К-6.

**`errors.py`** — `AppError(code, message, http_status, field=None, details=None)`; `ERROR_CATALOG: dict[code, (http_status, message_ru)]` — полный список из `api_contract.md` §1.5; подклассы `ExtractError(code, retryable)` и `PdfError(code)`.

**`security.py`** — хеширование паролей; общее для `storage.seed_admin`, CLI `faktura users` и `api_base`:
```python
MIN_PASSWORD_LEN = 8
def validate_password(p: str) -> None          # AppError("validation_error", field="password") при длине < 8
def hash_password(p: str) -> str               # argon2id
def verify_password(p: str, h: str) -> bool
def generate_password() -> str                 # 16 символов
```

**`ports.py`** — протоколы:
```python
class ParsedPage: n: int; width_pt: float; height_pt: float; char_count: int; image_ratio: float; has_text_layer: bool; needs_ocr: bool
class ParsedDocument: page_count: int; pages: list[ParsedPage]; needs_ocr: bool; text_by_page: list[str]; parser_version: str
class ParseAdapter(Protocol):
    def parse(self, pdf_path: Path) -> ParsedDocument: ...
    def render_page(self, pdf_path: Path, n: int, scale: float = 1.5, fmt: str = "webp") -> bytes: ...
    def find_rects(self, pdf_path: Path, page: int, needle: str) -> list[Rect]: ...
    def page_size(self, pdf_path: Path, page: int) -> tuple[float, float]: ...
class ExtractAdapter(Protocol):
    name: str                                    # "mock" | "http" | "raw"
    def extract(self, req: ExtractRequest) -> ExtractOutcome: ...   # бросает ExtractError
    def health(self) -> MlHealth: ...
class NormResult: normalized_value: Decimal | None; normalized_unit: str | None
                  norm_status: Literal["ok", "converted", "failed", "not_applicable", "passthrough"]
                  spread_value: Decimal | None; trace: list[TraceStep]; issue: str | None   # "unit_inconsistency" | "ambiguity" | None
class Normalizer(Protocol):                      # полный интерфейс faktura_norm, конвейер и API пользуются только им
    version: str; dictionary_version: str
    def parse_number(self, text: str) -> ParsedNumber: ...
    def normalize(self, metric_name: str, raw_value: str | None, raw_unit: str | None, header_unit: str | None = None) -> NormResult: ...
    def normalize_field(self, table: str, column: str, raw_value: str | None, raw_unit: str | None) -> NormResult: ...
    def match_metric(self, text: str) -> str | None: ...
    def canon_material(self, text: str) -> str | None: ...
    def layer_function(self, text: str, position: int | None = None) -> LayerFunction: ...   # (value из VOCAB | None, note | None)
    def normalize_encapsulation(self, value: object) -> tuple[bool | None, str | None]: ...
    def normalize_spectrum(self, text: str | None) -> str | None: ...
    def format_display(self, value: Decimal | None, unit: str | None, *, digits: int | None = None) -> str: ...
    def preview(self, collection: str, param: str, raw: str) -> NormPreview: ...
    def dictionary(self) -> DictionarySnapshot: ...       # для GET /dictionary
class QuoteChecker(Protocol):
    def check(self, page: int | None, fragment: str | None) -> bool | None: ...   # None — не проверяется
class FileStore(Protocol): put_bytes / put_file / get_bytes / open / exists / delete / copy_to(key, path) / signed_url(key, ttl_s) -> str | None
class EventBus(Protocol):
    def publish(self, workspace_id: str, event: str, data: dict) -> str: ...
    def subscribe(self, workspace_id: str, last_event_id: str | None) -> Subscription: ...
class Executor(Protocol):                         # InlineExecutor (CLI, тесты) и ThreadExecutor (API)
    def submit(self, fn: Callable[[], None]) -> None: ...
class EvalAdapter(Protocol):
    def evaluate(self, gold_dir: Path, pred_dir: Path) -> EvalReport: ...
```
`Rect = {x, y, w, h}` — доли страницы от левого верхнего угла.

**`services.py`** — контейнер зависимостей, общий для CLI и API:
```python
@dataclass
class Services:
    settings: Settings; db: Any  # storage.Database; store: FileStore; parser: ParseAdapter; normalizer: Normalizer
    bus: EventBus; executor: Executor; extractor_factory: Callable[[], ExtractAdapter]; evaluator: EvalAdapter
```
Собирает его `pipeline.build_services(settings, **overrides)`. Тесты API собирают его вручную из реализаций L0.

### 4.4. Фикстуры
- `tests/conftest.py`: `settings` (временные пути), `case_dir`, `dev_dir`, `dev_gold_dir`, `test_dir`, `check_script` (без данных — пропуск с маркером `data`), `tmp_db_url`, `make_pdf(pages=[...])` (PDF через PyMuPDF), `example_7_0_1`, `gold_bundles` (через `domain.io`, кешируется на сессию).
- `tests/fixtures/`: `ml_extract_example.json` (пример §7.0.1), `ml_extract_broken.json` (битые записи), `mini_gold/` (7 CSV из двух статей).

### 4.5. `faktura/api/__init__.py`
```python
API_PREFIX = "/api/v1"
ROUTER_MODULES = ("faktura.api.corpus", "faktura.api.papers", "faktura.api.review")   # у каждого — ROUTERS: list[APIRouter]
```
`api.base.create_app` импортирует их через `importlib` внутри функции. Если пакета нет (`ModuleNotFoundError` с именем именно этого модуля), он пропускается с предупреждением в лог. Любая другая ошибка импорта роняет запуск.

### 4.6. Прочее
`README.md` (установка, CLI, API, compose, переменные), `.env.example`, `Dockerfile` (образ CLI и API, `PYTHONUTF8=1`), `docker-compose.yml` (postgres:16, minio, api, ml-mock = `python -m faktura.ml.mock_server`).

---

## 5. Модули

| id | Модуль | Ур. | Зависит от | Сложность | P |
|---|---|---|---|---|---|
| `norm` | Нормализация `faktura.norm` | 0 | — | medium | P0 |
| `adapters` | PDF (`pdfio`) и ML-1 (`ml`): приём, разбор, цитаты, мок, HTTP, кеш | 0 | — | complex | P0 |
| `checks` | Правила, слияние, ключи, повторы, качество и покрытие | 0 | — | complex | P0 |
| `export` | Выборка, санитайзер, CSV, `_meta`, паспорт, валидаторы, оценка | 0 | — | complex | P0 |
| `storage` | БД, файлы, агрегаты (этап A — P0, этап B — P1) | 0 | — | complex | P0/P1 |
| `pipeline` | Конвейер, сборка выхода, прогоны, события, **CLI** | 1 | norm, adapters, checks, export, storage | complex | P0/P1 |
| `api_base` | Приложение, ошибки, вход, пользователи, health, сериализаторы, заглушки P2 | 1 | storage, norm | complex | P1 |
| `api_corpus` | Корпус, файлы, прогоны, SSE, `/extract`, экспорт, профили, паспорт, сводки | 2 | api_base, pipeline, export, storage, adapters | complex | P1 |
| `api_papers` | Статьи, страницы, факты (чтение), таблица фактов, язык запросов, точность | 2 | api_base, pipeline, storage, norm, export, adapters | complex | P1/P2-R |
| `api_review` | Правки, ручные факты, замечания, проверки, журнал, отмена | 2 | api_base, pipeline, storage, norm, checks | complex | P1 |

Пути модулей не пересекаются. Тесты модуля лежат в `backend/tests/<id>/`.

**Вехи внутри модулей.** `storage` и `pipeline` делятся на этапы. Этап P0 закрывается своими тестами (`tests/<id>/test_p0_*.py`) и идёт первым. Координатор запускает `pipeline` после этапа A у `storage` и готовности P0 у остальных модулей L0, не дожидаясь этапа B. Поэтому CLI не ждёт P1-объёма хранения.

```
Каркас + tests/shared ─► L0: norm · adapters · checks · export · storage(A ─► B)
                       ─► L1: pipeline(P0: конвейер + CLI ─► P1: фон, пауза, события, keep_review) · api_base
                       ─► L2: api_corpus · api_papers · api_review   (параллельно, без общего кода, кроме api.base)
                       ─► Интеграция
```
Критический путь сдачи: каркас → `storage` A, `export`, `adapters`, `norm`, `checks` (P0) → `pipeline` P0 (CLI).

---

### 5.1. `norm` — нормализация

**Пути:** `backend/src/faktura/norm/`, `backend/tests/norm/`.
**Назначение:** spec/06 с интерфейсом `faktura_norm` (REQ-028, REQ-035, REQ-037, REQ-038, REQ-042…REQ-048, К-7, К-13). Реализует протокол `Normalizer` полностью.

```python
NORM_VERSION: str; DICTIONARY_VERSION: str
class LocalNormalizer(Normalizer)               # все методы протокола, арифметика Decimal
class FakturaNormAdapter(Normalizer)            # обёртка над пакетом ML-2; если пакета нет — ImportError при создании
def get_normalizer(settings) -> Normalizer
```

Правила `normalize(metric_name, raw_value, raw_unit)`:
1. Метрика с целевой единицей в `TARGET_UNITS` и распознанная единица → пересчёт (`converted`) или `ok`.
2. Метрика с целевой единицей, но единицы и шкалы нет и вывести их нельзя → `failed`, `issue = unit_inconsistency`.
3. **Безразмерная нестандартная метрика** (нет в `TARGET_UNITS`, например `bifaciality_factor`, `CRI`) без единицы → `passthrough`: `normalized_value` = разобранное число, `normalized_unit` пусто, замечания нет. Так в `dev_gold`: D015 `bifaciality_factor 0.6`, D022 `CRI 89/74/79`.
4. Нестандартная метрика с единицей → значение и единица как есть (`not_applicable`), если единица не из словаря пересчёта.
5. `±`: `spread_value` = число после `±` **в единицах статьи, без пересчёта**. В `dev_gold` разброс хранится так же: D021 `Voc 305 mV`, `spread 32`.
6. Диапазон `18.2-19.0` → `failed`, `issue = ambiguity`. «FF 0.71 %» → 71 с `unit_inconsistency`.

Словари — `norm/data/*.json`: синонимы метрик, единицы и множители, материалы (MA, FA, Cs), функции слоёв, спектры. Версия словаря хранится в данных.

**Приёмка:**
- (data) регрессия на всех строках `results` из `dev_gold` с `raw_value`: `normalize()` даёт тот же `normalized_value`/`normalized_unit` (сравнение `Decimal`), включая 74 строки с пересчётом (mV→V — 56, fraction→% — 18) и 4 строки `passthrough` (D015, D022); статусы `converted`/`ok`/`passthrough` выставлены верно;
- отдельный тест: `normalize("CRI", "89", None)` → `passthrough`, 89, единица пустая, `issue is None`; `normalize("Voc", "1.1", None)` без шкалы → `failed` + `unit_inconsistency`;
- `1197 mV → 1.197`; `0.71 fraction → 71`; `18.6 ± 0.4` → 18.6 и spread 0.4; `305 ± 32 mV` → 0.305 V и spread 32; NBSP, `−`, запятая; Rs в Ohm без площади и Eg в nm не пересчитываются;
- множители spec/06 §6.1 (A/m2, A/cm2, kOhm·cm2, min, day, µm, Å, mm2); в `str(value)` нет артефактов float;
- `match_metric`: `V_oc`, `open-circuit voltage` → `Voc`; `PCE` внутри слова не срабатывает; канонические имена из `EVAL_METRICS` возвращаются как есть (тест по всем 11);
- `layer_function`: `CdS` и n-слой → `ETL`; металлическая сетка → `front_contact`; просветляющий слой → `interlayer` с `note` «исходно: …»; значения, уже входящие в `VOCAB[layers.layer_function]`, не меняются (тест по всем значениям словаря и по всем строкам `dev_gold/layers.csv`);
- `canon_material`: `MAPbI3` → `CH3NH3PbI3`, `FAPbI3` → `HC(NH2)2PbI3`;
- `normalize_encapsulation`: `unencapsulated` → `False`, `encapsulated` → `True`, прочее → `None` + пометка;
- `format_display(Decimal("1.1"), "V", digits=2) == "1,10 В"`; `preview("solar", "voc", "1100 мВ")` → `ok`, 1.1, `V`; `"1,1 кВ"` → `ok = False`.

**Сложность:** medium.

### 5.2. `adapters` — PDF и ML-1

**Пути:** `backend/src/faktura/pdfio/`, `backend/src/faktura/ml/`, `backend/tests/adapters/`.
**Назначение:** проверка файлов, сбор входа, локальный `ParseAdapter` на PyMuPDF, признак `needs_ocr`, рендер страниц, поиск якорей, перевод bbox, проверка цитат как в валидаторе; разбор ответа §7.0.1, три реализации `ExtractAdapter`, кеш сырых ответов, мок-сервер (REQ-010, REQ-060, REQ-062…REQ-067, REQ-072, REQ-073, REQ-079, К-1, К-2, К-15, Б-1).

`pdfio`:
```python
def inspect_pdf(path: Path, *, max_mb=50, max_pages=300) -> PdfInfo     # sha256, size, page_count; PdfError: unsupported_file|file_too_large|too_many_pages|pdf_encrypted|pdf_broken
def collect_inputs(src: Path) -> CollectResult                           # папка (рекурсивно) | ZIP | .txt (W2 → rejected validation_error)
    # CollectResult.files: list[InputFile(name, rel_path, publication_id, path, source)]; .rejected: list[Rejected(name, code, message)]
def safe_unzip(zip_path_or_stream, dest: Path, limits: ZipLimits) -> list[Path]   # zip-slip, бомба, глубина, __MACOSX, скрытые файлы, сигнатура %PDF-
def dedupe_names(files, *, existing_names: set[str]) -> NameDedupeResult         # только duplicate_name; дубли по sha решает pipeline
class LocalParseAdapter(ParseAdapter)                                    # needs_ocr по spec/11 §11.3
def bbox_to_rects(bbox, page_w, page_h, origin="top-left") -> list[Rect]
def validator_norm(s: str) -> str                                        # копия norm() из check_submission.py
class PypdfQuoteIndex(QuoteChecker)                                      # тексты страниц pypdf кешируются на статью
def title_key(title: str | None, year: int | None, first_author: str | None) -> str | None   # для смысловой дедупликации
```

`ml`:
```python
def parse_extract_response(data: dict, publication_id: str) -> ExtractOutcome
    # pydantic-валидация по записи; битая запись отбрасывается с warning; невалидный конверт → ExtractError("ml_schema_mismatch")
    # _bbox/_page/_confidence → RecordMeta; поля вне шаблона → meta.extra; ml_order — порядок в ответе
    # metric_value/metric_unit/normalized_* от ML отбрасываются (их считает бэк, REQ-028); spread_value от ML СОХРАНЯЕТСЯ
class MockGoldExtractor(ExtractAdapter)     # (gold_dir, issues_mode="all"|"ml_only"); строит JSON §7.0.1 из dev_gold через domain.io
                                             # и пропускает через parse_extract_response; замечания эталона — computed_by=ml, confidence=1.0;
                                             # неизвестный publication_id — ответ только с publication; model_version = "mock-gold-<sha8 эталона>"
class HttpExtractor(ExtractAdapter)         # (base_url, timeout_s, retry_delays, concurrency, sleep=time.sleep, transport=None)
    # multipart file, publication_id, domain, fields (JSON), request_id
    # ретраи: 429 (с Retry-After), 502, 504, обрыв соединения/ConnectError/ReadError; 400/413/422 без ретраев
    # коды: ml_unavailable (health 503 или нет связи после ретраев; retryable=True), ml_timeout, ml_schema_mismatch,
    #       pdf_no_text|pdf_encrypted|pdf_broken (из 422), ml_error
class RawDirExtractor(ExtractAdapter)       # <raw_dir>/<publication_id>.json; health всегда ok
class CachedExtractor(ExtractAdapter)       # (inner, cache: MlCache)
class MlCache                               # (FileStore | каталог); ключ (sha256, model_version)
    # раскладка: {sha256}/{model_version}.json и {sha256}/_last.json → последняя версия для этого sha
def resolve_model_version(requested: str, extractor: ExtractAdapter) -> str | None   # "latest" → health().model_version; None, если ML недоступен
def build_extractor(settings, *, kind=None, url=None, raw_dir=None, retry_delays=None, cache=None) -> ExtractAdapter
# ml/mock_server.py: FastAPI-приложение GET /health и POST /ml/extract поверх MockGoldExtractor
```

**Правило кеша (REQ-066).** Ключ — `(sha256, resolved_model_version)`. `"latest"` превращается в версию из `health()` **один раз в начале прогона**. Если ML недоступен, берётся `_last.json` для этого sha. Найден ответ — используется без обращения к ML (возобновление офлайн). Не найден — действует пауза (раздел 5.6). Ответы мока не кешируются: это не нужно и не даёт устаревших данных при правке эталона.

**Приёмка:**
- PDF, созданные в тесте: битый, зашифрованный, не-PDF, сверх лимита страниц → верные коды; страница-картинка → `needs_ocr = true`;
- ZIP с `../evil.pdf`, `__MACOSX/`, скрытым файлом, вложенностью 11 уровней, превышением лимита → отказ (`zip_bomb`/`zip_too_large`) или пропуск; два `P001.pdf` в разных подпапках → второй с `duplicate_name`;
- `bbox_to_rects` для обоих начал координат; `find_rects` находит фразу на созданной странице;
- (data) `PypdfQuoteIndex` на `dev` и `dev_gold` совпадает с `check_submission.py`: из 251 проверяемой цитаты не найдена ровно одна (D003); цитата, пустая после `norm()`, считается ненайденной;
- пример §7.0.1 разбирается без потерь, `_bbox` и `_confidence` попадают в `meta`; битые записи отброшены с warning; конверт без `publication` → `ml_schema_mismatch`; присланные ML `metric_value`/`normalized_*` отброшены, `spread_value` сохранён;
- `HttpExtractor` на `httpx.MockTransport`: 429 → 200 с `Retry-After`; **504 → 200**; **`httpx.ConnectError` → 200**; 422 без ретраев; 3 подряд 502 → `ExtractError(retryable=True)`; таймаут → `ml_timeout`; семафор не пускает больше `concurrency`; `sleep` подменён, тест идёт мгновенно; `health()` на 503 → `down`;
- (data) мок на каждой статье `dev` даёт столько же строк в каждой таблице, сколько `dev_gold`, и ровно 49 непустых `spread_value`; `ml_only` убирает `jv_consistency`, `eqe_consistency`, `champion_vs_mean`;
- `CachedExtractor`: второй вызов с тем же sha256 и версией не обращается к внутреннему адаптеру; при недоступном ML и наличии `_last.json` ответ берётся из кеша;
- `mock_server` через `TestClient` + `HttpExtractor` дают тот же bundle, что прямой мок;
- `title_key`: регистр, пунктуация и пробелы не влияют; без года или автора → `None`.

**Сложность:** complex. Две подсистемы, ретраи с паузой, кеш с разрешением версии, совпадение проверки цитат с валидатором.

### 5.3. `checks` — проверки

**Пути:** `backend/src/faktura/checks/`, `backend/tests/checks/`.
**Назначение:** правила бэка spec/05 §5.2, проверка находок ML, слияние, ключи, автосвязывание повторов, ключ сопоставимости, качество и покрытие статьи (REQ-031, REQ-032, REQ-049…REQ-059, К-6, К-15, К-21).

```python
RULES_VERSION: str
BACKEND_RULES = ("jv_consistency", "voc_limit", "range", "eqe_consistency", "champion_vs_mean", "hysteresis_index",
                 "contradiction", "unit_inconsistency", "rounding", "voc_deficit_consistency", "missing_conditions",
                 "plausibility", "figure_mismatch", "dedup_trap", "comparability")
def run_backend_rules(bundle: PaperBundle, rules: dict[str, RuleConfig], *, quote: QuoteChecker | None = None) -> list[IssueRecord]
def check_ml_issues(issues, bundle, quote) -> list[IssueRecord]      # висячие record_ids и ненайденная цитата → пометки, без удаления
def merge_issues(backend: list[IssueRecord], ml: list[IssueRecord]) -> list[IssueRecord]
def assign_keys(issues, publication_id) -> list[IssueRecord]         # К-6, уникальность ключа в статье
def link_duplicates(bundle) -> list[DuplicateLink]                   # REQ-032, note «связано бэком»
def comparability_key(result, bundle) -> tuple[str, bool]            # (ключ, неполный) — только фильтр
def paper_quality(facts: Sequence[Fact | FactDraft], issues) -> Quality
def paper_coverage(bundle, profile) -> Coverage                      # единственный владелец расчёта покрытия (summary и /papers/{id}/coverage)
def check_paper(bundle, profile, *, quote=None, autolink=False) -> CheckOutcome   # всё вместе
```

**Что считает бэк.**
- Правила работают по `effective_value`. Записи `rejected` и строки с `duplicate_of` пропускаются, кроме `contradiction`.
- `comparability`: межстатейная сопоставимость — только фильтр (REQ-049, REQ-059), замечаний не порождает. Внутристатейное правило есть (нет AM1.5G, нестандартный источник), но в `builtin_solar_profile` оно **выключено**. Находки `comparability` от ML проходят как есть.
- `missing_conditions` и другие правила со `scope = paper` дают не больше одного замечания на статью, `record_ids = []`.
- `source_page` и `source_fragment` замечания бэка берутся из **записи-якоря** — первой в упорядоченных `record_ids`. Берётся её `source_page` и `source_fragment`; если цитаты нет — ссылка «Table N, строка <device_label>» или «Figure N». У замечаний со `scope = paper` якорь — первая запись `results` типа J-V; если записей нет — страница 1 и `publications.title` (проверяется `QuoteChecker`, при ненайденной цитате фрагмент остаётся пустым, а в `meta.notes_backend` пишется пометка).

**Правила слияния `merge_issues`** (К-15, REQ-056). Ключ `k = (check_name, frozenset(record_ids))`.
1. **Совпадение ключа.** Одна запись. `record_ids` и их порядок, `source_page`, `source_fragment` и `confidence` — от ML, если там не пусто. `description`, `numbers`, `severity` — от бэка. `computed_by = backend`, `merged = True`.
2. **Пересечение.** Тот же `check_name`, `scope = record`, оба набора непусты и пересекаются → одна запись, как в п. 1. `record_ids` — набор ML; лишние id бэка уходят в `numbers.extra_record_ids`.
3. **Замечание о статье.** У находки ML пустые `record_ids`, у правила `scope = paper` и тот же `check_name` → одна запись с пустыми `record_ids`.
4. Прочие записи остаются как есть. Внутри находок бэка повторы по `k` схлопываются.

После слияния в статье нет двух замечаний с одинаковым `k`. У правил `scope = paper` не больше одного замечания на `check_name`.

**Приёмка:**
- (data) `ml_only` + правила на `dev_gold`: бэк воспроизводит все эталонные `jv_consistency` (7), `eqe_consistency` (1), `champion_vs_mean` (1) по множеству `record_ids`; на D021 нет ложных `jv_consistency`;
- (data) **снимок лишних срабатываний**: на `dev_gold` при `issues=all` после `merge_issues` (а) каждый эталонный ключ `(publication_id, k)` есть в выходе; (б) лишние записи только с `check_name` из `BACKEND_RULES` с `computed_by ∈ {backend, both}`; (в) их список совпадает со снимком `tests/checks/snapshots/dev_extra_issues.json`. Изменение снимка — осознанное, отдельным коммитом;
- синтетика на каждое правило: срабатывает и не срабатывает на границе порога; Pin из W/m2 ×0,1; AM1.5G без интенсивности → 100; `light_intensity = 0` → правило пропускается;
- слияние: все 4 случая и инвариант «нет двух записей с одним `k`» (параметризованный тест); порядок `record_ids` не влияет;
- у каждого замечания бэка непустые `source_page` и `source_fragment`, если они есть у записи-якоря; у правил `scope = paper` — по описанному запасному порядку;
- `comparability` при профиле по умолчанию не создаётся; при включённом правиле создаётся только по признакам этой статьи;
- ключи детерминированы: два прогона дают одинаковые `key`; при пустых `record_ids` ключ строится от параметров;
- `description` по-русски, с числами обеих сторон;
- `paper_coverage` и `paper_quality` на синтетике;
- (perf) `check_paper` на D021 (122 строки) — до 100 мс ×3.

**Сложность:** complex. Калибровка допусков на округление, слияние, эвристика повторов.

### 5.4. `export` — сдача, паспорт, оценка

**Пути:** `backend/src/faktura/export/`, `backend/src/faktura/passport/`, `backend/src/faktura/evaluation/`, `backend/tests/export/`.
**Назначение:** выборка по spec/14 §14.5, выгрузка ручных фактов, санитайзер, запись 7 CSV, `_meta`, ZIP, запуск валидатора, строгий валидатор, экспорт для людей, паспорт, оценка с `dev_gold` по §14.7 (REQ-002…REQ-041, REQ-053, REQ-057, REQ-112…REQ-114, REQ-116…REQ-120, К-3, К-9, К-14).

```python
# export/select.py
def select_for_submission(bundle: PaperBundle, profile: ExtractionProfile, *, failed: bool = False,
                          manual_facts: Sequence[Fact] = ()) -> SelectResult      # .bundle, .events
    # rejected — нет (у полевого факта очищается ячейка и её единица); corrected_value → normalized_value и metric_value;
    # absent — только при profile.flags.export_absent; находки ML с confidence < порога — нет; issues со status=ok — нет;
    # in_scope=false → только publications; failed=True → PaperBundle.failed_stub (К-14)
    # ручные факты → строки results (раздел «Ручные факты» ниже)
def materialize_manual_fact(fact: Fact, bundle: PaperBundle, profile) -> Result | None
# export/sanitize.py
def sanitize(bundles: list[PaperBundle]) -> SanitizeResult           # .bundles, .events (для errors.log)
    # словари → пусто + исходник в note; нечисла → привести или очистить + note; висячие ссылки → очистить + лог;
    # повтор id → каскадное переименование (duplicate_of, record_ids, device_id…); единица без значения → очистить (REQ-037);
    # пустые source_page/source_fragment у issues не трогаются (их нет у 9 эталонных замечаний; скрипт их не требует)
def number_issues(bundle) -> PaperBundle                             # I1, I2… по source_page (пустые — в конце), затем ml_order
# export/csv_submission.py
def write_submission(bundles, out_dir: Path) -> list[Path]           # через domain.io.tables_from_bundles; utf-8-sig, csv.writer (\r\n), QUOTE_MINIMAL
# export/validate.py
def strict_validate(tables) -> list[ValidationProblem]               # REQ-020, REQ-021; пустые source_page/fragment у issues → warning issue_source_missing
def run_check_script(script: Path, csv_dir: Path, pdf_dir: Path, timeout_s=900) -> CheckReport
    # subprocess(sys.executable), PYTHONUTF8=1, PYTHONIOENCODING=utf-8; разбор errors/warnings/quotes_*; crashed=True при трассировке
# export/meta.py
def write_meta(out_dir, *, manifest, files_rows: list[FileReport], facts_ui, errors, passport, passport_md, check_report) -> None
def build_manifest(...) -> dict                                      # поля spec/13 §13.6 + model_version, from_cache, versions
def zip_dir(dir: Path, *, include: Callable[[Path], bool] | None = None) -> bytes
# export/human.py (P2)
def export_flat(rows, fmt, options) -> bytes; def export_relational(bundles, fmt, options) -> bytes; def preview(rows, options) -> dict
# passport/
def build_passport(profile) -> dict; def passport_markdown(passport) -> str          # форма spec/04 §4.17, все 11 метрик (К-9)
# evaluation/
def evaluate(gold_dir, pred_dir, *, metrics=EVAL_METRICS) -> EvalReport             # сопоставление §14.7
def evaluate_issues(gold_dir, pred_dir) -> IssueEvalReport                          # recall/precision по ключу (publication_id, check_name, frozenset(record_ids))
def accuracy_response(report, *, collection, model_version) -> dict                 # форма spec/04 §4.13; linked=None без bbox
class LocalEval(EvalAdapter); class FakturaEvalAdapter(EvalAdapter)                 # второй — subprocess `faktura-eval`, когда его поставит ML-2
```

**Ручные факты в выгрузке (REQ-041).** `materialize_manual_fact` превращает ручной факт-показатель в строку `results`:
- `result_id = "RM" + n` — первый свободный номер в статье;
- `device_id` — из `fact.row_id`, если он указывает на запись `devices`; `measurement_id` — из тела факта или единственного измерения прибора, иначе пусто; `material_id` — `devices.absorber_material_id`;
- `metric_name` — через `param_to_metric`; `raw_value`/`raw_unit` — из `corrected_raw`; `normalized_*` = `metric_*` = `corrected_value`;
- `extraction_status = extracted`; `source_page` и `source_fragment` — из `source.page` и `source.quote` факта, `source_table`/`source_figure` — из `source.ref`;
- без страницы и цитаты или ссылки факт **не выгружается**, событие пишется в `errors.log`. В API у такого факта `exportable = false`.

Ручные факты-поля (`bandgap`, `thickness`, `area`) заполняют пустую ячейку своей записи или заменяют значение при `corrected_value`.

**Приёмка:**
- (data) `dev_gold` → `domain.io` → `select_for_submission` → `sanitize` → `write_submission` → `check_submission.py out dev`: 0 ошибок. Ячейки совпадают с эталоном с точностью до порядка, `\r\n` и задокументированных отличий: 4 ячейки `encapsulation` (К-7); 49 `spread_value` совпадают;
- fuzz санитайзера: значения вне словаря, `1,5`, `~1.9`, `18.2-19.0`, `15.28 mA`, висячие `device_id`/`duplicate_of`/`record_ids`, повтор `result_id` и `material_id`, `thickness_unit` без значения → 0 ошибок валидатора, все события в логе, ссылки переименованы вместе с id;
- issues с пустыми `source_page`/`source_fragment` проходят санитайзер без изменений, `strict_validate` даёт warning, а не error;
- ручной факт с цитатой → строка `RM1` со всеми ссылками, `check_submission.py` — 0 ошибок; без цитаты — не выгружен, событие в логе; `rejected`-полевой факт очищает ячейку и единицу;
- байты файла: начинается с BOM, заголовок 1:1 с `TABLES`, перевод строки `\r\n`;
- `run_check_script`: разбор реального вывода; поддельный скрипт с трассировкой → `crashed=True`;
- `evaluate(gold, gold)` — P = R = F1 = 1 по 11 метрикам; изменённые предсказания дают ожидаемые счётчики; `T80` с 0 строк помечается «мало данных»; `evaluate_issues(gold, gold)` — recall 1;
- паспорт: 7 таблиц, все колонки, 11 метрик, единицы, правила; Markdown строится;
- CSV для людей: `;` и десятичная запятая; XLSX открывается openpyxl.

**Сложность:** complex. Санитайзер с каскадом, сопоставление при оценке.

### 5.5. `storage` — хранение

**Пути:** `backend/src/faktura/storage/`, `backend/tests/storage/`.
**Назначение:** схема БД для сущностей spec/03 и spec/13, Unit of Work и репозитории, SQLite и Postgres, keyset, фасеты, агрегаты корпуса, файловое хранилище (REQ-071, REQ-074…REQ-076, REQ-080…REQ-083, REQ-088, REQ-090, REQ-094, REQ-101, REQ-104).

**Этап A (P0, нужен CLI):** таблицы `workspaces`, `corpora`, `corpus_files`, `runs`, `profiles` (встроенный профиль при создании схемы), `papers`, `tz_records` (строки 7 таблиц как JSON + `table`, `record_id`, `ml_order`), `facts`, `anchors`, `issues`; `LocalFileStore`.
```python
class Database:  # (url) → engine; SQLite: WAL, foreign_keys=ON, busy_timeout; Postgres: JSONB через with_variant
    def create_all(self) -> None; def uow(self) -> UnitOfWork
class UnitOfWork:  # контекстный менеджер; commit/rollback; репозитории — атрибуты
papers.create(...) -> Paper; papers.get(id) -> Paper
papers.save_processed(paper_id, bundle: PaperBundle, facts: list[FactDraft], anchors: list[AnchorDraft], issues: list[IssueRecord],
                      *, stats: PaperStats, model_version, versions: dict) -> SaveResult   # замена всего по статье одной транзакцией
papers.load_bundle(paper_id, *, apply_review=True) -> PaperBundle    # review_status/corrected_* из фактов → RecordMeta
papers.find_by_doi(workspace_id, doi) -> list[Paper]; papers.find_by_title_key(workspace_id, key) -> list[Paper]
papers.set_duplicate(paper_id, dup_of, dup_kind) -> None
facts.manual_for_paper(paper_id) -> list[Fact]
corpora.create/get/list; files.add/get/list/update_status; files.find_by_sha(workspace_id, sha256) -> list[CorpusFile]
runs.create/get/update/active_for_corpus(corpus_id) -> Run | None
class LocalFileStore(FileStore)
```
**Этап B (P1):** `users`, `refresh_sessions`, `login_attempts`, `actions`, `idempotency_keys`, `exports`, `gold_sets`/`gold_items`, `dictionary_synonyms`, `corpus_stats`; задел P2 — `views`, `projects`, `chats`, `turns` (минимальные поля).
```python
facts.query(filter: FactFilter, sort: list[SortKey], limit, cursor) -> Page[FactRow]     # keyset, устойчив к правкам
facts.facets(filter, fields) -> dict[str, dict[str, int]]
facts.update_review(fact_id, **changes) -> Fact; facts.add_manual(paper_id, draft) -> Fact
issues.upsert_preserving_review(paper_id, issues) -> IssuesDiff       # status переносится по key; несовпавшие → stale
actions.record(user, action, label, changes, *, paper_id=None, fact_id=None, group_id=None) -> Action
actions.last_undoable(user_id, within_h=24, depth=50) -> Action | None
stats.update_paper(paper_id, stats: PaperStats) -> None               # пересчитывает corpus_stats инкрементально
stats.overview(workspace_id, collection=None) -> dict                 # только из corpus_stats (REQ-101, ≤ 100 мс)
stats.corpus_summary(corpus_id) -> dict                               # форма spec/13 §13.8: агрегаты по papers/facts/issues
gold.load_from_dir(dataset, dir) -> GoldSet; gold.get(dataset) -> GoldSet | None
idempotency.get/put(user_id, key, response, ttl_h=24)
users.*, sessions.* (refresh: хеш, цепочка, гашение)
def encode_cursor(values) -> str; def decode_cursor(s) -> tuple
class S3FileStore(FileStore); def build_file_store(settings) -> FileStore
def seed_admin(db, settings) -> None                                  # через domain.security.hash_password
```
Кеш агрегатов для `/corpus/overview` живёт здесь: таблица `corpus_stats` обновляется в той же транзакции, что `save_processed`, `facts.update_review`, `facts.add_manual` и `issues.upsert_preserving_review`.

**Приёмка, этап A:**
- SQLite во временном файле: схема, CRUD корпуса, файла, прогона, статьи;
- (data) `save_processed` → `load_bundle` для каждой статьи `dev_gold` (через `domain.io`) возвращает тот же `PaperBundle`: `Decimal`, списки, порядок `ml_order`, `meta`;
- `LocalFileStore`: put/get/open/exists/delete/copy_to; ключи не выходят за корень.

**Приёмка, этап B:**
- мягкое удаление и восстановление; `gone` после 30 суток (часы подменяются);
- keyset: обход 1000 фактов страницами по 100 при вставках между запросами — без пропусков и повторов; сортировка по нескольким полям; фасеты;
- `upsert_preserving_review` сохраняет `fixed`/`ok` и помечает `stale`;
- `actions.last_undoable` учитывает глубину и окно 24 ч;
- `corpus_stats`: после `save_processed` и `update_review` `overview` совпадает с пересчётом «с нуля» (функциональный тест кеша);
- `seed_admin`: пароль из env хешируется, короткий пароль → ошибка запуска;
- (pg) тот же набор на Postgres; (s3) `S3FileStore` на MinIO из compose;
- (perf) 100 строк таблицы фактов при 100 тыс. фактов — до 300 мс ×3; `overview` — до 100 мс ×3.

**Сложность:** complex.

### 5.6. `pipeline` — конвейер, сборка выхода, прогоны, CLI

**Пути:** `backend/src/faktura/pipeline/`, `backend/src/faktura/cli/`, `backend/src/faktura/__main__.py`, `backend/tests/pipeline/`.
**Назначение:** обработка статьи по spec/07 §7.4; нормализация записи по полям; факты и якоря; приём в корпус и дедупликация; прогоны; пауза при недоступном ML; сборка выходной папки; события; CLI (REQ-001…REQ-011, REQ-024, REQ-028, REQ-035, REQ-037, REQ-038, REQ-061, REQ-065…REQ-078, REQ-097, REQ-103, REQ-119, REQ-120).

#### Этап P0 (веха «CLI»)

```python
def build_services(settings, **overrides) -> Services
def apply_normalization(bundle: PaperBundle, normalizer: Normalizer, profile) -> PaperBundle   # только через протокол
def build_facts(bundle, doc: ParsedDocument | None, parser: ParseAdapter, pdf_path, *, bbox_origin) -> tuple[list[FactDraft], list[AnchorDraft]]
def detect_semantic_duplicate(uow, workspace_id, paper_id, publication) -> tuple[str | None, DupKind | None]
class PaperProcessor:
    def process(self, paper_id, *, profile, mode: "full"|"renormalize"|"recheck", model_version: str) -> ProcessOutcome
        # parse → extract (через MlCache) → scope (in_scope=false: только publications) → apply_normalization
        # → build_facts → link_duplicates (если autolink) → check_paper (+merge) → quote_found → paper_quality/coverage
        # → save_processed (1 транзакция) → смысловые дубли → paper.done
class CorpusService:
    def create(self, workspace_id, user_id, name, collection, *, kind="regular", profile_id=None, project_id=None) -> Corpus
    def add_inputs(self, corpus_id, inputs: CollectResult, *, mode: "api"|"cli", dedupe=True) -> AddFilesResult
class RunService:
    def start(self, corpus_id, kind, *, file_ids=None, profile_id=None, model_version="latest", concurrency=4,
              keep_review=True, user_id, build_output: bool = False) -> Run        # 409 conflict, если идёт прогон
    def execute(self, run_id, *, on_progress=None, fail_fast=False, cancel: CancelToken | None = None) -> Run   # блокирующий
def build_output(services, corpus_id, out_dir: Path, *, profile, check_script: Path | None, pdf_dir: Path | None = None,
                 include_meta=True, run: Run | None = None) -> OutputResult
```

**`apply_normalization`** — порядок шагов; каждый шаг покрыт тестом:
1. `results.metric_name` ← `match_metric(metric_name)`, если имя не каноническое (`V_oc` → `Voc`). Исходное имя — в `meta.norm_trace`.
2. `results`: `normalize(metric_name, raw_value, raw_unit)` → `normalized_*`; `metric_value`/`metric_unit` = `normalized_*` (REQ-028). `failed` → `normalized_*` пусто, `meta.norm_issue` для правила `unit_inconsistency`. Строки без `raw_value` (`present_not_extractable`, `absent`) пропускаются.
3. **`spread_value`**: значение ML не затирается. Разбор `±` заполняет `spread_value`, только если от ML пусто. Если оба есть и различаются — остаётся ML, расхождение пишется в `meta.notes_backend`. Единицы не пересчитываются (раздел 5.1, п. 5).
4. `materials`: `bandgap_value` через `normalize_field`; `formula_normalized` ← `canon_material(formula)`, если от ML пусто (REQ-038).
5. `layers`: `layer_thickness` через `normalize_field`; `layer_function` ← `layer_function(...)`, если значения нет в `VOCAB`. Исходное значение — в `note` (REQ-035).
6. `devices`: `active_area` через `normalize_field`; `encapsulation` ← `normalize_encapsulation` (К-7).
7. `measurements`: `spectrum` ← `normalize_spectrum`; `light_intensity` — число.
8. **Единица только при значении** (REQ-037): по `UNIT_FOR_VALUE` единица очищается, если значения нет.

**Приём и дедупликация** (`CorpusService.add_inputs`):

| Случай | `mode="api"` (по спеке) | `mode="cli"` (REQ-024) |
|---|---|---|
| Отказ `inspect_pdf` (`pdf_encrypted`, `too_many_pages`, `file_too_large`, `pdf_broken`, `unsupported_file` у `.pdf`) | `rejected`, `CorpusFile` не создаётся | `CorpusFile` со статусом `failed` и кодом ошибки; в сдаче строка `publications` только с `publication_id` |
| `duplicate_name` | `rejected` | `rejected`: строка `publications` уже есть у первого файла; запись в `files.csv` со статусом `rejected`, код выхода 2 |
| Тот же sha256 в корпусе | `skipped_duplicate`, `duplicate_of` | **не пропускается**: у каждого файла свой `publication_id` и своя строка. ML повторно не вызывается — ответ берётся из кеша по sha. В `files.csv` — `dup_of`/`dup_kind=file` |
| Тот же sha256 в другом корпусе | при `reuse_existing` статья связывается без разбора | не применяется (БД временная) |
| Смысловой дубль: DOI, затем `title_key` (название + год + первый автор) | `Paper.dup_of`/`dup_kind` = `doi\|title`; данные не сливаются | в `files.csv` — `dup_of`/`dup_kind`; на CSV не влияет |
| `--no-dedupe` | — | отключает пометки по sha и смысловым дублям |

**Недоступный ML (REQ-065).** Перед прогоном и после каждой ошибки `ml_unavailable` вызывается `health()`.
- **CLI.** Ожидание по `--retry-delays` (по умолчанию `5,15,30`). Если ML поднялся, работа продолжается. Если нет, оставшиеся файлы получают `failed` с `ml_unavailable` (кроме тех, что есть в кеше), набор всё равно пишется, код выхода 2.
- **API (этап P1).** Прогон получает `paused = true`, `pause_reason = "ml_unavailable"`, файлы остаются `queued`, событие `run.paused`. `health()` опрашивается каждые `ml_pause_poll_s`. Когда ML поднялся — событие `run.resumed`. Если ML не поднялся за `ml_pause_max_s`, оставшиеся файлы получают `failed` с `ml_unavailable`, прогон — `done_with_errors`. Статус прогона на паузе остаётся `running`: новых значений перечисления нет.

**`build_output`** — одна сборка выходной папки для CLI и API:
1. Статьи корпуса по `publication_id`, включая `failed` (→ `failed_stub`) и файлы, отклонённые при приёме в CLI.
2. `load_bundle(apply_review=True)` + `facts.manual_for_paper`.
3. `select_for_submission` → `sanitize` → `number_issues` → `write_submission` (ровно 7 CSV в `out_dir`).
4. PDF кладутся во временный каталог `<tmp>/pdfs/<publication_id>.pdf`: из `FileStore` через `copy_to` в API или из входных путей в CLI. Если передан `pdf_dir`, берётся он.
5. `strict_validate`; `run_check_script`, если задан скрипт.
6. При `include_meta` — `_meta/` (`manifest.json`, `passport.json`, `passport.md`, `files.csv`, `facts_ui.json`, `errors.log`, `check_report.txt`).
7. Возвращает `OutputResult(csv_paths, meta_dir, check, exit_code_hint, events)`.

**CLI:**
```
faktura extract --domain solar --in <папка|ZIP|.txt> --out <папка> [--check <check_submission.py>]
    [--profile tz-solar-v1|./profile.json] [--format csv[,json,xlsx]] [--concurrency 4]
    [--extractor mock|http|raw] [--extract-url URL] [--raw-dir DIR] [--mock-issues all|ml_only] [--gold-dir DIR]
    [--model latest] [--parse-url URL (принимается, в волне 1 игнорируется с предупреждением)]
    [--retry-delays 5,15,30] [--no-dedupe] [--ocr auto|off|force] [--fail-fast]
    [--db PATH] [--cache-dir DIR | --no-cache] [--no-backend-rules]
    [--server URL]                    # удалённый режим, P2-R (ниже)
faktura import-raw --in ./raw --out ./out [--check ...]       # = extract --extractor raw
faktura eval --gold <dev_gold> --pred <out> [--issues] [--json report.json]
faktura check <папка CSV> <папка PDF> [--script PATH]
faktura users create|disable|enable|reset-password|list [--db URL]     # domain.security
faktura serve [--host --port]     # uvicorn, фабрика faktura.api.base.app:create_app (ленивый импорт)
```
- `extract`: настройки → SQLite (временный файл или `--db`) → корпус → `collect_inputs` → `add_inputs(mode="cli")` → прогон inline. Прогресс — в stderr: `[ 61/148] done  cigs_flex.pdf  facts=16 issues=1`. Затем `build_output` в `--out`, код выхода `0|1|2|3`. Экстрактор по умолчанию — `http`, если задан `--extract-url`, иначе `mock`.
- **Кеш ML** — `--cache-dir` (по умолчанию `settings.cache_dir`, вне `--out`). Если путь `--cache-dir` лежит внутри `--out`, это ошибка запуска с кодом 1. Повторный запуск берёт ответы из кеша и возобновляет работу после сбоя.
- **Удалённый режим (P2-R).** `--server URL` — те же шаги через API: `POST /auth/login` (почта и пароль из `FAKTURA_EMAIL`/`FAKTURA_PASSWORD`) → `POST /corpora` → `POST /corpora/{id}/files` → `POST /corpora/{id}/runs` → опрос `GET /runs/{id}` → `POST /exports {submission}` (асинхронно) → `GET /exports/{id}` → файл → распаковка в `--out`. Ключей API в волне 1 нет: `--api-key` даёт код 1 и сообщение «Ключи API появятся позже; задайте FAKTURA_EMAIL и FAKTURA_PASSWORD» (api_contract У-31).

**Приёмка этапа P0:**
- `apply_normalization` на синтетическом bundle: `V_oc` → `Voc`; CdS с функцией `buffer` → `ETL`; `Ag grid` → `front_contact`; AR-покрытие → `interlayer` + `note`; `MAPbI3` → `CH3NH3PbI3`; `thickness_unit` без значения очищается; `spread_value` от ML сохраняется; `±` заполняет пустой `spread_value`; `metric_*` = `normalized_*`; на всех `dev_gold`-бандлах шаги 4–8 ничего не меняют (кроме К-7);
- (data) **roundtrip REQ-120**: `faktura extract --in dev --out tmp --check <script>` с моком → код 0; в `tmp` ровно 7 CSV и `_meta/`; валидатор — 0 ошибок; `faktura eval` — P = R = 1 по 11 метрикам; 49 `spread_value` и 4 `passthrough`-строки на месте;
- (data) **roundtrip замечаний**: (а) с `--no-backend-rules` ключи `(publication_id, check_name, frozenset(record_ids))` в `issues.csv` совпадают с эталоном как мультимножества (53 записи); (б) с правилами — recall эталонных ключей = 1, лишние записи совпадают со снимком `checks` (`tests/checks/snapshots/dev_extra_issues.json`), в статье нет двух записей с одним ключом; `description` и `issue_id` не сравниваются (документированное отличие);
- (data, slow) `extract --in test` с моком: 90 строк `publications`, код 0, 0 ошибок валидатора;
- вход с битым, зашифрованным и сверхбольшим (лимит снижен в тесте) PDF → каждый даёт строку `publications` только с `publication_id`, статус `failed` в `files.csv`, запись в `errors.log`, код 2;
- два файла с одинаковым содержимым под разными именами → две строки `publications` с одинаковыми данными, ML вызван один раз (шпион);
- смысловой дубль (одинаковые DOI у двух статей) → `dup_kind = doi` в `files.csv`; `--no-dedupe` его убирает;
- **кеш**: второй `extract` с `--extractor http` на шпионском транспорте не вызывает `/ml/extract`; первый прогон с падением на 3-м файле, затем второй → ML вызывается только для упавших файлов; `--cache-dir` внутри `--out` → код 1;
- **недоступный ML**: `health` даёт `down, down, ok` → все файлы `done`, в stderr пауза; `health` всё время `down` → файлы `failed` с `ml_unavailable`, набор записан, код 2 (задержки подменены);
- поддельный валидатор с ошибкой или трассировкой → код 3; неверные аргументы → код 1; формат прогресса совпадает с регулярным выражением;
- `--extract-url` на `mock_server` (подмена транспорта) даёт те же CSV, что `--extractor mock`;
- `users create` с паролем короче 8 → код 1; `users create/list` работают на SQLite.

#### Этап P1 (веха «API»)

```python
class ThreadExecutor(Executor); class InlineExecutor(Executor)
RunService.submit(run_id) -> None                     # через services.executor
RunService.cancel(run_id) -> Run                      # незапущенные → cancelled; готовое сохраняется
RunService.retry_file(corpus_id, file_id) -> Run
RunService.start_or_append(corpus_id, file_ids, *, user_id) -> Run   # для корпуса kind="manual": добавить в активный прогон или начать selected
CorpusService.manual_corpus(workspace_id) -> Corpus   # «Загруженные вручную», kind="manual"
CorpusService.add_inputs(..., reuse_existing=True)    # связывание статьи из другого корпуса по sha
def recheck_paper(uow, paper_id, profile) -> IssuesDiff         # синхронно для правок (api_review)
def transfer_review(old_facts, new_facts) -> TransferResult     # прибор + показатель + измерение + место; значение в пределах 0,5 %
class InMemoryEventBus(EventBus)                       # кольцевой буфер на рабочее пространство, id по возрастанию; run.progress ≤ 1/с
# kinds: new_only, selected, failed_only, renormalize, recheck; пауза ML; события run.*, paper.*, issues.changed
# remote CLI (P2-R): faktura/cli/remote.py
```

**Приёмка этапа P1** (исполнитель — `InlineExecutor`, часы и `sleep` подменены):
- (data) прогон `full` по `dev` с моком через `RunService.start` + `submit`: 21 файл `done`; у D006 и D016 только `publications`; число фактов-результатов равно числу строк `results` эталона;
- битый PDF в корпусе → файл `failed` с кодом, прогон `done_with_errors`;
- `renormalize` и `recheck` не вызывают экстрактор (шпион);
- второй `start` → `AppError("conflict")`; `cancel` → незапущенные `cancelled`, готовое сохранено;
- `start_or_append` для служебного корпуса во время прогона → файл добавлен в тот же прогон, `counts.total` вырос, 409 нет;
- пауза ML: `health` = `down` → `paused = true`, событие `run.paused`, файлы `queued`; затем `ok` → `run.resumed`, файлы `done`; если `down` дольше `ml_pause_max_s` → `failed`/`ml_unavailable`, прогон `done_with_errors`;
- `keep_review`: принятый факт после повторного прогона остаётся `accepted`; несовпавшая правка становится ручным фактом;
- события: `run.progress` не чаще раза в секунду (часы подменены), `run.file` на каждый файл, `run.done` в конце; подписка с `Last-Event-ID` получает пропущенные; при переполнении буфера — `stream.reset`;
- якоря: при `_bbox` строятся `rects` в долях; без bbox — запасной поиск; `quote_found` заполнен;
- `reuse_existing`: тот же sha в другом корпусе → файл `done` без вызова ML, `paper_id` — существующей статьи;
- `recheck_paper` после правки значения открывает или закрывает `jv_consistency`;
- (P2-R) удалённый CLI на `httpx.MockTransport`: последовательность из раздела «CLI»; `--api-key` → код 1.

**Сложность:** complex.

### 5.7. `api_base` — приложение, вход, пользователи, общие механизмы

**Пути:** `backend/src/faktura/api/base/`, `backend/tests/api_base/`.
**Назначение:** фабрика приложения, middleware, ошибки, зависимости, пагинация, сериализаторы сущностей в форму контракта, вход и сессии, лимиты, пользователи, health, коллекции, заглушки P2 без своего владельца (REQ-085…REQ-099, REQ-111, REQ-127).

```python
def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI
    # services=None → лениво faktura.pipeline.build_services; роутеры — из faktura.api.ROUTER_MODULES (раздел 4.5)
    # middleware: X-Request-Id; CORS (белый список; expose: X-Action-Id, X-Request-Id, Content-Disposition, X-Check-Status, X-Faktura-Stub);
    # AppError/валидация → {"error": {...}}; лимит 600/мин → 429
# api/base/deps.py — для всех API-модулей
def get_services(request) -> Services; def get_uow(...); def current_user(...) -> CurrentUser; def admin_user(...)
def page_params(...) -> PageParams; def page_response(page: Page) -> dict
def mutation_response(payload: dict, action: Action, *, status=200) -> JSONResponse   # action_id в теле и X-Action-Id
def stub_response(payload: dict, *, status=200) -> JSONResponse                      # X-Faktura-Stub: 1
def idempotent(request, user) -> IdempotencyContext                                  # Idempotency-Key, 24 ч
# api/base/serializers.py — единая форма контракта §3
def user_out(u) / corpus_out(c) / corpus_file_out(f) / run_out(r) / paper_out(p, stats) / fact_out(f, normalizer) / anchor_out(a) / issue_out(i) / history_out(a)
# api/base/auth.py
def issue_tokens(user, *, remember: bool, now) -> TokenPair      # JWT 15 мин; refresh непрозрачный, хеш в БД, ротация; повтор гасит цепочку; remember=false → 12 ч
def decode_access(token, *, now) -> Claims                       # token_expired / unauthorized
class LoginLimiter                                               # 5 за 15 мин на почту, 20 на IP
class RateLimiter                                                # 600/мин на пользователя, 60 загрузок/ч
```
Эндпоинты: `/health`; `/auth/login|refresh|logout|me|register`; `/me` (GET, PATCH); `/collections`; `/admin/users*`; заглушки P2 — `/me/stats`, `/collections/{id}/stats`, `/views*`, `/graph*`, `/entities/*`, `/search`, `/projects*`, `/chats*`, `/turns*`, `POST/DELETE /dictionary/*`.

**Приёмка (TestClient, SQLite, `Services` собраны вручную):**
- вход: 200 с парой токенов и cookie; неверный пароль → 401 `invalid_credentials`; 6-я попытка → 429 `too_many_attempts` с `retry_after`; ротация refresh; повтор старого refresh → 401 `refresh_invalid`, вся цепочка погашена; без токена → 401 `unauthorized`; **истёкший access (часы подменены) → 401 `token_expired`**; **`remember=false` → `Max-Age` cookie 12 ч, refresh через 13 ч → 401**; `register` → 403 `registration_disabled`;
- **пароль короче 8** в `POST /admin/users`, `PATCH /admin/users/{id}` и `register` (при включённой регистрации) → 400 `validation_error`, `field = password`;
- **`GET /health`** без токена → 200 `{status, db, storage, ml}`; ML `down` (поддельный экстрактор) → `ml: degraded`, `status: ok`; отказ БД → `db: error`, HTTP 503;
- формат ошибок и `X-Request-Id` в каждом ответе; CORS для `http://localhost:5173`, отказ для чужого Origin; заголовки `expose` на месте;
- лимит 600/мин (часы подменены) → 429 с `Retry-After`;
- `create_app` без пакетов `api.corpus|papers|review` поднимается; с пакетом, который падает при импорте, — ошибка;
- каждая заглушка: код из контракта, `X-Faktura-Stub: 1`, ключи верхнего уровня как в контракте (параметризованный тест по таблице §4.14 контракта);
- `admin/users`: не админ → 403 `forbidden`; `email_taken` → 409; `generate: true` возвращает пароль один раз.

**Сложность:** complex. JWT, ротация с гашением цепочки, лимитеры, единые сериализаторы.

### 5.8. `api_corpus` — корпус, прогоны, события, экспорт

**Пути:** `backend/src/faktura/api/corpus/`, `backend/tests/api_corpus/`.
**Назначение:** корпус и файлы, прогоны, SSE, `/extract` и `/runs/{id}/output`, экспорт (сдача асинхронно, форматы для людей), профили, паспорт, сводки, `POST /papers` (REQ-071…REQ-079, REQ-100…REQ-103, REQ-112…REQ-115).

```python
ROUTERS: list[APIRouter]
def sse_stream(bus, workspace_id, last_event_id, *, ping_s=25, max_events=None, idle_timeout_s=None) -> AsyncIterator[str]   # тестируется напрямую
class ExportJobs                       # submit(scope, options, user) → Export; фон через services.executor → pipeline.build_output во временный каталог → ZIP в FileStore → export.ready
class EventTokens                      # одноразовые токены SSE на 60 с
```
Эндпоинты: `/corpus/overview`; `/corpora*` (CRUD, restore, files, retry, runs, summary); `/runs/{id}`, `/runs/{id}/cancel`, `/runs/{id}/output`; `/extract`; `POST /papers`; `/profiles*`; `/passport`; `/events`, `/events/token`; `/exports`, `/exports/{id}`, `/exports/{id}/file`; заглушка `/exports/preview`.

Поведение:
- `POST /exports {format: "submission"}` — **всегда асинхронно**: 202 `Export`, затем `export.ready`, `GET /exports/{id}` с итогом проверки, `GET /exports/{id}/file` (контракт У-29). PDF для валидатора материализует `build_output`.
- `POST /papers` → `CorpusService.manual_corpus` + `RunService.start_or_append`; 409 не бывает (контракт У-30).
- `/runs/{id}/output` — только для прогонов из `POST /extract` (`build_output=True`, сборка — последний шаг прогона перед `run.done`). Прогон не завершён → 409 `run_not_finished`; прогон не из `/extract` → 404 `not_found` (контракт У-32).

**Приёмка (TestClient, SQLite, мок, `InlineExecutor`, `sse_max_events`):**
- (data) ZIP с тремя PDF `dev` → 202 `{accepted: 3}`; прогон → `done`; `GET /corpora/{id}/summary` по форме spec/13 §13.8; повторный прогон во время активного (исполнитель на паузе через `threading.Event`) → 409; отмена → `cancelling` → `cancelled`;
- multipart: 201 файл → 413; ZIP больше `max_zip_mb` (лимит снижен) → 413; не PDF → в `rejected`; `urls`/`upload_id`/`server_path` → 400 `validation_error`;
- **`X-Action-Id`** и `action_id` в теле у `POST/PATCH/DELETE /corpora*`, `POST/PATCH /profiles*`, `POST /exports`, `POST /papers`;
- `POST /papers` два раза подряд, пока первый разбирается → оба 202, один прогон, `counts.total = 2`;
- SSE: `sse_stream` напрямую отдаёт `run.progress`/`run.file`/`run.done`; с `Last-Event-ID` догоняет пропущенное; эндпоинт через TestClient при `sse_max_events=3` закрывается сам; одноразовый `?token=` второй раз → 401;
- `POST /exports {submission, validate: true}` → 202 → `GET /exports/{id}`: `status: done`, `check.status: ok`; файл — ZIP из 7 CSV и `check_report.txt`, `X-Check-Status: ok`; поддельный валидатор с ошибкой → `status: failed`, `error.code = submission_invalid`, файл доступен; повтор с тем же `Idempotency-Key` → тот же `export_id`;
- `POST /extract` → 202; `GET /runs/{id}/output` до завершения → 409 `run_not_finished`; после → ZIP с 7 CSV и `_meta/`; у обычного прогона → 404;
- `GET /passport?format=md|json`; `PATCH /profiles/{id}` → версия +1 и `rerun_hint`; встроенный профиль → 403 `forbidden`;
- `GET /corpus/overview` без корпусов → `empty: true`; после прогона — счётчики из `corpus_stats`; (perf) ≤ 100 мс ×3.

**Сложность:** complex. Асинхронные выгрузки с идемпотентностью, SSE, приём больших загрузок.

### 5.9. `api_papers` — статьи, факты, таблица фактов, точность

**Пути:** `backend/src/faktura/api/papers/`, `backend/src/faktura/query/`, `backend/tests/api_papers/`.
**Назначение:** экраны статьи и таблицы фактов, язык запросов, точность и эталон, чтение словаря (REQ-104…REQ-106, REQ-118, REQ-121).

```python
ROUTERS: list[APIRouter]
# query/
def parse_query(collection: str, q: str, normalizer: Normalizer, profile) -> ParsedQuery   # filters[], unparsed, metric — spec/04 §4.11
def to_fact_filter(parsed: ParsedQuery, params) -> FactFilter
# api/papers/views.py — представления статьи: card, layout (из ParseAdapter), anchors, coverage (checks.paper_coverage), structure, processing, trace, source
```
Эндпоинты: `/papers` (список), `/papers/{id}` (карточка), `/pages/{n}`, `/file`, `/layout`, `/anchors`, `/coverage`, `/structure`, `/processing`, `/reprocess`, `DELETE`, `restore`, `PUT /papers/{id}/collection`; `/facts`, `/facts/{id}`, `/facts/{id}/source`, `/facts/{id}/trace`, `/papers/{id}/facts` (GET), `/facts/table`; `/data/query/parse` (P1); `/data/rows` (P2: настоящий разбор, пустые данные), `/data/filter-options`, `/data/compare` (заглушки); `/papers/{id}/links`, `/cited-by` (заглушки); P2-R — `/checks/accuracy`, `POST /admin/gold {dir}`, `GET /dictionary`, `POST /admin/reprocess`.

**Приёмка (TestClient, SQLite, корпус `dev` на моке через `pipeline` с `InlineExecutor`):**
- `GET /papers/{id}` по форме контракта §3.6 (`id` и `paper_id` совпадают); `/pages/{n}` — изображение с `ETag`, повтор с `If-None-Match` → 304; `/anchors` содержит `rects` в долях; `/file` поддерживает `Range`;
- `/facts/table`: фильтры, сортировки, keyset, фасеты; `q="КПД выше 18 после 2022"` → `filters` с `kind` `num` и `year`; `strict=true` с неразобранной строкой → 400 `query_unparsed`; `unparsed` заполнен;
- `/papers/{id}/coverage` и `summary.coverage[]` (из `api_corpus`) дают одинаковые числа на одной статье: обе строятся через `checks.paper_coverage`;
- `Fact.exportable` = `false` у `rejected`, `absent` и ручного факта без цитаты;
- `/checks/accuracy` без эталона → 404; эталон загружен через `uow.gold.load_from_dir` → форма §4.13 с `linked: null`, P = R = 1 на моке; то же после `POST /admin/gold {dataset: "dev", dir: "dev_gold"}` от админа; не админ → 403;
- `GET /dictionary` отдаёт словарь `normalizer.dictionary()`;
- заглушки этого модуля: код, `X-Faktura-Stub: 1`, форма из контракта.

**Сложность:** complex. Язык запросов, keyset-таблица с фасетами.

### 5.10. `api_review` — правки, замечания, журнал

**Пути:** `backend/src/faktura/api/review/`, `backend/src/faktura/review/`, `backend/tests/api_review/`.
**Назначение:** правки фактов с журналом и отменой, ручные факты и предпросмотр, замечания, каталог проверок (REQ-058, REQ-107…REQ-110).

```python
ROUTERS: list[APIRouter]
# review/service.py
class ReviewService:   # каждая правка: storage + pipeline.recheck_paper + stats.update_paper + actions.record в одной транзакции; события facts.changed/issues.changed
    def accept/reject/restore(self, user, fact_id, *, group_id=None) -> MutationResult
    def patch_value(self, user, fact_id, raw: str) -> MutationResult        # normalizer.preview; 422 fact_value_unrecognized (field=raw)
    def add_manual(self, user, paper_id, body) -> MutationResult            # added_by=manual; source.page/quote сохраняются для выгрузки
    def guess(self, paper_id, body) -> dict
    def bulk(self, user, action, *, fact_ids=None, filter=None) -> BulkResult   # ≤ 5000, одна запись журнала
    def resolve_issues(self, user, issue_ids, how, accept_fact_id=None, merge_action_ids=None) -> ResolveResult
    def reopen_issue(self, user, issue_id) -> ReopenResult
    def undo(self, user, action_id=None) -> UndoResult                       # 409 conflict / already_undone
```
Эндпоинты: `POST /facts/{id}/accept|reject|restore`, `PATCH /facts/{id}`, `POST /facts/bulk`, `POST /papers/{id}/facts`, `POST /papers/{id}/facts/guess`, `POST /normalize/preview`; `/issues`, `/papers/{id}/issues`, `/issues/resolve`, `/issues/{id}/reopen`; `/checks`, `/checks/rules`; `/history`, `/history/undo`, `/actions/{id}/undo`.

**Приёмка (TestClient, SQLite, корпус `dev` на моке):**
- `accept` → 200, `X-Action-Id`, `issues_changed`, обновлённые `paper.quality/open_issues/top_level`; `undo` возвращает состояние; повторный `undo` → 409 `already_undone`; отмена после чужой правки → 409 `conflict`;
- `PATCH {raw: "1100 мВ"}` для `voc` → `corrected_value = 1.1`, `extraction_status` не меняется; `"1,1 кВ"` → 422 `fact_value_unrecognized`, `field = raw`;
- правка пересчитывает проверки статьи синхронно (появилось или закрылось `jv_consistency`);
- `bulk` по фильтру → одна запись журнала с `count`; > 5000 → 400;
- `issues/resolve` → `fixed`; повтор → 409 `already_resolved`; `reopen` возвращает факт в `new`;
- `POST /papers/{id}/facts` с цитатой → `exportable = true`; затем `pipeline.build_output` даёт строку `RM1`, валидатор — 0 ошибок; без цитаты → `exportable = false`;
- `/checks/rules` — все имена `CHECK_CATALOG`, `by_rule` только из каталога;
- `/history`: служебные действия не показываются, но отменяются.

**Сложность:** complex. Журнал с отменой и конфликтами, синхронный пересчёт проверок.

---

## 6. Порядок работ и интеграция

1. **Каркас** (координатор): все `shared_paths` по разделу 4 и `tests/shared`. Проверка: `python -m pytest -q tests/shared` зелёный (тесты с данными — при наличии `data/`), `python -c "import faktura.domain"` работает.
2. **Уровень 0** — `norm`, `adapters`, `checks`, `export`, `storage`, параллельно. Модули опираются только на `faktura.domain` и `faktura.config`. Зависимости между ними внедряются через протоколы: например, `checks` получает `QuoteChecker`, а не импортирует `pdfio`. Эталон все читают через `faktura.domain.io`. Сначала закрываются тесты P0 (`storage` — этап A).
3. **Уровень 1** — `pipeline` (сначала P0 и CLI, затем P1) и `api_base`, параллельно. `pipeline` можно начинать после P0 зависимостей.
4. **Уровень 2** — `api_corpus`, `api_papers`, `api_review`, параллельно. Подключаются через `ROUTER_MODULES` без правки `api_base`.
5. **Интеграция:** полный `pytest`; `faktura extract` на `test` → 0 ошибок валидатора; `docker compose up` (postgres, minio, api, ml-mock) → health и сценарий «вход → импорт → прогон → экспорт» через HTTP; сверка ответов с `api_contract.md`.

Когда придёт настоящий JSON ML-1: проверить его контрактным тестом `adapters` (пример в `tests/fixtures`) и прогнать `faktura extract --extract-url …` на `dev` с `faktura eval`. Код конвейера при этом не меняется.

---

## 7. Тестирование

| Команда (из `backend/`) | Что |
|---|---|
| `python -m pytest -q` | все тесты, кроме `perf`; без данных кейса тесты `data` пропускаются; `pg` и `s3` — без переменных окружения |
| `python -m pytest -q tests/<module>` | тесты одного модуля |
| `python -m pytest -q -m "not slow"` | быстрый прогон |
| `python -m pytest -q -m perf` | замеры времени (допуск ×3 к порогам спеки) |
| `FAKTURA_TEST_PG_URL=… python -m pytest -q -m pg` | хранилище на Postgres |
| `FAKTURA_TEST_S3_URL=… python -m pytest -q -m s3` | `S3FileStore` на MinIO из compose |

- Данные кейса берутся из `FAKTURA_CASE_DIR` или `../data/caseB_kejs5`. Данные не копируются и не перемещаются.
- Тесты не ходят в сеть. HTTP к ML — через `httpx.MockTransport` и `mock_server`.
- Валидатор вызывается как есть (subprocess) с `PYTHONUTF8=1`.
- Фоновые задачи в тестах — `InlineExecutor`. Время и `sleep` подменяются. SSE — через `sse_stream` напрямую или с `sse_max_events`.
- Временные пороги — только под маркером `perf`.

---

## 8. Трассировка требований

| Требования | Модуль |
|---|---|
| REQ-001…011 (CLI, выход, коды, `_meta`) | pipeline (+ export) |
| REQ-012…022 (формат CSV, словари, санитайзер, валидатор, порядок) | export, domain.io |
| REQ-023…041 (содержание таблиц) | export (выборка, ручные факты), adapters (разбор), pipeline (`apply_normalization`, приём: REQ-024), norm |
| REQ-042…048 (нормализация) | norm, pipeline (`apply_normalization`) |
| REQ-049…060 (проверки, слияние, `quote_found`) | checks, adapters (`QuoteChecker`), pipeline (вызов) |
| REQ-061…070 (конвейер и адаптеры, кеш, пауза ML) | pipeline, adapters |
| REQ-071…079 (корпус, импорт, прогоны, дедупликация) | pipeline, adapters, api_corpus |
| REQ-080…084 (хранение, compose) | storage, каркас |
| REQ-085…099 (соглашения API, вход) | api_base, domain.security |
| REQ-100…103 (корпус, сводка, профиль, SSE) | api_corpus, storage (агрегаты) |
| REQ-104…106 (таблица фактов, статья) | api_papers |
| REQ-107…110 (факты, замечания, журнал) | api_review |
| REQ-111 (health) | api_base |
| REQ-112…115 (экспорт, паспорт, `/extract`) | export, pipeline (`build_output`), api_corpus |
| REQ-116…120 (оценка, roundtrip) | export (evaluation), pipeline (CLI-тесты), api_papers (accuracy) |
| REQ-121…125 (P2) | api_base, api_papers, api_corpus (заглушки по группам) |
| REQ-126…128 (производительность, секреты, тесты) | storage, api_base, все |

Принятые умолчания (`defaults_taken`) и где они реализуются:

| Умолчание | Где |
|---|---|
| Контракт ML-1 — только §7.0.1 | adapters (`ml`) |
| `_bbox` в пунктах, начало слева сверху, `rects` в долях | adapters (`bbox_to_rects`), pipeline (`build_facts`) |
| Оценка по §14.7, форма §4.13, `linked=null` | export (evaluation), api_papers |
| `fact_id`/`issue_id` непрозрачные; ключ `I-{check}-{якорь}`; `I1…` в выгрузке | domain.ids, checks, export |
| `added_by = auto\|manual` | domain.entities, storage, api_review |
| `encapsulation` — bool | domain.io, norm, export |
| Упавший PDF: строка только с id, код 2 | domain (`failed_stub`), pipeline (приём в CLI), export |
| `absent` и находки ML с confidence < 0,5 не выгружаются | export (select) |
| CSV: BOM, `\r\n`, QUOTE_MINIMAL, сортировка | export |
| Нестандартные метрики выгружаются, в оценку не идут | norm (`passthrough`), export, evaluation |
| `faktura.norm` + адаптер к пакету ML-2 | norm (протокол `Normalizer` полный) |
| Свой регрессионный модуль оценки до `faktura-eval` | export (`LocalEval`, `FakturaEvalAdapter`) |
| Слияние находок, `issues=all\|ml_only` у мока | checks (правила раздела 5.3), adapters |
| Автосвязывание повторов: реальный ML — да, мок — нет | config, pipeline, checks |
| CLI локально без входа; удалённый — `--server` (вход по почте и паролю; `--api-key` — когда появятся ключи) | pipeline (CLI) |
| Ретраи: CLI `5,15,30`, воркер `30/120/600` | config, adapters, pipeline |
| `ParseAdapter` локальный | adapters (`pdfio`) |
| URL/tus/server_path — 400 до волны 2 | adapters, api_corpus |
| Коллизия имён → `duplicate_name` | adapters, pipeline |
| Приоритеты P0/P1/P2, заглушки P2 | все API-модули |
| Корень кейса из `FAKTURA_CASE_DIR`/`--check` | config, pipeline (CLI) |
| Открытые пункты 1–13 BACKEND — по умолчаниям | api_base (refresh в cookie), pipeline (`reuse_existing`, перенос правок), api_base (чат — заглушка P2) |

---

## 9. Риски плана

| Риск | Мера |
|---|---|
| Итоговый JSON ML-1 отличается от §7.0.1 | Разбор изолирован в `ml.parse_extract_response`; контрактный тест на присланном примере; битые записи отбрасываются с предупреждением; санитайзер держит 0 ошибок |
| Ответ мока для `test` пустой: сдача валидна, качество около нуля | Решение Б-1: ML-1 успеет. Конвейер подключается к контейнеру флагом `--extract-url` без правок кода; `import-raw` принимает результаты файлами |
| Критический путь P0 зависит от P1-объёма | Этапы A/B у `storage` и P0/P1 у `pipeline`; `pipeline` стартует после P0 зависимостей |
| Расхождение SQLite и Postgres | Специфика СУБД только в `storage`; тесты `pg` |
| Ложные срабатывания правил | Снимок лишних замечаний на `dev_gold`; изменение снимка — осознанное |
| Модули L0 расходятся в типах и разборе CSV | Все обменные типы и чтение 7 CSV — в `faktura.domain` с тестами `tests/shared`; порядок исправления shared (раздел 4) |
| Долгая проверка сдачи в API (валидатор ≈ 15 с на dev, ≈ 90 с на test) | Сдача через API всегда асинхронна (202 + `export.ready`) |

---

## 10. Готовность волны 1 (Definition of Done)

1. `python -m pytest -q` из `backend/` — всё зелёное (тесты с данными — при наличии `data/`).
2. `faktura extract --domain solar --in ../data/caseB_kejs5/test --out ./out --check ../data/caseB_kejs5/tools/check_submission.py` → код 0, ровно 7 CSV, 0 ошибок валидатора, `_meta/` полный.
3. Roundtrip на `dev`: 0 ошибок валидатора; `faktura eval` по 11 метрикам — 1.0 на моке; замечания — точное совпадение ключей без правил бэка, recall 1 и снимок лишних с правилами.
4. Повторный `faktura extract` с HTTP-адаптером не вызывает ML (кеш).
5. `faktura serve` поднимает API; эндпоинты P1 работают по `api_contract.md`; P2 отдают заглушки нужной формы.
6. `docker compose up` поднимает postgres, minio, api и ml-mock; `/api/v1/health` → `ok`.
7. Подключение реального ML-1 — одна настройка: `--extract-url` или `FAKTURA_EXTRACT_URL`.

---

## 11. Ответы на замечания критика (редакция 2)

| Замечание | Что изменено |
|---|---|
| Скрытая зависимость от загрузчика `dev_gold` | Чтение 7 CSV в `PaperBundle` перенесено в shared `faktura.domain.io` (`read_tables`, `parse_cell`, `format_cell`, `bundles_from_tables`, `tables_from_bundles`) и покрыто `tests/shared`. `ml`, `checks`, `storage`, `export` и фикстура `gold_bundles` используют его (разделы 4.3, 4.4) |
| Неполные общие типы | В `domain/entities.py` добавлены `FactDraft`, `AnchorDraft`, `FactFilter`, `SortKey`, `Page`, `FactRow`, `IssuesDiff`, `Action`, `CurrentUser`, `PaperStats`, `RunOutput`, `FileReport`, `OutputResult`, `CheckReport` и перечисления статусов (раздел 4.3) |
| Протокол `Normalizer` неполон | Протокол расширен до полного интерфейса: `parse_number`, `normalize_field`, `normalize_encapsulation`, `normalize_spectrum`, `format_display`, `preview`, `dictionary`. Конвейер и API работают только через протокол (разделы 4.3, 5.6) |
| Инверсия зависимостей от auth | Хеширование паролей и политика длины — в shared `domain/security.py`; им пользуются `storage.seed_admin`, CLI `users` и `api_base` (раздел 4.3) |
| Нет владельца сборки выходной папки | `pipeline.build_output` — единственный владелец; его вызывают CLI, `POST /exports` и `POST /extract`; материализация PDF для валидатора — его шаг 4 (раздел 5.6) |
| Roundtrip для issues не определён | Формальные правила слияния (4 случая) и инвариант; roundtrip замечаний: точное совпадение ключей при `--no-backend-rules`; recall 1 и снимок лишних при правилах (разделы 5.3, 5.6) |
| Приёмка `norm` противоречит данным | Правило `passthrough` для безразмерных нестандартных метрик и отдельный тест; `failed` — только для метрик с целевой единицей (раздел 5.1) |
| Потеря `spread_value` | ML `spread_value` не затирается и сохраняется при разборе; `±` заполняет только пустое; единицы разброса — как в статье (разделы 5.1, 5.2, 5.6) |
| REQ-028/035/037/038 не применяются | `apply_normalization` — 8 шагов, включая `match_metric`, `layer_function`, `canon_material`, «единица только при значении», с тестом на каждый шаг (раздел 5.6) |
| REQ-074: смысловая дедупликация | `detect_semantic_duplicate` (DOI, затем `title_key`) в `pipeline`, `title_key` в `pdfio`, репозиторий `find_by_doi`/`find_by_title_key`; `--no-dedupe`; тесты (разделы 5.2, 5.5, 5.6) |
| REQ-024: строка на каждый PDF | Таблица приёма API/CLI: в CLI отказ при приёме → `failed` со строкой; дубль по sha не пропускается (кеш по sha); `duplicate_name` — `rejected` с кодом 2 (раздел 5.6) |
| REQ-066: кеш для CLI | `cache_dir` по умолчанию вне `--out`; разрешение `latest` через `health()` один раз на прогон; `_last.json` для офлайна; тесты повтора и возобновления (разделы 4.2, 5.2, 5.6) |
| REQ-065: недоступный ML | Определено поведение CLI (ожидание по `--retry-delays`, затем `failed`, код 2) и API (`paused`, `run.paused`/`run.resumed`, предел `ml_pause_max_s`); тесты на 504 и обрыв соединения (разделы 5.2, 5.6; контракт У-33) |
| Удалённый режим CLI противоречит спеке | `--server` — P2-R, вход по почте и паролю; `--api-key` — ошибка до появления ключей; зафиксировано в контракте У-31 |
| Синхронный POST /exports | Сдача всегда асинхронна: 202, `export.ready`, итог проверки в `GET /exports/{id}`; PDF материализует `build_output` (контракт У-29) |
| POST /papers и «один прогон на корпус» | Для служебного корпуса файлы добавляются в активный прогон (`start_or_append`), 409 нет (контракт У-30) |
| Заглушка `/admin/gold` против приёмки | Точность и `/admin/gold {dir}` — P2-R, реализуются в волне 1; в тесте эталон грузится и через storage, и через эндпоинт (раздел 5.9) |
| Мелочи контракта | 409 `run_not_finished`; `max_zip_mb = 2048`; тип `RunOutput`; таблица ключей id по сущностям, `paper_id`/`user_id` добавлены рядом с `id` (контракт §3.0, У-32) |
| Перегрузка `api_core`/`api_data` | API разбит на `api_base` (L1: приложение, auth, заглушки), `api_corpus`, `api_papers`, `api_review` (L2, параллельно); всё помечено complex. Чтобы уложиться в 10 модулей, `pdfio` и `ml` объединены в `adapters`, CLI вошёл в `pipeline` (один владелец пути сдачи) |
| Критический путь через P1 | Этапы A/B у `storage`, P0/P1 у `pipeline`, ранний старт `pipeline` (раздел 5) |
| Циклический импорт, владелец покрытия | Ленивое подключение по `ROUTER_MODULES` с обработкой отсутствия пакета (раздел 4.5); покрытие считает только `checks.paper_coverage`; кеш агрегатов — `storage.stats` |
| Нетестируемые критерии | «Отчёт о лишних» заменён снимком; «без дублей» определено через ключ `k`; пороги времени — под `perf` с допуском ×3; SSE — `sse_stream` и `sse_max_events`; фон — `InlineExecutor`; добавлены тесты S3, `X-Action-Id`, `token_expired`, TTL при `remember=false`, длины пароля, `/health`, кеша `overview` |
| Shared без тестов | `tests/shared` сверяет `TABLES`/`VOCAB`/`NUMERIC` со скриптом через `ast`, проверяет `domain.io`, каталог и профиль; порядок исправления shared (раздел 4) |
| Выгрузка ручных фактов | `export.materialize_manual_fact` (правила раздела 5.4), `storage.facts.manual_for_paper`, тесты в `export` и `api_review`; `Fact.exportable` в контракте |
| `source_page`/`source_fragment` у замечаний; `comparability` | Правила бэка берут их из записи-якоря с запасным порядком; пустые значения от ML не трогаются, строгий валидатор даёт warning; правило `comparability` бэка по умолчанию выключено, межстатейная сопоставимость — только фильтр (разделы 5.3, 5.4) |
