# Фактура AI — API ядра (v1) для фронта

Это **первый рабочий срез** бэкенда: загрузить PDF → разобрать → посмотреть 7 таблиц → скачать набор для сдачи. Всё остальное из `BACKEND_SPEC §15` пока **заморожено** (список в конце) — на эти экраны оставляй мок.

Формы ответов совпадают с §15, поэтому менять нужно только слой данных (`src/case/index.ts`): то, что ниже, приходит в тех же полях, просто полей меньше.

## 0. Общее

| | |
|---|---|
| База | `http://localhost:8000/api/v1` (CORS открыт для `http://localhost:5200`, меняется в `FAKTURA_CORS_ORIGINS`) |
| Область | `solar` (кейс 5, область 1) и `rag` (область 2, см. §5). Другое значение → `422 domain_not_supported` |
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
| `domain` | `"solar"` \| `"rag"` | |
| `pinned` | bool | закреплён; закреплённые идут первыми в `GET /runs` (§4c) |
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
| `status` | `queued` \| `running` \| `done` \| `out` \| `error` \| `nodata` \| `ocr` (только `rag`) | `out` — статья не по теме (`reason` = `exclusion_reason`); `nodata` — разобрана, значений нет; `error` — `reason` = текст ошибки |
| `status_label` | string | подпись по-русски: «в очереди», «разбор», «готово», «нет результатов», «ошибка», для `out` — причина |
| `stage` | string \| null | «Извлечение» → «Нормализация» → «Проверка» — пока `running`; у конечных статусов `null` |
| `progress` | int 0..100 | см. §4b |
| `eta_seconds`, `pages`, `started_at` | | см. §4b |
| `has_data` | bool | есть строки `results` |
| `results`, `issues` | int | |
| `reason` | string \| null | |
| `size`, `sha256` | | |
| `suggested_domain` | `"rag"` \| `"solar"` \| `null` | подсказка «статья из другой области»; пока всегда `null`; только в полном `files` (§4c) |

Статус `ocr` (подпись «скан, распознано OCR») бывает только в `rag`: у PDF нет текстового слоя, значения получены распознаванием. В `solar` сканов нет.

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
Параметры: `limit` (по умолчанию 20), `cursor`, `files=false` (без списка файлов). Сортировка — закреплённые (`pinned`) сверху, затем новые.
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
`application/pdf`, поддерживает `Range` (pdf.js), `ETag`. Подсветка цитаты — на фронте, как в §15.5. Отдаётся для любого принятого файла разбора, в том числе `out`/`nodata`/`error` (§4c). `404` — файла нет.

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
Ответ — сразу файл: `application/zip`, `Content-Disposition: attachment; filename="submission.zip"; filename*=UTF-8''Набор%20для%20сдачи%20—%20{title}.zip`. Внутри **7 CSV** шаблона области 1 по всем статьям разбора (статья не по теме — только строка в `publications`) **+ `PASSPORT.md` и `passport.json`** (критерии отбора, поля, словари, правила проверок, нормализация, версии модели/правил/словаря; формат и отдельные запросы — §4d).

`?check=true` — сервер прогоняет `check_submission.py`: в ZIP добавляется `check_report.txt`, заголовок `X-Check-Status: ok|warnings|failed`; если скрипт нашёл ОШИБКИ — вместо файла `422 submission_invalid` (в `details.errors` — первые ошибки).

Заголовки для тоста (открыты в `Access-Control-Expose-Headers`): `X-Export-Tables: 7`, `X-Export-Results`, `X-Export-Papers`, `X-Export-Filename`.

Пока разбор идёт — `409 run_in_progress`; `?partial=true` — выгрузить уже готовые статьи.

## 4a. Изменения 21.11

- Разборы видны только владельцу: чужой разбор → `404 not_found`.
- Тексты для людей (`reason`, `message`) — «сервис разбора» и «модель», без технических терминов: например «Сервис разбора временно недоступен» (`extractor_unavailable`, 503), «Модель вернула некорректные данные: …», «Превышено время разбора файла (10 мин)».
- Во всех ошибках есть `request_id`.
- `RunFile.stage`/`progress` — настоящие (0 → 10 → 60 → 85 → 100), разбор `error` только если упали все файлы.
- `POST /auth/register` убран (учётки заводит сервер: `faktura users create --email … --name … --generate`).

## 4b. Прогресс и оставшееся время

Поля есть в `GET /runs/{id}`, `GET /runs` (в т.ч. `files` в коротком виде) и в ответе `POST /runs`.

| Где | Поле | Тип | Описание |
|---|---|---|---|
| Run | `progress` | int 0..100 | общий процент, взвешенный по страницам (не среднее по файлам) |
| Run | `eta_seconds` | int \| null | секунд до конца всего разбора с учётом очереди и параллелизма |
| Run | `started_at` | datetime \| null | когда стартовал первый файл |
| RunFile | `progress` | int 0..100 | плавный процент файла |
| RunFile | `eta_seconds` | int \| null | секунд до конца файла; для `queued` — вместе с ожиданием очереди |
| RunFile | `pages` | int \| null | число страниц PDF (известно сразу после загрузки) |
| RunFile | `started_at` | datetime \| null | когда файл начал разбираться |

**Процент файла** = доля этапа × ход внутри него: «Извлечение» 0–70 %, «Нормализация» 70–85 %, «Проверка» 85–100 %. Сервис извлечения отвечает один раз в конце, поэтому ход внутри извлечения — оценка по времени (прошло / ожидаемое = страницы × сек/стр), не выше 95 % этапа, пока он не завершился. Процент не убывает и равен 100 только в конечном статусе (`done`, `out`, `nodata`, `ocr`, `error`).

**ETA.** Скорость этапов (сек/стр) — скользящее среднее по последним 50 разобранным файлам, отдельно для `solar` и `rag` (хранится на сервере, переживает рестарт); пока замеров нет — значения по умолчанию (извлечение: solar 10, rag 15 сек/стр). Файл: оставшиеся страницы × скорости этапов впереди. Прогон: вся оставшаяся работа / число параллельных обработчиков, но не меньше ETA самого долгого файла (в т.ч. ждущего в очереди). Значение сглаживается (не более ±30 % между опросами) и округляется до 5 с.

`eta_seconds = null`, если оценить нельзя (неизвестно число страниц) — тогда оценивайте на клиенте. В конечных статусах `eta_seconds = null`, `progress = 100`.

```json
{ "id": "run_7", "status": "running", "progress": 43, "eta_seconds": 370, "started_at": "2026-11-21T10:02:11Z",
  "files": [
    { "id": "D001", "status": "done", "progress": 100, "eta_seconds": null, "pages": 9 },
    { "id": "D002", "status": "running", "stage": "Извлечение", "progress": 38, "eta_seconds": 45, "pages": 12 },
    { "id": "D003", "status": "queued", "progress": 0, "eta_seconds": 150, "pages": 17 } ] }
```

Те же поля несёт SSE `/runs/{id}/events` (§4e).

## 4c. Управление разбором и «Разобрать как…» (спека §7а.3, §7а.4)

Все запросы — только для своих разборов: чужой или удалённый разбор → `404 not_found`. У Run есть поле `pinned` (bool, по умолчанию `false`); у RunFile — `suggested_domain` (`"rag"` \| `"solar"` \| `null`, пока всегда `null`: сервис разбора не сообщает, из какой области статья «не по теме»).

### `POST /runs/{id}/rerun-as` — разобрать файлы в другой области
```json
{ "domain": "rag", "file_ids": ["D002", "D007"], "title": "D001 и ещё 9 · RAG" }
```
→ `202` — новый Run (как у `POST /runs`, плюс `rejected`; заголовок `Location`), прогон стартует сам. Исходный разбор не меняется.
- PDF берутся из хранилища исходного разбора, повторная загрузка не нужна. `file_ids` — `RunFile.id` исходного разбора; подходят любые принятые файлы (`out`, `nodata`, `error` тоже).
- `title` необязателен: «{название исходного} · RAG» или «… · солнечные».
- Ошибки: `404 not_found` — нет разбора или файла (`field: file_ids`); `409 run_in_progress` — исходный разбор ещё идёт; `422 domain_not_supported`; `422 validation_error` — нет `domain`, пустой `file_ids`, та же область, что у исходного, файл не был принят (нет PDF), слишком длинное название.

### `PATCH /runs/{id}` — переименовать / закрепить
Тело `{ "title"?: string, "pinned"?: bool }` → `200` Run (полный). `422 validation_error` — пустое название (после обрезки пробелов), длиннее 300 символов или нет ни одного поля. В `GET /runs` закреплённые разборы идут первыми, внутри групп — новые сверху.

### `DELETE /runs/{id}` — удалить
`204`, мягкое удаление: разбор пропадает из списка, а `GET /runs/{id}` и всё остальное по нему отвечают `404`. Идущий разбор сначала останавливается.

### `POST /runs/{id}/restart` — перезапустить
Тело необязательно: `{ "file_ids"?: [...] }`. Без тела (или без `file_ids`) перезапускается всё, иначе только перечисленные файлы. → `202` Run, тот же `id`, файлы снова `queued`, прогон стартует сам. Ошибки: `404 not_found` (нет разбора/файла), `409 run_in_progress` (разбор ещё идёт — сначала `stop`), `422 validation_error` (пустой `file_ids: []`). Результаты уже разобранных файлов могут прийти из кеша сервера (тот же PDF и версия модели).

### `POST /runs/{id}/stop` — остановить
`200` Run. Незаконченные файлы (`queued`, `running`) получают статус `error` и `reason: "остановлено"`; готовые остаются как есть. Новые файлы не берутся в работу, а результат файла, который разбирался в момент остановки, отбрасывается. Итоговый `status` разбора считается как обычно (`done`, а `error` — если ни один файл не успел). Повторный `stop` и `stop` завершённого разбора безопасны (`200`, без изменений). Остановленные файлы можно запустить через `restart`.

### Статьи для `out` / `nodata` / `error` (спека §7а.3)
- `GET /papers/{domain}/{pid}/pdf?run=` отдаёт исходный PDF **любого принятого файла** разбора, включая `out`, `nodata`, `error`. `pid` — `RunFile.id`. `404` — нет такого файла или PDF нет в хранилище.
- `GET /papers/{domain}/{pid}?run=` для `out` — `200`: `paper_status: "out"`, строка `publication` с `in_scope: "false"` и `exclusion_reason`, остальные массивы пустые (у `rag` — `counts` по нулям, кроме `publications`, `metrics` и `issues` пусты). Для `nodata` — `200`, `paper_status: "nodata"`, пустые таблицы; для `ocr` (rag) — `has_data`. `404` — у файла нет статьи (отклонён при приёме).

## 4d. Паспорт набора с сервера (спека §7а.6)

### `GET /runs/{id}/passport.json`
`200 application/json` — паспорт набора области этого разбора. **Байт в байт тот же `passport.json`, что внутри `submission.zip`** (один и тот же код генерации). `404 not_found` — нет разбора. Паспорт доступен и пока разбор идёт; версии модели берутся по уже разобранным статьям.

### `GET /runs/{id}/passport.md`
`200 text/markdown; charset=utf-8` — тот же `PASSPORT.md`, что в ZIP (собирается из этого же JSON).

Формат:
```json
{
  "title": "Паспорт данных «Фактура AI»", "version": "1",
  "format": "CSV UTF-8 с BOM, запятая, точка, списки через «;»; пустая ячейка = в статье нет",
  "versions": { "model": "extractor-2026-11-20", "rules": "1.0.0", "dictionary": "…" },
  "domains": [ { "id": "solar", "name": "Солнечные элементы",
    "guides": [ { "id": "inclusion|comparability|extraction", "title": "…", "description": "…", "items": ["…"] } ],
    "entities": [ { "id": "results", "title": "Результаты", "row": "…", "key": "publication_id + result_id",
      "fields": [ { "name": "metric_name", "type": "enum", "unit": null, "required": true, "description": "…",
                    "values": ["PCE", "…"], "ref": null } ] } ],
    "rules": [ { "id": "jv_consistency", "title": "…", "description": "…", "level": "warn", "computed_by": "бэк" } ],
    "normalization": [ { "quantity": "Voc", "from": "mV", "to": "V", "factor": 0.001, "note": null } ] } ]
}
```
- `domains` — одна область (область разбора). `entities` — 7 таблиц шаблона в порядке выгрузки, `fields` — колонки ровно как в шаблоне (`solar`: `submission_template/*.csv`; `rag`: `rag_schema.json`).
- `versions.model` — `model_version` статей набора (несколько — через «, »; `null`, пока нет разобранных статей). `versions.rules` — версия набора проверок (solar: `1.0.0`, rag: `rag-checks-1`), `versions.dictionary` — версия словаря нормализации (solar) или схемы rag.
- В ZIP лежит этот же JSON и `PASSPORT.md` в том же виде, что рисует фронт (+ строка «Собран с версиями»). Раньше в ZIP был паспорт старого формата (`collection`, `tables`, `metrics`…): он заменён.
- Содержимое областей (критерии, правила, нормализация) — файлы `backend/src/faktura/passport/data/{solar,rag}.json`; при изменении порогов правятся они, фронт подхватывает сам.

## 4e. SSE: события разбора

### `GET /runs/{id}/events` — `text/event-stream`
Ход разбора без опроса. Заголовки ответа: `Cache-Control: no-cache`, `X-Accel-Buffering: no`. Только владелец разбора; чужой или неизвестный `{id}` — `404 not_found` обычным JSON `{error:{…}}` (до начала потока).

**Авторизация.** `Authorization: Bearer <access>` как везде. Браузерный `EventSource` не умеет заголовки, поэтому здесь (и только здесь) принимается `?access_token=<access>`; если есть и заголовок, берётся он. Токен проверяется целиком (подпись, срок, блокировка), на остальных маршрутах параметр игнорируется (`401`). Токен проверяется один раз при подключении; access живёт 15 мин, поток им не ограничен. Путь в логах пишется без query. Токен в URL может попасть в логи прокси: nginx для этого маршрута лучше не логировать query.

**События** (`event:` / `data:` JSON, `id:` растёт в пределах соединения; `Last-Event-ID` не используется, после переподключения всегда приходит свежий снимок):

| event | когда | data |
|---|---|---|
| `file.progress` | снимок при подключении (по одному на каждый файл), затем при каждом изменении файла | поля файла как в `GET /runs/{id}`: `id, name, status, status_label, stage, progress, eta_seconds, pages, started_at, has_data, results, issues, reason` |
| `file.done` | файл стал конечным (`done`, `out`, `nodata`, `ocr`, `error`); в снимке не шлётся | то же |
| `run.progress` | снимок при подключении, затем при каждом изменении | `id, status, progress, eta_seconds, started_at, updated_at, counts, results, issues` — как у Run |
| `run.done` | разбор в конечном статусе (`done`, `error`; в т.ч. остановленный) | то же, что `run.progress`; после него поток закрывается |

Значения берутся теми же функциями, что `GET /runs/{id}`, поэтому совпадают с ним в тот же момент (прогресс плавно растёт внутри этапа, поэтому пока разбор идёт, `file.progress`/`run.progress` приходят примерно раз в 2 с — только изменившиеся). Каждые ~15 с без событий шлётся комментарий `: ping`. Если разбор уже завершён, придёт снимок и сразу `run.done`.

```
id: 1
event: file.progress
data: {"id": "D001", "status": "running", "stage": "Извлечение", "progress": 38, "eta_seconds": 45, "pages": 12, "started_at": "2026-11-21T10:02:11Z", …}

id: 2
event: file.progress
data: {"id": "D002", "status": "queued", "stage": null, "progress": 0, "eta_seconds": 150, "pages": 17, "started_at": null, …}

id: 3
event: run.progress
data: {"id": "run_7", "status": "running", "progress": 43, "eta_seconds": 370, "started_at": "2026-11-21T10:02:11Z", "counts": {…}, …}

: ping

id: 4
event: file.done
data: {"id": "D001", "status": "done", "progress": 100, "eta_seconds": null, …}

id: 7
event: run.done
data: {"id": "run_7", "status": "done", "progress": 100, "eta_seconds": null, …}
```

Клиент: `new EventSource('/api/v1/runs/run_7/events?access_token=' + token)`; на `run.done` вызвать `close()` (иначе браузер переподключится).

## 4f. v2: правки, замечания, журнал (спека §9)

Правки привязаны к разбору: ключ (`run`, `domain`, `pid`, `result_id`). Строки разбора не меняются, правки накладываются при чтении и выгрузке. Все мутации возвращают `action_id` (и заголовок `X-Action-Id`) для отмены. `pid` — id статьи в разборе (`RunFile.id`).

### Правка значения
`PATCH /runs/{id}/papers/{domain}/{pid}/results/{rid}/review`
```json
{ "status": "accepted", "corrected_value": 12.5 }
```
`status` ∈ `new` | `accepted` | `rejected`; `corrected_value` — число в целевой единице (строка или число; без поля значение не меняется, `null` или `""` снимает). Нечисло → `400 validation_error`; нет строки → `404`.
```json
{ "review": { "result_id": "R002", "status": "accepted", "corrected_value": "12.5", "at": "2026-11-21T10:00:00+00:00", "who": "Анна" },
  "applied_to": null, "issues_changed": { "opened": [], "closed": ["I3"] }, "action_id": "act_…", "elapsed_ms": 18.4 }
```
- Правка, присланная на повтор (`duplicate_of` не пусто), применяется к основной записи: `applied_to` = её `result_id`, `review.result_id` тоже.
- Проверки статьи пересчитываются сразу: `issues_changed` — id замечаний, ставших открытыми / переставших быть открытыми. Пересчёт правил бэка (solar) может открыть новые замечания (id `V…`, «дополнительные»).
- Статья должна быть разобрана, иначе `409 conflict`.

### Пакет и сброс
- `POST /runs/{id}/reviews/batch`, `{ "items": [ { "domain": "solar", "pid": "D001", "rid": "R002", "status": "rejected" } ] }` → `{ "count": 1, "action_id": "…" }`; пустой `items` → `400`.
- `DELETE /runs/{id}/reviews` — сбрасывает правки, состояния замечаний и дополнительные замечания всего разбора → `{ "count": N, "action_id": "…" }`.

### Состояние замечания
`PATCH /runs/{id}/papers/{domain}/{pid}/issues/{iid}`, `{ "state": "open" | "fixed" | "ok" }` → `{ "issue": { "issue_id": "I3", "state": "ok", "auto": false }, "action_id": "…" }`; нет замечания → `404`.
- **Автозакрытие:** когда все `record_ids` замечания исправлены (есть `corrected_value`) или отклонены, либо пересчёт правил бэка по правкам его больше не находит, оно становится `fixed` с `auto: true`. Откат правки возвращает замечание в `open`.
- Вручную открыть авто-закрытое замечание нельзя: `409 issue_auto_fixed`. Чтобы вернуть — отменить правку (undo) или сбросить правки.

### Чтение
- `GET /runs/{id}/papers/{domain}/{pid}/review` → `{ "reviews": { "R002": {result_id, status, corrected_value, at, who} }, "issues": [ { "issue_id", "state", "auto", "extra" } ], "extra_issues": [ IssueRow ], "open_issue_count": 3 }`.
- `GET /issues/stats?run=` → `{ "data": [ { "check_name": "range", "count": 5, "papers": 3 } ] }` — только открытые, с учётом состояний.
- `GET /papers/{domain}/{pid}?run=` (и `rag`): добавлены `reviews` (`{ result_id: {status, corrected_value, at, who} }`, только тронутые строки) и `issue_states` (`{ issue_id: {state, auto} }`, только записанные состояния; нет записи = `open`). Дополнительные замечания пересчёта дописаны в конец `issues` (колонки шаблона, id `V…`) и учтены в `meta.results[rid].issue_ids`. Строки таблиц остаются колонками шаблона; `record_ids` в них — как в разборе (без вычета отклонённых).
- `GET /results`: к каждому элементу `review` = `{status, corrected_value, at, who}` (`status: "new"`, если не трогали). `GET /issues`: к элементу `state` (`open`|`fixed`|`ok`) и `auto`; дополнительные замечания в этот список не входят (они в `GET /papers/…`, `/issues/stats` и счётчиках).

### Влияние на `submission.zip`
- `rejected`: строка не выгружается; убирается из `issues.record_ids` и `duplicate_of`; первый повтор становится основной записью.
- Исправление: `normalized_value` и `metric_value` = исправленное значение, `raw_*` из статьи; в `note` дописывается «Исправлено вручную: было X».
- Замечания: выгружаются `open` и `fixed` (и дополнительные), `ok` — нет.

### Журнал и отмена
Каждая мутация пишет действие с прежними значениями.
- `GET /history?run=&limit=50` → `{ "data": [ { "id", "action": "review|batch|issue|reset", "label", "run_id", "undone", "at", "changes" } ] }`, новые сверху.
- `POST /actions/{id}/undo` и `POST /history/undo?run=` (последнее неотменённое) → `{ "undone": {…}, "undo_action_id": "…" }`. Уже отменённое → `409 conflict`; чужое / нет → `404`; нечего отменять → `404`.

### `POST /runs/{id}/files` — догрузить файлы
`multipart/form-data`: `file` (повторяемое, PDF) и/или `archive` (ZIP); поля, лимиты, проверки и формат `rejected` — как у `POST /runs` (§2; область берётся из разбора). → `202` — Run (+ `rejected`), заголовок `Location`.
- Дубликат (тот же SHA-256 уже в разборе) → в `rejected` с `code: "duplicate"`; одинаковые имена с разным содержимым — `P042`, `P042-2`.
- Ничего подходящего → `400 no_files` (`details.rejected`). Нет разбора → `404`.
- Идущий разбор (`queued`/`running`): файлы дописываются в тот же прогон, он не перезапускается. Завершённый разбор: стартует новый прогон только по новым файлам, остальные не трогаются, `status` снова `queued`/`running`. Разбор в состоянии остановки → `409 run_in_progress`.

### Счётчики открытых замечаний
- `RunFile.open_issue_count` (в `GET /runs/{id}`, коротком `files` списка и ответе `POST`), `Run.issues_open` и `summary.issues_open`: только открытые замечания с учётом состояний (`fixed`/`ok` не считаются, дополнительные замечания пересчёта считаются). `issues` / `issues_total` остаются числом замечаний в исходных строках. Состояния и дополнительные замечания читаются тремя запросами на разбор, не на статью.

### «Лучшее значение»
`GET /runs/{id}/summary` → `best_values`: по каждому из 11 оцениваемых показателей `solar` (у `rag` — `{}`):
```json
"best_values": { "PCE": { "pid": "D004", "result_id": "R012", "value": 21.4, "unit": "%", "aggregation": "champion", "corrected": false } }
```
- Подходят строки: `result_origin = primary`, `extraction_status = extracted`, не повтор, не отклонены (с учётом повышения повтора после отклонения основной записи).
- Значение — исправленное, если есть, иначе `normalized_value`. Если у показателя есть champion-строки, выбирается среди них (champion важнее среднего); иначе среди всех. Для `Rs` и `HI` лучше меньшее, для остальных — большее. Ничья — по `pid`, затем `result_id`. Показатели без подходящих строк не входят. `pid` — `RunFile.id`.

## 5. Область RAG (`domain=rag`)

Те же эндпоинты, что у `solar`. Отличаются таблицы, объёмы и несколько полей.

**Таблицы шаблона RAG** (колонки — `submission_template/schema.json` области 2, значения строками как в CSV):
`publications`, `configurations`, `datasets`, `tasks`, `experiments`, `results`, `issues`. Вместо materials/devices/layers/measurements здесь конфигурации RAG-систем, наборы данных, задачи и эксперименты.
- Закрытые словари: `in_scope`, `result_origin`, `extraction_status`; рекомендуемый — `metric_direction` (`higher_is_better` / `lower_is_better` / `target`).
- Остальные поля (`metric_group`, `metric_name`, `task_type`, `check_name`…) — свободный текст. Тексты в строках RAG — на английском, как в эталоне.
- **Объём:** в одной статье бывает до ~28 тыс. строк `results` (значения с графиков) — строки только постранично.

**`POST /runs`** — `domain=rag`. В `counts` разбора добавляется ключ `ocr`.

**`GET /papers/rag/{pid}?run=`** — по умолчанию **сводка**, без строк:
```json
{ "publication": { PublicationRow },
  "counts": { "publications": 1, "configurations": 18, "datasets": 14, "tasks": 8, "experiments": 12, "results": 619, "issues": 20 },
  "metrics": [ { "metric_name": "EM", "n": 120 }, … ],
  "issues": [ IssueRow, … ],
  "paper_status": "has_data",
  "meta": { "file_status": "done", "run_ids": ["run_…"], "pdf_pages": 13, "is_scan": false } }
```
**Строки таблиц** — тем же запросом с `?tables=configurations,results&limit=100&cursor=`:
```json
{ "tables": { "configurations": { "data": [ ConfigurationRow, … ], "total": 18, "next_cursor": null },
              "results":        { "data": [ ResultRow, … ],        "total": 619, "next_cursor": "…" } } }
```
`limit` 1..1000; курсор свой у каждой таблицы — передавай `next_cursor` нужной таблицы; неизвестная таблица → `400 validation_error`.

**`GET /results?run=`** для `rag`, фильтры:
- `pid` (повт.), `metric` или `metric_name` (повт.), `config_id`, `dataset_id`, `experiment_id`, `task_id`;
- `duplicates`, `sort` (`publication_id` | `metric` | `value` | `page`), `dir`, `limit` (до 1000), `cursor`;
- `scored=true` для `rag` не действует (оцениваемого списка метрик нет).

Ответ `{ "data": [ { "row": ResultRow, "paper_title": "…", "meta": { "quote_found": true, "issue_ids": [] } } ], "total": N, "next_cursor": … }` — без `device_label`.

**`GET /runs/{id}/summary`** для `rag`: `coverage` — топ-20 метрик по числу статей (`[{metric, n, total, results}]`); `scored_results` = `results`; в `papers_in_scope` входят и статьи со статусом `ocr`.

**`GET /runs/{id}/submission.zip`** — 7 CSV RAG + `PASSPORT.md` + `passport.json`. `?check=true` — проверка скриптом области 2: `X-Check-Status: ok | warnings` (у RAG «нужна проверка цитат», например на сканах, — это `warnings`, не ошибка), ошибки скрипта → `422 submission_invalid`.

**Ещё на будущее** (в работе у модели, в API ничего не меняется): значения с векторных графиков извлекаются из геометрии PDF, с растровых — моделью со зрением (`note` = «approximate, digitized…»); если рисунок не удалось оцифровать — замечание `figure_only`.

## 6. Что заморожено (на этих экранах пока мок)

Вернёмся к ним после того, как пришлёшь обновлённую спеку фронта:
- правки человека: `PATCH …/review`, `reviews/batch`, undo/история, влияние правок на выгрузку;
- управление разбором: `PATCH/DELETE /runs/{id}`, `restart`, `stop`, догрузка `POST /runs/{id}/files`, демо-разбор dev;
- SSE `/runs/{id}/events` (пока — опрос);
- «Данные» по всем разборам: `/results` без `run`, язык запросов (`q`), `group_by`, `/results/summary`, `/compare`, «лучшее значение»;
- состояния замечаний, `/issues/stats`;
- `dataset.json`, `dataset.xlsx`, `/exports`, `/passport`;
- просмотр OCR-слоя страниц (`/papers/{domain}/{pid}/ocr`);
- чат разбора, граф связей, проекты, словари, поиск.

Если какой-то из этих пунктов нужен раньше — скажи, переставим.
