# Волна 2. Контракт API, CLI, ML-1 и OCR (уточнения к спеке), ревизия 2

Источник истины для интерфейса — `docs/spec_src/spec/15-runs.md` (§15). Этот документ фиксирует то, что спека оставляет открытым, и решения волны 2 (К2-x, В2-x из `docs/wave2/01_spec_analysis.md`). Контракт волны 1 (`docs/wave1/api_contract.md`) действует только для входа (§2) и общих соглашений. Эндпоинты корпусов, `/facts*`, `/papers/{id}`, `resolve/reopen` для фронта **не публикуются**. План и владельцы модулей — `docs/wave2/02_plan.md`.

Изменения ревизии 2: пересчёт после правки и замечания разбора (§5.1), имя статьи в выгрузке (§2.2, §6), стадия «Проверка формата» и единый набор страниц для OCR (§2.2, §10, §11), `check.ocr` и предупреждение в CLI (§8), оценка rag (§8.1), коды `zip_bomb` (422) и `too_many_requests` (429).

---

## 0. Общие соглашения (BACKEND §0, §8.3; К2-10)

- Префикс `/api/v1`. JSON UTF-8. Даты — ISO 8601 UTC (`2026-11-20T09:00:00Z`).
- Авторизация: `Authorization: Bearer <access>` (§2, код волны 1). Без входа — `401 unauthorized`. Все разборы личные: чужой или удалённый разбор → `404 not_found` (не 403).
- Ошибка: `{"error": {"code", "message", "field"?, "details"?}}`. `message` — по-русски. Заголовок `X-Request-Id` есть всегда.
- Пагинация §15: `limit` по умолчанию 100, максимум 1000 (больше — урезается до 1000, меньше 1 — `400 validation_error`); `cursor` непрозрачный; ответ `{data, next_cursor, total?|total_estimate?}`. Прочие эндпоинты — 50/500.
- Мутации отдают `action_id` в теле и в `X-Action-Id`.
- `Access-Control-Expose-Headers`: `X-Request-Id, X-Action-Id, X-Export-Tables, X-Export-Results, X-Export-Rejected, X-Export-Papers, X-Export-Filename, X-Check-Status, Content-Disposition`.
- Значения строк 7 таблиц — **строки, как в CSV**: `""` = нет сведений, `true`/`false`, списки через `;`. Числа — исходной десятичной строкой (`"0.330"`).
- Тексты для людей: исполнители — «сервис разбора» и «модель» (§15.15). `computed_by` в API: `backend` → «сервис разбора», `ml` и `both` → «модель».
- Область: `domain ∈ {solar, rag}`. Иначе → `400 validation_error` (`field: "domain"`).
- Коды лимитов: `429 too_many_requests` (600 запросов/мин, 60 загрузок/час), `422 zip_bomb` (§15.3), `413 file_too_large`, `415 unsupported_file`.

## 1. Вход (§2) — без изменений по сравнению с волной 1

`POST /auth/login` → `200 {access_token, token_type: "bearer", expires_in, user}`; `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me`. Учётки заводит сервис (`faktura users add`, `FAKTURA_ADMIN_*`). CLI и тесты работают от системного пользователя без входа.

## 2. Сущности

### 2.1. Run (§15.2)
```json
{ "id": "run_7k2…", "title": "21 статья · 20 ноя", "domain": "solar", "pinned": false, "demo": false,
  "status": "running", "created_at": "…", "updated_at": "…", "owner_id": "usr_…",
  "counts": { "files": 21, "queued": 9, "running": 4, "done": 7, "out": 1, "ocr": 0, "error": 0, "nodata": 0 },
  "results": 312, "issues": 17, "model_version": "mock-dev_gold", "profile_version": "tz-solar-v1",
  "files": [ RunFile ] }
```
- `status`: `queued | running | done | stopped | error`. `stopped` — остановлен пользователем, есть неначатые файлы. `error` — все файлы в `error`. `done` — нет файлов `queued`/`running`.
- `issues` — только открытые (`state = open` с учётом пересчёта разбора, §15.12); `results` — строки results без отклонённых.
- `title` по умолчанию: имя ZIP или папки без расширения, если загружен один архив или одна папка; иначе «N статей · D мес» (`1 статья`, `2 статьи`, `5 статей`; месяц коротко: янв, фев, мар, апр, мая, июн, июл, авг, сен, окт, ноя, дек).
- Внутри сервиса Run — это корпус `kind=ui` с одним прогоном; `Run.id` = id корпуса.

### 2.2. RunFile (§15.2, К2-1)
```json
{ "id": "D003", "name": "D003.pdf", "status": "done", "progress": 100, "has_data": true, "scan": false,
  "ocr_confidence": null, "status_label": "готово", "open_issue_count": 2, "reason": null,
  "stage": "Проверка", "results": 31, "issues": 2, "size": 1203344, "sha256": "9f2c…" }
```
| Состояние конвейера | `status` | `status_label` | `reason` |
|---|---|---|---|
| в очереди / остановлено до начала | `queued` | «в очереди» (при остановке: «остановлено») | — |
| обрабатывается | `running` | «разбор» | — |
| готово, есть results | `done` | «готово» | — |
| готово, `in_scope=false` | `out` | текст `exclusion_reason` | `exclusion_reason` |
| готово, 0 строк results | `nodata` | «нет результатов» | — |
| OCR недоступен (`ocr_unavailable`) | `ocr` | «скан без текста» | «Распознавание скана недоступно — перезапустите файл позже» |
| ошибка файла, `ocr_failed`, ошибка ML, ответ не по формату | `error` | «ошибка» | текст ошибки по-русски |

- Успешно распознанный скан: `status=done`, `scan=true`, `ocr_confidence` = средняя уверенность OCR (§11.2, К2-1). При `ocr_partial` — `done` и предупреждение в `reason`.
- `stage` ∈ «Разбор PDF», «Распознавание скана», «Извлечение», «Проверка формата», «Нормализация», «Проверка».
- `id` = `publication_id` = имя файла без расширения. Если в разборе уже есть файл с таким именем и другим содержимым, добавляется суффикс `-2`, `-3` (К2-14). Тот же SHA-256 в разборе → `rejected: duplicate`. `name` — исходное имя файла.
- **Имя статьи в выгрузке (К2-14):** основа исходного имени (без каталогов и `.pdf`), если она встречается ровно один раз среди выгружаемых файлов разбора и не совпадает с `id` другого файла; иначе `id` с суффиксом. Правило одно для всех: `domain.rows.export_publication_ids`.
- Короткая форма (в списке `GET /runs`): `{id, name, status, has_data, results, issues, reason?}`.

### 2.3. Строка с подсказками (§15.4)
```json
{ "row": { "…": "колонки шаблона своей области" },
  "review": { "status": "accepted", "corrected_value": "18.7", "by": "Исследователь", "at": "…" },
  "meta": { "confidence": 0.93, "bbox": { "page": 5, "rects": [ { "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.02 } ] },
            "quote_found": true, "norm_status": "converted", "issue_ids": ["I1"], "duplicates": ["R002-1"], "duplicate_page": 7,
            "source_table_row_label": "pristine", "source_table_row_text": "…", "ocr_used": false, "ocr_confidence": null } }
```
- `review.status` ∈ `new | accepted | rejected | corrected`. `corrected` — это `accepted` или `new` с заполненным `corrected_value`. `corrected_value` — десятичная строка в `normalized_unit` или `null`. Без `run` — всегда `{"status": "new"}`.
- `meta.issue_ids` — только открытые замечания (в контексте `run`, с учётом пересчёта разбора).

### 2.4. Состояние замечания (§15.12)
`state ∈ open | fixed | ok`; `state_auto: true`, если все `record_ids` исправлены или отклонены. Автозакрытие сильнее ручной отметки. Отмена правки значения снова открывает замечание. Ключ отметки — стабильный ключ замечания (К-6), поэтому отметка переживает restart.

---

## 3. Разборы (§15.3) — модуль `api_runs`

| Метод и путь | Тело / параметры | Ответ | Ошибки |
|---|---|---|---|
| `POST /runs` | multipart: `file` (повторяемое; имя может содержать относительный путь папки) **или** `archive` (ZIP); `domain` (`solar`\|`rag`, по умолчанию `solar`); `title` (опц.) | `202 {id, title, domain, status: "queued", created_at, demo: false, files: [RunFile], rejected: [{name, code, message}]}`; прогон стартует сам | `400 validation_error` (нет файлов / нет ни одного PDF), `413 file_too_large`, `415 unsupported_file`, `422 zip_bomb`, `429 too_many_requests` |
| `POST /runs/{id}/files` | то же тело (без `domain`, `title`) | `202 {added: [RunFile], rejected}` | 404, 413, 415, 422, 429 |
| `GET /runs` | `limit`, `cursor`, `q` (подстрока названия, без учёта регистра), `domain`, `files` (`true` по умолчанию) | `{data: [Run, files короткие], next_cursor}`; порядок `pinned desc, created_at desc, id desc` | — |
| `GET /runs/{id}` | — | Run с полными `files` | 404 |
| `PATCH /runs/{id}` | `{title?: string(1..200), pinned?: bool}` | `{run, action_id}` | 404, 400 |
| `DELETE /runs/{id}` | — | `{action_id}`; мягко; статьи удаляются, только если не входят в другие разборы | 404 |
| `POST /runs/{id}/restore` | — | `{run}` | 404 |
| `POST /runs/{id}/restart` | `{files: ["D003"] \| null, keep_review: true}` | `202 {run}` | 404, `400 validation_error` (неизвестный файл) |
| `POST /runs/{id}/stop` | `{cancel_running: false}` | `202 {run}` | 404 |
| `GET /runs/{id}/events` | SSE; `Last-Event-ID` (заголовок) или `last_event_id`; авторизация — Bearer или `?token=` (одноразовый, `POST /events/token` → `{token, expires_in: 60}`) | поток, см. ниже | 401, 404 |
| `GET /runs/{id}/summary` | — | форма §15.3 (см. ниже) | 404 |
| `GET /runs/{id}/submission.zip` | `results=D001:R002,…`, `check=true`, `partial=true` | файл ZIP, см. раздел 6 | 404, `409 run_in_progress`, `422 submission_invalid` |
| `POST /runs/{id}/submission.zip` | `{results: ["D001:R002", …], check?, partial?}` | то же | то же |
| `GET /runs/{id}/submission/stats` | `results=…`, `partial` | `{tables: 7, results, rejected, papers, filename}` | 404, 409 |
| `GET /runs/{id}/dataset.json` (should) | — | поток `{run, <7 таблиц области>, reviews: {"D001:R002": review}}` | 404, 409 |
| `GET /passport?domain=` (should) | — | паспорт области: поля и правила с `executor: "Сервис разбора" \| "Модель"` | 400 |

Отклонённые при загрузке (`rejected[].code`): `unsupported_file` («Можно загрузить только PDF»), `duplicate` («Этот файл уже есть в разборе»), `unsafe_path` (обход пути / zip-slip), `pdf_encrypted`, `pdf_broken`, `file_too_large`, `too_many_pages`.

**Демо (should, W2-078, К2-13).** При первом `GET /runs` нового пользователя создаётся демо-разбор solar dev (21 статья, `demo: true`, title «Демо — dev, 21 статья с эталоном»). Он переиспользует общие версии статей и ML заново не вызывает. Правки, отметки и пересчитанные замечания других разборов на те же версии статей в демо не попадают: у нового демо `edit_count = 0`. Удалённый демо-разбор не пересоздаётся. Демо rag не создаётся.

### 3.1. SSE `GET /runs/{id}/events`
```
id: 41
event: file
data: {"id":"D003","status":"running","progress":40,"stage":"Извлечение"}

id: 42
event: file
data: {"id":"D003","status":"done","progress":100,"results":31,"issues":2,"scan":false}

id: 43
event: run
data: {"status":"running","counts":{"files":21,"done":7,"out":1,"running":4,"queued":9,"ocr":0,"error":0,"nodata":0}}

id: 60
event: done
data: {"status":"done","counts":{…},"results":659,"issues":53}
```
- Пинг — комментарий `: ping` каждые 25 с. После `done` поток закрывается. Если разбор уже завершён, сразу отдаются `run` и `done`.
- `Last-Event-ID` продолжает поток с буфера (1000 событий); при переполнении приходит полный снимок `run`.
- Токен в query одноразовый, живёт 60 с и в логи не пишется (маскируется).

### 3.2. Сводка `GET /runs/{id}/summary` (§15.3, К2-4)
```json
{ "files": 21, "papers_in_scope": 19, "papers_out": 2, "files_without_results": 0, "files_ocr": 0, "files_error": 0,
  "results": 659, "scored_results": 627, "issues_total": 53, "issues_open": 41,
  "reviewed": 18, "accepted": 12, "corrected": 4, "rejected": 2, "edit_count": 21,
  "coverage": [ { "metric": "PCE", "n": 19, "total": 19, "results": 205 } ],
  "issues_by_check": [ { "check_name": "contradiction", "total": 8, "open": 6 } ] }
```
- solar: `coverage` — 11 оцениваемых показателей; `scored_results` — строки с этими `metric_name`.
- rag (В2-8): `coverage` — каждый `metric_group`, затем 11 самых частых `metric_name` разбора (`metric` — имя группы или метрики, `n` — статьи по теме, где есть строка); `scored_results` = `results`.
- `reviewed` — значения с любой правкой; `corrected` — с `corrected_value`; `edit_count` — правки значений плюс отметки замечаний.
- Отклонённые значения не входят в `results`. `issues_*` учитывают пересчёт разбора (§5.1).

### 3.3. Заглушки §15.9 (could; заголовок `X-Faktura-Stub: 1`)
- `GET /runs/{id}/messages` → `{data: [{id: "m1", role: "user", kind: "files", files: [pid…], at}, {id: "m2", role: "system", kind: "run", at}]}`.
- `POST /runs/{id}/messages {text}` → SSE: `step {"text": "Ищу в N статьях разбора"}`, `text {"text": "Вопросы по данным разбора появятся позже."}`, `done {}`; пустой `text` → `400`.
- `GET /runs/{id}/suggestions` → `{data: [3–5 строк]}` по данным разбора, без повторов.
- `POST /runs/{id}/messages/{mid}/stop` → 202; `PUT /runs/{id}/messages/{mid}/rating` → 204.

---

## 4. Данные (§15.4, §15.5) — модуль `api_data`

### 4.1. `GET /papers/{domain}/{pid}`
Параметры: `run` (опц.), `results=summary|page|all` (по умолчанию `summary` для rag и `all` для solar), `limit` (для `page`, по умолчанию 500, максимум 1000), `cursor`.

Ответ (solar — `materials, devices, layers, measurements`; rag — `configurations, datasets, tasks, experiments`):
```json
{ "domain": "rag", "publication": { "publication_id": "D014", "…": "…" },
  "configurations": [ … ], "datasets": [ … ], "tasks": [ … ], "experiments": [ … ],
  "results": [ … ] , "results_summary": [ { "metric_name": "Answer F1", "metric_group": "…", "config_id": "C01", "dataset_id": "DS1", "experiment_id": "E1", "count": 120, "min": "0.21", "max": "0.62", "pages": [7, 8] } ],
  "next_cursor": null, "issues": [ … ], "reviews": { "R002": { "status": "accepted", "corrected_value": null, "by": "…", "at": "…" } },
  "meta": { "results": { "R002": { "…": "как в 2.3" } },
            "issues": { "I1": { "state": "open", "state_auto": false, "computed_by": "модель", "confidence": 0.8,
                                "records": [ { "record_id": "R261", "metric_name": "EM", "value": "0.204", "unit": "fraction", "rag_variant": "RA-CoT", "dataset_name": "HotpotQA", "source_page": 7 } ] } },
            "pdf_pages": 18, "run_ids": ["run_7"], "file_status": "done", "scan": true, "ocr_confidence": 0.91 },
  "paper_status": "has_data", "open_issue_count": 3 }
```
- `results` присутствует при `page` и `all`, `results_summary` — при `summary`. `results=all` у rag-статьи с числом строк ≥ 2000 (`results_all_max`) → `400 validation_error` «Слишком много строк: используйте results=page или GET /results».
- Без `run` отдаётся последняя готовая версия статьи из разборов пользователя, `reviews` пустой, состояния замечаний — `open`, пересчёт разбора не применяется (В2-6).
- С `run` в `issues` входят базовые замечания без подавленных пересчётом и добавленные пересчётом (§5.1).
- `paper_status`: `has_data` | `nodata` | `out`. Для `out` — только `publication` (`in_scope: "false"`, `exclusion_reason`), остальные массивы пустые.
- `records` у замечания: solar — `device_label`; rag — `rag_variant` и `dataset_name`; `value` — действующее значение (с правкой разбора).
- 404 — нет статьи в разборах пользователя.

### 4.2. `GET /papers/{domain}/{pid}/pdf` (§15.5)
`200 application/pdf` | `206` с `Content-Range` (один диапазон `bytes=a-b`, `a-`, `-n`) | `416 range_not_satisfiable` для неверного диапазона; `Accept-Ranges: bytes`, `ETag: "<sha256>"` (`If-None-Match` → 304), `Cache-Control: private, max-age=86400`. Для хранилища S3 допустим `302` на подписанную ссылку со сроком 300 с. 404 — нет файла или доступа.

### 4.3. `GET /papers/{domain}/{pid}/ocr` (should)
`{engine, engine_version, mean_confidence, pages: [{n, ocr_confidence, words: [{text, bbox: {x, y, w, h}, confidence}]}]}`; bbox — в долях страницы. 404, если статья не скан.

### 4.4. `GET /results`
Параметры — таблица §15.4. Уточнения:
- `domain` обязателен без `run`; с `run` берётся из разбора.
- `review=new|accepted|rejected|corrected`, `min`/`max` по действующему значению требуют `run`, иначе `400 validation_error`.
- `pid`, `metric`, `config_id`, `dataset_id`, `task_id`, `experiment_id`, `aggregation`, `result_origin`, `extraction_status` повторяемы.
- `has_issues` — есть открытые замечания (в контексте `run`, с учётом пересчёта). `duplicates=exclude` — только строки без `duplicate_of`.
- Большие статьи rag: если выборка охватывает rag-статью с числом строк > 20 000 (`results_large_rows`) и не задан ни один из фильтров `metric`, `config_id`, `dataset_id`, `task_id`, `experiment_id` или `group_by`, ответ — `400 validation_error` с подсказкой.
- `sort` — через запятую из `publication_id|device|metric|value|page|issues`; `dir=asc|desc`. Ключ курсора — поля сортировки плюс (`domain`, `pid`, порядковый номер строки).
- Ответ: `{data: [{row, device_label?, paper_title, review, meta}], total | total_estimate, next_cursor, filters: [{id, label, match}]}`. `total` — точный до 100 000, дальше вместо него `total_estimate`.
- `group_by=metric_name|config_id|dataset_id|experiment_id|publication_id` → `{data: [{key, count, min, max, unit?, pages}], total}`.
- `q`: подстрока (название статьи, `publication_id`, `device_label`, `raw_value`, `source_fragment`) плюс язык запросов spec/04 §4.11 (В2-13):
  - число: `<параметр> <оператор> <число>[ %]`, между параметром и оператором до 24 символов без цифр; «выше, больше, более, свыше, >» — строго больше; «от, не ниже, >=» — не меньше; «ниже, меньше, менее, <» — строго меньше; «до, не выше, <=» — не больше; для долей число > 1 делится на 100;
  - год: «после 2022» (строго позже), «с/от/не раньше 2022» (включительно), «до/раньше 2023» (включительно);
  - тема (solar: перовскит, MAPbI, CIGS, CdTe, тандем, кремний, олово, бессвинцовые, Sn; rag: гибрид, hybrid, BM25, rerank, переранж, кросс, чанк, chunk) — темы через «или», остальное через «и»;
  - замечания: «противоречия», «не сходится», «расхождения», «сомнения», «ошибки» → `has_issues`;
  - rag: `config_id:…`, `dataset_id:…`, `experiment_id:…`.
  Распознанные условия возвращаются в `filters` (`{id, label, match, kind}`; например `{"id": "num:pce:min", "label": "КПД > 18 %", "match": "КПД выше 18"}`).

### 4.5. `GET /results/summary?domain&run&pid`
`{by_metric: [{metric_name, metric_group, count, configs, datasets, min, max, unit}], by_config: [{config_id, rag_variant, generator_model, count}], by_dataset: [{dataset_id, dataset_name, count}], by_experiment: [{experiment_id, count}], by_page: [{page, count}], total}`. Для solar блоки rag пустые. Агрегаты считаются в БД.

### 4.6. `GET /issues`, `GET /issues/stats`
- `/issues`: `domain`, `run`, `pid[]`, `check_name[]`, `record_id`, `state=open|all` (по умолчанию `all`), `limit`, `cursor`, `sort=publication_id|check_name|page` → `{data: [{row, paper_title, meta: {computed_by, confidence, state, state_auto, resolved_at, resolved_by, records}}], total, next_cursor}`. Замечания с `exported=false` (`ocr_low_confidence`) показываются, в выгрузку не идут.
- `/issues/stats?run&domain` → `{data: [{check_name, count, papers}]}`: `count` — открытые замечания, `papers` — число статей с ними.

### 4.7. `GET /compare?domain&pids=D001,D003&metric=PCE&run=` (should)
`{metric, unit, data: [{pid, paper_title, result_id, value, unit, aggregation, label, values: {"Voc": {result_id, value, unit}, …}}], comparable: {ok, reasons: ["…"]}}`. Выбор — правило «лучшее значение» (§15.13, раздел 7).

### 4.8. Заглушки §15.10 и поиск (could, `X-Faktura-Stub: 1`)
`GET /graph?run&domain&kinds` → `{nodes: [], edges: []}` (допускается простая реализация по таблицам); `GET /papers/{domain}/{pid}/related?run` → `{data: [], cited: {count: 0, refs: []}}`; `GET /search?q&run&domain` → `{data: []}`. Заглушки волны 1 с теми же путями в `api/base` удалены (шаг 0).

---

## 5. Правки, замечания, журнал (§15.6, §15.12, §15.17) — модуль `api_data`

| Метод и путь | Тело | Ответ | Ошибки |
|---|---|---|---|
| `PATCH /runs/{id}/papers/{domain}/{pid}/results/{rid}/review` | `{status: "accepted"\|"rejected"\|"new", corrected_value?: number\|string\|null}` | `{review, action_id, issues_changed: {opened: [iid], closed: [iid]}, applied_to: rid}` | 404; `422 fact_value_unrecognized` (`corrected_value` не число, `field: "corrected_value"`) |
| `POST /runs/{id}/reviews/batch` | `{items: [{domain, pid, rid, status}]}` или `{filter: {…параметры GET /results…}, status}` | `{count, action_id}` | 404, 400 |
| `PATCH /runs/{id}/papers/{domain}/{pid}/issues/{iid}` | `{state: "open"\|"fixed"\|"ok"}` | `{issue: {…row, state, state_auto, resolved_at, resolved_by}, action_id, open_issue_count}` | 404; `409 issue_auto_fixed` |
| `DELETE /runs/{id}/reviews` | — | `{removed_reviews, removed_issue_marks, action_id}` | 404 |
| `GET /history?run=&limit&cursor` (should) | — | `{data: [{action_id, at, who, label, kind, run_id, count, undone}], next_cursor}` | — |
| `POST /history/undo` | `{run?}` | `{undone: {action_id, label}, changed: [...]}` | `404` (нечего отменять) |
| `POST /actions/{action_id}/undo` | — | то же | 404, `409 already_undone` |

Правила:
- `corrected_value` задаётся в `normalized_unit` (для rag единица может быть пустой). Принимаются число и строка с «.» или «,». Хранится десятичной строкой без экспоненты, цифры не теряются (`"0,330"` → `"0.330"`). Статус `new` без `corrected_value` удаляет правку.
- Правка на повтор (`duplicate_of`) применяется к действующей основной записи (`applied_to`). Если основная отклонена, основной становится первый неотклонённый повтор.
- Журнал: у каждой записи есть `at`, `who`, `prev` (`{status, corrected_value}` или `prev_state`). Пакетная правка и сброс — одна запись с компактным `prev`; отмена атомарна. «Отменить последнее» — своё последнее действие за 24 ч, глубина 50.
- Сброс `DELETE /runs/{id}/reviews` удаляет правки, отметки и пересчитанные замечания разбора.
- Подписи журнала по-русски: «Принято значение R002», «Исправлено значение R002: 16,8 → 18,7», «Отклонено значение R002», «Замечание I3: исправлено», «Массовая правка: 120 значений», «Сброс правок разбора».

### 5.1. Пересчёт после правки (§5.4, §15.6)
- Пересчёт идёт сразу и укладывается в 500 мс даже на D014.
- **Область пересчёта.** rag — (`experiment_id`, `metric_name`) строки, к которой применена правка. В неё входят строки baseline других конфигураций, поэтому `protocol_mismatch` пересчитывается верно. Пересчитываются `metric_scale`, `metric_k`, `contradiction`, `protocol_mismatch`. solar — вся статья, все включённые правила сервиса (`builtin_solar_profile().rules`).
- Подставляются действующие значения разбора. Отклонённые записи исключаются из проверок.
- Результат сравнивается с сохранёнными замечаниями сервиса этой области по стабильному ключу (К-6). Новое замечание добавляется в разбор: его `issue_id` = ключ вида `I-1a2b3c4d`. Исчезнувшее подавляется. Замечания модели (`computed_by = ml | both`) не подавляются: они закрываются только автозакрытием или отметкой.
- Это состояние разбора: в других разборах и без `run` его не видно. Отмена правки и сброс возвращают исходный набор.
- `issues_changed` перечисляет замечания, у которых изменилось состояние (с учётом автозакрытия, добавления и подавления).

---

## 6. Набор для сдачи (§15.7) — модули `export` и `api_runs`

- Ответ: `200 application/zip`, `Content-Disposition: attachment; filename="submission.zip"; filename*=UTF-8''<percent-encoded «Набор для сдачи — {title}.zip»>`.
- Заголовки: `X-Export-Tables: 7`, `X-Export-Results`, `X-Export-Rejected`, `X-Export-Papers`, `X-Export-Filename` (percent-encoded), `X-Check-Status: ok|warnings|failed` (при `check=true`).
- Внутри ZIP ровно 7 CSV области разбора, плюс `check_report.txt` при `check=true`. solar — UTF-8 с BOM, rag — без BOM, `,`, `QUOTE_MINIMAL`; многострочные ячейки экранируются по правилам CSV.
- Правки (§15.6): `accepted` и `new` выгружаются как есть. `rejected` не выгружается: id вычищается из `issues.record_ids`, ссылки `duplicate_of` переводятся на первый неотклонённый повтор. При исправлении `normalized_value` и `metric_value` получают новое значение, `raw_*` не меняются, к `note` через «; » дописывается «Исправлено вручную: было X». Повторы получают значение и пометку основной записи.
- Замечания: выгружаются `open` и `fixed`, `ok` — нет; `exported=false` — нет. Учитывается пересчёт разбора (§5.1): подавленные не выгружаются, добавленные выгружаются. Замечание без записей остаётся, только если у него есть `source_fragment` и `source_page` (К2-22). Одинаковые замечания (`check_name` + множество `record_ids`) не дублируются.
- `results=D001:R002,…` (ключи API): в `results` и `record_ids` попадают только выбранные и неотклонённые записи; остальные таблицы статьи выгружаются целиком.
- Статья вне области — только строка `publications`. Файл `error`/`ocr`/`nodata` — строка `publications` (rag: `in_scope=true`; solar: пусто).
- **`publication_id` в выгрузке** — имя по правилу §2.2 (`export_publication_ids`). Ключ API `P042-2` выгружается как `P042`, если исходное имя `P042.pdf` единственное в разборе. Переименование затрагивает все 7 таблиц статьи. Набор PDF для скрипта собирается с теми же именами.
- Пока разбор идёт — `409 run_in_progress`; с `partial=true` выгружаются готовые статьи (`done`, `out`, `nodata`).
- `check=true`: solar — `check_submission.py` области 1; rag — скрипт **без `--ocr`**, синхронно, либо свежий отчёт `--ocr` из кэша (ключ — хэш CSV и набора PDF) (К2-15). Первая строка `check_report.txt` — режим («проверка без OCR» / «проверка с OCR»). Код скрипта 0 → `ok`, 2 → `warnings`, 1 → `422 submission_invalid` (`details.report` — текст отчёта). Enum `X-Check-Status` не расширяется.

---

## 7. Правило «лучшее значение» (§15.13; одна функция `query.best`)

1. Кандидаты: `result_origin=primary`, `extraction_status=extracted`, пустой `duplicate_of`, не отклонённые.
2. Значение: `corrected_value`, иначе `normalized_value`.
3. `aggregation=champion` важнее остальных (среднее, медиана, пусто).
4. Направление: solar — `Rs`, `HI` меньше лучше, остальные больше лучше. rag — по `metric_direction` (`higher_is_better`/`lower_is_better`; `target` и пусто не ранжируются) и только внутри одной `metric_unit` (самой частой у кандидатов).
5. Связка: solar — все показатели с прибора лучшего `PCE`, с тем же `measurement_id` и `aggregation`. rag — с той же конфигурации, набора и эксперимента, что лучший результат выбранной метрики.
6. Ничья — меньший `source_page`, затем порядок строки. Наружу отдаётся (`domain`, `pid`, `result_id`).

---

## 8. CLI (spec/13 §13.6) — модуль `pipeline`

```
faktura extract --domain solar|rag|auto --in <папка|ZIP|.txt> --out <папка>
  [--check] [--check-solar PATH] [--check-rag PATH] [--tesseract PATH] [--ocr-lang eng] [--check-no-ocr]
  [--ocr auto|off|force] [--no-dedupe] [--fail-fast] [--concurrency 4] [--extractor mock|http|raw] [--extract-url URL]
  [--ocr-url URL] [--gold-dir PATH] [--gold-dir-rag PATH] [--db PATH] [--cache-dir PATH] [--no-cache] [--model latest]
faktura eval --domain solar|rag --pred <out/domain> [--gold PATH]
```
- `--domain` по умолчанию `solar`. `solar` или `rag` пишет только свою папку, `auto` — обе (`out/solar`, `out/rag`).
- `auto` (В2-4): область задают подпапки `solar/`, `rag/` или корни кейсов `caseB*` (solar), `caseA*` (rag) в относительном пути (без учёта регистра). Иначе — тема из ответа ML-2, если есть. Иначе ошибка «укажите --domain» (код 1). Каждый файл попадает ровно в одну область.
- `--ocr`: `auto` — распознаются страницы `needs_ocr` (§10); `force` — все страницы; `off` — OCR не вызывается.
- Дубль имени внутри области → `rejected duplicate_name` в `files.csv`, в pdfdir для скрипта не попадает.
- Каждый принятый PDF области получает строку `publications`.
- `_meta/`: `manifest.json` (`{tool, version, started_at, finished_at, domains: {solar: {…}, rag: {…}}, input, …}`; у области — `result {publications, in_scope, results, issues, files_failed, files_ocr}` и `check {status, exit_code, errors, warnings, quotes_requested, quotes_matched, quotes_unresolved, ocr: bool}`), `files.csv` (`domain, name, sha256, status, scan, ocr_confidence, publication_id, results, issues, error`), `passport_solar.json/.md`, `passport_rag.json/.md`, `check_report_solar.txt`, `check_report_rag.json`, `facts_ui.json`, `errors.log`.
- `--check`: скрипт области запускается как есть: `sys.executable -I <script>`, `PYTHONUTF8=1`. pdfdir — временный каталог ровно с PDF этой области, имена = `publication_id.pdf`. rag: `<out/rag> <pdfdir> --ocr --tesseract <абсолютный путь> --ocr-language eng --json-report out/_meta/check_report_rag.json`, таймаут 3600 с; допускается разбиение по публикациям на `check_ocr_shards` процессов со слиянием отчётов.
- **Без Tesseract или с `--check-no-ocr`** скрипт запускается без `--ocr`. В manifest `domains.rag.check.ocr=false`, в stderr — «Проверка rag выполнена без OCR: <причина>». Цитаты сканов тогда не подтверждаются, и скрипт даёт код 2 по другой причине, чем при `--ocr`. Поэтому это видно в manifest и в строке причины кода 2.
- Коды выхода: `0` — успех; `2` — есть файлы `error`/`ocr` или скрипт rag вернул 2; `3` — скрипт любой области вернул 1, упал или превысил таймаут; `1` — ошибка запуска. Приоритет: 1 > 3 > 2 > 0. Строка причины кода 2 в stderr: «Код 2: файлов с ошибкой N, без OCR M; цитат требует проверки K[; проверка без OCR]».
- Прогресс в stderr: `[ 61/148] done  name.pdf  results=16 issues=1`.
- **Приёмка (W2-014):** solar — 0 ошибок скрипта; rag — 0 ошибок, код скрипта 0 или 2, проверка с `--ocr` (`check.ocr=true`).

### 8.1. Оценка (W2-017)
`faktura eval --domain solar` — локальная оценка волны 1. `faktura eval --domain rag` вызывает инструмент ML-2 (предварительный контракт): `faktura-eval --domain rag --gold <dir> --pred <dir> --json-report <file>` → JSON `{metrics: {name: value}, per_publication: {…}}`. Команда — `FAKTURA_EVAL_RAG_CMD`, иначе `faktura-eval` из `PATH`. Нет инструмента → код 1 и сообщение «Оценка области rag недоступна: не найден инструмент faktura-eval (задайте FAKTURA_EVAL_RAG_CMD)».

---

## 9. ML-1: ответ `/ml/extract` для `domain=rag` (spec/07 §7.0.1; Б2-1)

Запрос — как в волне 1: multipart `file`, `publication_id`, `domain=rag`, `request_id`, а также опционально `document` (JSON с текстом страниц и OCR) и `model`. Ответ в одном из форматов (`FAKTURA_EXTRACT_RAG_FORMAT`, `auto` — по `Content-Type`):

1. `application/json` — 7 таблиц целиком:
```json
{ "publication_id": "D001", "model_version": "extract-0.3",
  "publication": { "title": "…", "year": 2024, "DOI": "…", "authors": "…", "in_scope": true, "exclusion_reason": null },
  "configurations": [ { "config_id": "C01", "rag_variant": "RA-CoT", "generator_model": "LLaMA3-8B-Instruct", "top_k": null, "_confidence": 0.9 } ],
  "datasets": [ … ], "tasks": [ … ], "experiments": [ … ],
  "results": [ { "result_id": "R001", "metric_name": "EM", "raw_value": ".204", "metric_value": 0.204, "source_page": 7,
                 "source_fragment": "RA-CoT\n.204\n.330", "_confidence": 0.9, "_bbox": [x0, y0, x1, y1], "_row_label": "RA-CoT", "_row_text": "…", "_ocr_confidence": 0.95 } ],
  "issues": [ { "issue_id": "I01", "record_ids": ["R261", "R263"], "check_name": "percentage_vs_percentage_point", "description": "…", "source_page": 7, "source_fragment": "…", "_confidence": 0.8 } ] }
```
2. `application/x-ndjson` — строка на запись: `{"table": "results", …колонки…}`; первая строка (опц.) — `{"table": "_meta", "model_version": "…", "publication_id": "…"}`.
3. Страницы: `POST /ml/extract?part=N` → тот же JSON плюс `next_part: N+1 | null`.

Разбор: числа JSON сохраняются исходным текстом (`parse_float=str`, `parse_int=str`) → `"0.330"`; `null` → `""`; `true/false` → `"true"/"false"`; массив `record_ids` → `"R261;R263"`. Поля `_*` уходят в метаданные строки, неизвестные — в `meta.extra`, недостающие колонки — `""`. Ошибки — как в §7.0.1: 400, 413, 422 (`pdf_no_text|pdf_encrypted|pdf_broken`), 429 + `Retry-After`, 502, 504. Кэш ответов — по (`domain`, `sha256`, `model_version`).

После разбора ответ проходит стадию «Проверка формата» (§11): словари, числа, id, ссылки, повторы. В БД и API попадают только проверенные строки.

**Мок ML-1:** по (`domain`, `publication_id`) отдаёт строки `dev_gold` своей области (solar — `data/caseB_kejs5/dev_gold`, rag — `data/caseA_drive/dev_gold`); для остальных статей — пустой ответ (только `publications` с пустым `in_scope`). Режим `mock_issues=all|ml_only` действует для обеих областей.

---

## 10. OCR: предварительный контракт с ML (Б2-2)

Бэкенд OCR не выполняет. Он определяет страницы `needs_ocr` по §11.2:
1. текст pypdf **и** текст PyMuPDF после `rag_quote_norm` пусты на обоих движках; **или**
2. меньше 20 символов при изображении ≥ 70 % площади; **или**
3. больше 30 % символов U+FFFD/PUA.

Этот один набор страниц и передаётся OCR-сервису ML в режиме `auto`, и определяет признак скана: статья — скан, если набор не пуст (W2-050). В режиме `force` распознаются все страницы, признак скана считается по тому же набору. Схема ниже предварительная: она живёт в `faktura/domain/ocr.py` и `faktura/ocr/contract.py` и при получении контракта ML меняется только там.

Запрос: `POST {FAKTURA_OCR_URL}/ml/ocr`, multipart `file` (PDF), `pages` (JSON-массив номеров с 1), `language` (`eng`), `dpi` (300), `sha256`. Таймаут 600 с.

Ответ `200`:
```json
{ "engine": "tesseract", "engine_version": "5.5.3", "language": "eng", "dpi": 300, "mean_confidence": 0.91,
  "pages": [ { "n": 2, "width_pt": 612, "height_pt": 792, "deskew_deg": 0.4, "ocr_confidence": 0.93,
               "text": "Table 2: Results on HotpotQA …",
               "words": [ { "text": "Table", "bbox": [72.0, 90.1, 101.5, 101.0], "confidence": 0.97 } ] } ],
  "partial_pages": [] }
```
- `bbox` слова — `[x0, y0, x1, y1]` в пунктах PDF от левого верхнего угла; `confidence` — 0…1 (если ML пришлёт 0…100, адаптер делит на 100).
- Ошибки: соединение не установлено, `503` или таймаут → `ocr_unavailable` (файл `ocr`, повтор через restart); `422`/`500` или ответ не по схеме → `ocr_failed` (файл `error`); непустой `partial_pages` → `ocr_partial` (файл `done` с предупреждением).
- Кэш — по (`sha256`, `engine`, `engine_version`, `pages`).
- `ocr_confidence` значения (в `meta`) берётся из `_ocr_confidence` ML-1, иначе — минимум уверенности слов цитаты, иначе — уверенность страницы. Правило `ocr_low_confidence`: < 0,8 → замечание только для интерфейса.
- `quote_found` для страниц, распознанных OCR, проверяется по тексту OCR из ответа ML; для остальных — по тексту pypdf и PyMuPDF.

**Мок OCR** (`FAKTURA_OCR_IMPL=mock`): детерминированный. Сначала ищется фикстура `ocr_mock_dir/<sha256>.json` или `<pid>.json` в формате ответа. Иначе текст страницы собирается из `source_fragment` строк `results` и `issues` эталона rag на этой странице (слова с условными bbox, уверенность 0,9). Иначе текст пустой, уверенность 0.

`check_submission.py --ocr` распознаёт сканы сам, локальным Tesseract (`FAKTURA_TESSERACT`, `FAKTURA_OCR_LANG=eng`); наш кэш OCR он не использует.

---

## 11. Стадии конвейера (W2-060, §11.1)

| Стадия (`stage`) | Что делает | Ошибка → статус файла |
|---|---|---|
| Разбор PDF | текст pypdf/PyMuPDF, набор `needs_ocr` | `pdf_encrypted`/`pdf_broken` → `error` |
| Распознавание скана | OCR ML для `needs_ocr` (`auto`) или всех страниц (`force`); пропускается при `off` или пустом наборе в `auto` | `ocr_unavailable` → `ocr`; `ocr_failed` → `error` |
| Извлечение | `ExtractAdapter` (ML-1 или мок) | ошибка ML → `error` |
| Проверка формата | валидация и санитайзер по шаблону области: словари, числа, id, ссылки, разошедшиеся повторы (→ `contradiction`) | ответ не по формату → `error` |
| Нормализация | solar — словарь и единицы волны 1; rag — §6.6 | — |
| Проверка | правила сервиса и находки модели, проверка цитат | — |

После последней стадии строки сохраняются вместе с `model_version`. Сбой одного файла не останавливает прогон.
