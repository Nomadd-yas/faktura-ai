# Области (solar, rag): реестр и общее хранение таблиц сдачи

Решение 21.11: строки 7 таблиц сдачи всех областей хранятся в **одной таблице `sub_rows`**,
состав таблиц и колонок области берётся из **реестра областей**. Солнечная область переезжает на ту же
схему, поведение API для неё не меняется.

## 1. Реестр областей — `faktura/domain/registry.py`

```python
@dataclass(frozen=True)
class DomainSpec:
    name: str                                   # "solar" | "rag"
    title: str                                  # «Тонкоплёночные солнечные элементы» / «RAG-системы»
    tables: dict[str, tuple[str, ...]]          # таблица -> колонки в порядке шаблона (7 таблиц)
    keys: dict[str, str]                        # таблица -> колонка-ключ (publication_id, result_id, ...)
    closed_enums: dict[str, tuple[str, ...]]    # "results.result_origin" -> допустимые значения
    recommended_enums: dict[str, tuple[str, ...]]  # нарушение -> предупреждение, не ошибка
    refs: dict[str, tuple[str, str]]            # "results.config_id" -> ("configurations", "config_id")
    list_fields: frozenset[str]                 # "issues.record_ids", "measurements.device_id" (solar)
    scored_metrics: tuple[str, ...] | None      # solar: 11 оцениваемых; rag: None (все метрики)
    result_filters: tuple[str, ...]             # колонки results для фильтра /results
    summary_by_default: bool                    # rag: /papers отдаёт сводку, строки — по запросу
    check_exit_review_ok: bool                  # rag: код выхода 2 check_submission = «нужна проверка», не ошибка
```

- `get_domain(name) -> DomainSpec`, `DOMAINS: dict[str, DomainSpec]`. Неизвестная область → `422 domain_not_supported`.
- **solar** строится из `domain/tables.py` (TABLES, VOCAB, LIST_FIELDS и т. д.); всё, что уже есть, не дублируется.
- **rag** строится из `domain/data/rag_schema.json`, а это копия `data/caseA_drive/submission_template/schema.json`.
  Закрытые словари: `publications.in_scope`, `results.result_origin`, `results.extraction_status`.
  Рекомендуемый словарь: `results.metric_direction`.
  Ссылки из results на `experiment_id`, `config_id`, `dataset_id`, `task_id`.
  Фильтры: `metric_name`, `config_id`, `dataset_id`, `experiment_id`, `task_id`.
- Настройки по областям (`config.py`): `case_dir` / `mock_gold_dir` / `check_script` — для solar, как сейчас;
  `rag_case_dir` (по умолчанию `data/caseA_drive`), `rag_mock_gold_dir`, `rag_check_script` выводятся из него.
  Функция `domain_paths(settings, name)` возвращает тройку.

## 2. Хранение — таблица `sub_rows`

| колонка | тип | смысл |
|---|---|---|
| `id` | bigint PK autoincrement | |
| `paper_id` | FK papers (ON DELETE CASCADE) | статья |
| `corpus_id` | str | разбор |
| `domain` | str(16) | `solar` / `rag` |
| `publication_id` | str | |
| `table_name` | str(32) | `results`, `configurations`, ... |
| `position` | int | порядок строки в таблице статьи |
| `row_id` | str nullable | значение колонки-ключа (`result_id`, `issue_id`, ...) |
| `metric_name` | str nullable | денормализация для results |
| `check_name` | str nullable | денормализация для issues |
| `num_value` | float nullable | `metric_value` числом — сортировка `/results?sort=value` в БД |
| `source_page` | int nullable | сортировка `sort=page` в БД |
| `quote_found` | bool nullable | результат проверки цитаты (results, issues) — без `load_bundle` на запрос |
| `data` | JSON (JSONB в Postgres) | `{колонка: строка}` ровно колонки шаблона, значения строками как в CSV |

Индексы:
- `(paper_id, table_name, position)`;
- `(corpus_id, table_name, publication_id)`;
- `(corpus_id, table_name, metric_name)`;
- `(corpus_id, table_name, check_name)`.

Фильтры RAG по `config_id` и т. п. делаются через `data[...]` (`as_string()` в SQLAlchemy работает и в SQLite, и в Postgres)
поверх индекса по `(corpus_id, table_name)`.

**Запись** — `SubRowsRepo.replace(paper_id, corpus_id, domain, tables)`: delete по `paper_id`, затем пачки `executemany` по 1000 строк,
в той же транзакции сохранения статьи. 28 тыс. строк D014 должны писаться за секунды.

**Чтение** — тот же интерфейс, что у нынешнего `SubTablesRepo`, плюс `domain`:
- `paper_tables(paper_id) -> {table: [row dict]}`;
- `list_results(corpus_id, pids, metrics, filters: dict[col, value], exclude_duplicates, sort, dir, limit, cursor) -> (rows, total, next_cursor)`
  с сортировкой в БД по `num_value` / `source_page`;
- `list_issues(...)`;
- `paper_summary(paper_id) -> {table: count, metrics: [{metric_name, n}], ...}` — для `/papers/rag/{pid}`.

**Переезд:** при `create_all`, если `sub_rows` пуст, а старые `sub_*` не пусты, строки однократно копируются в `sub_rows`
(`domain=solar`). Старые таблицы `sub_*` остаются нетронутыми и в коде больше не используются; удалить их можно позже.

`views.py` (счётчики по файлам, сводка) и `routes_runs.submission_zip` читают только через репозиторий или запросы к `sub_rows`.

## 3. Конвейер по областям

- **solar** — нынешний `PaperProcessor`. В конце он пишет `sub_rows` через `SubRowsRepo.replace(..., domain="solar", ...)`.
- **rag** — `GenericProcessor`:
  1. ответ модели → `{table: [rows]}`;
  2. проверка по `DomainSpec`: колонки, закрытые словари (ошибка), рекомендуемые (предупреждение), ссылки, уникальность ключей;
  3. лёгкая нормализация: `raw_value` / `raw_unit` как есть, `normalized_value` только при однозначном числе и известной шкале,
     доли и проценты без шкалы не пересчитывать;
  4. проверка цитат results/issues: та же нормализация, что в `check_submission.py`;
     у сканов цитаты, не найденные в тексте, получают `quote_found=None`;
  5. сохранение пачками.

  Числовых правил, как у solar, у RAG нет. `issues` — от модели, `check_name` — свободный текст.
- Статус файла `ocr`: если у PDF нет текстового слоя (pypdf и PyMuPDF дают пустоту). Задаётся при приёме.
- **Мок RAG** — `MockGoldExtractor` по `rag_mock_gold_dir` (`data/caseA_drive/dev_gold`), чтобы бэкенд проверялся без модели.
- В модель область передаётся полем `domain` формы `/extract` (протокол `pdf_dataset` уже его шлёт).

## 4. API

- `POST /runs`: `domain` ∈ `solar` / `rag`.
- `GET /papers/{domain}/{pid}?run=`:
  - solar — как сейчас;
  - rag — по умолчанию сводка (`publication` + число строк по таблицам + метрики + `issues`);
  - `?tables=configurations,results&limit=&cursor=` — строки по таблицам с пагинацией.
- `GET /results?run=`: фильтры из `DomainSpec.result_filters`, `limit` до 1000, сортировка в БД.
- `GET /runs/{id}/summary`:
  - `coverage` по `scored_metrics` (solar);
  - для rag — топ метрик по числу статей.
- `submission.zip`: колонки и таблицы из `DomainSpec`.
  - `?check=true` запускает скрипт области;
  - для rag код выхода 2 → `X-Check-Status: warnings`, а не 422;
  - паспорт для rag — поля, словари, версии.
- `GET /papers/{domain}/{pid}/pdf` — без изменений.

## 5. Порядок работ

- **R1** — реестр, `sub_rows`, `SubRowsRepo`, переезд solar, views и выгрузка через новый репозиторий.
  Поведение solar-API не меняется, тесты `api_core` зелёные.
- **R2** — RAG на бэке: `GenericProcessor`, мок RAG, API (`domain=rag`, сводка, фильтры), выгрузка и проверка, статус `ocr`.
  Приёмка: 21 dev-PDF RAG через мок → `check_submission.py` RAG без ошибок.
- **R3** — извлечение RAG в `ml/pdf-dataset`, параллельно с R1:
  - схемы и промпты по `rag_schema.json`;
  - results по таблицам и разделам, кусками;
  - метки страниц, проверка значений, дозаполнение — как у solar;
  - графики — отдельным этапом.

  Оценка — `ml/eval/score_rag.py` против `data/caseA_drive/dev_gold`.
