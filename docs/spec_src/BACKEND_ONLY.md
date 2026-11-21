# Фактура AI — спецификация бэкенда (только бэк)

Документ для бэкенд-разработчика. Здесь только то, что делает сервер: API, хранение, конвейер, нормализация, проверки, выгрузка, безопасность.

**Не входит:**
- как модель извлекает значения из PDF (для бэка это внешний сервис-«чёрный ящик», §4.3);
- интерфейс и вёрстка;
- чат, граф, проекты.

**Главное:** результат продукта — **7 CSV формата сдачи** (`docs/case5/submission_template`). Их проверяет `check_submission.py`.
- Значение = строка `results.csv`.
- Замечание = строка `issues.csv`.
- Всё, что отдаёт API, — это строки этих таблиц. Что видит пользователь, то и уходит в сдачу.

Связанные документы:
- `docs/CORE_API.md` — текущий срез API v1, согласованный с бэком;
- `docs/BACKEND_SPEC.md` и `docs/spec/*` — полная спецификация продукта со всеми ролями.

Если они расходятся с этим документом, для бэка верен этот документ.

---

## 0. Этапы

| Этап | Что | Статус |
|---|---|---|
| **v1 — обязательно** | вход, разборы (загрузка → прогон → статусы), 7 таблиц статьи, PDF, `/results`, `/issues`, сводка, ZIP для сдачи (+ паспорт) | делается, фронт уже подключён (`VITE_DATA_SOURCE=api`) |
| **Новое 21.11** | выбор области человеком, `rerun-as`, PDF и `/papers` для `out`, процент и время, управление разбором, паспорт с сервера — §7а | ждём бэк |
| **v2 — следующее** | правки значений, состояния замечаний, журнал и отмена, переименовать / удалить / перезапустить / остановить разбор, SSE | заморожено, во фронте показано «В разработке» |
| **Область RAG** | `domain=rag`, OCR сканов, строки постранично — по `docs/CORE_API.md` §5 | сделано на сервере 21.11, фронт подключён |

---

## 1. Соглашения

| | |
|---|---|
| База | `/api/v1`. Фронт в разработке ходит через прокси Vite: `http://localhost:5200/api` → `http://127.0.0.1:8000/api` |
| Формат | JSON, UTF-8, `snake_case`, даты ISO 8601 UTC (`2026-11-20T09:00:00Z`) |
| Авторизация | `Authorization: Bearer <access_token>`. Без токена доступны только `POST /auth/login`, `POST /auth/refresh`, `GET /health` |
| Ошибка | всегда `{"error": {"code", "message", "field", "details", "request_id"}}`. `message` по-русски, пригоден для показа как есть |
| Пагинация | `limit` + `cursor`; ответ `{"data": [...], "total": N, "next_cursor": "…" \| null}` |
| Статья | пара (`domain`, `publication_id`). `publication_id` = имя файла без `.pdf` (`D001`, `P042`). Коды в разных областях совпадают, поэтому пара нужна везде: в путях, ключах БД, файлах |
| Значения строк таблиц | **строками, как в CSV**: `""` — в статье нет, `true`/`false`, несколько значений через `;`, числа с точкой |
| Заголовки | `X-Request-Id` в каждом ответе |
| Тексты для людей | исполнители называются «сервис разбора» (бэк) и «модель» (извлечение). Слова «бэк», «ML» в `message` и описаниях не используются |

---

## 2. Хранение

**PostgreSQL** (≥ 14). Минимальная схема:

| Таблица | Ключ | Поля |
|---|---|---|
| `users` | `id` | `email` (уник.), `password_hash` (argon2id / bcrypt ≥ 12), `name`, `role` (`""` по умолчанию), `team` (`""`), `is_admin`, `disabled` |
| `refresh_tokens` | `id` | `user_id`, `token_hash`, `family_id`, `expires_at`, `revoked_at` |
| `runs` | `id` (`run_…`) | `owner_id`, `title`, `domain`, `status`, `pinned`, `created_at`, `updated_at` |
| `run_files` | (`run_id`, `file_id`) | `name`, `size`, `sha256`, `storage_key`, `status`, `stage`, `progress`, `reason`, `pdf_pages`, `model_version` |
| строки 7 таблиц | (`run_id`, `domain`, `publication_id`, `<id строки>`) | колонки **ровно как в шаблоне**, все `text` |
| `result_meta` | (`run_id`, `domain`, `pid`, `result_id`) | `quote_found` (bool) |
| v2: `reviews` | (`run_id`, `domain`, `pid`, `result_id`) | `status` `new\|accepted\|rejected`, `corrected_value`, `at`, `who` |
| v2: `issue_states` | (`run_id`, `domain`, `pid`, `issue_id`) | `state` `open\|fixed\|ok`, `at`, `who` |
| v2: `actions` | `id` | `user_id`, `run_id`, `kind`, `payload`, `prev`, `at`, `undone_at` |

Требования к хранению:
- **Индексы:** (`run_id`, `domain`, `publication_id`) и (`run_id`, `metric_name`) на `results`; (`run_id`, `check_name`) на `issues`.
- **Файлы** лежат в S3-совместимом хранилище (MinIO) или на диске по ключу `runs/{run_id}/{file_id}.pdf`. Ключи не угадываются, фронт получает файл только через API.
- **Видимость:** разборы личные (`owner_id`). Чужой разбор → `404`.
- Одна и та же статья в двух разборах — **независимые** данные, правки разных разборов не смешиваются.

---

## 3. Авторизация

Схема: JWT access + refresh.
- **access**: срок 15 мин (`expires_in: 900`), payload `sub`, `exp`, `iat`, `jti`.
- **refresh**: случайная строка в cookie `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth`. Срок 30 дней, при `remember=false` — 12 ч. Одноразовый с ротацией. Повторное предъявление погашенного refresh гасит всю цепочку (`family_id`).

| Запрос | Тело | Ответ | Ошибки |
|---|---|---|---|
| `POST /auth/login` | `{email, password, remember}` | `200 {access_token, token_type:"Bearer", expires_in, user}` + cookie | `400 validation_error`, `401 invalid_credentials` «Неверная почта или пароль», `403 account_disabled`, `429 too_many_attempts` (5 на почту / 15 мин, 20 на IP; `details.retry_after`) |
| `POST /auth/refresh` | cookie | `200` (как login, без `user`) | `401 refresh_invalid` «Сессия истекла, войдите снова» |
| `POST /auth/logout` | `{all?: bool}` | `204`, cookie стирается | — |
| `GET /auth/me` | — | `200 user` | `401 unauthorized` |
| `GET /health` | — | `200 {status:"ok", version, model_version}` | — |

`user = {id, email, name, role, team, color, is_admin, pending_email}`, где `role` и `team` по умолчанию `""`.

**Учётные записи заводит бэк.** Регистрации в API нет.
- CLI: `faktura users create --email … --name … [--admin] [--generate]`, `disable | enable | reset-password | list`.
- Первый админ создаётся из `FAKTURA_ADMIN_EMAIL` / `FAKTURA_ADMIN_PASSWORD`.

**Опционально, фронт пока показывает только вид:**
- смена пароля: `POST /auth/password {current, new}`;
- смена почты с подтверждением письмом: `POST /auth/email-change {new_email, password}`, `POST /auth/email-change/confirm {token}`, `DELETE /auth/email-change`;
- сброс пароля: `POST /auth/password-reset {email}` (всегда `204`), `…/confirm {token, password}`.

OAuth (VK, Яндекс) не нужен.

---

## 4. Конвейер разбора

### 4.1. Разбор (Run)
Разбор = одна загрузка из N PDF одной области. Прогон стартует сам сразу после приёма файлов.

Статус разбора: `queued` → `running` → `done` | `error`. Это `done`, когда все файлы в конечном статусе (`done` / `out` / `nodata` / `error`). Это `error`, только если упали все файлы.

### 4.2. Статусы файла (`RunFile.status`)
| Статус | Когда | `status_label` |
|---|---|---|
| `queued` | ждёт очереди | «в очереди» |
| `running` | идёт; `stage` = «Извлечение» → «Нормализация» → «Проверка», `progress` 0..100 | «разбор» |
| `done` | есть строки `results` | «готово» |
| `nodata` | статья по теме, но значений нет | «нет результатов» |
| `out` | статья вне области: `publications.in_scope=false`, `reason` = `exclusion_reason` | причина |
| `error` | сбой; `reason` = текст ошибки по-русски | «ошибка» |
| `ocr` (v3) | скан, текст распознан OCR | «скан, распознано OCR» |

### 4.2а. Прогресс и оставшееся время

Фронт показывает: «Разбираем 21 статью · 43 % · осталось ≈ 6 мин», а у каждого файла — этап, процент и подсказку «осталось ≈ 40 с».

**Новые поля** (в `GET /runs/{id}`, `GET /runs` и ответе `POST /runs`):

| Где | Поле | Тип | Что |
|---|---|---|---|
| Run | `progress` | int 0..100 | общий процент разбора, взвешенный по объёму работы (страницам), а не среднее по файлам |
| Run | `eta_seconds` | int \| null | сколько секунд осталось до конца **всего** разбора, с учётом очереди и параллельности |
| Run | `started_at` | datetime \| null | когда начался первый файл |
| RunFile | `progress` | int 0..100 | реальный процент файла, плавно, а не ступеньками 0 → 10 → 60 → 85 → 100 |
| RunFile | `eta_seconds` | int \| null | сколько секунд осталось файлу; у `queued` — с учётом ожидания в очереди |
| RunFile | `pages` | int \| null | число страниц PDF (известно после приёма) |
| RunFile | `started_at` | datetime \| null | когда файл начал разбираться |

**Как считать процент файла.** Доля этапа × прогресс внутри этапа:
- **Извлечение** — 0–70 %. Внутри этапа прогресс = обработанные страницы / все страницы; если модель отдаёт ответ потоком — по полученным частям.
- **Нормализация** — 70–85 %.
- **Проверка** — 85–100 %.

Процент **никогда не уменьшается**. До 100 % файл доходит только в конечном статусе.

**Как считать оставшееся время.**
1. **Скорость.** Сервер хранит скользящее среднее «секунд на страницу» по каждому этапу за последние ~50 файлов (своё для `solar` и `rag`). Первая оценка — по умолчанию, например 3 с на страницу на извлечение.
2. **Файл:** `eta_seconds` = оставшиеся страницы × скорость этапов, которые ещё впереди.
3. **Разбор:** сумма оставшейся работы по всем файлам / число параллельных обработчиков (`FAKTURA_PARALLEL`), и не меньше `eta_seconds` самого долгого из идущих файлов.
4. **Когда `null`.** Если оценить нельзя (первый файл, нет данных о скорости), отдавать `null`. Фронт тогда сам оценит по скорости роста `progress`.
5. **Шум.** Сглаживать: не прыгать больше чем на 30 % между соседними опросами. Округлять до 5 с.

**Конечные статусы:** `eta_seconds = null`, `progress = 100`.

Пример:
```json
{ "id": "run_7", "status": "running", "progress": 43, "eta_seconds": 370, "started_at": "2026-11-21T10:02:11Z",
  "files": [
    { "id": "D001", "status": "done", "progress": 100, "eta_seconds": null, "pages": 9 },
    { "id": "D002", "status": "running", "stage": "Извлечение", "progress": 38, "eta_seconds": 45, "pages": 12 },
    { "id": "D003", "status": "queued", "progress": 0, "eta_seconds": 150, "pages": 17 } ] }
```

SSE `/runs/{id}/events`, когда появится, несёт те же поля в `file.progress` и `run.progress`.

### 4.3. Шаги для каждого PDF
1. **Приём.** Проверить сигнатуру `%PDF-`, размер, шифрование, число страниц. Посчитать SHA-256; тот же хеш в этом разборе — отклонить (`duplicate`). Сохранить файл.
2. **Извлечение** — внешний сервис («модель»):
   - бэк передаёт PDF (и при необходимости список полей из паспорта);
   - получает строки 7 таблиц статьи и находки-замечания;
   - бэк **проверяет ответ по схеме**: колонки шаблона, словари (§7.3), ссылки между таблицами, уникальность id. Нарушения → файл `error` с понятной причиной; пустой ответ → `nodata`;
   - сервис недоступен → повтор с паузой (3 попытки), затем `error` «Сервис разбора временно недоступен»;
   - формат обмена согласуется отдельно. Пока сервиса нет, используется мок по `docs/case5/dev_gold`: для `D001…D021` отдаются строки эталона.
3. **Нормализация** (§5) — детерминированно на бэке.
4. **Проверка цитат.** Для каждой строки `results` и `issues` ищется `source_fragment` на `source_page` той же нормализацией, что в `check_submission.py`: нижний регистр, только `a-z0-9.%`, лигатуры, переносы. Результат пишется в `quote_found`. Строки, начинающиеся с `Table` / `TABLE` / `Supplementary`, не проверяются.
5. **Проверки по числам** (§6): к замечаниям от модели добавляются замечания бэка.
6. **Сохранение одной транзакцией** вместе с `model_version`, затем файл переходит в конечный статус.

Требования к выполнению:
- **Очередь:** параллелизм настраивается (по умолчанию 4 файла).
- **Идемпотентность:** перезапуск файла полностью заменяет его строки.
- **Таймаут** на файл — 10 мин, затем `error`.

---

## 5. Нормализация (бэк)

Единицы в API и выгрузке — **ASCII**, как в шаблоне. `raw_value` / `raw_unit` хранятся всегда, как в статье. `metric_value` / `metric_unit` = `normalized_value` / `normalized_unit`.

| Показатель / поле | Целевая единица | Перевод |
|---|---|---|
| PCE, stabilized_PCE, retained_PCE, HI | `%` | доля (≤ 1) × 100 |
| FF | `%` | доля или без единицы при значении ≤ 1 → × 100 |
| Voc, Voc_deficit, Vmpp | `V` | `mV` × 0.001 |
| Jsc, Jsc_EQE, Jmpp | `mA/cm2` | `mA cm-2` × 1, `A/m2` × 0.1, `A/cm2` × 1000 |
| Rs, Rsh | `Ohm*cm2` | `kOhm*cm2` × 1000; `Ohm` без площади **не пересчитывается** |
| T80 | `h` | `min` / 60, `day` × 24 |
| bandgap_value | `eV` | край поглощения в `nm` **не пересчитывается** |
| layer_thickness | `nm` | `µm` × 1000, `Å` × 0.1 |
| active_area | `cm2` | `mm2` × 0.01 |
| light_intensity, time_point | как в статье + единица | не пересчитывается (для проверок Pin считается в mW/cm2) |

Алгоритм для числа:
- `−` → `-`; запятая → точка; убираются пробелы.
- `18.6 ± 0.4` → значение 18.6 и `spread_value=0.4`.
- Диапазон `18.2–19.0` → `normalized_value` пусто, замечание `ambiguity`.
- Неизвестная единица → `normalized_value` пусто, замечание `unit_inconsistency`. `extraction_status` при этом не меняется.
- Не округлять сверх исходной точности: `1197 mV` → `1.197`.
- **Не восстанавливать неизвестное по догадке:** пустое остаётся пустым.

---

## 6. Проверки (бэк)

Работают **внутри одной статьи** по действующему значению: исправленному человеком (v2), иначе `normalized_value`. Отклонённые строки и строки с `duplicate_of` не участвуют (кроме `contradiction`). Исходные записи не меняются, замечание только ссылается на них через `record_ids`.

| `check_name` | Условие |
|---|---|
| `jv_consistency` | `calc = Voc·Jsc·FF/Pin` (Pin из `light_intensity`, при AM1.5G без интенсивности — 100 mW/cm2). Замечание, если `|calc − PCE| / PCE > 3 %` |
| `rounding` | то же расхождение, но объясняется округлением последнего знака — тогда это имя вместо `jv_consistency` / `contradiction` |
| `voc_limit` | `Voc [V] ≥ bandgap_value [eV]` поглотителя прибора |
| `range` | PCE ∉ (0; 100); FF ∉ (0; 100]; Jsc ≤ 0 или > 50; Voc ≤ 0 или > 3 у ячейки; Rs, Rsh ≤ 0; retained_PCE > 150 |
| `eqe_consistency` | `|Jsc_EQE − Jsc| / Jsc > 5 %` |
| `champion_vs_mean` | у одного прибора и измерения champion < mean / median |
| `hysteresis_index` | HI из статьи расходится с `(PCE_rev − PCE_fwd)/PCE_rev` больше чем на 1 п.п., или HI < 0 |
| `contradiction` | основная запись и её повторы (или одно и то же значение из разных мест) различаются больше округления |
| `missing_conditions` | нет `J-V` со `spectrum` и `light_intensity`, или у приборов нет `active_area` |
| `unit_inconsistency` | единица не распознана или не согласуется с величиной («FF 0.71 %») |
| `voc_deficit_consistency` | `Voc_deficit ≠ Eg − Voc` |
| `plausibility` | выше рекорда класса материала (таблица рекордов в конфиге) |

Правила оформления замечаний:
- **`description`** по-русски, с числами с обеих сторон: «PCE 2,36 % не согласуется с Voc·Jsc·FF/Pin = 3,11 % (расхождение 31,7 %)».
- **`source_page` / `source_fragment`** берутся у первой записи из `record_ids`.
- **`issue_id`** у правил бэка детерминированный (`I-{check}-{result_id}`), чтобы отметки переживали пересчёт. В выгрузке id перенумеровываются в `I1`, `I2`, … по страницам.
- **Замечания модели** (`ambiguity`, `typo`, `figure_only`, …) бэк не пересчитывает. Он только проверяет, что `record_ids` существуют, и проверяет цитату.

---

## 7. API v1 (обязательно)

### 7.1. Разборы
**`POST /runs`**:
- тело `multipart/form-data`: `file` (повторяемое, PDF; для папки — относительный путь в имени) **или** `archive` (ZIP), плюс `domain` (по умолчанию `solar`) и необязательный `title`;
- ответ `202`: Run + `rejected: [{name, code, message}]`, где `code` ∈ `unsupported_file`, `duplicate`, `broken_pdf`, `empty_file`, `encrypted_pdf`;
- ошибки: `400 no_files`, `413 too_large`, `415 unsupported_media_type`, `422 zip_bomb | domain_not_supported` (для `rag` до v3).

ZIP распаковывается потоково, с защитой:
- не больше 500 файлов и 2 ГБ в распакованном виде;
- без `../` и абсолютных путей;
- `__MACOSX` и скрытые файлы игнорируются.

**`GET /runs?limit=20&cursor=&files=false`**: новые сверху, у файлов в списке короткие поля.

**`GET /runs/{id}`**: разбор целиком. Фронт опрашивает его раз в 2 с, пока разбор `queued` / `running`, поэтому ответ должен быть дешёвым.

Run:
```json
{ "id": "run_7", "title": "21 статья · 20 ноя", "domain": "solar", "status": "running",
  "created_at": "…", "updated_at": "…",
  "counts": { "files": 21, "queued": 3, "running": 4, "done": 12, "out": 1, "error": 0, "nodata": 1 },
  "results": 412, "issues": 31,
  "files": [ { "id": "D001", "name": "D001.pdf", "status": "done", "status_label": "готово", "stage": null,
               "progress": 100, "has_data": true, "results": 34, "issues": 2, "reason": null,
               "size": 812345, "sha256": "…" } ] }
```

Правила для полей:
- `title` по умолчанию — «N статей · дд мес» или имя ZIP.
- `file.id` = `publication_id`; при совпадении имён внутри разбора — `P042-2`.
- `has_data` = есть строки `results` (у `out` и `nodata` — `false`).

**`GET /runs/{id}/summary`**:
```json
{ "files": 21, "papers_in_scope": 19, "papers_out": 2, "files_without_results": 0, "files_error": 0,
  "results": 659, "scored_results": 627, "issues_total": 53,
  "coverage": [ { "metric": "PCE", "n": 19, "total": 19, "results": 205 } ],
  "issues_by_check": [ { "check_name": "contradiction", "total": 8 } ] }
```
- `coverage` строится по 11 оцениваемым показателям: PCE, Voc, Jsc, FF, Rs, Rsh, HI, Jsc_EQE, stabilized_PCE, T80, retained_PCE.
- `scored_results` считает только их.

### 7.2. Статья
**`GET /papers/{domain}/{pid}?run={id}`**: все 7 таблиц статьи.
```json
{ "publication": { PublicationRow }, "materials": [], "devices": [], "layers": [], "measurements": [],
  "results": [], "issues": [], "paper_status": "has_data",
  "meta": { "file_status": "done", "run_ids": ["run_7"], "pdf_pages": 9,
            "results": { "R002": { "quote_found": true, "issue_ids": ["I1"], "duplicates": ["R015"] } } } }
```
- `paper_status` ∈ `has_data` / `nodata` / `out`. У `out` массивы пустые, а в `publication` стоят `in_scope=false` и `exclusion_reason`.
- Без `run` берётся последний разбор с этой статьёй.
- Пока файл ещё разбирается: `200` с пустыми массивами и `meta.file_status` = `queued` / `running`.

**`GET /papers/{domain}/{pid}/pdf?run=`**: `application/pdf`, поддерживает `Range` и `ETag`, `Cache-Control: private`. Без файла → `404`.

### 7.3. Строки
**`GET /results`**, параметры:
- `run` — обязателен;
- `pid` (повт.);
- `metric` (повт.), `scored=true`;
- `device_id`, `measurement_id`, `material_id`;
- `duplicates=include|exclude`;
- `sort=publication_id|metric|value|page`, `dir`;
- `limit` (100, до 1000), `cursor`.

```json
{ "data": [ { "row": ResultRow, "device_label": "Y-Sn3O4", "paper_title": "…",
              "meta": { "quote_found": true, "issue_ids": [] } } ], "total": 659, "next_cursor": null }
```

**`GET /issues?run=&pid=&check_name=&limit=&cursor=`** → `{ data: [{ row: IssueRow, paper_title }], total, next_cursor }`.

Колонки строк — **ровно колонки шаблона**, в его порядке (`docs/case5/submission_template/README.md`; типы `PublicationRow…IssueRow` — `src/case/types.ts`).

### 7.4. Набор для сдачи
**`GET /runs/{id}/submission.zip[?partial=true]`** отдаётся сразу файлом:
- `Content-Type: application/zip`;
- `Content-Disposition: attachment; filename*=UTF-8''Набор%20для%20сдачи%20—%20{title}.zip`.

Состав ZIP:
```
publications.csv  materials.csv  devices.csv  layers.csv  measurements.csv  results.csv  issues.csv
PASSPORT.md  passport.json
```

Заголовки (должны быть в `Access-Control-Expose-Headers`): `X-Export-Tables: 7`, `X-Export-Results`, `X-Export-Papers`, `X-Export-Filename`, а в v2 ещё `X-Export-Rejected`.

Пока разбор идёт — `409 run_in_progress`; с `?partial=true` выгружаются готовые статьи.

---

## 7а. Новое от фронта (21.11) — что нужно от бэка

Фронт всё это уже вызывает. Пока бэк чего-то не умеет, фронт идёт запасным путём или показывает «В разработке».

### 7а.1. Область выбирает человек
- У поля загрузки переключатель «Солнечные элементы» / «RAG»; по умолчанию «Солнечные элементы». Автоопределения области нет ни во фронте, ни на бэке.
- `POST /runs` всегда приходит с `domain`. ZIP приходит архивом (`archive`), браузер его не распаковывает.
- Одна загрузка — один разбор одной области, смешанных загрузок нет.

### 7а.2. Прогресс и оставшееся время
Описано в §4.2а: у разбора `progress`, `eta_seconds`, `started_at`; у файла `progress` (плавный), `eta_seconds`, `pages`, `started_at`.

### 7а.3. Статьи не той области → новый разбор в другой области
Человек загрузил статьи RAG как солнечные (или наоборот). Сервер помечает их `out` («не по теме»). Под итогом разбора фронт показывает:
- карточку «N статей не подошли к области „Солнечные элементы“. Возможно, это RAG»;
- список этих статей с галочками (по умолчанию отмечены все) и кнопку «Разобрать N как RAG».

Новый разбор — отдельная «папка»; исходный разбор не меняется.

**Новый запрос:**

`POST /runs/{id}/rerun-as`
```json
{ "domain": "rag", "file_ids": ["D002", "D007"], "title": "D001 и ещё 9 · RAG" }
```
→ `202` — новый Run (как у `POST /runs`), прогон стартует сам.
- PDF берутся из хранилища исходного разбора, повторная загрузка не нужна.
- `file_ids` — `RunFile.id` исходного разбора. Подходят любые принятые файлы, не только `out`.
- `title` необязателен. По умолчанию: «{название исходного} · RAG» или «· солнечные».
- Ошибки:
  - `404 not_found` — нет разбора или файла;
  - `409 run_in_progress` — исходный разбор ещё идёт;
  - `422 domain_not_supported`;
  - `422 validation_error` — пустой `file_ids` или та же область, что у исходного.

**Запасной путь фронта, пока запроса нет.** Фронт берёт PDF, отправленные из этой вкладки. Если их нет (страницу перезагрузили), скачивает `GET /papers/{domain}/{pid}/pdf?run=` и создаёт разбор обычным `POST /runs`. Для этого нужно, чтобы:
- **`GET /papers/{domain}/{pid}/pdf?run={id}`** отдавал исходный PDF **любого принятого файла** разбора, включая `out`, `nodata`, `error`. Сейчас для статей «не по теме» он отвечает 404.
- **`GET /papers/{domain}/{pid}?run={id}`** для `out` отвечал `200` (`paper_status: "out"`, строка `publication` с `in_scope=false` и `exclusion_reason`, остальные массивы пустые), а не `404`. Так сказано в §7.2, а сейчас приходит 404.

Фронт запрашивает `/papers/…` только у файлов со статусом `done`, `out`, `nodata`, `ocr`. У `queued`, `running`, `error` не запрашивает.

**Желательно (необязательно):** `RunFile.suggested_domain: "rag" | "solar" | null` — если модель при отборе видит, что статья из другой области. Фронт тогда сможет предлагать «Разобрать как RAG» только для таких статей, а не для всех `out`.

### 7а.4. Управление разбором (сейчас кнопки серые, «В разработке»)
| Запрос | Тело | Ответ |
|---|---|---|
| `PATCH /runs/{id}` | `{ "title"?: string, "pinned"?: bool }` | `200` Run (у Run появляется поле `pinned`) |
| `DELETE /runs/{id}` | — | `204` (мягкое удаление) |
| `POST /runs/{id}/restart` | `{ "file_ids"?: [...] }` — без тела перезапускает всё | `202` Run |
| `POST /runs/{id}/stop` | — | `200` Run; незаконченные файлы → `error`, `reason` «остановлено» |

В `GET /runs` закреплённые разборы идут первыми.

### 7а.5. Проверка набора при скачивании
Фронт всегда скачивает `GET /runs/{id}/submission.zip?check=true` (§7.4):
- читает `X-Check-Status`;
- при `422 submission_invalid` показывает первую ошибку из `details.errors` и кнопку «Скачать без проверки» (тот же запрос без `check`).

### 7а.6. Паспорт набора с сервера

**Источник паспорта — сервер.** Экран «Паспорт» во фронте показывает ровно тот паспорт, который сервер кладёт в ZIP набора (`PASSPORT.md` + `passport.json`). Что на экране, то и в архиве. Своего описания фронт в режиме с бэком не подставляет: пока запроса нет, на экране «В разработке».

#### `GET /runs/{id}/passport.json`
→ `200 application/json` — паспорт набора этого разбора (его области), **байт в байт тот же `passport.json`, что внутри `submission.zip`**. `404 not_found` — нет разбора.

Формат (фронт уже умеет его показывать и скачивать; Markdown фронт собирает из этого же JSON):
```json
{
  "title": "Паспорт данных «Фактура AI»",
  "version": "1",
  "format": "CSV UTF-8 с BOM, запятая, точка, списки через «;»; пустая ячейка = в статье нет",
  "versions": { "model": "extractor-2026-11-20", "rules": "checks-3", "dictionary": "dict-7" },
  "domains": [
    {
      "id": "solar",
      "name": "Солнечные элементы",
      "guides": [
        { "id": "inclusion", "title": "Критерии отбора статей", "description": "…", "items": ["…", "…"] },
        { "id": "comparability", "title": "Критерии сопоставимости", "description": "…", "items": ["…"] },
        { "id": "extraction", "title": "Правила извлечения", "description": "…", "items": ["…"] }
      ],
      "entities": [
        { "id": "results", "title": "Результаты", "row": "один показатель одного прибора при одних условиях", "key": "publication_id + result_id",
          "fields": [
            { "name": "metric_name", "type": "enum", "unit": null, "required": true, "description": "Показатель", "values": ["PCE", "Voc", "…"], "ref": null },
            { "name": "device_id", "type": "string", "unit": null, "required": false, "description": "Прибор", "values": null, "ref": "devices.device_id" },
            { "name": "normalized_value", "type": "number", "unit": "по metric_name", "required": false, "description": "…", "values": null, "ref": null }
          ] }
      ],
      "rules": [
        { "id": "jv_consistency", "title": "КПД против Voc·Jsc·FF/Pin", "description": "Расхождение больше 3 % относительных", "level": "warn", "computed_by": "бэк" }
      ],
      "normalization": [
        { "quantity": "Voc", "from": "mV", "to": "V", "factor": 0.001, "note": null },
        { "quantity": "Rs", "from": "Ohm", "to": "Ohm*cm2", "factor": null, "note": "без площади не пересчитывается" }
      ]
    }
  ]
}
```

| Поле | Что |
|---|---|
| `versions` | версии модели, правил проверки и словаря, с которыми собран набор; фронт показывает строкой «Собран с версиями: …» |
| `domains[]` | одна область — область разбора |
| `guides[].id` | `inclusion` \| `comparability` \| `extraction` |
| `entities[]` | 7 таблиц шаблона в порядке выгрузки, `fields` — колонки ровно как в шаблоне |
| `fields[].type` | `string` \| `int` \| `number` \| `bool` \| `enum` \| `list` |
| `rules[].level` | `error` \| `warn` \| `info` \| `format` |
| `rules[].computed_by` | `бэк` \| `ML` \| `бэк + ML` (на экране — «Сервис разбора» / «Модель») |
| `normalization[].factor` | множитель к канонической единице; `null` — не пересчитывается |

Начальное наполнение можно взять из фронта: `src/passport.ts` (`PASSPORT`, функция `passportJSON()` выдаёт ровно этот формат для `solar` и `rag`). Дальше источник — сервер: при изменении порогов, словаря, модели меняется паспорт на сервере, фронт подхватит сам.

Необязательно: `GET /runs/{id}/passport.md` — тот же `PASSPORT.md`, что в ZIP (сейчас фронт собирает Markdown из JSON сам).

---

## 8. Формат CSV (жёстко)

Формат и кодировка:
- UTF-8 **с BOM**, разделитель `,`, `QUOTE_MINIMAL`, десятичная точка.
- Пустая ячейка = «в статье нет»: никаких `null`, `NA`, `—`, `0`.
- Логические значения — `true` / `false`; несколько значений — через `;`.

Содержание строк:
- Строка в `publications` есть для **каждой** статьи, включая вне области (`in_scope=false` + `exclusion_reason`). У статей вне области в остальных таблицах строк нет.
- `source_page` — страница **PDF-файла** с 1, а не журнальная.
- Повтор значения — отдельная строка со своим `source_*` и `duplicate_of` = `result_id` основной записи. Повторы не сливать, не допускать циклов и ссылок на себя.
- `cited`: `cited_publication_id` как в статье, `device_id` / `measurement_id` пустые.
- `present_not_extractable`: значения пустые, место указано.

Словари проверяются при сохранении:

| Поле | Значения |
|---|---|
| `material_class` | `perovskite`, `CIGS`, `CdTe`, `kesterite`, `Sb2Se3`, `thin-film Si`, `other` |
| `dimensionality` | `3D`, `2D`, `2D-3D` |
| `bandgap_method` | `Tauc`, `EQE`, `PL`, `cited` |
| `device_role` / `device_type` | `control`, `target`, `record` / `cell`, `minimodule`, `module` |
| `architecture` | `n-i-p`, `p-i-n`, `substrate`, `superstrate` |
| `substrate_type` / `area_definition` | `rigid`, `flexible` / `designated illumination`, `aperture`, `total` |
| `layer_function` | `front_contact`, `ETL`, `absorber`, `passivation`, `HTL`, `interlayer`, `recombination`, `back_contact` |
| `measurement_type` | `J-V`, `MPP tracking`, `EQE`, `stability` |
| `scan_direction` | `reverse`, `forward`, `average` |
| `stability_protocol` | `ISOS-D`, `ISOS-L`, `ISOS-T`, `other` |
| `aggregation` | `champion`, `mean`, `median`, пусто |
| `result_origin` / `extraction_status` | `primary`, `cited` / `extracted`, `present_not_extractable`, `absent` |

**Проверка выгрузки.** Бэк вызывает `docs/case5/tools/check_submission.py` **как есть** через `subprocess`. С `?check=true` в ZIP кладётся `check_report.txt` и ставится заголовок `X-Check-Status: ok|warnings|failed`. Ошибки скрипта означают, что сдача не принимается: CLI завершается с кодом `3`, API отвечает `422 submission_invalid`.

**Паспорт** (`PASSPORT.md` + `passport.json`) содержит:
- поля, типы и единицы;
- словари;
- правила проверок с порогами (§6);
- нормализацию (§5);
- критерии отбора статей (`in_scope`), сопоставимости и извлечения;
- версии: модели, правил, словаря.

Скрипт проверки лишние файлы в ZIP игнорирует.

**CLI для сдачи:** `faktura extract --domain solar --in ./pdfs --out ./out [--check]` прогоняет тот же конвейер без API и записывает 7 CSV + паспорт.

---

## 9. v2 — правки и управление (контракт заранее)

**Правки значений** привязаны к разбору, ключ (`run_id`, `domain`, `pid`, `result_id`).
- `PATCH /runs/{id}/papers/{domain}/{pid}/results/{rid}/review`, тело `{status: new|accepted|rejected, corrected_value?}`.
  - `corrected_value` — в целевой единице.
  - Ответ: `{review, action_id, issues_changed: {opened, closed}}`. Проверки статьи пересчитываются сразу, меньше чем за 100 мс.
  - Правка, присланная на повтор, применяется к основной записи (`applied_to`).
- `POST /runs/{id}/reviews/batch`, тело `{items: [{domain, pid, rid, status}]}`, ответ `{count, action_id}`.
- `DELETE /runs/{id}/reviews` сбрасывает все правки разбора.

Как правки попадают в выгрузку:
- **`rejected`:** строка не выгружается. Она убирается из `issues.record_ids` и `duplicate_of`; первый повтор становится основной записью.
- **Исправление:** `normalized_value` и `metric_value` = исправленное значение, `raw_*` остаются из статьи. В `note` дописывается «Исправлено вручную: было X».

**Состояния замечаний:** `PATCH /runs/{id}/papers/{domain}/{pid}/issues/{iid}`, тело `{state: open|fixed|ok}`.
- **Автозакрытие:** когда все `record_ids` исправлены или отклонены, замечание становится `fixed` само. Попытка открыть его снова → `409 issue_auto_fixed`.
- **Выгрузка:** в CSV идут `open` и `fixed`, `ok` не идёт.
- **Счётчики** (`open_issue_count`, `issues_open`) считают только открытые.

**Журнал и отмена:**
- каждое изменение пишет `action_id` (заголовок `X-Action-Id`) и прежнее значение;
- `POST /actions/{id}/undo`, `POST /history/undo`, `GET /history?run=`.

**Управление разбором:**
- `PATCH /runs/{id}`, тело `{title?, pinned?}`;
- `DELETE /runs/{id}` (мягкое удаление);
- `POST /runs/{id}/restart` (`{files?: [...]}`) и `POST /runs/{id}/stop`;
- `POST /runs/{id}/files` — догрузка файлов.

**Прогресс:** `GET /runs/{id}/events` (SSE) с событиями `file.progress`, `file.done`, `run.done`. Это замена опроса.

**Остальное:**
- `GET /issues/stats?run=` → `{data: [{check_name, count, papers}]}`;
- «лучшее значение» для сводки и сравнения: только primary + extracted + не повтор + не отклонено; champion важнее среднего; для Rs и HI лучше меньшее значение. Бэк отдаёт выбранную строку (`pid`, `result_id`).

---

## 10. Область RAG (контракт — `docs/CORE_API.md` §5)

- **Формат:** `domain=rag`, свои 7 таблиц (`docs/case5_area2/submission_template/schema.json`): `configurations`, `datasets`, `tasks`, `experiments` вместо приборов. Скрипт проверки возвращает коды выхода 0/1/2, где 2 = «нужна проверка» (норма для сканов).
- **Объём:** до 28 тыс. строк `results` на статью (D014). Ответ сервиса извлечения принимать потоком (NDJSON) и писать пачками. Для `/results` пагинация и фильтры (`metric_name`, `config_id`, `dataset_id`, `experiment_id`) обязательны. `GET /papers/rag/{pid}` по умолчанию отдаёт сводку, а не строки.
- **Сканы:** нет текста ни в pypdf, ни в PyMuPDF → статус `ocr`, хранить `ocr_confidence`, проверка цитат запускается с `--ocr`.
- **Значения с графиков** (около 82 % в RAG): цитаты не проверяются буквально, помечаются в `meta` (`from_figure: true`).
- **Шкалы:** доли и проценты без известной шкалы не пересчитывать.

---

## 11. Нефункциональные требования

| | |
|---|---|
| PDF | до **50 МБ** на файл, до 300 страниц; до 200 файлов и 1 ГБ на разбор |
| Лимиты | 600 запросов/мин на пользователя, 60 загрузок/час |
| Скорость | `GET /runs/{id}` < 100 мс; `/papers/…` < 200 мс; `/results` (страница 100) < 300 мс |
| CORS | список источников из `FAKTURA_CORS_ORIGINS` (`http://localhost:5173`, `http://localhost:5200`, прод), не `*`; `Allow-Credentials: true`; expose `X-Request-Id, X-Action-Id, Content-Disposition, X-Export-*, X-Check-Status` |
| Безопасность | тексты из PDF отдаются как текст; ZIP распаковывается с защитой; эндпоинт сервиса извлечения — во внутренней сети |
| Логи | `request_id`, время, пользователь; ошибки сервиса извлечения — с `file_id` |
| Резерв | БД — ежедневно, хранилище — версионирование |

**Коды ошибок:**

| HTTP | `error.code` |
|---|---|
| 400 | `validation_error`, `no_files` |
| 401 | `unauthorized`, `token_expired`, `invalid_credentials`, `refresh_invalid` |
| 403 | `forbidden`, `account_disabled` |
| 404 | `not_found` (в том числе чужой объект) |
| 409 | `run_in_progress`, `conflict`, `issue_auto_fixed` |
| 413 / 415 | `too_large` / `unsupported_media_type` |
| 422 | `zip_bomb`, `domain_not_supported`, `submission_invalid`, `pdf_broken`, `pdf_encrypted`, `too_many_pages` |
| 429 | `too_many_requests`, `too_many_attempts` (`details.retry_after`) |
| 500 | `internal_error` (+ `request_id`) |
| 502 / 503 | `extractor_unavailable` «Сервис разбора временно недоступен» |

---

## 12. Открытые вопросы к бэку

1. Что отдаёт `/papers/{domain}/{pid}` пока файл в `running`: пустые массивы (предложение фронта) или `409`?
2. `has_data` у файлов `out`: всегда `false`?
3. Срок жизни refresh-cookie и `SameSite` при одном домене через прокси.
4. Имена полей multipart (`file` / `archive`) и набор заголовков `X-Export-*` — подтвердить.
5. Формат обмена с сервисом извлечения (синхронно или очередь, JSON или NDJSON) — согласовать отдельно; бэк валидирует результат по шаблону.

## 13. Приёмка v1

- [ ] Вход, refresh по 401, выход; без токена → 401.
- [ ] Загрузка 21 PDF dev (файлами и ZIP) → разбор `done`: 19 статей по теме, 2 вне области (D006, D016).
- [ ] `GET /papers/solar/D001?run=` отдаёт 7 таблиц, колонки 1:1 с шаблоном.
- [ ] PDF открывается с `Range`.
- [ ] `GET /runs/{id}/submission.zip` → `check_submission.py <папка> dev` без ОШИБОК; в ZIP 7 CSV + паспорт.
- [ ] Сводка: `results`, `issues_total` и покрытие совпадают с суммой по файлам.
- [ ] Ошибки в формате `{error: {...}}` с русским `message`.
