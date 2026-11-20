# 13. Корпус и пакетная обработка — основной поток

[← К оглавлению](../BACKEND_SPEC.md)

> **Это главный сценарий продукта.** По ТЗ (стр. 11–12) организаторы дают корпус PDF по каждой области и оценивают результат по эталонной разметке и **независимым проверочным примерам** — то есть систему прогонят на новых PDF. Программа берёт все PDF, находит значения по заданному списку полей (PCE, Voc, …), пишет их в связанные таблицы с указанием страницы и места, проверяет и отдаёт набор данных; интерфейс даёт искать и фильтровать по этой таблице.
>
> Чат и загрузка одной статьи (§4.5, §4.6) — дополнительные сценарии поверх того же конвейера.
>
> Экраны «Корпус» (импорт, статусы файлов, «Прогнать всё») и «Таблица фактов» (вся таблица корпуса) — **в интерфейсе появится**. В прототипе их нет: ближайшие аналоги — очередь загрузки (`UploadQueue.vue`) и Обзор данных (`ExploreView.vue`).

## 13.1. Поток

```
Корпус создан [USER] → файлы: папка / ZIP / список URL [USER] → дедупликация [BE]
  → Прогон (BatchJob) по профилю извлечения [BE] → для каждого файла: MlJob [ML] → нормализация [BE] → проверки [BE]
  → Сводка корпуса [BE] → Таблица фактов: поиск, фильтры [BE→FE] → источник на странице [FE] → замечания → разбор [USER]
  → Экспорт набора данных + паспорт + файл замечаний [BE]
```
Консольный запуск (§13.6) проходит тот же путь без интерфейса.

## 13.2. Сущности

### Corpus — корпус
| Поле | Тип | Обяз. | Ист. | Описание |
|---|---|---|---|---|
| `corpus_id` | id | да | [BE] | |
| `workspace_id`, `owner_id` | id | да | [BE] | |
| `name` | string | да | [USER] | «Солнечные — датасет организаторов» |
| `collection` | enum(`solar`,`rag`,`auto`) | да | [USER] | `auto` — тема каждой статьи определяется классификатором (§7.5) |
| `profile_id` | id | да | [USER]\|[BE] | профиль извлечения (§13.3); по умолчанию — стандартный профиль коллекции |
| `project_id` | id | нет | [USER] | связать с проектом: статьи корпуса видны в проекте и в его чатах |
| `files_total`, `papers_done`, `papers_failed`, `facts`, `open_issues` | int | да | [BE] | |
| `last_run_id` | id | нет | [BE] | |
| `created_at`, `updated_at`, `deleted_at` | datetime | | [BE] | |

Статья принадлежит ровно одному корпусу (`Paper.corpus_id`, [BE]); статьи, загруженные поштучно, попадают в служебный корпус «Загруженные вручную» команды.

### CorpusFile — файл корпуса
| Поле | Тип | Обяз. | Ист. | Описание |
|---|---|---|---|---|
| `file_id` | id | да | [BE] | |
| `corpus_id` | id | да | [BE] | |
| `name` | string | да | [USER] | имя файла или путь внутри ZIP/папки (`pdfs/2023/cigs_flex.pdf`) |
| `source` | enum(`upload`,`zip`,`url`,`server_path`) | да | [BE] | |
| `url` | string | нет | [USER] | для списка URL |
| `size`, `sha256`, `page_count` | | нет | [BE] | |
| `status` | enum(`pending`,`downloading`,`queued`,`processing`,`done`,`failed`,`skipped_duplicate`,`needs_ocr`,`cancelled`) | да | [BE] | `needs_ocr` — нет текстового слоя (признак от ML-2), ждёт OCR (§11) |
| `stage`, `progress` | | нет | [BE] | этап и процент (как `Paper.processing`) |
| `paper_id` | id | нет | [BE] | статья после приёма |
| `duplicate_of` | id | нет | [BE] | `file_id`/`paper_id`, если дубль |
| `facts`, `issues` | int | да | [BE] | найдено фактов, открытых замечаний |
| `error` | `{code, message}` | нет | [BE] | |
| `attempts` | int | да | [BE] | |
| `model_version` | string | нет | [ML] | |

### BatchJob — прогон
| Поле | Тип | Обяз. | Ист. | Описание |
|---|---|---|---|---|
| `run_id` | id | да | [BE] | |
| `corpus_id` | id | да | [BE] | |
| `kind` | enum(`full`,`new_only`,`selected`,`failed_only`,`renormalize`,`recheck`) | да | [USER] | что прогнать (§13.5) |
| `file_ids` | id[] | нет | [USER] | для `selected` |
| `profile_id`, `profile_version` | | да | [BE] | по какому списку полей |
| `model_version` | string | да | [BE] | какую модель ML звать |
| `rules_version`, `dictionary_version` | string | да | [BE] | |
| `status` | enum(`queued`,`running`,`done`,`done_with_errors`,`cancelling`,`cancelled`,`failed`) | да | [BE] | |
| `counts` | `{total, queued, processing, done, failed, skipped, cancelled}` | да | [BE] | |
| `progress` | int 0..100 | да | [BE] | доля завершённых файлов (с учётом процента текущих) |
| `facts_found`, `issues_found` | int | да | [BE] | |
| `concurrency` | int | да | [USER]\|[BE] | параллельных задач ML (по умолчанию 4) |
| `keep_review` | bool | да | [USER] | переносить статусы проверки и правки человека на совпавшие факты |
| `started_at`, `finished_at`, `eta_s` | | | [BE] | |
| `created_by` | id | да | [BE] | |
| `output` | `{export_id, url}` | нет | [BE] | если прогон запущен с автоэкспортом (CLI) |

## 13.3. Профиль извлечения — список полей как конфигурация

Прогон идёт **по списку полей**, а не по зашитой схеме. Профиль — это «паспорт» в рабочей форме: какие параметры искать в каждой области, с какими единицами и правилами. Стандартные профили совпадают с ТЗ (§3.6, §3.7) и с `GET /passport`.

### ExtractionProfile
| Поле | Тип | Ист. | Описание |
|---|---|---|---|
| `profile_id`, `version` | id, int | [BE] | версия растёт при каждом изменении |
| `name` | string | [USER] | «ТЗ ВИНИТИ — солнечные, v1» |
| `collection` | enum | [USER] | |
| `fields` | ProfileField[] | [USER]\|[BE] | |
| `rules` | `{check_name, enabled, params}`[] | [USER]\|[BE] | включённые проверки и их пороги (`jv_consistency.threshold_pct = 3`, `eqe_consistency.threshold_pct = 5`) |
| `builtin` | bool | [BE] | стандартный профиль (редактировать — только копию) |

### ProfileField
| Поле | Тип | Описание |
|---|---|---|
| `key` | string | ключ параметра коллекции (`pce`) или поле ТЗ (`active_area`, `scan_direction`, `metric_k`) |
| `entity` | string | таблица ТЗ: `result`, `material`, `measurement`, `device`, `layer`, `dataset_task`, `experiment`, `rag_config`, `metric_result`, `publication` |
| `tz_name` | string | имя поля ТЗ (`metric_value` с `metric_name = PCE`, `bandgap_value`, `layer_thickness`) |
| `label` | string | «КПД» |
| `type` | enum(`number`,`text`,`enum`,`bool`) | |
| `unit` | string | каноническая единица (§6.1) |
| `units` | `Record<string, number>` | |
| `enum_values` | string[] | |
| `required` | bool | ожидается в каждой статье (для покрытия) |
| `metric_k` | int | для @k |
| `synonyms` | string[] | подсказки ML и словарю |
| `enabled` | bool | искать ли |

Эндпоинты:
| Метод и путь | Описание | Ист. |
|---|---|---|
| `GET /profiles?collection=` | список профилей | BE/USER |
| `GET /profiles/{id}` | профиль с полями и правилами | BE/USER |
| `POST /profiles` | `{name, collection, base_profile_id, fields?, rules?}` — копия стандартного с изменениями | USER |
| `PATCH /profiles/{id}` | изменить поля/правила; `version + 1`; ответ содержит `rerun_hint`: какие прогоны нужны (новые поля → `full` извлечение; только пороги правил → `recheck`; только единицы → `renormalize`) | USER |
| `GET /passport?profile_id=` | паспорт данных по профилю (§4.17) | BE |

ML получает список полей в задаче (`fields` вместо `collections.params` в §7.3) и ищет только их; значения вне списка возвращать может — бэк сохранит их в `unmapped_values`.

## 13.4. Импорт корпуса

### `POST /corpora` — создать корпус
Тело [USER]: `{ "name": "Солнечные — датасет организаторов", "collection": "solar", "profile_id": "prf_solar_tz_v1", "project_id": null }`. 201 — корпус.

### `GET /corpora`, `GET /corpora/{id}`, `PATCH /corpora/{id}` (`name`, `profile_id`, `project_id`), `DELETE /corpora/{id}` (мягко, со статьями; «Вернуть» → `POST /corpora/{id}/restore`).

### `POST /corpora/{id}/files` — загрузить файлы
Три способа (один на запрос):
1. **multipart**, много полей `file` (папка — браузер отдаёт файлы с относительными путями в `filename`): до 200 файлов и 1 ГБ за запрос; для больших папок фронт шлёт пачками.
2. **ZIP**: одно поле `archive` (`application/zip`, до 2 ГБ, resumable через tus — `POST /uploads` → `PATCH /uploads/{id}` → `{upload_id}`, затем `POST /corpora/{id}/files {"upload_id": "…"}`). Бэк распаковывает в потоке, берёт только `*.pdf` (по сигнатуре), игнорирует `__MACOSX`, скрытые файлы; защита от zip-бомбы (суммарный распакованный размер ≤ 10 ГБ, ≤ 20 000 файлов, глубина путей ≤ 10).
3. **Список URL**: JSON `{ "urls": ["https://…/paper1.pdf", "…"] }` (до 5000). Бэк скачивает сам (таймаут 60 с, до 50 МБ, только `http(s)`, без частных адресов сети), статус файла `downloading`.
4. (CLI/админ) **Папка на сервере**: `{ "server_path": "/data/vinity/solar" }` — только для `is_admin`, путь из белого списка.

Ответ **202**:
```json
{ "accepted": 148, "duplicates": 3, "rejected": [ { "name": "readme.txt", "code": "unsupported_file", "message": "Можно загрузить только PDF" } ],
  "files": [ { "file_id": "cf_001", "name": "pdfs/2023/cigs_flex.pdf", "status": "pending", "sha256": "…" } ] }
```
Ошибки: 413, 415 (архив не ZIP), 422 `zip_too_large` / `zip_bomb`.

### Дедупликация [BE]
| Уровень | Когда | Что делает |
|---|---|---|
| Точный — SHA-256 файла | при приёме | внутри корпуса — `skipped_duplicate`, `duplicate_of`; с другим корпусом команды — по настройке `reuse_existing` (по умолчанию `true`): статья не разбирается заново, а **связывается** с корпусом (факты общие) |
| Смысловой — DOI | после артефакта A3 | помечается `dup_of` у статьи, файл `done`, в сводке — «возможные дубли: N»; данные не сливаются автоматически |
| Смысловой — название + год + первый автор | после A3 | то же |

### `GET /corpora/{id}/files`
Таблица файлов экрана «Корпус». Параметры: `status` (повторяемый), `q` (имя), `has_errors`, `sort` (`name|status|facts|issues|updated_at`), `dir`, `limit` (до 500), `cursor`. Источник: [BE].
```json
{ "data": [ { "file_id": "cf_001", "name": "pdfs/2023/cigs_flex.pdf", "status": "done", "paper_id": "pap_cigs", "title": "CIGS на гибкой подложке…", "facts": 16, "issues": 1, "page_count": 9, "model_version": "extractor-2026.11", "error": null },
  { "file_id": "cf_017", "name": "pdfs/scan_1998.pdf", "status": "failed", "error": { "code": "pdf_no_text", "message": "В PDF нет текста — распознавание сканов пока не поддерживается" }, "attempts": 1 } ],
  "total": 151, "next_cursor": "…" }
```

## 13.5. Прогон

### `POST /corpora/{id}/runs` — запустить
Тело [USER]:
```json
{ "kind": "full", "file_ids": null, "profile_id": "prf_solar_tz_v1", "model_version": "latest", "concurrency": 4, "keep_review": true }
```
| `kind` | Что делает | Когда нужен |
|---|---|---|
| `new_only` | только файлы, которые ещё не разбирались (`pending`) | после догрузки файлов («Прогнать всё» по умолчанию) |
| `full` | все файлы заново: ML + нормализация + проверки | новая версия модели; в профиль добавлены поля |
| `selected` | только `file_ids` | точечно |
| `failed_only` | только `failed` | после сбоя |
| `renormalize` | без ML: заново нормализация + проверки по сохранённым результатам ML | изменились словарь или единицы |
| `recheck` | без ML и нормализации: только проверки | изменились правила или пороги |

202 `{ "run_id": "run_12", "status": "queued", "counts": { "total": 148, … } }`. Ошибки: `409 conflict` «Прогон уже идёт» (один активный прогон на корпус), 400.

Выполнение [BE]: очередь файлов → не больше `concurrency` задач ML одновременно → по каждому файлу §7.4 → счётчики → по окончании — сводка корпуса (статистика, без межстатейных проверок: статьи не сравниваются попарно). Сбой одного файла не останавливает прогон: файл `failed`, прогон `done_with_errors`. При `keep_review` правки человека переносятся (§7.3, «Версия модели»).

### `GET /corpora/{id}/runs`, `GET /runs/{run_id}`
```json
{ "run_id": "run_12", "corpus_id": "crp_solar", "kind": "full", "status": "running", "progress": 41,
  "counts": { "total": 148, "queued": 80, "processing": 4, "done": 60, "failed": 2, "skipped": 2, "cancelled": 0 },
  "facts_found": 931, "issues_found": 57, "model_version": "extractor-2026.11", "profile_version": 3, "eta_s": 610, "started_at": "…" }
```

### Прогресс
- SSE: поток `GET /events` (§4.20) + события `run.progress` `{run_id, progress, counts, facts_found, issues_found}` (не чаще раза в секунду), `run.file` `{run_id, file_id, status, facts, issues, error}`, `run.done` `{run_id, status, counts}`.
- Опрос: `GET /runs/{run_id}` раз в 2–5 с.

### `POST /runs/{run_id}/cancel`
Незапущенные файлы — `cancelled`; идущие задачи ML отменяются (`POST {ML}/v1/jobs/{id}/cancel`); уже разобранное сохраняется. 202 → `status: cancelling` → `cancelled`.

### `POST /corpora/{id}/files/{file_id}/retry` — один файл заново (= `selected`).

## 13.6. Консольный запуск

Для проверки на скрытых примерах — без интерфейса, без входа (локально) или через API с ключом.

### Команда
```
faktura extract --domain solar --in ./pdfs --out ./dataset \
  [--profile tz-solar-v1 | --profile ./profile.json] \
  [--format csv,json,xlsx] [--concurrency 4] [--model latest] \
  [--extract-url http://faktura-extract:8000] [--parse-url http://faktura-parse:8000] \
  [--no-dedupe] [--ocr auto|off|force] [--fail-fast] [--check tools/check_submission.py]
```
- `--domain solar|rag|auto`; `--in` — папка (рекурсивно) или ZIP или файл со списком URL (`.txt`).
- Работает тем же кодом бэка (импорт → прогон → экспорт) с локальной БД SQLite/PostgreSQL во временной папке или против сервера: `--server https://api.faktura.ai --api-key $GEODE_KEY` (тогда это вызовы `POST /corpora`, `POST /corpora/{id}/files`, `POST /corpora/{id}/runs`, опрос, `POST /exports`).
- Код выхода: `0` — всё разобрано; `2` — есть файлы `failed` (набор всё равно записан); `3` — выход не прошёл `check_submission.py` (§14); `1` — ошибка запуска.
- Формат выходной папки — **предварительный**, сверяется с `tools/check_submission.py` (§14).
- Прогресс — в stderr построчно: `[ 61/148] done  cigs_flex.pdf  facts=16 issues=1`.

Аналог через API одним вызовом: `POST /extract` (multipart `archive` + `domain` + `profile_id`) → 202 `{run_id}`; по готовности `GET /runs/{id}/output` → ZIP выходной папки.

### Формат выходной папки (область 1 — по шаблону сдачи, §14)
```
out/                        — это и есть папка сдачи: ровно 7 CSV шаблона
  publications.csv          — строка на КАЖДЫЙ PDF, в т. ч. in_scope=false
  materials.csv
  devices.csv
  layers.csv
  measurements.csv
  results.csv
  issues.csv
  _meta/                    — служебное, в сдачу не входит (скрипт проверки его не читает)
    manifest.json           — что, чем и когда прогнали
    passport.json, passport.md
    files.csv               — по каждому входному файлу: имя, sha256, статус, publication_id, результатов, замечаний, ошибка
    check_report.txt        — вывод check_submission.py (при --check)
    facts_ui.json           — служебные поля для интерфейса: confidence, bbox, review_status (для импорта в корпус)
    errors.log
```
CSV — точно как шаблон: те же колонки, UTF-8 (с BOM, как файлы шаблона; скрипт читает `utf-8-sig`), разделитель `,`, точка, `true`/`false`, несколько значений через `;`, пустая ячейка = нет в статье. `publication_id` — имя PDF без расширения. Проверка: `python docs/case5/tools/check_submission.py out test`.

Область 2 (`--domain rag`): формат сдачи уточнить, когда будет архив по области 2; пока — CSV по полям ТЗ (`publications`, `datasets_tasks`, `experiments`, `rag_configs`, `metric_results`, `issues`) в `out/rag/`.

`_meta/manifest.json`:
```json
{ "tool": "faktura", "version": "1.4.0", "started_at": "…", "finished_at": "…", "domain": "solar", "split": "test",
  "profile": { "id": "tz-solar-v1", "version": 3 }, "extract_model": "extract-0.3", "parse_model": "parse-0.2", "norm_version": "2026-11-20", "rules_version": "1",
  "input": { "path": "./test", "files": 90, "accepted": 90, "duplicates": 0 },
  "result": { "publications": 90, "in_scope": 84, "results": 2810, "issues": 230, "failed": 0 },
  "check": { "errors": 0, "warnings": 12, "quotes_checked": 2410, "quotes_not_found": 9 } }
```

## 13.7. Главная таблица фактов корпуса

Основной экран данных (в интерфейсе появится; сейчас частично — Обзор). Серверные поиск, фильтры, сортировка и пагинация: корпус — сотни и тысячи статей, десятки тысяч фактов.

### `GET /facts/table`
| Параметр | Описание |
|---|---|
| `corpus_id` / `collection` / `project_id` | область (хотя бы одно) |
| `q` | язык запросов (§4.11): «КПД > 20 % перовскиты», «после 2022», «противоречия» — применяется к образцам, в таблице остаются факты подходящих образцов |
| `text` | полнотекстовый поиск по `raw_value`, `source_fragment`, названию статьи |
| `param` (повт.), `entity`, `metric_name`, `metric_k` | поле |
| `min`, `max` | по нормализованному значению (в канонической единице `param`) |
| `review_status`, `extraction_status`, `source_kind`, `result_origin` | (повт.) |
| `has_issues`, `check_name`, `severity` | замечания |
| `paper_id` (повт.), `year_from`, `year_to`, `topic` | статья |
| `min_confidence`, `max_confidence` | |
| `sort` | `paper|year|param|value|confidence|page|issues|updated_at`, несколько через запятую; `dir` |
| `limit` (по умолчанию 100, до 1000), `cursor` | keyset-пагинация, стабильная при изменениях |
| `columns` | какие колонки вернуть |
| `facets` | `true` — вернуть счётчики для фильтров |

Ответ (источник: значения — [ML]→[BE], статусы — [USER], замечания — [BE]):
```json
{ "data": [ { "fact_id": "fct_stab_c_h0_pce", "param": "pce", "label": "КПД", "raw_value": "21,3 %", "normalized_value": 21.3, "unit": "%", "display": "21,3 %",
  "paper_id": "pap_stab", "paper_title": "Стабильность перовскитных элементов при 85 °C", "year": 2023, "row_id": "h0", "row_label": "0 ч",
  "source_page": 7, "source_kind": "table", "source_ref": "Табл. 2", "source_where": "строка 0 ч", "anchor_id": "anc_…",
  "review_status": "new", "extraction_status": "extracted", "confidence": 0.95, "issues": [ { "issue_id": "…", "check_name": "jv_consistency", "severity": "warn" } ] } ],
  "total": 37, "next_cursor": "…",
  "filters": [ { "id": "num:pce:min", "label": "КПД > 20 %", "match": "КПД > 20 %", "kind": "num" }, { "id": "topic:перовскит", "label": "перовскит", "match": "перовскиты", "kind": "topic" } ],
  "facets": { "param": { "pce": 37 }, "review_status": { "new": 30, "accepted": 7 }, "severity": { "error": 2, "warn": 5 }, "year": { "2023": 12, "2024": 25 } } }
```
- Строка таблицы — факт; группировка по статье/образцу — [FE] (данные позволяют).
- Действия в строке — §4.8 (принять, отклонить, исправить), массовые — `POST /facts/bulk` с тем же фильтром, что у таблицы (`filter` принимает параметры `GET /facts/table`).
- Источник на странице: `anchor_id` → `GET /papers/{id}/anchors` + `GET /papers/{id}/pages/{n}` — фронт открывает PDF на странице с подсветкой.
- Экспорт выборки: `POST /exports` с `scope.table_filter` = параметры таблицы.
- Производительность: индексы по `(workspace, collection, param, value)`, `(paper_id)`, `(review_status)`, полнотекстовый по `raw_value/source_fragment`; ответ на 100 строк — до 300 мс при 100 тыс. фактов.

Широкий вид «образец × параметры» (как Обзор) — `GET /data/rows` (§4.11) с `corpus_id`.

## 13.8. Сводка по корпусу

### `GET /corpora/{id}/summary`
Источник: [BE].
```json
{ "corpus_id": "crp_solar", "collection": "solar", "profile": { "id": "prf_solar_tz_v1", "version": 3 }, "model_version": "extractor-2026.11", "last_run": { "run_id": "run_12", "status": "done_with_errors", "finished_at": "…" },
  "files": { "total": 151, "done": 146, "failed": 2, "duplicates": 3, "processing": 0 },
  "papers": { "total": 146, "in_scope": 139, "out_of_scope": 7, "possible_duplicates": 2, "with_issues": 58 },
  "facts": { "total": 2310, "accepted": 120, "rejected": 14, "new": 2176, "manual": 9, "norm_failed": 31, "from_table": 1840, "from_text": 352, "from_figure": 118, "mean_confidence": 0.91 },
  "issues": { "open": 211, "by_rule": [ { "check_name": "jv_consistency", "title": "КПД не сходится с Voc × Jsc × FF", "severity": "warn", "open": 23, "checked": 512 }, { "check_name": "contradiction", "title": "Текст противоречит таблице", "severity": "error", "open": 9, "checked": 140 } ] },
  "coverage": [ { "field": "pce", "label": "КПД", "papers_with": 141, "papers_total": 146, "pct": 96.6 }, { "field": "scan_direction", "label": "Направление развёртки", "papers_with": 38, "papers_total": 146, "pct": 26.0 } ],
  "not_found": [ { "field": "certified", "label": "Сертифицировано", "papers_with": 0 }, { "field": "stability_protocol", "label": "Протокол стабильности", "papers_with": 0 } ],
  "errors": [ { "code": "pdf_no_text", "count": 1 }, { "code": "pdf_encrypted", "count": 1 } ] }
```
`coverage` — по каждому полю профиля: в скольких статьях есть хотя бы одно значение; `not_found` — поля профиля, не найденные ни в одной статье (кандидаты на доработку ML или синонимы словаря).

### `GET /corpus/overview` — лёгкая сводка для первого экрана
Фронт запрашивает **сразу после входа**, чтобы отрисовать экран «Корпус» (`/corpus`) без тяжёлых запросов. Только счётчики из кешированных агрегатов (обновляются по событиям), без списков файлов и фактов; ответ — до 100 мс. Параметр `collection` (опц.). Источник: [BE].
```json
{ "corpora": [
    { "corpus_id": "crp_solar", "name": "Солнечные — датасет организаторов", "collection": "solar",
      "files": { "total": 151, "done": 146, "failed": 2, "processing": 0 }, "facts": 2310, "open_issues": 211,
      "active_run": null, "last_run": { "run_id": "run_12", "status": "done_with_errors", "finished_at": "2026-11-20T08:40:00Z" } },
    { "corpus_id": "crp_rag", "name": "RAG — датасет организаторов", "collection": "rag",
      "files": { "total": 64, "done": 20, "failed": 0, "processing": 4 }, "facts": 410, "open_issues": 18,
      "active_run": { "run_id": "run_13", "progress": 37, "eta_s": 300 }, "last_run": null } ],
  "totals": { "papers": 166, "facts": 2720, "open_issues": 229, "errors_by_rule_top": [ { "check_name": "contradiction", "open": 11 } ] },
  "empty": false }
```
`empty: true` (корпусов нет) — фронт показывает пустое состояние «Загрузите папку или ZIP с PDF». Подробности — по клику: `GET /corpora/{id}/summary`, `GET /corpora/{id}/files`, `GET /facts/table`.

## 13.9. Кто за что (корпус)
| Функция | Фронт | Бэк | ML |
|---|---|---|---|
| Импорт корпуса | экран «Корпус» (появится): выбор папки/ZIP/вставка списка URL, загрузка пачками, прогресс передачи | приём, распаковка ZIP с защитой, скачивание URL, проверка PDF, SHA-256, дедупликация, `CorpusFile` | — |
| Пакетный прогон | кнопка «Прогнать всё», выбор вида прогона, статусы файлов, отмена | очередь, параллелизм, задачи ML, нормализация и проверки по каждому файлу, счётчики, события, отмена | разбор каждого PDF (A1–A8) по списку полей профиля |
| Повторный прогон | выбор: всё / выбранные / упавшие / только нормализация / только проверки; подсказка после изменения профиля | определяет, что пересчитать; переносит правки человека; версии модели, правил, словаря | новый разбор при `full`/`selected` |
| Консольный запуск | — | CLI `faktura extract` и `POST /extract`, выходная папка, `manifest.json`, коды выхода | тот же разбор |
| Сводка по корпусу | экран сводки (появится) | файлы, статьи, факты, замечания по правилам, покрытие полей, ненайденные поля, ошибки | — |
| Профиль полей | редактор профиля (появится) | хранит, версионирует, отдаёт паспорт, передаёт список полей ML | ищет только поля профиля |
| Таблица фактов корпуса | таблица (появится), фильтры, переход к источнику | серверные поиск, фильтры, сортировка, пагинация, фасеты | — |
