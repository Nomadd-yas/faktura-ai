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
| `issues_open` | int | открытые замечания с учётом правок (§4f) |
| `progress`, `eta_seconds`, `started_at` | | см. §4b |
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
| `open_issue_count` | int | открытые замечания файла с учётом правок (§4f) |
| `reason` | string \| null | |
| `size`, `sha256` | | |
| `suggested_domain` | `"rag"` \| `"solar"` \| `null` | подсказка «статья из другой области»: заполнена только при `status: "out"`, если сервис разбора счёл статью принадлежащей другой области (§4c); иначе `null`; только в полном `files` |
| `ocr_confidence` | number 0..1 \| `null` | надёжность текста файла по оценке сервиса разбора (§5, «ocr_confidence»); `null` — сервис не сообщил (старые разборы); для статуса `ocr` — всегда число, если сервис его прислал |

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
В списке `files` короткие: `id, name, status, has_data, results, issues, open_issue_count, reason, stage, progress, eta_seconds, pages, started_at`.

### `GET /runs/{id}` — разбор целиком, `files` полные. `404 not_found`.

**Прогресс:** поток событий SSE `GET /runs/{id}/events` (§4e) или опрос `GET /runs/{id}` раз в 2 с, пока `status` = `queued`/`running`.

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

Все запросы — только для своих разборов: чужой или удалённый разбор → `404 not_found`. У Run есть поле `pinned` (bool, по умолчанию `false`); у RunFile — `suggested_domain` (`"rag"` \| `"solar"` \| `null`).

**`suggested_domain`.** Когда статья вне запрошенной области (`status: "out"`), сервис разбора может подсказать, что она, вероятно, из другой: RAG-статья в разборе `solar` → `"rag"`, статья про тонкоплёночные солнечные элементы в разборе `rag` → `"solar"`. Модель заполняет подсказку только при `in_scope=false` и только если уверена, иначе `null`. Подсказка живёт в метаданных ответа сервиса (в строку `publications` она не попадает: колонки шаблона фиксированы), бэк сохраняет её в файле разбора и отдаёт здесь, а также в `GET /papers/{domain}/{pid}` → `meta.suggested_domain`. Для любого статуса кроме `out` — `null`. Предлагаемый сценарий интерфейса: кнопка «Разобрать как rag/solar» → `POST /runs/{id}/rerun-as` с этим доменом.

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
- `GET /history?run=&limit=50` → `{ "data": [ { "id", "action": "review|batch|issue|fix|reset", "label", "run_id", "undone", "at", "changes" } ] }`, новые сверху.
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

## 4f-1. Исправления у замечаний

Кнопка «Исправить» у замечания: бэк детерминированно (без LLM) предлагает конкретное исправление, пользователь применяет его одним кликом. Применение идёт через те же правки v2 (§4f): исправленные значения, журнал, undo, автозакрытие. Чужой разбор → `404`; разбор не готов → `409`; нет замечания → `404`.

### `GET /runs/{id}/papers/{domain}/{pid}/issues/{iid}/fix`
```json
{
  "issue_id": "I3", "check_name": "jv_consistency", "state": "open",
  "suggestions": [
    { "rid": "R004", "metric": "FF", "current": 780.0, "suggested": 78.0, "unit": "%",
      "reason": "FF, согласующийся с КПД 18.9 %, Voc и Jsc: 78.1 %; текущее значение отличается сдвигом запятой (×0.1)",
      "method": "rule", "confidence": "high" }
  ],
  "alternatives": [
    { "title": "Исправить PCE", "confidence": "medium", "items": [ { "rid": "R001", "metric": "PCE", "current": 18.9, "suggested": 188.0, "unit": "%", "reason": "…", "method": "rule", "confidence": "medium" } ] }
  ],
  "resolution": null,
  "note": null
}
```
- `suggestions` — основной вариант; все его элементы применяются **вместе** (например, обмен значений champion/mean — два элемента). Элемент либо с числом (`suggested`), либо без числа: `{ rid, metric, current, action: "reject" | "note", reason, … }` (`reject` — отклонить строку, `note` — отметить проверенной без изменения значения).
- `alternatives` — другие варианты, когда неясно, какое число ошибочно (jv_consistency: ошибочны PCE, FF, Jsc или Voc; contradiction / eqe_consistency: исправить одно из двух). Основным выбран вариант, где правка похожа на опечатку (сдвиг запятой ×10^n); `confidence: "high"` — именно такой случай.
- `resolution` — `{ "issue_state": "ok", "reason": "…" }`, когда числового исправления нет (`ambiguity`, `measurement_conditions`, `missing_conditions`, `comparability`, `plausibility`, `rounding`, `figure_only` и др.) или его не удалось определить: предложить закрыть замечание как проверенное.
- Правила: `unit_inconsistency`, `range`, `voc_limit`, `metric_scale` (rag) — масштаб (доля ↔ %, мВ → В, А/см² и мкА/см² → мА/см²); `jv_consistency` — пересчёт по Voc·Jsc·FF/Pin; `champion_vs_mean` — обмен значений; `derived_values` — `reject`; `recall_k` — обмен значений. Неизвестная проверка → пустые `suggestions` и `alternatives`, `resolution: null`.
- Значения берутся с учётом уже сделанных правок.

### `POST /runs/{id}/papers/{domain}/{pid}/issues/{iid}/fix/apply`
```json
{ "items": [ { "rid": "R004", "suggested": 78.0 }, { "rid": "R009", "action": "reject" } ], "issue_state": "fixed" }
```
`issue_state`: `fixed` (по умолчанию) или `ok`. Элементы можно брать из `suggestions` или из выбранной альтернативы. Всё применяется **одним действием журнала**: отмена `POST /actions/{action_id}/undo` возвращает и значения, и состояние замечания.
→ `200`, тело как у `GET /runs/{id}/papers/{domain}/{pid}/review` (§4f) + `action_id`. Пустой `items` допустим только с `issue_state: "ok"` (закрыть без правки).
- `400 validation_error` с `field: "items"`: `rid` не из этой статьи; в элементе не одно из `suggested` / `action`; `suggested` не число (бесконечность, NaN); пустой `items` при `fixed`.

### `GET /runs/{id}/fixes?domain=&pid=`
Предложения по всем открытым замечаниям статьи (для «Исправить всё»): `{ "data": [ { issue_id, check_name, state, suggestions, alternatives, resolution, note } ] }`; замечания без предложений не входят.

### `fix_available`
В `GET /runs/{id}/papers/{domain}/{pid}/review` у каждого элемента `issues` и в ответе `…/fix/apply` есть `fix_available: bool` (открытое замечание, по которому есть предложение или `resolution`) — по нему показывать кнопку «Исправить».

### Как использовать во фронте
1. Кнопка «Исправить» (если `fix_available`) → `GET …/fix`.
2. Показать для каждого элемента `suggestions`: `current → suggested unit` и `reason`; для `action` — «Отклонить строку» / «Отметить проверенной». Если есть `alternatives` — выбор варианта. Если только `resolution` — кнопка «Закрыть как проверенное».
3. «Применить» → `POST …/fix/apply`; обновить статью по ответу (он содержит новые `reviews`, `issues`, `open_issue_count`).
4. «Отменить» → `POST /actions/{action_id}/undo` (§4f).

## 4g. Экспорт: JSON, Excel, набор по всем разборам

Для экранов «Экспорт» и «Настройки». Все три - только для владельца (чужой разбор `404 not_found`), идущий разбор - `409 run_in_progress`, `?partial=true` - выгрузить уже готовые статьи. Правки v2 (§4f) применяются так же, как в `submission.zip`: отклонённые строки не выгружаются, исправленные значения подставлены, замечания `ok` исключены. Заголовки `X-Export-Tables|Results|Papers|Filename` те же, что у `submission.zip`.

### `GET /runs/{id}/dataset.json`
`application/json`, `Content-Disposition: attachment; filename="submission-{run_id}.json"` (у rag - `submission-rag-{run_id}.json`, как «Один файл JSON» на фронте). Тело: `{ "meta": {...}, "publications": [...], ..., "issues": [...] }` - все 7 таблиц области, ключи объектов - колонки шаблона в порядке схемы.
- `meta`: `{ "generated_by": "Фактура AI", "tables": { "<таблица>": <число строк> } }`; у rag между ними `"area": "rag"`.
- Пустая ячейка - `null`. Числовые колонки - числом (solar: `year layer_order active_area bandgap_value metric_value normalized_value source_page spread_value n_devices`; rag: `year source_page metric_value normalized_value baseline_value metric_k top_k chunk_size chunk_overlap latency_ms tokens_per_query sample_size`), нечисловое значение остаётся строкой.
- Булевы: solar `in_scope mask_used certified`, rag `in_scope`. Списки (разделитель `;`) - массивом строк: `record_ids`, solar `measurements.device_id`, rag `authors`.

### `GET /runs/{id}/dataset.xlsx` - «Только значения, одним листом»
`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, `filename="results-{run_id}.xlsx"` (rag - `results-rag-{run_id}.xlsx`). Книга с одним листом «Факты»: таблица `results` (первая строка - имена колонок шаблона, дальше значения). Числа записаны числами (запятая в десятичных заменяется точкой, число с ведущим нулём, например `007`, остаётся текстом), остальное - текстом, пустые ячейки не пишутся. Писатель минимальный, на `zipfile` (как `toXlsx` фронта), новых зависимостей нет.

### `GET /submission.zip?domain=solar|rag[&check=true][&partial=true]` - набор по всем разборам
ZIP той же структуры, что `/runs/{id}/submission.zip` (7 CSV + `PASSPORT.md` + `passport.json`, `check_report.txt` и `X-Check-Status` при `check=true`, `422 submission_invalid` при ошибках проверки), `filename*=UTF-8''Набор для сдачи — все разборы.zip`. Берутся все разборы текущего пользователя этой области.
- Статья с одним `publication_id` в нескольких разборах входит один раз - из самого свежего разбора, где она разобрана (его правки учитываются). Упавшая статья solar даёт строку `publications` только с id, и лишь если нигде не разобрана.
- Паспорт - по области; версии модели/правил/словаря берутся по разобранным статьям всех разборов (свежие первыми).
- Нет ни одного разбора области - `400 no_files`; неизвестная область - `422 domain_not_supported`. Разборы есть, но разобранных статей нет - валидный ZIP с пустыми таблицами.

## 4h. Словарь нормализации (экран «Настройки → Словарь нормализации»)

Словарь = встроенное (`norm/data/*.json`, параметры и единицы коллекций) + **свои синонимы рабочей области** (общие для всех её пользователей, хранятся на сервере, а не в `localStorage`). Все ответы - обычные (без `X-Faktura-Stub`), изменения несут `action_id` / `X-Action-Id` и пишутся в журнал действий (`dictionary.synonym_add` / `dictionary.synonym_remove`, не отменяются).

### `GET /dictionary?collection=solar|rag` (по умолчанию `solar`; другая - `400 validation_error`)
```json
{ "collection": "solar", "version": "solar-2025.1+u3f9a12bc",
  "params":     { "pce": [ {"text": "КПД", "custom": false}, {"text": "PCE", "custom": false},
                           {"id": "syn_…", "text": "Эффективность ячейки", "custom": true} ], "voc": […] },
  "units":      [ {"param": "voc", "label": "Voc", "unit": "mV", "canon": "V", "factor": 0.001, "is_canon": false, "custom": false} ],
  "materials":  [ {"id": "ch3nh3pbi3", "collection": "solar", "canon": "CH3NH3PbI3", "name": "CH3NH3PbI3",
                   "synonyms": [ {"text": "mapbi3", "custom": false} ]} ],
  "conditions": [ {"id": "am15g", "canon": "AM1.5G", "name": "Стандартный спектр AM1.5G (глобальное излучение)",
                   "synonyms": [ {"text": "AM 1.5G", "custom": false} ]} ] }
```
Форма - как `dictionary` во фронте (`dictionary.ts`): `params` - словарь «ключ параметра → синонимы», `Synonym = {text, custom}`; у своих синонимов есть ещё `id`. Ключи параметров - те же, что в `GET /collections`. Соответствие данным сервера:
- **params**: встроенные написания = подпись параметра, `metric_name`, синонимы профиля и словаря метрик (`metrics.json`). У параметров без метрики (`bandgap`, `thickness`, `area`, `conditions`, rag-параметры) встроенных написаний только подпись.
- **units**: из `collection_config` (`units` параметра, множитель к канонической единице); текстовые параметры пропущены. Своих единиц нет: `POST /dictionary/units` пока заглушка.
- **materials** (только solar): по записи на каноническую формулу из `materials.json` (псевдонимы + известные формулы); `id` - формула строчными без знаков (`ch3nh3pbi3`), `canon` - формула как в таблицах (ASCII, без подстрочных индексов), `name` = `canon` (человеческих расшифровок на сервере нет), встроенные синонимы - ключи псевдонимов (`mapbi3`, `cigs`).
- **conditions** (только solar): спектры освещения из `spectra.json` (`am0`, `am15d`, `am15g`); в файле лежат регулярные выражения, поэтому встроенные написания - короткий список читаемых образцов. Условий вида «MPP-трекинг» на сервере нет.
- **rag**: `params` и `units` из конфигурации rag, `materials` и `conditions` - пустые списки.
- `version` - версия словаря нормализатора; при наличии своих синонимов к ней добавляется `+u<8 hex>` (хеш набора своих синонимов рабочей области, не зависит от порядка и `id`).

### Свои синонимы
- `POST /dictionary/params/{collection}/{param}/synonyms` `{"text"}` → `201 {synonym, renormalize, action_id}`. `synonym = {id, text, custom: true, collection, param, created_at}`. `404 not_found` - нет параметра, `400 validation_error` (`field: text`) - пусто, не строка, длиннее 200 символов (пробелы внутри схлопываются).
- `POST /dictionary/entries/{entry_id}/synonyms` `{"text"}` - то же для материала или условия (`id` из `GET /dictionary`; в `synonym` добавлено `entry_id`). `404 not_found` - нет записи.
- **Дубликаты - `409 synonym_exists`** (не идемпотентно), без учёта регистра: написание уже есть у любого параметра коллекции (для записей - у любого материала или условия) или уже добавлено в рабочей области. В пределах коллекции своё написание привязано ровно к одной цели.
- `DELETE /dictionary/params/{collection}/{param}/synonyms/{text}` и `DELETE /dictionary/entries/{entry_id}/synonyms/{text}` (`text` - в URL-кодировке, без учёта регистра) → `200 {removed, renormalize, action_id}`. Встроенное написание - **`409 synonym_builtin`** («Встроенное написание удалить нельзя - удаляются только свои»); нет такого своего - `404 not_found`.
- `renormalize` - всегда `{"papers": 0, "job_id": null}`: автоматического пересчёта нет.

### Что делают свои синонимы
Применяются при нормализации **новых разборов и перезапусков** (`POST /runs`, `/runs/{id}/restart`, «догрузить файлы», «разобрать как…») в рабочей области владельца разбора; уже готовые разборы сами не меняются, пока их не перезапустят. Действуют так же, как встроенные (без учёта регистра, пробелов и знаков; для метрик ещё и целым словом внутри подписи):
- синоним **параметра с метрикой** (`pce`, `voc`, `jsc`, `ff`, `rs`, `rsh`, `hi`, `jsc_eqe`, `stabilized_pce`, `t80`, `retained_pce`) - сырое `metric_name` из ответа модели заменяется канонической метрикой и значение пересчитывается по её единицам;
- синоним **материала** - даёт `formula_normalized`, если модель её не заполнила;
- синоним **условия** - значение `spectrum` (`AM1.5G`…);
- rag: синоним параметра с метрикой (`r5`, `r10`, `mrr`) заменяет `metric_name` в `results` до проверок.
Синонимы остальных параметров (`bandgap`, `thickness`, `area`, `conditions`, `chunk`, `retriever`) сохраняются и показываются, но на разбор не влияют.

`versions.dictionary` в паспорте (§4d) берётся из версии, с которой нормализованы статьи набора: у разбора, запущенного со своими синонимами, это `solar-2025.1+u<hash>`, без них - прежнее `solar-2025.1`. Паспорт и `passport.json` в `submission.zip` по-прежнему байт-в-байт равны.

## 4i. Вопросы по разбору

Чат разбора: вопрос по-русски -> ответ в том же формате `CaseAnswer`, который рисует фронт (`src/case/ask.ts`: `text`, `cards`, `cites`, `suggestions`). Числа берутся только из строк разбора (с правками v2: отклонённые строки не учитываются, исправленные значения подставлены; замечания — только открытые). Только владелец разбора, чужой или неизвестный — `404 not_found`.

### `POST /runs/{id}/ask`
```json
{ "question": "Лучший КПД перовскитов со слоем SnO2", "pids": ["D001", "D002"], "pid": "D001" }
```
`question` — до 500 символов (иначе `422 validation_error`), `pids` — необязательно: ограничить вопрос этими статьями (`RunFile.id`). `pid` — необязательно: статья, которую сейчас открыл пользователь (фронт шлёт его на странице статьи); агент получает в подсказке «пользователь смотрит статью X (название …), „эта статья“ = X». Если `pid` не принадлежит разбору — `400 validation_error` с `field: "pid"` (так выбрано вместо молчаливого игнорирования: фронт сразу видит ошибку; пустая строка = контекста нет).

**Ответ даёт агент (§4j), когда он включён** (то же условие, что у `/messages`: `FAKTURA_AGENT_MODE != off` и ML настроен, в разборе есть данные). Агент читает и таблицы, и полный текст статей, поэтому отвечает и на «о чём статья», «объясни метод», «выводы», и на вопросы по таблицам; форма ответа прежняя — `CaseAnswer`. **История ведётся (§4k):** вопрос и ответ `/ask` записываются в чат разбора — без `thread_id` в текущий (последний), `"thread_id": "new"` — в новый, `"thread_id": "thr_…"` — в выбранный; модель видит прошлые обмены этого чата (до 6), поэтому работают уточнения и «а для Voc?». К ответу добавлены `thread_id`, `message_id` (для оценки `PUT …/messages/{mid}/rating`), `question_id`. Пока в этом чате пишется ответ (`/messages`) — `409 turn_busy`; чужой или удалённый чат — `404`. Агент выключен, недоступен или проверка отбросила больше половины пунктов — отвечает прежний базовый планировщик (ниже), `meta.planner = "base"`/`rules`/`llm`.

Сноски агента отдаются в форме `CaseCite` фронта: `{n, pid, rid?, iid?, table?, row?, page? (строкой), label, domain?: "rag", fragment?, chunk_id?, calc_id?, expr?}`: значение таблицы -> `{pid, rid, page, label: "D001 · строка R017 · стр. 5"}`; фрагмент текста или страница -> `{pid, page, label: "D001 · стр. 1 · «цитата»", fragment}` (`rid` нет — фронт открывает страницу); замечание -> `iid`; другая таблица -> `table`+`row`; агрегат -> первая строка-источник (`pid`, `rid`) и `label` с описанием агрегата; расчёт -> `label: "Расчёт: формула = значение; входы"`, `calc_id`, `expr`. Карточки — как в §4j (`results`, `issues`, `compare` с `title` и `metrics: [metric]`, `fix`). Уточнение: `text` — вопрос, `suggestions` — варианты, `clarify: true`. Лимит — 30 вопросов в минуту на пользователя (`429 too_many_requests`). Разбор без данных — не ошибка: `200` с текстом «В этом разборе пока нет данных…» и пустыми `cards` / `suggestions`.

```json
{ "text": "Лучший КПД (перовскиты, слой SnO2): 16,07 % у D001 (0.01 M SrCl2) [1]. Подходящих значений: 13 в 2 статьях.",
  "cards": [ { "kind": "results", "title": "Лучшее: КПД", "rows": [ { "pid": "D001", "rid": "R017" } ] } ],
  "cites": [ { "pid": "D001", "rid": "R017", "page": "5", "label": "D001 · 0.01 M SrCl2 · КПД 16,07 % · results · R017 · стр. 5" } ],
  "suggestions": ["Замечания у D001", "Сколько значений", "Что в таблице devices"],
  "meta": { "planner": "llm", "writer": "llm", "degraded": false, "intent": "best", "tokens_in": 3566, "tokens_out": 210,
            "cost": 0.76, "cost_currency": "RUB", "seconds": 2.4, "model": "deepseek/deepseek-v4.1-flash-1017",
            "plan": { "intent": "best", "metrics": ["PCE"], "material_class": ["perovskite"], "layers": ["sno2"] } } }
```
Карточки (`kind` как у фронта; `compare` не используется):

| intent планировщика | карточки | сноски `cites` |
|---|---|---|
| `best` (лучшее/худшее значение показателя; solar: champion важнее среднего, у `Rs` и `HI` лучше меньшее) | `results` `{title, rows:[{pid, rid}]}` — по одной лучшей строке на статью, до 5 | `{pid, rid, page, label}` |
| `results` (список значений по условиям: класс материала, слой, прибор, диапазон, агрегация, `cited`) | `results`, до 50 строк; текст говорит «Показаны первые 50 из N» | то же |
| `best` / `results`, область rag | `ragTable` `{title, cols:["Статья","Значение","Конфигурация","Датасет","Место в статье"], rows:[{cells, pid, rid, best}], note?}`; сравниваются только значения одной группы условий (задача, датасет, метрика, вариант, k, единица), в тексте — сколько ещё групп | `{pid, rid, page, label, domain:"rag"}` |
| `issues` | `issues` `{title, items:[{pid, iid}], domain?:"rag"}` — открытые замечания, до 50 | `{pid, iid, page, label}` |
| `table_rows` | `tableRows` `{title, table, cols, rows:[{pid, id, cells}], domain}` — до 50 строк, `cols` не больше 6 | `{pid, table, row, rid?/iid?, page, label}` |
| `schema` (что значит таблица/поле, какие поля обязательны) | `schema` `{title, table, fields:[{name, ru, required, desc, ref?}]}` + `tableRows` с 2–3 примерами строк | `{pid, table, row, …}` |
| `paper` | до 3 карточек `paper` / `ragPaper` `{pid}` | — |
| `rag_list` (датасеты, конфигурации, задачи, генераторы, эмбеддинги, ретриверы, реранкеры) | `ragTable` `{cols:["Название","Статей","Какие"], rows}`, до 15 строк | — |
| `count`, `unknown` | без карточек | — |

Строк в карточке не больше 50: если подходящих больше, текст это говорит, а `meta.capped = {total, shown}`. Названного кода нет в разборе или метрики нет в данных — текст-подсказка без карточек («Такой метрики в разборе нет: … Самые частые: …»).

**Как строится ответ.** (1) Планировщик (LLM на ML-сервисе) превращает вопрос в строгий JSON-запрос по краткому описанию разбора: коды статей с названиями, имена метрик со счётчиками, таблицы, названия проверок — строк результатов он не видит. Запрос проверяется: чужие коды и неизвестные метрики отбрасываются, лимит ограничен. (2) Исполнитель (бэкенд, без LLM) считает по строкам. (3) Автор текста (LLM) получает верхние строки и счётчики и шаблонный текст и пишет 1–3 предложения; если в его тексте есть число или сноска, которых нет в фактах, берётся шаблонный текст.

**`meta`** (необязательное для фронта поле): `planner` — `llm` | `llm+rules` (LLM ответил «не понял», сработали правила) | `rules`; `writer` — `llm` | `template`; `degraded` — ML настроен, но не ответил (таймаут ~30 с, не `200`): ответ собран правилами и шаблоном, ошибки нет; `tokens_in`/`tokens_out`/`cost` — суммарно по вызовам ML; **`cost` — в рублях** (RouterAI присылает `usage.cost` в RUB), `cost_currency: "RUB"` (раньше поле называлось `cost_usd` по ошибке; ML и бэк принимают оба имени, отдают только `cost`). Ответ агента: `meta = {model, provider, planner: "agent"|"base", tool_calls, tokens_in, tokens_out, cost, cost_currency, seconds, dropped, fallback?}`; `plan` — запрос планировщика после проверки.

**Без ML** (`FAKTURA_EXTRACTOR=mock` или не задан адрес) вопрос разбирает правиловый планировщик по ключевым словам (`faktura.ask.rules`), текст — шаблонный; формат ответа тот же.

**Настройки:** `FAKTURA_ASK_URL` (адрес ML; по умолчанию адрес извлечения без `/extract`, если `FAKTURA_EXTRACTOR=http`), `FAKTURA_ASK_TIMEOUT_S` (30), `FAKTURA_ASK_MODE=off` — всегда правила. ML-сервис: `POST /ask/plan` `{question, run}` -> `{plan, meta}` и `POST /ask/write` `{question, intent, facts, draft}` -> `{text, meta}`; провайдер LLM закреплён (`ASK_PROVIDER` | `ROUTERAI_PROVIDER`, без fallback), рассуждение выключено. Мок ML (`faktura.ml.mock_server`) отвечает на оба детерминированно.

### `GET /runs/{id}/ask/suggestions`
`["Лучший КПД", "Замечания у D001", "Лучший КПД перовскитов", "Сколько значений"]` — до 4 вопросов-подсказок из данных разбора (RAG: `Лучший {метрика}`, `Замечания у {pid}`, `Какие датасеты`, `Что в таблице configurations`). Пустой список, если данных нет.

## 4j. Вопросы внутри разбора: агент (§15.9)

Чат живёт только внутри разбора (глобальных `/chats` нет). Вопрос по-русски -> агент в бэкенде ходит в данные разбора инструментами и отвечает `{text, cards, cites, suggestions}`; каждое число проверяется до отправки (спека §12.4). Область — только этот разбор (подставляет бэк); чужой или неизвестный разбор — `404 not_found`. Базовый вариант (§4i, `POST /runs/{id}/ask`) остаётся запасным путём. Устройство, инструменты, промпт и оценка стоимости — `docs/design/AGENT.md`.

### `GET /runs/{id}/messages`
`{"data": [...]}` — ветка по порядку. Первые два сообщения синтезируются из разбора: `{"id":"m_files","role":"user","kind":"files","files":["D001","D002"],"at":"…"}` и `{"id":"m_run","role":"system","kind":"run","at":"…"}`; дальше пары вопрос/ответ:
```json
{ "id": "msg_x", "role": "user", "kind": "text", "text": "КПД выше 20 % у перовскитов", "at": "…" }
{ "id": "msg_y", "role": "assistant", "kind": "answer", "status": "running|done|stopped|error", "at": "…",
  "answer": { "text": "…", "cards": [], "cites": [], "suggestions": [] }, "clarify": true, "rating": "up|down|null", "rating_reason": null }
```
`answer` приходит у `done` и `stopped`; `clarify` — только у уточняющего вопроса (ниже).

### `POST /runs/{id}/messages` — вопрос
Тело `{ "text": "сравни D001 и D003", "pid": "D001" }`: `pid` — необязательно, статья, которую открыл пользователь (смысл «эта статья»; чужой — `400 validation_error`, `field: "pid"`; сохраняется в `meta.pid`). Если в разборе ровно одна статья, «статья/эта статья/она» и так означает её — агент не переспрашивает, какая. Пусто или длиннее 500 символов — `400 validation_error`; лимит чата (30 в минуту на пользователя, общий с `/ask`) — `429`; пока предыдущий ответ ещё пишется — `409 turn_busy`.
- `Accept: text/event-stream` -> поток SSE (ниже);
- иначе `202 {"message_id": "msg_y", "question_id": "msg_x", "stream_url": "/api/v1/runs/{id}/messages/msg_y/stream"}`; поток по `stream_url` воспроизводится с начала (`GET`, токен — Bearer или `?access_token=` для `EventSource`; после завершения или перезапуска — из сохранённого ответа).

Ответ пишется рабочим потоком независимо от соединения: обрыв SSE ответ не останавливает, остановка — только `stop`.

**События** (порядок: `step`* -> `text`* -> `card`* -> `cite`* -> `suggestions` -> `done`; при сбое вместо `done` — `error`):
```
event: step         data: {"text":"Разбираю вопрос"}
event: step         data: {"text":"Запрос к таблице results: metric_name = PCE, value > 15"}
event: step         data: {"text":"Проверяю сопоставимость 3 значений"}
event: step         data: {"text":"Сверяю с источниками"}
event: text         data: {"text":"Выше 15 % у перовскитов: 19,0 % [1] и 16,07 % [2].\n"}
event: card         data: {"card":{"kind":"results","title":"Значения из ответа","domain":"solar","rows":[{"pid":"D001","rid":"R047"}]}}
event: cite         data: {"cite":{"n":1,"domain":"solar","pid":"D001","result_id":"R047","issue_id":null,"page":1,"fragment":"PCE of 19.0%"}}
event: suggestions  data: {"suggestions":["Замечания у D001","Сколько значений"]}
event: done         data: {"message_id":"msg_y","status":"done","answer":{"text":"…","cards":[…],"cites":[…],"suggestions":[…]}}
event: error        data: {"code":"agent_error","message":"Не удалось ответить на вопрос","retryable":true}
```
Шаги идут по мере работы. Проверка идёт до отправки текста, поэтому `text` приходит уже проверенным, строками. `done.status = "stopped"` — ответ остановлен (`answer.text = "Ответ остановлен."`).

**Ответ** `{text, cards, cites, suggestions, meta}` + необязательные `clarify`, `fallback`, `partial`, `unverified`. `meta` теперь есть и в `done.answer`, и в сохранённом ответе: `{model, provider, planner: "agent"|"base", tool_calls, tokens_in, tokens_out, cost, cost_currency: "RUB", seconds, dropped (отброшено пунктов), fallback?}`.

Агент отвечает не только по таблицам, но и по тексту статей: «о чём статья», «объясни», «что сделали авторы», «выводы» -> `read_page(pid, 1)` + `search_text` (английские запросы) -> связный пересказ 4–8 предложений (проблема, подход, результаты, вывод). Проза без чисел маркеров не требует и проверкой не отбрасывается; ответ-пересказ может быть до ~10 предложений, ответы по таблицам — короткие.
- `cards` — как в §4i: `results` `{title, domain, rows:[{pid, rid}]}` (строки, на которые сослался ответ, или из которых посчитан агрегат/расчёт), `issues` `{items:[{pid, iid}]}`, `compare` `{title, domain, pids, metric, metrics:[metric]}` (если агент проверял сопоставимость); **новая `fix`** `{kind:"fix", title, domain, items:[{pid, rid, metric, current, suggested, unit, reason}]}` — предложенные агентом исправления значений (`current`/`suggested` — числа в единице `unit`). **Агент сам ничего не применяет.** Фронту: показать карточку с кнопкой «Применить» у каждой строки и по нажатию вызвать существующий v2 `PATCH /runs/{id}/results/{rid}/review` с `corrected_value = suggested` (тип `CaseCard` во фронте нужно дополнить вариантом `fix`); у запасного ответа — любые карточки §4i;
- `cites` — по порядку сносок `[n]` в тексте: `{n, domain, pid, result_id | issue_id | chunk_id, page, fragment}`. Агрегат: `{n, agg_id, pid: null, text: "среднее value по 37 строкам из 12 статей", result_ids: [до 50]}`; фрагмент текста: `{n, pid, chunk_id, page, fragment: цитата}`; значение другой таблицы: `{n, pid, table, row_id, page, fragment}`; страница статьи: `{n, domain, pid, result_id: null, issue_id: null, page, fragment: цитата}`; расчёт: `{n, pid, calc_id, expr, value, inputs:{имя:{fact_id, value, unit}}, fact_ids, fragment: "формула = значение; входы"}`. У запасного ответа остаются поля §4i (`label`, `table`, `row`);
- `suggestions` — до 3 вопросов, **не повторяющих заданные** в этой ветке (сравнение «D001 и D003» = «D003 и D001»); `GET /runs/{id}/suggestions` — то же правило (3–5).

**Маркеры** модели -> сноски `[n]`. Наша схема вместо `fact_id` спеки: идентификатор значения — строка `results`, адресуется парой `pid/result_id`: `[[fact:D003/R010]]`, `[[agg:a2]]` (агрегат инструмента `group`, выданный в этом же ответе), `[[chunk:ID|цитата]]` (цитата должна быть подстрокой фрагмента), `[[page:D001/3|цитата]]` (страница, прочитанная `read_page`; цитата обязательна и должна быть подстрокой текста страницы без учёта пробелов и регистра; числа из текста страницы допускаются, как у `chunk`), `[[calc:c1]]` (результат инструмента `calc` в этом же ответе; число в тексте должно совпасть со значением с точностью округления); добавлены `[[issue:D005/I03]]` (замечание) и `[[row:devices/D001/DEV1]]` (число из другой таблицы). Правила проверки §12.4: каждое число (кроме годов, номеров страниц, таблиц, рисунков, порядковых и чисел из самого вопроса) — с маркером; факт есть в области, не отклонён и совпадает с действующим значением (правки v2) с точностью округления; «0,74» и «74 %» равны только если единица строки — доля; единицы не пересчитываются. Предложения без чисел и маркеров (проза пересказа) не проверяются и не отбрасываются; откат на базовый ответ (`verification`) — если отброшено больше половины всех предложений ответа (заголовки списков не считаются). Нарушивший пункт отбрасывается (`RAG_UNVERIFIED=drop`, по умолчанию) или остаётся с пометкой «(не подтверждено)» и без сноски (`mark`; тогда в `answer.unverified` — исходные пункты).

**Уточнение (`clarify`).** Если вопрос неоднозначен (какой показатель, какая статья), агент вместо ответа вызывает `clarify`: `answer.text` — короткий вопрос, `answer.suggestions` — 2–4 варианта ответа (фронт рисует их кнопками), `answer.clarify = true`, у сообщения `clarify: true`, `cites` и `cards` пусты, в `done` тоже `clarify: true`. Следующий вопрос пользователя (в том числе нажатие варианта) идёт обычным `POST`, а прошлый обмен, включая уточняющий вопрос, уходит модели в историю.

**Запасной базовый ответ (`fallback`).** Если агент не справился — модель недоступна (`llm_unavailable`), не уложился в ~40 с (`timeout`), вернул пустое (`empty`) или проверка отбросила **больше половины** пунктов (`verification`) — бэк отвечает базовым вариантом §4i (правила + шаблон по строкам разбора), добавляет в конец `text` строку «Ответ собран по шаблонам: …» и ставит `answer.fallback`. Если агент выключен (`FAKTURA_AGENT_MODE=off`) или ML не настроен, базовый ответ идёт без пометки. Разбор без данных — базовый текст «В этом разборе пока нет данных…».

**Разбор ещё идёт (§15.9: «409 или по готовым»).** Выбран ответ по готовым статьям: `409` не отдаётся; в конец `text` добавляется «Разбор ещё идёт: ответ по N из M готовых статей.», `answer.partial = {done, total}`. Если готовых данных нет — «пока нет данных».

### `GET /runs/{id}/suggestions`
`{"data": ["КПД выше 20 % у перовскитов", …]}` — 3–5 подсказок из данных разбора без уже заданных вопросов.

### `POST /runs/{id}/messages/{mid}/stop`
Ответ `running` -> `stopped` (агент проверяет флаг между шагами: после текущего вызова модели следующий инструмент уже не запускается). Возвращает сообщение. Завершённый ответ не меняется (`200` с прежним статусом). Неизвестный `mid` — `404`.

### `PUT /runs/{id}/messages/{mid}/rating`
`{"rating":"up"|"down","reason":"Неверная цифра"|"Не тот источник"|"Неполный ответ"}`; `{"rating":null}` снимает. Причина — только при `down` (иначе `400`). Возвращает сообщение. Вопрос, ответ, сноски, вызовы инструментов, отброшенные пункты, модель, токены и стоимость (`cost`, `cost_currency = "RUB"`; колонка `cost_usd` устарела и не пишется) хранятся в таблице `run_messages` для оценки качества.

**Правила, расчёты и правки.** Инструмент `rules` отдаёт определения проверок области (`check_name`, название, описание, уровень, формула, пороги, типичные причины) из паспорта (`passport/data/*.json`) — по нему агент объясняет, почему значения не сходятся (`jv_consistency`: КПД против Voc·Jsc·FF/Pвх, порог 3 %; `unit_inconsistency`: доля против %, мВ против В; `champion_vs_mean`; `hysteresis_index`; `eqe_consistency`…). Инструмент `calc` безопасно считает арифметику (числа, имена входов, `+ - * /`, скобки; вызовы, атрибуты, степени и прочее — ошибка) над именованными входами `{имя: fact_id | число}` по действующим значениям (с правками v2): `{calc_id, value, inputs:{имя:{fact_id, value, unit}}, expr}`. Инструмент `propose_fix` формирует карточку `fix`. На вопрос «как правильно записать, чтобы значения сошлись» агент: берёт замечание/строки -> `rules` -> `calc` (ожидаемое против фактического) -> называет вероятно ошибочное значение и причину (шкала/единица, опечатка, среднее против лучшего, разные приборы) -> даёт исправленное число и карточку `fix`; если текст статьи разрешает конфликт (в таблице 0,71, в тексте 71 %) — цитирует его `[[page:…]]`; если из статьи это не восстановить — честно говорит об этом и советует оставить замечание открытым или отметить «Всё верно» с причиной.

**Настройки бэка:** `FAKTURA_AGENT_MODE=off` — без агента (всегда §4i), `FAKTURA_AGENT_TIMEOUT_S` (40), `FAKTURA_AGENT_MAX_TOOL_CALLS` (12), `RAG_UNVERIFIED=drop|mark`; адрес ML — как у `/ask` (`FAKTURA_ASK_URL` или адрес извлечения), таймаут одного вызова — `FAKTURA_ASK_TIMEOUT_S`.

**ML-сервис:** `POST /llm/chat` `{messages, tools?, max_tokens?, temperature?, reasoning?, tool_choice?}` (сообщения и tools в формате OpenAI) -> `{message:{role, content, tool_calls?}, finish_reason, meta:{model, provider, tokens_in, tokens_out, cost, currency, seconds}}`. Провайдер закреплён всегда (`provider.order` из `ASK_PROVIDER` или `ROUTERAI_PROVIDER` + `allow_fallbacks=false`; без провайдера — `503 not_configured`, запрос не уходит), рассуждение выключено (`reasoning.enabled=false`, включается полем `reasoning`), модель — `ASK_AGENT_MODEL`, иначе `ASK_MODEL`, иначе по умолчанию; повторов нет. Мок ML (`faktura.ml.mock_server`) отвечает детерминированно: ход 1 — вызов `filter` по `results`, дальше — «Первое значение …: v [[fact:pid/rid]]».

## 4k. История чатов и возобновление

У разбора может быть сколько угодно чатов; к любому можно вернуться в любой момент и продолжить — модель видит прошлые обмены именно этого чата. Чат хранится в БД (`run_threads`, сообщения — `run_messages.thread_id`), переживает перезапуск сервера и выход пользователя; удаление разбора удаляет и его чаты. Видит чаты только владелец разбора (чужой — `404`).

**Текущий чат** — последний по времени сообщения. Вопрос без `thread_id` (и в `/ask`, и в `/messages`) идёт в него; если чатов ещё нет — создаётся. Так старый фронт получает историю без изменений. Новый чат: `POST /runs/{id}/threads` или `thread_id: "new"` в вопросе.

**Имя** — первый вопрос (до 80 символов, с «…»); пока вопросов нет — `"Новый чат"`; пользователь может переименовать, после этого вопросы имя не меняют.

### `GET /runs/{id}/threads` — список (свежие сверху)
```json
{ "data": [ { "id": "thr_x", "title": "Какой лучший КПД…", "created_at": "…", "updated_at": "…",
              "questions": 3, "last_message": { "role": "assistant", "text": "Лучший КПД 19,0 % [1]…", "at": "…" },
              "running_message_id": null } ] }
```
`running_message_id` — ответ ещё пишется (можно показать «пишется…» в списке). Пустой разбор — `{"data": []}`.

### `POST /runs/{id}/threads` — новый чат
Тело `{ "title": "Сравнение КПД" }` или пустое. `201` + чат (как в списке, `questions: 0`). Название длиннее 200 символов — `400`, `field: "title"`.

### `GET /runs/{id}/threads/{tid}` — открыть чат (возобновление)
Чат из списка + `messages` (как `GET /runs/{id}/messages`: сначала синтезированные `files` и `run`, потом вопросы и ответы) + `stream_url`. Если ответ ещё пишется (пользователь закрыл вкладку посреди ответа и вернулся) — `running_message_id` и `stream_url`: подключиться к нему (`EventSource`, `?access_token=`) и дочитать поток с начала; ответ пишется на сервере независимо от соединения. Иначе `stream_url: null`.

У сообщения-ответа есть `thread_id` и `source`: `"ask"` — ответ `/ask`, `answer` в форме `CaseAnswer` (§4i, сноски с `label`); `"messages"` — ответ `/messages` (§4j). Фронт рисует историю тем же компонентом, что и свежий ответ этого эндпоинта.

### `PATCH /runs/{id}/threads/{tid}` — переименовать
`{ "title": "Моё" }` -> чат. Пустое или длиннее 200 — `400`, `field: "title"`.

### `DELETE /runs/{id}/threads/{tid}` — удалить
`204`; идущий ответ останавливается, сообщения чата удаляются. Повторно — `404`. После удаления текущим становится следующий по времени чат.

### Чат в существующих эндпоинтах
- `POST /runs/{id}/ask` — `thread_id?` в теле, в ответе `thread_id`, `message_id`, `question_id` (§4i);
- `POST /runs/{id}/messages` — `thread_id?` в теле; `202` содержит `thread_id`, у SSE он в заголовке `X-Thread-Id` (открыт в CORS);
- `GET /runs/{id}/messages?thread=tid` — сообщения чата (без параметра — текущего), в ответе `thread_id` (`null`, если чатов нет);
- `GET /runs/{id}/suggestions?thread=tid` — подсказки без вопросов этого чата;
- `409 turn_busy` — только если ответ пишется **в этом же** чате; в другом чате можно спрашивать параллельно.

Сообщения, записанные до появления чатов, при первом обращении собираются в один чат с именем по первому вопросу.

**Фронту:** боковая панель «История» = `GET /threads`; клик — `GET /threads/{tid}` (+ подключение к `stream_url`, если не `null`); «Новый чат» — `POST /threads` (или просто `thread_id: "new"` в первом вопросе); дальше каждый вопрос с `thread_id` открытого чата; переименование и удаление — `PATCH` / `DELETE`. Открыть разбор «как был» — `GET /threads` и взять первый.

## 4l. Внешние источники (OpenAlex)

Агент умеет выйти за пределы разбора и найти **научные работы из OpenAlex**: связанные с статьёй, цитирующие её, саму статью (DOI, ссылки, число цитирований) или работы по запросу. Срабатывает, когда пользователь спрашивает о других / похожих работах, литературе, «кто цитирует статью», «источники», «что ещё почитать». Работы **не входят в данные разбора** и не проверяются по ним: модель называет только работы, которые вернул инструмент `external_sources` (выдуманных нет — маркер `[[src:Sn]]` на неизвестную работу отбрасывает пункт); из чисел в тексте допустимы только год и число цитирований этой работы. Текст ответа остаётся чистым: список работ фронт рисует из поля `sources`, а подробности — на отдельной странице «Источники».

**Поле ответа.** У `answer` (SSE `done`, `GET /messages`, ответ `POST /ask`) есть `sources` — работы ответа в порядке упоминания (`[]`, если не искали). В тексте на работу ссылается обычная сноска `[n]`, у неё в `cites` — `src_id`, `openalex_id`, `url`, `label: "Источник: <название> (<год>)"`.
```json
"sources": [ { "id": "S1", "openalex_id": "W2741809807", "title": "Related paper", "authors": ["A. Author", "B. Author", "C. Author"],
               "year": 2021, "venue": "Nature Energy", "doi": "10.1038/xxxx", "url": "https://doi.org/10.1038/xxxx",
               "oa_url": "https://example.org/paper.pdf", "cited_by_count": 120, "relation": "related",
               "abstract": "Короткая аннотация (до 400 символов)…" } ]
```
`relation`: `this` (сама статья), `related` (связанные), `cites` (цитируют статью; самые цитируемые первыми), `search` (поиск по запросу). `id` (`S1…`) действует внутри одного ответа. `doi`, `venue`, `oa_url`, `abstract` могут отсутствовать/быть `null`; `url` — ссылка на DOI, иначе на страницу OpenAlex.

**SSE.** Новое событие `event: sources`, `data: {"sources":[…]}` — после `cite`, перед `suggestions`; приходит только если у ответа есть работы. Те же `sources` лежат в `done.answer`.

### `GET /runs/{id}/messages/{mid}/sources` — источники одного ответа
`{ "message_id": "msg_x", "question": "какие есть похожие статьи?", "sources": [ … ], "generated_at": "…" }`. `sources` — как в ответе; нет работ — `[]`. Чужой разбор или не ответ — `404`.

### `GET /runs/{id}/sources` — источники всех чатов разбора или одного чата
`{ "thread_id": null, "sources": [ { …работа без id…, "cited_in": [ { "message_id": "msg_x", "question": "…", "thread_id": "thr_x" } ] } ], "generated_at": "…" }`.
- без параметра или `?thread=all` — работы из **всех** чатов разбора, `thread_id: null`;
- `?thread=tid` — только этого чата, `thread_id: "tid"`.
Повторы по `openalex_id` слиты, `cited_in` — в каких вопросах (и каких чатах) встретилась работа. Чатов нет — `sources: []`. Чужой разбор — `404`.

### Настройки (сервер, `.env`)
| Переменная | По умолчанию | Смысл |
|---|---|---|
| `FAKTURA_OPENALEX_ENABLED` | `true` | `false` — инструмент не предлагается модели, `sources` всегда `[]` |
| `FAKTURA_OPENALEX_KEY` | нет | ключ OpenAlex (необязателен, без него лимит ниже); админ кладёт его в `.env` сервера, в репозиторий и во фронт он не попадает |
| `FAKTURA_OPENALEX_MAILTO` | нет | почта для «вежливого пула» OpenAlex; по умолчанию не отправляется |

В OpenAlex уходят только название и DOI статьи и поисковый запрос; таймаут 8 с, ответы кэшируются на час. Если OpenAlex недоступен, агент честно говорит, что внешние источники сейчас недоступны.

**Фронту:** под ответом с непустым `sources` — компактный список (название, авторы, год, ссылка) и кнопка «Источники» → отдельная страница: для одного ответа `GET /runs/{id}/messages/{mid}/sources`, для всего чата `GET /runs/{id}/sources?thread=tid`; карточка работы — название, авторы, год, журнал, `cited_by_count`, ссылки `url` / `oa_url`, аннотация, «встречается в ответах» (`cited_in`). Блок и кнопку показывать только при непустом `sources`.

## 4m. Цитирования статей разбора (вкладка «Цитирования» в «Данных»)

### `GET /runs/{id}/citations`
```json
{ "data": [ { "pid": "D003", "sources": [
    { "openalex_id": "W123", "title": "…", "authors": ["…"], "year": 2024, "venue": "…", "doi": "10.…", "url": "https://doi.org/10.…",
      "oa_url": null, "cited_by_count": 42, "relation": "this", "abstract": "…", "pid": "D003" },
    { "…": "…", "relation": "cites" }, { "…": "…", "relation": "related" } ] } ],
  "generated_at": "…", "warning": null }
```
- Для каждой разобранной статьи — поиск в OpenAlex по `DOI` из `publications`, без DOI — по названию (+ год). В `sources`: сама статья (`relation: "this"`, с `cited_by_count`), до 10 самых цитируемых работ, которые её цитируют (`"cites"`), до 10 связанных из `related_works` (`"related"`). Формат работы — как `sources` в ответах агента (§4l), плюс `pid`. Статья не найдена в OpenAlex — `sources: []`.
- Пропускаются: статьи не по теме (`out`), без данных, с `in_scope = false`, без DOI и без названия.
- Разбор ещё идёт — `200` по уже готовым статьям. Чужой разбор — `404`.
- OpenAlex недоступен (или ограничил запросы) — `200`, у затронутых статей `sources: []`, `"warning": "Научные базы сейчас недоступны"`.
- Кэш на сервере по DOI (без DOI — по названию) на сутки: повторное открытие вкладки — без запросов в OpenAlex. Таймаут запроса — как у агента (8 с); статьи запрашиваются параллельно (6 одновременно), первое открытие разбора на 90 статей — до ~30 с.
- В набор для сдачи и паспорт ничего из этого не попадает.

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
  "meta": { "file_status": "done", "run_ids": ["run_…"], "pdf_pages": 13, "is_scan": false,
            "ocr_confidence": 0.93, "suggested_domain": null } }
```
**`ocr_confidence`** (`RunFile.ocr_confidence`, `meta.ocr_confidence` статьи) — число 0..1: насколько можно доверять тексту, из которого извлечены данные. Считает сервис разбора как среднее по страницам: страница из текстового слоя PDF — 1.0 (слой хороший), 0.5 (плохой), 0.2 (нет); страница, распознанная OCR, — собственная оценка распознавателя, если он её отдаёт, иначе эвристика `0.9 × (1 − min(1, 5 × доля «мусорных» символов))`, пустая страница 0.5. Для PDF-скана значение низкое, для обычной статьи ≈ 1.0. `null` — сервис не сообщил. Значение показывается для статуса `ocr` («скан, распознано OCR») и вообще везде, где оно есть.

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

## 6. Что ещё не сделано

Сделано с 21.11 и больше не заморожено: правки человека, состояния замечаний, журнал и отмена, `/issues/stats`, «лучшее значение» в сводке (§4f); управление разбором и догрузка файлов (§4c, §4f); SSE (§4e); паспорт (§4d); экспорт JSON/Excel и набор по всем разборам (§4g); словарь нормализации (§4h); вопросы по разбору (§4i); история чатов и возобновление (§4k).

Пока не сделано (во фронте выключено или не используется):
- проекты, поиск, своя единица `POST /dictionary/units` — эндпоинты отвечают заглушкой с заголовком `X-Faktura-Stub: 1`;
- `/exports` (общий механизм фоновых выгрузок; нужные форматы уже есть — §4g).

Не нужно фронту (по его коду на 21.11): демо-разбор dev (маршрут убран); «Данные» по всем разборам (`/results` без `run`, `q`, `group_by`, `/results/summary`) — экран собирает данные сам из статей разборов; просмотр OCR-слоя страниц (`/papers/{domain}/{pid}/ocr`) — во фронте его нет.

Вычеркнуто фронтом 21.11 (экраны «Сравнение» и «Связи» убраны, сопоставимость закрыта паспортом и полями CSV): `/compare`, `/graph`, `/papers/{domain}/{pid}/related`, канон названий материалов и моделей для склейки связей. `best_values` в сводке (§4f) остаётся как необязательное поле.

Если какой-то из этих пунктов нужен раньше — скажи, переставим.
