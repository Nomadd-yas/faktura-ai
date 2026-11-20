# 15. Разбор (Run) и данные для интерфейса — то, что вызывает фронт

[← К оглавлению](../BACKEND_SPEC.md)

> Это **основной набор эндпоинтов для интерфейса** (новый фронт, `docs/FRONTEND_PLAN.md`). Слой данных фронта — `src/case/` (`types.ts`, `index.ts`): сейчас он читает эталон dev и localStorage, после подключения бэка будет читать эти эндпоинты **в тех же формах** — меняется только `src/case/index.ts`.
>
> Связь с §13: **корпус** (`Corpus`, `BatchJob`) остаётся для CLI, сдачи и больших прогонов; **разбор** (`Run`) — то, чем пользуется интерфейс: одна загрузка — N файлов, ветка «как в чате». Внутри бэка разбор — это корпус с одним прогоном (`Run.id` = `corpus_id`, `Run.kind = "ui"`), так что конвейер, нормализация, проверки и экспорт общие.

## 15.1. Экраны и что они вызывают

| Экран | Что показывает | Эндпоинты |
|---|---|---|
| `/app` — главная (композер) | зона «Перетащите PDF, папку или ZIP», «Разобрать N», область (`solar` / `rag`), «Открыть демо — dev», «Недавние разборы» | `GET /runs?limit=6`, `POST /runs` |
| `/run/:id` — разбор (ветка чата) | «сообщение» с файлами → «ответ»: прогресс по файлам → сводка → таблица результатов → замечания → «Скачать набор (7 CSV)»; дальше — вопросы по данным разбора; внизу поле — вопрос или догрузить PDF | `GET /runs/{id}`, `GET /runs/{id}/events` (SSE), `GET /runs/{id}/summary`, `GET /results?run=`, `GET /issues?run=`, `POST /runs/{id}/files`, `GET /runs/{id}/submission.zip`, `GET/POST /runs/{id}/messages`, `GET /runs/{id}/suggestions` |
| `/paper/:domain/:pid?run=&r=&i=` — статья | настоящий PDF; вкладки «Результаты», «Приборы и слои», «Условия», «Материал», «Замечания» | `GET /papers/{domain}/{pid}`, `GET /papers/{domain}/{pid}/pdf`, `PATCH /runs/{id}/papers/{domain}/{pid}/results/{rid}/review` |
| Данные: `/facts` | все результаты всех разборов, фильтры, выбор разбора | `GET /results` |
| `/checks` | замечания по всем разборам, счётчики по `check_name` | `GET /issues`, `GET /issues/stats` |
| `/compare` | результаты разных статей рядом, причины несопоставимости | `GET /results?pid=a&pid=b…` + `GET /compare?pids=` |
| `/export` | 7 CSV (главный), JSON со связями, Excel; выбор разбора | `GET /runs/{id}/submission.zip`, `GET /runs/{id}/dataset.json`, `POST /exports` |
| `/passport` | поля, типы, единицы, словари, правила | `GET /passport` |
| `/graph` (Данные → Связи), вкладка статьи «Похожие» | сеть связей по общим сущностям | `GET /graph`, `GET /papers/{domain}/{pid}/related` |
| Боковая панель | «Новый разбор» (→ `/app`), «Разборы» со статьями внутри (точка — есть замечания), «Данные» | `GET /runs` (+ `files[]` у каждого) |

После входа фронт ведёт на **`/app`** (не на `/corpus`).

### Идентичность статьи: пара (`domain`, `publication_id`)
Коды `D001`… и `P001`… **совпадают в двух областях** (D001 солнечных ≠ D001 RAG). Поэтому статья везде — пара: в API `/papers/{domain}/{pid}` (`/papers/solar/D001`, `/papers/rag/D001`), во фронте маршрут `/paper/:domain/:pid`, в `RunFile` — `domain` разбора, в ссылках ответов чата и графа — `{domain, pid}`. В выгрузке области разнесены по папкам `solar/` и `rag/`.

## 15.2. Сущность Run

| Поле | Тип | Обяз. | Ист. | Описание |
|---|---|---|---|---|
| `id` | id | да | [BE] | |
| `title` | string | да | [BE]\|[USER] | по умолчанию — «21 статья · 20 ноя» или имя ZIP/папки; переименование |
| `domain` | enum(`solar`,`rag`) | да | [USER] | один разбор — одна область; статьи разбора — пары (`domain`, `publication_id`) |
| `pinned` | bool | да | [USER] | закреплён — сверху списка (`sortedRuns()` во фронте: закреплённые, затем по дате) |
| `created_at`, `updated_at` | datetime | да | [BE] | |
| `owner_id` | id | да | [BE] | |
| `demo` | bool | да | [BE] | демо-разбор dev (21 статья с эталоном) — создаётся сам для нового пользователя |
| `status` | enum(`queued`,`running`,`done`,`stopped`,`error`) | да | [BE] | |
| `files` | RunFile[] | да | [BE] | |
| `counts` | `{files, queued, running, done, out, ocr, error, nodata}` | да | [BE] | |
| `model_version`, `profile_version` | string | нет | [BE]/[ML] | |

### RunFile — совпадает с `RunFile` в `src/case/types.ts`
| Поле | Тип | Ист. | Описание |
|---|---|---|---|
| `id` | string | [BE] | **`publication_id`** = имя файла без расширения (`D001`, `P042`); при совпадении имён внутри команды — суффикс `P042-2` |
| `name` | string | [USER] | имя файла или путь в ZIP/папке |
| `status` | enum(`queued`,`running`,`done`,`out`,`ocr`,`error`,`nodata`) | [BE] | `out` — вне области (`in_scope=false`); `ocr` — нет текстового слоя, нужен OCR (§11); `nodata` — разобрана, но ни одного значения не найдено |
| `progress` | int 0..100 | [BE] | |
| `has_data` | bool | [BE] | есть ли строки результатов (`hasData` во фронте) |
| `scan` | bool | [BE] | PDF без текстового слоя, распознан OCR (§11) — пометка «скан, распознано OCR» |
| `ocr_confidence` | number | [ML] | средняя уверенность OCR; низкая — отдельная пометка |
| `status_label` | string | [BE] | подпись для людей: `queued` — «в очереди», `running` — «разбор», `done` — «готово», `nodata` — «нет результатов», `ocr` — «скан без текста», `error` — «ошибка», `out` — текст причины из `exclusion_reason` |
| `open_issue_count` | int | [BE] | открытые замечания статьи — для точки в навигации и поиске |
| `reason` | string | [ML]\|[BE] | `exclusion_reason` для `out`, текст ошибки для `error`/`ocr` |
| `stage` | string | [BE] | «Разбор PDF», «Извлечение», «Нормализация», «Проверка» |
| `results`, `issues` | int | [BE] | |
| `size`, `sha256` | | [BE] | |

Соответствие статусам корпуса (§13.2): `queued`/`pending` → `queued`; `processing` → `running`; `done` + `in_scope=false` → `out`; `needs_ocr` → `ocr`; `failed` → `error`; `done` без результатов → `nodata`.

## 15.3. Эндпоинты разбора

### `POST /runs` — создать разбор с файлами
`multipart/form-data`: `file` (повторяемое; папка — файлы с относительными путями), или `archive` (ZIP), `domain` (`solar`), `title` (опц.). Для больших ZIP — сначала resumable `POST /uploads` (§13.4), затем `{upload_id}`.
→ `202`
```json
{ "id": "run_7", "title": "21 статья · 20 ноя", "domain": "solar", "status": "queued", "created_at": "2026-11-20T09:00:00Z", "demo": false,
  "files": [ { "id": "D001", "name": "D001.pdf", "status": "queued", "progress": 0, "has_data": false } ],
  "rejected": [ { "name": "readme.txt", "code": "unsupported_file", "message": "Можно загрузить только PDF" } ] }
```
Прогон стартует сам. Ошибки: 400 (нет файлов), 413, 415, 422 (`zip_bomb`), 429. Источник: [USER] файлы → [BE].

### `POST /runs/{id}/files` — догрузить в этот разбор
То же тело. Новые файлы встают в очередь этого разбора. `202` — `{added: [RunFile], rejected: [...]}`. Дубль по SHA-256 внутри разбора — `rejected` с `duplicate`.

### `GET /runs` — список разборов
Параметры: `limit`, `cursor`, `q`, `domain`. Сортировка — `pinned desc, created_at desc`. Источник: [BE].
```json
{ "data": [ { "id": "run_demo", "title": "Демо — dev, 21 статья с эталоном", "domain": "solar", "demo": true, "status": "done", "created_at": "…",
  "counts": { "files": 21, "done": 19, "out": 2, "queued": 0, "running": 0, "ocr": 0, "error": 0, "nodata": 0 },
  "results": 659, "issues": 53,
  "files": [ { "id": "D001", "name": "D001.pdf", "status": "done", "has_data": true, "results": 46, "issues": 1 }, { "id": "D006", "name": "D006.pdf", "status": "out", "reason": "Только расчёт…" } ] } ],
  "next_cursor": null }
```
`files` — короткие (для боковой панели, где статьи раскрываются внутри разбора); `?files=false` — без них.

### `GET /runs/{id}` → разбор целиком (как элемент списка, `files` полные). 404.

### `PATCH /runs/{id}` — переименовать, закрепить
`{ "title": "Кейс 5 — test" }` или `{ "pinned": true }` [USER] → разбор + `action_id`.

### `DELETE /runs/{id}` — удалить
Мягко, со статьями разбора (если они не входят в другие разборы). `{action_id}`; «Вернуть» → `POST /runs/{id}/restore`. Демо-разбор удалить можно; он не пересоздаётся, пока пользователь не нажмёт «Открыть демо».

### `POST /runs/{id}/restart` — перезапустить
`{ "files": ["D003", "D007"] | null, "keep_review": true }` — все файлы или выбранные; правки человека переносятся (§13.5). `202`.

### `POST /runs/{id}/stop` — остановить
Неначатые файлы → остаются `queued` с пометкой «остановлено», идущие доводятся или отменяются (`cancel_running: true`). `202`.

### `GET /runs/{id}/events` — поток прогресса (SSE)
```
event: file
data: {"id":"D003","status":"running","progress":40,"stage":"Извлечение"}

event: file
data: {"id":"D006","status":"out","progress":100,"reason":"Только расчёт: ab initio … По разделу 3.1 расчётные работы … не относятся."}

event: file
data: {"id":"D003","status":"done","progress":100,"results":31,"issues":2}

event: run
data: {"status":"running","counts":{"files":21,"done":7,"out":1,"running":4,"queued":9}}

event: done
data: {"status":"done","counts":{"files":21,"done":19,"out":2},"results":659,"issues":53}
```
`Last-Event-ID` поддерживается; пинг каждые 25 с. Опрос — `GET /runs/{id}` раз в 2 с.

### `GET /runs/{id}/summary` — сводка
Источник: [BE]. Все счётчики замечаний — с учётом состояния (§15.12).
```json
{ "files": 21, "papers_in_scope": 19, "papers_out": 2, "files_without_results": 0, "files_ocr": 0, "files_error": 0,
  "results": 659, "scored_results": 627,
  "issues_total": 53, "issues_open": 41,
  "reviewed": 18, "accepted": 12, "corrected": 4, "rejected": 2, "edit_count": 21,
  "coverage": [ { "metric": "PCE", "n": 19, "total": 19, "results": 205 }, { "metric": "Voc", "n": 18, "total": 19, "results": 109 }, { "metric": "Jsc", "n": 18, "total": 19, "results": 111 },
                { "metric": "FF", "n": 18, "total": 19, "results": 110 }, { "metric": "Rs", "n": 5, "total": 19, "results": 33 }, { "metric": "Rsh", "n": 5, "total": 19, "results": 32 },
                { "metric": "HI", "n": 2, "total": 19, "results": 3 }, { "metric": "Jsc_EQE", "n": 6, "total": 19, "results": 8 }, { "metric": "stabilized_PCE", "n": 4, "total": 19, "results": 7 },
                { "metric": "T80", "n": 0, "total": 19, "results": 0 }, { "metric": "retained_PCE", "n": 6, "total": 19, "results": 14 } ],
  "issues_by_check": [ { "check_name": "contradiction", "total": 8, "open": 6 }, { "check_name": "jv_consistency", "total": 7, "open": 5 } ] }
```
- `papers_in_scope` / `papers_out` — статьи по теме и не по теме; `files_without_results` — файлы `nodata`.
- `reviewed` — «проверено вами»: значения с **любой** правкой (принято, исправлено, отклонено).
- `issues_open` — только `state = open`; на экране «нужно проверить» — это число.
- `coverage` — по 11 оцениваемым показателям области 1 (`METRICS` в `src/case/types.ts`): `n` — в скольких статьях по теме есть хотя бы одно значение, `total` — статей по теме; на экране «N из M статей». Для области 2 — по `metric_group` и самым частым `metric_name`. Цифры в примере — иллюстрация.

## 15.4. Форма данных для фронта: 7 таблиц шаблона

**Фронт читает статьи как строки 7 таблиц шаблона своей области** — область 1: `publications`, `materials`, `devices`, `layers`, `measurements`, `results`, `issues`; область 2: `publications`, `configurations`, `datasets`, `tasks`, `experiments`, `results`, `issues` — **с именами колонок 1:1**, как в `src/case/types.ts` (`PublicationRow`, `MaterialRow`, `DeviceRow`, `LayerRow`, `MeasurementRow`, `ResultRow`, `IssueRow`). Значения — **строками, как в CSV**: пустая строка = «в статье нет», `true`/`false`, несколько значений через `;`. Так фронт показывает ровно то, что уйдёт в сдачу.

Всё, что сверх шаблона (правки человека, `confidence`, `bbox`, статусы), отдаётся **отдельными полями рядом**, а не внутри строки:

```json
{ "row": { "publication_id": "D001", "result_id": "R002", "…": "колонки шаблона" },
  "review": { "status": "accepted", "corrected_value": null, "by": "Исследователь", "at": "2026-11-20T09:10:00Z" },
  "meta": { "confidence": 0.93, "bbox": null, "quote_found": true, "norm_status": "converted", "issue_ids": ["I1"], "duplicates": ["R002-1"] } }
```
`review` — как `Review` в `src/case/types.ts` (`status`, `corrected`, `at`); `meta` — необязательные подсказки интерфейсу.

### `GET /papers/{domain}/{pid}` — все 7 таблиц одной статьи
`pid` = `publication_id`. Параметр `run` (если одна статья есть в нескольких разборах с разными версиями). Источник: строки — [ML]→[BE]; `review` — [USER]; `meta` — [ML]/[BE].
```json
{ "publication": { "publication_id": "D001", "title": "Improved Photovoltaic Performance of CsPbI2Br Perovskite Films via Bivalent Metal Chloride Doping", "year": "2021", "DOI": "10.3389/fenrg.2021.692059", "authors": "Qiao H.W.; Chen M.; Zhou Z.; Cheng Q.; Hou Y.; Yang H.G.", "in_scope": "true", "exclusion_reason": "" },
  "materials":    [ { "publication_id": "D001", "material_id": "M1", "material_name_raw": "CsPbI2Br (pristine)", "formula_normalized": "CsPbI2Br", "material_class": "perovskite", "dimensionality": "3D", "additives": "", "bandgap_value": "1.9", "bandgap_unit": "eV", "bandgap_method": "cited", "note": "…" } ],
  "devices":      [ { "publication_id": "D001", "device_id": "DV1", "absorber_material_id": "M1", "device_label": "pristine", "device_role": "control", "device_type": "cell", "architecture": "n-i-p", "substrate_type": "rigid", "active_area": "0.0625", "area_unit": "cm2", "area_definition": "aperture", "encapsulation": "", "note": "…" } ],
  "layers":       [ { "publication_id": "D001", "layer_id": "DV1-L1", "device_id": "DV1", "layer_order": "1", "layer_function": "front_contact", "layer_material": "FTO (glass, 8 Ω/sq)", "layer_thickness": "", "thickness_unit": "", "deposition_method": "", "annealing_temperature": "", "annealing_time": "", "atmosphere": "", "note": "" } ],
  "measurements": [ { "publication_id": "D001", "measurement_id": "MS1", "device_id": "DV1;DV2;DV3;DV4;DV5;DV6", "measurement_type": "J-V", "spectrum": "AM1.5G", "light_intensity": "100", "intensity_unit": "mW/cm2", "scan_direction": "reverse", "scan_rate": "0.15 V/s", "mask_used": "true", "certified": "false", "certification_lab": "", "temperature": "", "stability_protocol": "", "stress_conditions": "", "note": "…" } ],
  "results":      [ { "publication_id": "D001", "result_id": "R002", "device_id": "DV1", "measurement_id": "MS1", "material_id": "M1", "metric_name": "Voc", "metric_value": "1.197", "metric_unit": "V", "aggregation": "", "spread_value": "", "n_devices": "", "time_point": "", "time_unit": "", "raw_value": "1197", "raw_unit": "mV", "normalized_value": "1.197", "normalized_unit": "V", "result_origin": "primary", "cited_publication_id": "", "source_page": "5", "source_section": "Results and Discussion", "source_table": "TABLE 1", "source_figure": "FIGURE 5B", "source_fragment": "Table 1, строка pristine", "extraction_status": "extracted", "duplicate_of": "", "note": "…" } ],
  "issues":       [ { "publication_id": "D001", "issue_id": "I1", "record_ids": "R003", "check_name": "unit_inconsistency", "description": "В тексте FF контрольного прибора записан как «0.71%», в Table 1 — 0.71 (доля). Верная нормализация — 71 %.", "source_page": "5", "source_fragment": "…" } ],
  "reviews": { "R002": { "status": "accepted", "corrected_value": null, "at": "…" } },
  "meta": { "results": { "R002": { "confidence": 0.93, "bbox": null, "quote_found": true } }, "pdf_pages": 9, "run_ids": ["run_demo"], "file_status": "done" } }
```
Для `domain=rag` вместо `materials/devices/layers/measurements` — `configurations`, `datasets`, `tasks`, `experiments` (колонки §14.B), `results` — 30 колонок RAG.

**Объём.** В RAG одна статья даёт до десятков тысяч строк `results` (в dev D014 — 28 649, из них `Answer F1` — 12 074). Поэтому `GET /papers/{domain}/{pid}` отдаёт `results` **не целиком**: параметр `results=summary` (по умолчанию для `rag`) — вместо строк сводка `results_summary` (по `metric_name`, `metric_group`, `config_id`, `dataset_id`, `experiment_id`: число строк, диапазон значений, страницы); `results=page&limit=500` — первая страница + `next_cursor`; `results=all` — только для `solar` или статей < 2000 строк. Строки дальше — `GET /results?domain=&pid=` с фильтрами.

**Статус статьи** (поле `paper_status` рядом с таблицами): `has_data` — есть результаты; `nodata` — разобрана, результатов нет (фронт показывает свой текст `NODATA_TEXT`); `out` — не по теме, с `exclusion_reason`. Плюс `open_issue_count` — счётчик на вкладке «Замечания» и точки у значений считаются **только по открытым**.

**Исходное и исправленное.** Строка `results` всегда отдаётся **как извлечена** (исходное `normalized_value`), исправление — в `reviews[rid].corrected_value`; фронт показывает и подсвечивает текущее (исправленное, если есть) значение и пометки «принято / исправлено / отклонено вами».

**Значения замечаний для людей.** В `meta.issues[issue_id].records` — по каждому `record_id`: `metric_name`, значение (текущее), единица, `device_label` (область 1) или `rag_variant` + `dataset_name` (область 2), `source_page` — чтобы описать замечание без отдельных запросов.

**Место значения в таблице.** У табличных значений области 1 `source_fragment` часто — адрес «Table 1, строка X», а не цитата. Поэтому бэк отдаёт в `meta.results[rid]` поля `source_table_row_label` (метка строки, например `pristine`) и `source_table_row_text` (сырой текст строки таблицы из PDF), если ML их вернул (необязательные поля `_row_label`, `_row_text` в ответе `/ml/extract`, §7.0.1), и `bbox` — подсветка без поиска. Если фраза на странице не нашлась, фронт показывает саму фразу.

Также для экранов: `device_label` по `results.device_id`, «то же число на стр. N» — по `duplicate_of` (`meta.results[rid].duplicates` и `duplicate_page`), колонка «Источник» — из `source_page`, `source_table`, `source_figure`.

`reviews` — только для изменённых строк (у остальных `new`). Статья вне области: `publication` с `in_scope: "false"` и `exclusion_reason`, остальные массивы пустые. Ошибки: 404.

### `GET /results` — строки `results` с пагинацией
Для таблицы разбора и «Данных → Таблица».

| Параметр | Описание |
|---|---|
| `domain` | `solar` \| `rag` (обязателен без `run`) |
| `run` | разбор (без него — все разборы пользователя этой области) |
| `pid` (повт.) | статьи |
| `metric` (повт.) | `metric_name`; `scored=true` — только 11 оцениваемых (область 1) |
| `config_id`, `dataset_id`, `task_id`, `experiment_id` (повт.) | область 2 — **обязательные фильтры для больших статей** |
| `metric_group`, `metric_variant`, `metric_k`, `metric_unit` | область 2 |
| `device_id`, `measurement_id`, `material_id` | область 1 |
| `q` | поиск по статье (название, `publication_id`), `device_label`, `raw_value`, `source_fragment`; плюс язык запросов (§4.11): «PCE > 20», «перовскит», «после 2022» |
| `aggregation`, `result_origin`, `extraction_status` | (повт.) |
| `min`, `max` | по действующему значению (`corrected` или `normalized_value`) |
| `review` | `new|accepted|rejected|corrected` |
| `has_issues` | bool |
| `duplicates` | `include` (по умолчанию) `| exclude` (только основные записи) |
| `sort` | `publication_id|device|metric|value|page|issues` (через запятую), `dir` |
| `limit` (по умолчанию 100, до 1000), `cursor` | keyset-пагинация; `total` — точный до 100 000, дальше `total_estimate` |
| `group_by` | `metric_name|config_id|dataset_id|experiment_id|publication_id` — вместо строк вернуть сводку по группам (`count`, `min`, `max`, `pages`) |

Ответ:
```json
{ "data": [ { "row": { "publication_id": "D003", "result_id": "R010", "…": "все 27 колонок results" },
              "device_label": "Y-Sn3O4", "paper_title": "Yttrium-doped Sn3O4 …", "review": { "status": "new" },
              "meta": { "confidence": 0.95, "issue_ids": [] } } ],
  "total": 659, "next_cursor": "…",
  "filters": [ { "id": "num:PCE:min", "label": "КПД > 20 %", "match": "PCE > 20" } ] }
```
`device_label` и `paper_title` — для колонок таблицы, чтобы фронт не грузил `devices`/`publications` отдельно.

### `GET /results/summary?domain=&run=&pid=` — сводки по статье
Для экрана статьи и таблицы разбора, когда строк тысячи. Ответ: `{by_metric: [{metric_name, metric_group, count, configs, datasets, min, max, unit}], by_config: [{config_id, rag_variant, generator_model, count}], by_dataset: [...], by_experiment: [...], by_page: [{page, count}], total}`. Источник: [BE]. Обязателен для области 2.

### `GET /issues` — строки `issues`
Параметры: `domain`, `run`, `pid` (повт.), `check_name` (повт.), `record_id`, `limit`, `cursor`, `sort` (`publication_id|check_name|page`). Ответ — `{data: [{row: IssueRow, paper_title, meta: {computed_by, confidence, state: "open"|"fixed"|"ok", state_auto: bool, resolved_at, resolved_by, records: [...]}}], total, next_cursor}`. Параметр `state` (`open` по умолчанию для счётчиков, `all` для списка). Формулировки `computed_by` для людей — «сервис разбора» / «модель».

### `GET /issues/stats?run=` — счётчики по `check_name` (экран «Проверки»): `{data: [{check_name, count, papers}]}`.

### `GET /compare?domain=solar&pids=D001,D003&metric=PCE`
Значения статей рядом + `comparable: {ok, reasons[]}` — область 1: класс материала, тип прибора, условия, площадь, агрегация, развёртка; область 2: задача, датасет и разбиение, определение метрики и k, единица, протокол (§5.3). Значения статьи выбираются правилом «лучшее значение» (§15.13): все показатели — с одного прибора с лучшим КПД (тот же `measurement_id` и тип значения); в ответе — `result_id` каждой ячейки.

## 15.5. PDF для просмотра

### `GET /papers/{domain}/{pid}/pdf`
Исходный PDF: `Content-Type: application/pdf`, поддержка `Range` (pdf.js грузит частями), `ETag`, `Cache-Control: private, max-age=86400`. Можно `302` на подписанную ссылку на 5 мин. 404 — нет файла (например, статья из эталона без PDF у команды).

**Подсветку делает фронт** через pdf.js: открывает `source_page`, ищет `source_fragment` в текстовом слое страницы **той же нормализацией, что `check_submission.py` своей области**: область 1 — нижний регистр, лигатуры fi/fl/ff, «−»/«–» → «-», только `a-z0-9.%`; область 2 — NFKC, пробелы, перенос слова через дефис, знаки и цифры сохраняются (многострочные фрагменты — по строкам). Для сканов (§11) — по текстовому слою OCR (`GET /papers/{domain}/{pid}/ocr` → `words` с bbox). Если фрагмент вида «Table 1, строка …» — ищется подпись таблицы. Не найдено — плашка с текстом цитаты.

Поэтому **`bbox` от ML необязателен** — это «улучшение точности подсветки» (если есть, фронт подсвечивает прямоугольник, а не найденный текст). Бэк хранит `bbox`, если ML его прислал, и отдаёт в `meta.bbox` как `{page, rects: [{x, y, w, h}]}` в долях страницы.

## 15.6. Правки человека по результатам

### `PATCH /runs/{id}/papers/{domain}/{pid}/results/{rid}/review`
```json
{ "status": "accepted" }
{ "status": "accepted", "corrected_value": 18.7 }
{ "status": "rejected" }
{ "status": "new" }
```
`corrected_value` — в целевой единице (`normalized_unit`). Ответ: `{review, action_id, issues_changed: {opened: [], closed: []}}` (числовые проверки пересчитываются сразу). Ошибки: 404, 422 (`corrected_value` не число).

### `POST /runs/{id}/reviews/batch`
```json
{ "items": [ { "domain": "solar", "pid": "D001", "rid": "R002", "status": "accepted" }, { "domain": "solar", "pid": "D001", "rid": "R003", "status": "accepted" } ] }
```
или по фильтру `GET /results`: `{ "filter": { "run": "run_demo", "review": "new", "has_issues": false }, "status": "accepted" }`. Одна запись журнала → `{count, action_id}`.

### Отмена
Через журнал: `POST /actions/{action_id}/undo` (кнопка «Отменить» в уведомлении), `POST /history/undo` (Ctrl+Z), `GET /history` — §4.15.

### Влияние на выгрузку 7 CSV
| Правка | В `results.csv` | В других таблицах |
|---|---|---|
| `accepted` | как есть | — |
| `new` | как есть (не проверено человеком — всё равно выгружается) | — |
| `rejected` | **строка не выгружается** | `result_id` убирается из `issues.record_ids` (замечание без записей остаётся, если у него есть цитата, иначе тоже не выгружается); ссылки `duplicate_of` на отклонённую удаляются (первый повтор становится основной записью) |
| `corrected_value` | `normalized_value` и `metric_value` = исправленное; `raw_value`/`raw_unit` — из статьи без изменений; **прежнее значение уходит в `note`**: «Исправлено вручную: было 16.8» (дописывается к существующему `note` через «; ») | — |

## 15.7. Экспорт по разбору

### `GET /runs/{id}/submission.zip`
**Прямое скачивание**: ответ сразу файлом (`Content-Type: application/zip`, `Content-Disposition: attachment; filename*=UTF-8''Набор для сдачи — {название разбора}.zip`). ZIP ровно из 7 CSV шаблона (§14) по статьям разбора, с учётом правок (§15.6); статьи вне области — только строка в `publications`.

Параметр `results=D001:R002,D001:R003,…` — **только эти значения** (выбор «только выбранные» в таблице); статьи выгружаются целиком в остальных таблицах, а в `results` и `issues.record_ids` — только выбранные и не отклонённые. Для большого списка — `POST /runs/{id}/submission.zip {"results": ["D001:R002", …]}`.

Для тоста «Скачано: 7 таблиц, N значений · отклонённые (K) не вошли» — заголовки ответа `X-Export-Tables: 7`, `X-Export-Results: N`, `X-Export-Rejected: K`, `X-Export-Papers`, `X-Export-Filename` (для `fetch` их нужно открыть в `Access-Control-Expose-Headers`). То же можно получить заранее без файла: `GET /runs/{id}/submission/stats?results=…` → `{tables, results, rejected, papers, filename}`. Параметр `check=true` — бэк прогоняет `check_submission.py` и кладёт `check_report.txt` в ZIP, заголовок `X-Check-Status: ok|warnings|failed`. Пока разбор идёт — `409 run_in_progress` «Разбор ещё идёт — дождитесь окончания или скачайте готовые статьи (`?partial=true`)».

### `GET /runs/{id}/dataset.json`
JSON со связями: `{run: {...}, publications: [...], materials: [...], devices: [...], layers: [...], measurements: [...], results: [...], issues: [...], reviews: {...}}` — те же строки, что в CSV, плюс правки отдельным полем.

### `GET /runs/{id}/dataset.xlsx` — Excel: по листу на таблицу.

Общий `POST /exports` (§4.16) остаётся для выборок из «Данных → Таблица» (`scope.table_filter`).

## 15.9. Вопросы внутри разбора (чат разбора)

Разбор — это ветка чата: первое сообщение — загруженные файлы, ответ системы — прогресс → сводка → таблица → замечания; **дальше человек задаёт вопросы по данным этого разбора**: «КПД выше 20 % у перовскитов», «сравни D001 и D003», «замечания у D005», «где написано КПД D003». Старый чат прототипа («Задать вопрос» вне разбора, проекты) остаётся **[В ФОНЕ]** (§4.4, §4.5).

Сейчас фронт отвечает сам правилами по данным (`src/case/ask.ts`). В продукте отвечает **бэк**: базовый вариант — те же правила по таблицам разбора (язык запросов §4.11 + шаблоны); агентный RAG от ML-2 — **[ОПЦИОНАЛЬНО]**, тем же контрактом (§12). **Правило: каждое число в ответе ссылается на результат и страницу** (`cites`), иначе бэк его не отдаёт.

### `GET /runs/{id}/messages`
Сообщения ветки по порядку. Первые два — системные (`kind: "files"` — загруженные файлы; `kind: "run"` — ответ системы, фронт рисует его из `GET /runs/{id}`, `/summary`, `/results`, `/issues`), дальше — пары вопрос/ответ.
```json
{ "data": [
  { "id": "m1", "role": "user", "kind": "files", "files": ["D001", "D002", "…"], "at": "…" },
  { "id": "m2", "role": "system", "kind": "run", "at": "…" },
  { "id": "m3", "role": "user", "kind": "text", "text": "КПД выше 20 % у перовскитов", "at": "…" },
  { "id": "m4", "role": "assistant", "kind": "answer", "status": "done", "at": "…",
    "answer": { "text": "…", "cards": [ … ], "cites": [ … ], "suggestions": [ … ] } } ] }
```

### `POST /runs/{id}/messages` — вопрос
Тело [USER]: `{ "text": "сравни D001 и D003" }`. Ответ — поток SSE (`Accept: text/event-stream`) или `202 {message_id, stream_url}`.

События: `step` (`{"text":"Ищу в 21 статье разбора"}`), `text` (кусок текста ответа), `card`, `cite`, `suggestions`, `done`, `error`. Итоговый формат ответа:
```json
{ "text": "Выше 20 % у перовскитов — 5 значений в 3 статьях; лучшее — 23,5 % у D003 [1].",
  "cards": [
    { "kind": "results", "domain": "solar", "query": { "run": "run_demo", "metric": "PCE", "min": 20, "q": "перовскит" }, "total": 5 },
    { "kind": "compare", "domain": "solar", "pids": ["D001", "D003"], "metric": "PCE" },
    { "kind": "issues", "domain": "solar", "pids": ["D005"], "check_name": null },
    { "kind": "paper", "domain": "solar", "pid": "D003", "result_id": "R010" } ],
  "cites": [ { "n": 1, "domain": "solar", "pid": "D003", "result_id": "R010", "issue_id": null, "page": 6, "fragment": "PCE of 23.5%" } ],
  "suggestions": ["Сравни D003 и D001", "Замечания у D003", "Где написано КПД D003"] }
```
- `cards` — `results` (таблица результатов по запросу, фронт грузит строки через `GET /results` с `query`), `compare` (сравнение статей), `issues` (замечания), `paper` (открыть статью на значении/замечании).
- `cites` — `domain`, `pid`, `result_id` или `issue_id`, `page`, `fragment`: по клику фронт открывает `/paper/:domain/:pid?r=…` и подсвечивает цитату.
- Проверка [BE]: каждое число в `text` — со сноской на `result_id`, значение совпадает с действующим значением строки (с учётом правок) до округления; иначе фрагмент отбрасывается (§12.4).
- Область поиска — только статьи этого разбора.
- Ошибки: 400 (пусто), `409 run_in_progress` для вопросов о ещё не разобранных статьях — ответ по готовым с пометкой; 429.

### `GET /runs/{id}/suggestions`
3–5 подсказок по данным разбора [BE]: `{ "data": ["КПД выше 20 % у перовскитов", "Замечания у D005", "Сравни D001 и D003"] }` — строятся из того, что есть в разборе (темы, статьи с открытыми замечаниями, лучшие значения). **Не повторяют уже заданные вопросы**, в том числе то же сравнение в обратном порядке («сравни D003 и D001» = «сравни D001 и D003»): бэк нормализует вопрос (намерение + множество статей + показатель) и исключает совпадения. То же правило — у `suggestions` в ответе.

`stop` и оценка ответа — как у реплик чата: `POST /runs/{id}/messages/{mid}/stop`, `PUT /runs/{id}/messages/{mid}/rating`.

## 15.10. Сеть связей статей (на данных 7 таблиц)

Граф снова нужен, но **строится по данным таблиц сдачи**, а не по спискам литературы прототипа. Связи между статьями — только через общие сущности; связи «статья цитирует статью корпуса» не выдумываются.

| Вид ребра (`kind`) | Область 1 | Область 2 |
|---|---|---|
| `class` | общий `materials.material_class` | — |
| `absorber` | общий поглотитель (`formula_normalized`) | — |
| `layer` | общие материалы слоёв ETL / HTL / контактов (`layers.layer_material` по `layer_function`) | — |
| `dataset` | — | общий `datasets.dataset_name` (канон) |
| `generator` | — | общий `configurations.generator_model` |
| `embedding` | — | общий `embedding_model` |
| `retriever` | — | общий `retriever_model` / `retrieval_method` |
| `cited` | статья → цитируемая работа (`results.cited_publication_id`, как в статье: `[12]`, «Lewis et al., 2020») — узел «внешняя работа», без сопоставления со статьями корпуса | то же |

Кто считает: **бэк** по таблицам (узлы — статьи и общие сущности, рёбра — принадлежность); **ML-2** — нормализация названий материалов и моделей для склейки (`MAPbI3` = `CH3NH3PbI3`; `LLaMA3-8B-Instruct` = `Llama-3-8B-Instruct`; `HotpotQA` = `Hotpot QA`), словарь с версией.

### `GET /graph?run=&domain=&kinds=`
`kinds` — через запятую (по умолчанию все для области). Ответ (совпадает с `LinkGraph` в `src/case/links.ts`):
```json
{ "nodes": [ { "id": "D001", "kind": "paper", "label": "D001", "pid": "D001", "domain": "solar", "weight": 1 },
             { "id": "abs:CsPbI2Br", "kind": "material", "label": "CsPbI2Br", "weight": 3 },
             { "id": "cls:perovskite", "kind": "class", "label": "перовскит", "weight": 15 } ],
  "edges": [ { "from": "D001", "to": "abs:CsPbI2Br", "kind": "absorber", "label": "поглотитель" } ] }
```

### `GET /papers/{domain}/{pid}/related?run=`
Похожие статьи с перечнем общего (`Related` в `src/case/links.ts`):
```json
{ "data": [ { "pid": "D009", "domain": "solar", "shared": [ { "kind": "absorber", "label": "поглотитель CsPbI2Br" }, { "kind": "layer", "label": "ETL SnO2" } ], "score": 2 } ],
  "cited": { "count": 3, "refs": ["[12]", "Bai et al., 2018"] } }
```
Сортировка — по числу общих сущностей. Источник: [BE] (канон названий — [ML-2]).

## 15.12. Состояние замечания

У каждого замечания — состояние `state`: `open` | `fixed` | `ok`, **в контексте разбора** (ключ — `(run_id, domain, pid, issue_id)`, §15.17).

| Действие во фронте | Запрос | Результат |
|---|---|---|
| «Исправлено» | `PATCH /runs/{id}/papers/{domain}/{pid}/issues/{iid} {"state": "fixed"}` | `fixed`, `resolved_at`, `resolved_by` |
| «Всё верно» | `PATCH … {"state": "ok"}` | `ok` (не ошибка) |
| «Открыть снова» | `PATCH … {"state": "open"}` | снимает ручную отметку |

**Автозакрытие** [BE]: если **все** значения замечания (`record_ids`) исправлены или отклонены человеком, замечание считается `fixed` само (`state_auto: true`). «Открыть снова» на таком замечании не действует: ответ `409 issue_auto_fixed` с `message` «Все значения замечания исправлены или отклонены — замечание закрыто автоматически» (фронт показывает тост). Отмена правки значения снова открывает автозакрытое замечание.

Ответ `PATCH` — `{issue: {..., state, state_auto, resolved_at, resolved_by}, action_id, open_issue_count}`; отмена — через журнал. Поля `resolved_at`, `resolved_by` необязательны.

**Счётчики только по открытым**: `open_issue_count` у статьи и файла разбора, `issues_open` в сводке, точки в боковой панели, поиске и у значений, «нужно проверить». Полные числа (`issues_total`) — только в сводке. В выгрузку `issues.csv` идут `open` и `fixed` (§5.1); `ok` — нет.

В модели (§3.8) это поле `Issue.status`; в API для интерфейса оно называется `state` (оба имени допустимы в ответе).

## 15.13. Правило «лучшее значение»

Одно правило для сравнения, ответов в разборе и сводки [BE]:
1. берутся только строки `result_origin = primary`, `extraction_status = extracted`, без `duplicate_of`, не отклонённые человеком;
2. значение — исправленное человеком (`corrected_value`), иначе `normalized_value`;
3. `aggregation = champion` важнее среднего / медианы / пустого;
4. направление: для `Rs` и `HI` лучше меньшее, для остальных показателей области 1 — большее; для области 2 — по `metric_direction` (пусто — не ранжируется);
5. **в сравнении все показатели берутся с одного прибора** — того, у которого лучший КПД (`PCE`), с тем же `measurement_id` и тем же типом значения (`aggregation`); для области 2 — с одной конфигурации, набора и эксперимента лучшего результата выбранной метрики.

Ответы `GET /compare`, `POST /runs/{id}/messages` (карточки `compare`, тексты «лучшее — …») и сводки отдают выбранную строку (`domain`, `pid`, `result_id`), чтобы фронт не выбирал сам.

## 15.14. Выбор разбора в «Данных»

Экраны «Данные» берут разбор из `?run=` или текущий открытый разбор; «Все разборы» — объединение статей всех разборов пользователя (параметр не передаётся). Все списочные эндпоинты (`/results`, `/issues`, `/issues/stats`, `/compare`, `/graph`) принимают `run` и `domain`.

## 15.15. Исполнители в текстах для людей

В текстах, которые видит человек (паспорт, подсказки, описания замечаний, `computed_by`), исполнители называются **«сервис разбора»** (то, что считает бэк) и **«модель»** (то, что извлекает ML). Слова «бэк», «бэкенд», «ML» в текстах для людей не используются. В паспорте (`GET /passport`) у полей и правил — `executor: "Сервис разбора" | "Модель"`. Подписи `aggregation` для людей — «лучший прибор» / «среднее» / «медиана», пусто — «не указано» (это делает фронт).

## 15.17. Правки и отметки — в контексте разбора

1. **Ключ.** Правки значений (принять / исправить / отклонить) и отметки замечаний («Уже исправлено», «Всё верно») привязаны к **разбору**, а не к статье: ключ — (`run_id`, `domain`, `publication_id`, `result_id` / `issue_id`). Одна и та же статья в двух разборах проверяется независимо. Демо-разбор dev для каждого пользователя всегда начинается **чистым**. Переход в статью несёт `?run=` (`/paper/:domain/:pid?run=&r=&i=`); `GET /papers/{domain}/{pid}?run=` отдаёт `reviews` и состояния замечаний этого разбора (без `run` — без правок).
2. **Эндпоинты** (все — внутри разбора): `PATCH /runs/{id}/papers/{domain}/{pid}/results/{rid}/review`, `POST /runs/{id}/reviews/batch`, `PATCH /runs/{id}/papers/{domain}/{pid}/issues/{iid} {state}`. Прежние пути без `/runs/{id}` не используются.
3. **Повторы** (`duplicate_of`): у повтора **своей правки нет** — он наследует правку основной записи. Правка, присланная на повтор, бэком применяется к основной записи (ответ содержит `applied_to: <rid основной>`). В выгрузке повтор получает то же значение, что основная (исправленное — с той же пометкой в `note`); отклонение основной — §15.6 (первый повтор становится основной).
4. **Сброс правок разбора:** `DELETE /runs/{id}/reviews` — снимает все правки значений и отметки замечаний разбора, одной записью журнала; ответ `{removed_reviews, removed_issue_marks, action_id}`, «Вернуть» — `POST /actions/{action_id}/undo`. В сводке разбора — `edit_count` (число правок и отметок разбора; «проверено вами» — `reviewed`).
5. **Журнал:** у каждой правки и отметки замечания хранятся `at`, `who` и **прежнее значение** (`prev: {status, corrected_value}` или `prev_state`) — для «Журнала правок» и «Отменить последнее» (`POST /history/undo`, `GET /history?run=`).
6. **Поиск внутри разбора:** `GET /results`, `GET /issues`, `GET /issues/stats`, `GET /search` принимают `run`; счётчики и поиск учитывают правки и состояния этого разбора.
7. **Тексты замечаний:** `description` желательно отдавать **по-русски** — десятичная запятая, русские единицы («PCE 18,6 % не согласуется с Voc·Jsc·FF/Pin = 20,33 %»); сейчас фронт переводит сам правилами. В `issues.csv` при выгрузке — как есть (числа в описании допустимы в любом виде; колонки чисел — с точкой).
8. **Набор для сдачи** (`GET /runs/{id}/submission.zip`) собирается **с правками этого разбора**.

## 15.16. Кто за что
| Функция | Фронт | Бэк | ML-1 | ML-2 |
|---|---|---|---|---|
| Создать разбор, догрузить | композер `/app`, поле внизу `/run/:id` | `POST /runs`, `POST /runs/{id}/files`, очередь | — | — |
| Прогресс | ветка «ответа»: файлы, статусы, сводка | SSE `GET /runs/{id}/events`, статусы `queued…nodata` | разбор каждого файла | `/ml/parse`, `needs_ocr` → статус `ocr` |
| Статья | PDF (pdf.js), вкладки, подсветка цитаты | `GET /papers/{domain}/{pid}` (7 таблиц + `reviews` + `meta`), `GET /papers/{domain}/{pid}/pdf` | строки таблиц, цитаты, (опц.) bbox | — |
| Правки | принять / исправить / отклонить, массово, отмена | `PATCH /runs/{id}/papers/…/review`, `POST /runs/{id}/reviews/batch`, журнал, пересчёт проверок, влияние на выгрузку | — | — |
| Вопросы в разборе | поле внизу ветки, карточки, сноски | `POST /runs/{id}/messages` (SSE), правила по таблицам, проверка чисел ↔ `result_id` | — | [ОПЦИОНАЛЬНО] агентный RAG тем же контрактом |
| Сеть связей | граф, «Похожие» | `GET /graph`, `/related` по таблицам | — | канон названий материалов и моделей |
| OCR | пометка «скан, распознано OCR», низкая уверенность отдельно | статус `scan`, `quote_found` по OCR | — | OCR страниц без текста (обязателен для RAG) |
| Экспорт | «Скачать набор (7 CSV)» | `GET /runs/{id}/submission.zip`, `dataset.json`, `check_submission.py` | — | — |
