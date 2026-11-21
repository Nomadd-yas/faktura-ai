# Фактура AI — API ядра (v1) для фронта

Это **первый рабочий срез** бэкенда: загрузить PDF → разобрать → посмотреть 7 таблиц → скачать набор для сдачи. Всё остальное из `BACKEND_SPEC §15` пока **заморожено** (список в конце) — на эти экраны оставляй мок.

Формы ответов совпадают с §15, поэтому менять нужно только слой данных (`src/case/index.ts`): то, что ниже, приходит в тех же полях, просто полей меньше.

## 0. Общее

| | |
|---|---|
| База | `http://localhost:8000/api/v1` (CORS открыт для `http://localhost:5200`, меняется в `FAKTURA_CORS_ORIGINS`) |
| Область | **только `solar`** (кейс 5, область 1). `rag` → `422 domain_not_supported` |
| Авторизация | `Authorization: Bearer <access_token>`; без токена — `401` |
| Даты | ISO 8601 UTC, `2026-11-20T09:00:00Z` |
| Ошибка | всегда `{"error": {"code", "message", "field", "details", "request_id"}}`; `message` — по-русски, можно показывать как есть |
| Пагинация | `limit` + `cursor`, в ответе `next_cursor` (`null` — конец) |
| Документация | `GET /docs` (Swagger) — живой список на сервере |

Статья везде — пара (`domain`, `publication_id`), `publication_id` = имя файла без `.pdf` (`D001`, `P042`).

## 1. Вход

### `POST /auth/login`
```json
{ "email": "admin@faktura.local", "password": "…", "remember": false }
```
→ `200`
```json
{ "access_token": "…", "token_type": "Bearer", "expires_in": 900,
  "user": { "id": "u_1", "user_id": "u_1", "email": "admin@faktura.local", "name": "Админ", "role": "", "team": "",
            "color": "#…", "focus": null, "is_admin": true } }
```
Refresh-токен — в `httpOnly` cookie (запросы с `credentials: "include"`). Ошибки: `401 invalid_credentials`, `429`.

### `POST /auth/refresh` → новая пара (тело как у login, без `user`). `401` — снова на вход.
### `POST /auth/logout` → `204`.
### `GET /auth/me` → `user`.
### `GET /health` — без авторизации, `{"status": "ok", "version": "0.1.0", "model_version": "…", …}`.

Регистрации нет: учётку выдаём мы (логин/пароль скажу отдельно).

## 2. Разбор (Run)

Разбор = одна загрузка из N файлов одной области. Прогон стартует сам после загрузки.

### Run
| Поле | Тип | Описание |
|---|---|---|
| `id` | string | `run_…` |
| `title` | string | «21 статья · 20 ноя» или имя ZIP |
| `domain` | `"solar"` | |
| `status` | `queued` \| `running` \| `done` \| `error` | |
| `created_at`, `updated_at` | datetime | |
| `counts` | `{files, queued, running, done, out, error, nodata}` | |
| `results`, `issues` | int | всего строк по разбору |
| `files` | RunFile[] | |

### RunFile
| Поле | Тип | Описание |
|---|---|---|
| `id` | string | `publication_id`; при совпадении имён в одном разборе — `P042-2` |
| `name` | string | имя файла / путь в ZIP |
| `status` | `queued` \| `running` \| `done` \| `out` \| `error` \| `nodata` | `out` — статья не по теме (`reason` = `exclusion_reason`); `nodata` — разобрана, значений нет; `error` — `reason` = текст ошибки |
| `status_label` | string | подпись по-русски: «в очереди», «разбор», «готово», «нет результатов», «ошибка», для `out` — причина |
| `stage` | string \| null | «Извлечение» → «Нормализация» → «Проверка» — пока `running`; у конечных статусов `null` |
| `progress` | int 0..100 | |
| `has_data` | bool | есть строки `results` |
| `results`, `issues` | int | |
| `reason` | string \| null | |
| `size`, `sha256` | | |

Статуса `ocr` в v1 нет: в области `solar` сканов нет.

### `POST /runs` — создать разбор
`multipart/form-data`:
- `file` — повторяемое поле, PDF (для папки — каждый файл с относительным путём в имени), **или**
- `archive` — один ZIP с PDF;
- `domain` — `solar` (по умолчанию `solar`);
- `title` — необязательно.

→ `202` — Run + отклонённые файлы:
```json
{ "id": "run_7", "title": "21 статья · 20 ноя", "domain": "solar", "status": "queued", "created_at": "2026-11-20T09:00:00Z",
  "counts": { "files": 21, "queued": 21, "running": 0, "done": 0, "out": 0, "error": 0, "nodata": 0 },
  "results": 0, "issues": 0,
  "files": [ { "id": "D001", "name": "D001.pdf", "status": "queued", "status_label": "в очереди", "progress": 0, "has_data": false } ],
  "rejected": [ { "name": "readme.txt", "code": "unsupported_file", "message": "Можно загрузить только PDF" } ] }
```
Коды в `rejected`: `unsupported_file`, `duplicate` (тот же SHA-256 в этом разборе), `broken_pdf`, `encrypted_pdf`, `empty_file`, `file_too_large`, `too_many_pages`. Файлы с одинаковым именем, но разным содержимым принимаются: id `P042`, `P042-2`.
Ошибки: `400 no_files`, `413 too_large`, `415 unsupported_media_type`, `422 zip_bomb` / `domain_not_supported`. Лимиты: PDF до 50 МБ и 300 страниц; до 200 файлов и 1 ГБ на разбор; ZIP — до 500 файлов и 2 ГБ в распакованном виде.

### `GET /runs` — список
Параметры: `limit` (по умолчанию 20), `cursor`, `files=false` (без списка файлов). Сортировка — новые сверху.
```json
{ "data": [ Run, … ], "total": 23, "next_cursor": null }
```
В списке `files` короткие: `id, name, status, has_data, results, issues, reason`.

### `GET /runs/{id}` — разбор целиком, `files` полные. `404 not_found`.

**Прогресс — опросом:** `GET /runs/{id}` раз в 2 с, пока `status` = `queued`/`running`. (SSE `/runs/{id}/events` — позже, формы событий будут как в §15.3.)

### `GET /runs/{id}/summary` — сводка
```json
{ "files": 21, "papers_in_scope": 19, "papers_out": 2, "files_without_results": 0, "files_error": 0,
  "results": 659, "scored_results": 627, "issues_total": 53,
  "coverage": [ { "metric": "PCE", "n": 19, "total": 19, "results": 205 }, … ],
  "issues_by_check": [ { "check_name": "contradiction", "total": 8 }, … ] }
```
`coverage` — по 11 оцениваемым показателям области 1 (`METRICS` в `src/case/types.ts`): в `n` статьях из `total` по теме есть хотя бы одно значение. Полей про правки человека (`reviewed`, `accepted`, …, `issues_open`) в v1 нет.

## 3. Данные: 7 таблиц шаблона

Строки — **ровно колонки CSV шаблона** (`submission_template/README.md`, типы `PublicationRow`…`IssueRow` в `src/case/types.ts`), значения **строками как в CSV**: `""` — в статье нет, `true`/`false`, несколько значений через `;`. Что видит фронт — то и уйдёт в сдачу.

Повторы не сливаются: повтор — отдельная строка `results` со своим `source_*`, в `duplicate_of` — `result_id` основной записи.

### `GET /papers/solar/{pid}?run={id}` — все 7 таблиц статьи
`run` обязателен, если статья есть в нескольких разборах (иначе берётся последний).
```json
{ "publication":  { "publication_id": "D001", "title": "…", "year": "2021", "DOI": "…", "authors": "…", "in_scope": "true", "exclusion_reason": "" },
  "materials":    [ MaterialRow, … ],
  "devices":      [ DeviceRow, … ],
  "layers":       [ LayerRow, … ],
  "measurements": [ MeasurementRow, … ],
  "results":      [ ResultRow, … ],
  "issues":       [ IssueRow, … ],
  "paper_status": "has_data",
  "meta": { "file_status": "done", "run_ids": ["run_7"], "pdf_pages": 9,
            "results": { "R002": { "quote_found": true, "issue_ids": ["I1"], "duplicates": ["R015"] } } } }
```
- `paper_status`: `has_data` | `nodata` | `out` (у `out` — `publication.in_scope = "false"` и `exclusion_reason`, остальные массивы пустые).
- `meta.results[rid]` — подсказки интерфейсу, необязательные: `quote_found` (нашлась ли цитата на странице), `issue_ids`, `duplicates` (повторы этого значения).
- `reviews`, `confidence`, `bbox`, `source_table_row_*` — в v1 не приходят.
- `404 not_found` — нет такой статьи.

### `GET /papers/solar/{pid}/pdf` — исходный PDF
`application/pdf`, поддерживает `Range` (pdf.js), `ETag`. Подсветка цитаты — на фронте, как в §15.5. `404` — файла нет.

### `GET /results` — строки `results` (таблица разбора)
| Параметр | |
|---|---|
| `run` | обязателен в v1 |
| `pid` (повт.) | статьи |
| `metric` (повт.) | `metric_name`; `scored=true` — только 11 оцениваемых |
| `device_id`, `measurement_id`, `material_id` | |
| `duplicates` | `include` (по умолчанию) \| `exclude` — только основные записи |
| `sort` | `publication_id` \| `metric` \| `value` \| `page`, `dir` = `asc`\|`desc` |
| `limit` (100, до 1000), `cursor` | |
```json
{ "data": [ { "row": ResultRow, "device_label": "Y-Sn3O4", "paper_title": "…", "meta": { "quote_found": true, "issue_ids": [] } } ],
  "total": 659, "next_cursor": "…" }
```

### `GET /issues` — строки `issues`
Параметры: `run` (обязателен), `pid` (повт.), `check_name` (повт.), `limit`, `cursor`.
```json
{ "data": [ { "row": IssueRow, "paper_title": "…" } ], "total": 53, "next_cursor": null }
```
Состояний замечаний (`open/fixed/ok`) в v1 нет — все замечания считаются открытыми.

## 4. Скачать набор

### `GET /runs/{id}/submission.zip`
Ответ — сразу файл: `application/zip`, `Content-Disposition: attachment; filename="submission.zip"; filename*=UTF-8''Набор%20для%20сдачи%20—%20{title}.zip`. Внутри **7 CSV** шаблона области 1 по всем статьям разбора (статья не по теме — только строка в `publications`) **+ `PASSPORT.md` и `passport.json`** (поля, словари, правила проверок, нормализация, версии модели/правил/словаря).

`?check=true` — сервер прогоняет `check_submission.py`: в ZIP добавляется `check_report.txt`, заголовок `X-Check-Status: ok|warnings|failed`; если скрипт нашёл ОШИБКИ — вместо файла `422 submission_invalid` (в `details.errors` — первые ошибки).

Заголовки для тоста (открыты в `Access-Control-Expose-Headers`): `X-Export-Tables: 7`, `X-Export-Results`, `X-Export-Papers`, `X-Export-Filename`.

Пока разбор идёт — `409 run_in_progress`; `?partial=true` — выгрузить уже готовые статьи.

## 4a. Изменения 21.11

- Разборы видны только владельцу: чужой разбор → `404 not_found`.
- Тексты для людей (`reason`, `message`) — «сервис разбора» и «модель», без технических терминов: например «Сервис разбора временно недоступен» (`extractor_unavailable`, 503), «Модель вернула некорректные данные: …», «Превышено время разбора файла (10 мин)».
- Во всех ошибках есть `request_id`.
- `RunFile.stage`/`progress` — настоящие (0 → 10 → 60 → 85 → 100), разбор `error` только если упали все файлы.
- `POST /auth/register` убран (учётки заводит сервер: `faktura users create --email … --name … --generate`).

## 5. Что заморожено (на этих экранах пока мок)

Вернёмся к ним после того, как пришлёшь обновлённую спеку фронта:
- правки человека: `PATCH …/review`, `reviews/batch`, undo/история, влияние правок на выгрузку;
- управление разбором: `PATCH/DELETE /runs/{id}`, `restart`, `stop`, догрузка `POST /runs/{id}/files`, демо-разбор dev;
- SSE `/runs/{id}/events` (пока — опрос);
- «Данные» по всем разборам: `/results` без `run`, язык запросов (`q`), `group_by`, `/results/summary`, `/compare`, «лучшее значение»;
- состояния замечаний, `/issues/stats`;
- `dataset.json`, `dataset.xlsx`, `/exports`, `/passport`;
- область `rag`, OCR (`/papers/{domain}/{pid}/ocr`, статус `ocr`);
- чат разбора, граф связей, проекты, словари, поиск.

Если какой-то из этих пунктов нужен раньше — скажи, переставим.
