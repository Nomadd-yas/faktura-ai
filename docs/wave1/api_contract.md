# API-контракт бэкенда для фронтенда (волна 1)

**Версия контракта:** 1.1 (волна 1) · **Базовый путь:** `/api/v1` · **Источник истины:** `docs/spec_src/BACKEND_SPEC.md` (§0, §2, §8), `docs/spec_src/spec/04-api.md`, `spec/13-corpus.md`.

Контракт **не изобретается заново**. Этот документ:
1. сводит эндпоинты спеки в один список с приоритетом и статусом реализации в волне 1;
2. фиксирует **уточнения** там, где спека противоречит сама себе или молчит (раздел 2, со ссылками на разделы спеки);
3. даёт схемы ключевых сущностей и примеры запросов и ответов.

Если форма ответа здесь не расписана, верна форма из указанного раздела спеки. Если здесь есть уточнение, верно уточнение.

Ссылки: `BACKEND §N` — `docs/spec_src/BACKEND_SPEC.md`; `spec/04 §4.N` — `docs/spec_src/spec/04-api.md`; `spec/13 §13.N` — `docs/spec_src/spec/13-corpus.md`; `К-N`, `REQ-N` — `docs/wave1/01_spec_analysis.md`; модули бэка — `docs/wave1/02_plan.md`.

**Правила изменения контракта.** В пределах версии 1.x изменения только добавляющие: новые необязательные поля, новые эндпоинты, новые значения перечислений с запасным показом на фронте. Удаление и переименование полей, смена типов и кодов ответа — только в версии 2, с записью в журнале изменений (раздел 7). Фронт обязан игнорировать неизвестные поля. Исключение — правки версии 1.1 до первой интеграции с фронтом (раздел 7).

---

## 1. Общие соглашения

### 1.1. Формат

| Что | Правило | Источник |
|---|---|---|
| Кодировка, формат | JSON, UTF-8, `Content-Type: application/json; charset=utf-8` | BACKEND §0 |
| Имена полей | `snake_case`. Поля таблиц сдачи — ровно как в шаблоне (`publication_id`, `metric_value`, `DOI`) | BACKEND §0 |
| Числа | Числа JSON с точкой (`18.6`). Форматирование «18,6 %» — в поле `display` или на фронте | BACKEND §0, §8.4 |
| Единицы | ASCII: `%`, `V`, `mA/cm2`, `Ohm*cm2`, `h`, `eV`, `nm`, `cm2`. По-русски — только в `display`, `text`, `message` | BACKEND §8.5, spec/06 §6.1 |
| Даты | ISO 8601 UTC: `2026-11-20T09:15:00Z` | BACKEND §0 |
| Идентификаторы | Непрозрачные строки с префиксом: `usr_`, `crp_`, `cf_`, `run_`, `prf_`, `pap_`, `fct_`, `iss_`, `anc_`, `act_`, `exp_`. Фронт не разбирает их по частям. Имя ключа у каждой сущности — §3.0 | BACKEND §0, К-4, К-6 |
| Пустое значение | `null` («в статье нет»). Фронт показывает «не указано» | spec/14 §14.2 |
| Тексты из PDF | Отдаются как текст. Фронт не вставляет их как HTML | BACKEND §8.5 |

### 1.2. Авторизация

- `Authorization: Bearer <access_token>`. Access — JWT на 15 минут (BACKEND §2.1).
- Refresh-токен передаётся в cookie `faktura_refresh` с атрибутами `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth` (принятое умолчание по BACKEND «Остаётся открытым» п.9). Срок — 30 суток при `remember: true` и 12 часов при `remember: false`. Фронт делает запросы к `/auth/*` с `credentials: "include"`.
- Если API и фронт живут на разных доменах без общих cookie, сервер запускают с `FAKTURA_REFRESH_IN_BODY=true`. Тогда `refresh_token` приходит в теле login и refresh, а фронт передаёт его в теле `/auth/refresh`.
- Без токена доступны только `POST /auth/login`, `POST /auth/refresh`, `POST /auth/register`, `GET /health`.
- При `401 token_expired` фронт один раз вызывает `/auth/refresh` и повторяет запрос. При `401 refresh_invalid` — ведёт на `/login`.
- SSE: `EventSource` не умеет передавать заголовки, поэтому используется одноразовый токен `?token=` из `POST /events/token` (раздел 4.13).
- Ключей API (`/me/api-keys`) в волне 1 нет (У-31).

### 1.3. Заголовки

| Заголовок | Направление | Когда |
|---|---|---|
| `X-Request-Id` | запрос (необязательно) и ответ (всегда) | Сквозной id. Если клиент его не прислал, сервер генерирует свой |
| `X-Action-Id` | ответ | Каждый изменяющий запрос, который создаёт действие (§1.6); дублирует `action_id` в теле |
| `Idempotency-Key` | запрос | `POST /papers`, `POST /exports`, `POST /chats/ask`, `POST /chats/{id}/turns`. Повтор с тем же ключом в течение 24 ч возвращает первый ответ |
| `Last-Event-ID` | запрос к `/events` | Продолжить поток событий |
| `X-Check-Status` | ответ `GET /exports/{id}/file` для `submission` | `ok \| warnings \| failed` — итог `check_submission.py` (spec/14 §14.6). То же значение — в `Export.check.status` (У-29) |
| `X-Faktura-Stub` | ответ | `1` — эндпоинт P2 пока заглушка: форма ответа верная, данные пустые (У-15) |
| `Content-Disposition` | ответ с файлом | Имя файла выгрузки |
| `Retry-After` | ответ 429 и 503 | Через сколько секунд повторить |
| `ETag` / `If-None-Match` | `/papers/{id}/pages/{n}` | Кеш страниц; повтор с тем же `ETag` → 304 |

**CORS** (BACKEND §8.5): `Access-Control-Allow-Origin` — белый список из `FAKTURA_CORS_ORIGINS` (по умолчанию `http://localhost:5173`); `Allow-Credentials: true`. Разрешённые заголовки запроса: `Authorization, Content-Type, X-Request-Id, Last-Event-ID, Idempotency-Key`. Заголовки, открытые для чтения: `X-Action-Id, X-Request-Id, Content-Disposition, X-Check-Status, X-Faktura-Stub`. Два последних добавлены (У-16).

### 1.4. Пагинация и сортировка

```json
{ "data": [ ], "total": 42, "next_cursor": "eyJrIjpbIkQwMDEiLDEyXX0" }
```
- `limit` — по умолчанию 50, максимум 500. Исключения: `/facts/table` — 100 и 1000 (spec/13 §13.7); `/data/rows` — 500 (spec/04 §4.11); `/history` — 100.
- `cursor` — значение `next_cursor` из прошлого ответа. Курсор keyset: при правках в соседних строках страница не «прыгает». `next_cursor: null` — данных больше нет.
- `sort` — поле или несколько через запятую; `dir` — `asc|desc`.
- Справочные списки (коллекции, правила, профили) приходят целиком: `{ "data": [ ] }`.

### 1.5. Ошибки

```json
{ "error": { "code": "fact_value_unrecognized",
             "message": "Не удалось распознать значение «1,1 кВ»: единицы «кВ» нет в словаре для параметра «Voc».",
             "field": "raw", "details": { "unit": "кВ", "param": "voc" }, "request_id": null } }
```
`code` стабилен, `message` — по-русски и пригоден для показа. `field` указывает поле формы, если ошибка относится к нему. `request_id` заполняется у 5xx.

Каталог кодов (BACKEND §8.3 + уточнения):

| HTTP | `code` | Когда | `message` (пример) |
|---|---|---|---|
| 400 | `validation_error` | неверное тело или параметры; `field` указывает поле. Также пароль короче 8 символов (`field: "password"`) и неподдержанный пока способ импорта (У-11) | «Поле «name» не может быть пустым»; «Пароль должен быть не короче 8 символов» |
| 400 | `query_unparsed` | `strict=true`, в запросе ничего не распознано | «Не понял запрос: попробуйте «КПД выше 18»» |
| 401 | `unauthorized` | нет токена или он неверен | «Нужно войти» |
| 401 | `token_expired` | access истёк | «Сессия истекла» |
| 401 | `invalid_credentials` | неверная почта или пароль | «Неверная почта или пароль» |
| 401 | `refresh_invalid` | refresh погашен, истёк или предъявлен повторно | «Сессия истекла, войдите снова» |
| 403 | `forbidden` | нет прав (не админ, встроенный профиль, встроенный синоним) | «Нет доступа» |
| 403 | `registration_disabled` | `POST /auth/register` при выключенной регистрации | «Регистрация закрыта — учётную запись выдаёт администратор» |
| 403 | `account_disabled` | учётная запись отключена | «Учётная запись отключена. Обратитесь к администратору» |
| 404 | `not_found` | объекта нет или он чужой; эталона нет (`/checks/accuracy`); у прогона нет выходной папки (У-32) | «Статья не найдена» |
| 409 | `conflict` | идёт прогон корпуса; отмена после чужой правки; конфликт коллекции | «Прогон уже идёт» |
| 409 | `run_not_finished` | `GET /runs/{id}/output` до завершения прогона (У-32) | «Прогон ещё идёт — выходная папка будет готова после завершения» |
| 409 | `already_resolved` | все замечания уже разобраны | «Замечание уже разобрано» |
| 409 | `already_undone` | действие уже отменено | «Уже отменено» |
| 409 | `synonym_exists` | синоним уже есть | «Такое написание уже есть в словаре» |
| 409 | `email_taken` | регистрация или создание пользователя с занятой почтой | «Почта уже используется» |
| 409 | `turn_busy`, `version_conflict` | чат (P2) | — |
| 410 | `gone` | окно восстановления (30 суток) истекло; файл выгрузки старше 24 ч | «Уже удалено окончательно» |
| 413 | `file_too_large` | файл > 50 МБ; multipart > 1 ГБ; ZIP > 2 ГБ | «Файл больше 50 МБ» |
| 415 | `unsupported_file` | не PDF (тип или сигнатура `%PDF-`); архив не ZIP | «Можно загрузить только PDF» |
| 422 | `fact_value_unrecognized` | правка или ручной факт не распознаны (`field: "raw"`) | «Не удалось распознать «1,1 кВ»…» |
| 422 | `pdf_encrypted` / `pdf_broken` / `pdf_no_text` / `too_many_pages` | проблемы PDF | «PDF защищён паролем» |
| 422 | `zip_too_large` / `zip_bomb` | защита распаковки | «Архив слишком большой после распаковки» |
| 422 | `nothing_to_export` | нечего выгружать | «Нечего экспортировать — выберите статьи или снимите «только принятые»» |
| 422 | `paper_not_ready` | действие требует разобранной статьи | «Статья ещё разбирается» |
| — | `submission_invalid` | итог асинхронной выгрузки сдачи (`Export.error.code`), если `check_submission.py` нашёл ошибки (У-29). HTTP-ответом не приходит | «Набор не прошёл проверку: 3 ошибки» |
| 429 | `too_many_requests` | лимит запросов; `details.retry_after` | «Слишком много запросов, подождите минуту» |
| 429 | `too_many_attempts` | лимит попыток входа; `details.retry_after` | «Слишком много попыток. Попробуйте через 15 мин» |
| 500 | `internal_error` | сбой сервера, `request_id` в теле | «Что-то пошло не так. Мы уже разбираемся» |
| 503 | `ml_unavailable` | сервис ML-1 недоступен (только у синхронных вызовов; прогоны встают на паузу, У-33) | «Сервис разбора временно недоступен — статья встанет в очередь» |

Коды ошибок **файла и статьи** (поле `error.code` у `CorpusFile`, `Paper.processing`, `rejected[]`): `unsupported_file`, `file_too_large`, `too_many_pages`, `pdf_encrypted`, `pdf_broken`, `pdf_no_text`, `duplicate_name`, `ml_unavailable`, `ml_timeout`, `ml_schema_mismatch`, `ml_error`, `internal_error`. Статус `cancelled` кодом ошибки не сопровождается.

### 1.6. Изменяющие запросы

- Действие (`action_id` в теле и `X-Action-Id`) создают: правки фактов и замечаний, `POST/PATCH/DELETE /corpora*`, `POST /corpora/{id}/restore`, `POST/PATCH /profiles*`, `POST /papers`, `DELETE /papers/{id}`, `POST /exports`. Отменяемые действия отменяются через `POST /actions/{id}/undo` (раздел 4.11). Выгрузка (`export`) записывается в журнал, но не отменяется.
- Правки фактов и замечаний возвращают ещё `issues_changed` и сводку статьи, потому что проверки пересчитываются синхронно (spec/04 §4.8, spec/05 §5.4):
```json
{ "action_id": "act_k2m9q1x7p3aa",
  "issues_changed": { "opened": ["iss_8h2k…"], "closed": [] },
  "paper": { "quality": { "total": 18, "sourced_pct": 100, "normalized_pct": 100, "accepted": 1, "issues": 2 }, "open_issues": 2, "top_level": "conflict" } }
```

### 1.7. Лимиты

600 запросов в минуту на пользователя; 60 загрузок в час; 30 вопросов в чат в минуту; 5 неудачных входов за 15 минут на почту и 20 на IP (BACKEND §2.1, §8.3). Превышение — 429 с `Retry-After`.

Размеры (spec/13 §13.4): файл — до 50 МБ и 300 страниц; multipart — до 200 файлов и 1 ГБ на запрос; ZIP — до 2 ГБ, после распаковки до 10 ГБ, до 20 000 файлов, вложенность до 10.

### 1.8. Приоритеты и заглушки

| Метка | Значение |
|---|---|
| **P1** | Реализуется в волне 1, форма и поведение по контракту |
| **P2-R** | P2, который реализуется в волне 1 после P1. До реализации — заглушка, как у P2 |
| **P2** | Реализуется после волны 1. Сейчас — заглушка: HTTP-код по контракту, верная форма ответа, пустые списки или нули, заголовок `X-Faktura-Stub: 1`. Изменяющие P2-заглушки ничего не сохраняют |
| **W2** | Вторая волна. Ответ `400 validation_error` с `message` «Пока не поддерживается: …» |

---

## 2. Уточнения к спеке

| № | Тема | Решение в контракте | Почему / ссылка |
|---|---|---|---|
| У-1 | `fact_id` | Непрозрачный глобальный `fct_…`. У факта отдельно есть `publication_id` и `target.id` (для показателя это `result_id` из CSV, например `R004`). Примеры спеки вида `fct_mapbi_c_B_pce` — только иллюстрация | К-4; spec/03 §3.5 против BACKEND §0 |
| У-2 | `paper_id` и `publication_id` | `id`/`paper_id` — непрозрачный `pap_…`; `publication_id` — имя PDF без расширения (`P042`), как в сдаче. В примерах spec/04 §4.7–4.8 они совпадают (`pap_mapbi`), в контракте это разные поля | spec/03 §3.4; spec/14 §14.2 |
| У-3 | `issue_id` | Непрозрачный глобальный `iss_…`. Детерминирован: переживает пересчёт, разбор человеком сохраняется. В CSV сдачи `issue_id` — `I1`, `I2`… по порядку страниц; в API этих номеров нет. Примеры вида `pap_mapbi:jv_consistency:B` — иллюстрация | К-6; spec/05 §5.0 |
| У-4 | `added_by` | `auto \| manual` (а не `model \| user` из spec/03) | К-5; spec/04 §4.8–4.9 |
| У-5 | `extraction_status` | Только `extracted \| present_not_extractable \| absent`. `PATCH /facts/{id}` **не** ставит `corrected`: исправление видно по `corrected_value != null`, `corrected_raw` и `review_status = accepted` | BACKEND «Закрыто архивом»; spec/03 §3.5 (в spec/04 §4.8 устаревшая фраза) |
| У-6 | `param` и `metric_name` | `param` — ключ параметра коллекции или профиля в нижнем регистре: `pce`, `voc`, `jsc`, `ff`, `rs`, `rsh`, `hi`, `jsc_eqe`, `stabilized_pce`, `t80`, `retained_pce`, `bandgap`, `thickness`, `area`, `conditions`. Нестандартные показатели от ML — `metric_name` в нижнем регистре (`voc_deficit`). Точное имя ТЗ — в поле `metric_name` (`PCE`, `Jsc_EQE`; `null` у полевых фактов). Подписи — в `param_label` и `GET /collections` | spec/04 §4.3, §4.8; spec/03 §3.0 (в spec/03 §3.5 `param` дан как `PCE`) |
| У-7 | `target` факта | Форма spec/04: `{ "entity": "result\|material\|device\|layer\|measurement", "id": "<id из CSV>", "field": "metric_value\|bandgap_value\|layer_thickness\|active_area\|…" }` | spec/04 §4.8 (в spec/03 §3.5 форма `{table, id, column}`) |
| У-8 | Какие факты есть в волне 1 | Факт на каждую строку `results` (включая повторы с `duplicate_of`, cited и `present_not_extractable`); полевые факты: `bandgap` (`materials.bandgap_value`), `thickness` (`layers.layer_thickness` слоя `absorber`), `area` (`devices.active_area`), `conditions` (measurement). У cited `row_id = null` | spec/03 §3.0, §3.5 |
| У-9 | `rects` и якоря | `Rect = {x, y, w, h}` в долях страницы 0…1 от левого верхнего угла. От ML-1 приходит `_bbox` в пунктах — сервер переводит. Если bbox нет (мок), сервер ищет `source_fragment` или `raw_value` на `source_page`. Не нашёл — `anchor_id: null`, и фронт открывает страницу без подсветки | К-2; spec/07 §7.0.1; spec/03 §3.5 |
| У-10 | `devices.encapsulation` | `true \| false \| null` | К-7 |
| У-11 | Способы импорта | Волна 1: multipart `file` (много) и ZIP `archive`. `urls`, `upload_id`, `server_path` и `/uploads` — W2: `400 validation_error`, `field` — имя поля, `message` «Пока не поддерживается: загрузка по ссылкам появится во второй волне» | В-16; spec/13 §13.4 |
| У-12 | Коллизия имён | Файл с тем же `publication_id`, что уже есть в корпусе (в том числе в другой подпапке ZIP), попадает в `rejected` с `code: "duplicate_name"`. Суффиксы не добавляются: `publication_id` обязан совпадать с именем файла | В-17; Р-15 |
| У-13 | Что видно в UI, но не выгружается | Факты `absent`, находки ML с `confidence < 0.5`, отклонённые факты и ручные факты без страницы и цитаты есть в API (`exportable: false`), но в сдачу не попадают. Замечания со `status = ok` в сдачу не попадают | BACKEND «Остаётся открытым» п.4–5; spec/14 §14.5; REQ-041 |
| У-14 | Упавший файл | `CorpusFile.status = failed`, `error{code, message}`; у статьи `processing.status = failed`, `in_scope: null`, фактов нет. В сдаче — строка `publications` только с `publication_id` | К-14 |
| У-15 | Заглушки P2 | Заголовок `X-Faktura-Stub: 1`, форма верная, данных нет. После реализации заголовок пропадает, форма не меняется | Коорд. п.3; В-15 |
| У-16 | CORS expose | К списку BACKEND §8.5 добавлены `X-Check-Status` (нужен фронту при экспорте сдачи) и `X-Faktura-Stub` | spec/04 §4.16, spec/14 §14.6 |
| У-17 | `issue.text` и `issue.description` | Оба поля есть и совпадают: русский текст с числами обеих сторон. Числа в тексте — по-русски (запятая) | spec/03 §3.8 (`description = text`); BACKEND §8.4 |
| У-18 | `by_rule` и `plugin` | В `by_rule`, `check_name` и фильтрах — только имена из каталога spec/05 §5.2. Группы `physics`, `claims` и т. п. — только в поле `plugin` | К-8 |
| У-19 | Паспорт | Содержит все 11 оцениваемых `metric_name` (в примере spec/04 §4.17 их 9) | К-9 |
| У-20 | `/checks/accuracy` | Форма — spec/04 §4.13. Подсчёт — по правилам spec/14 §14.7 (публикация + метрика + прибор + агрегация + направление развёртки + момент времени, сравнение `normalized_value`), только 11 метрик. `linked: null`, если у эталона нет bbox. `by_source[].kind` ∈ `table, text, figure, abstract, supplementary` | К-3; Коорд. п.7 |
| У-21 | Коллекции в волне 1 | Работает только `solar`. `POST /corpora` с `collection: "rag"` или `"auto"` → `400 validation_error`, `field: "collection"`, «Пока поддерживается только «Солнечные элементы»». В `GET /collections` `rag` присутствует как задел | Коорд.: RAG — только задел |
| У-22 | `POST /events/token` | Ответ `{ "token": "evt_…", "expires_in": 60 }`. Токен одноразовый | spec/04 §4.20 (форма не задана) |
| У-23 | Потерянная история событий | Если `Last-Event-ID` старше буфера (1000 событий на рабочее пространство), сервер первым шлёт событие `stream.reset` `{ "reason": "history_lost" }`. Фронт перечитывает состояние экрана | spec/04 §4.20 (случай не описан) |
| У-24 | Скачивание выгрузки | `GET /exports/{id}` → `Export` (§3.11). При локальном хранилище `url` = `/api/v1/exports/{id}/file` (с Authorization), при S3 — подписанная ссылка на 5 мин | spec/04 §4.16; BACKEND §8.2 |
| У-25 | Профиль по умолчанию | Встроенный профиль `prf_solar_tz_v1` (псевдоним `tz-solar-v1` для CLI), `builtin: true`, правится только копией (`PATCH` → 403 `forbidden`) | spec/13 §13.3 |
| У-26 | `model_version` прогона | `"latest"` допустимо и означает текущую версию подключённого ML-1 (берётся из его `/health` в начале прогона). В ответах — фактическая версия (`extract-0.3`, у мока `mock-gold-…`) | spec/13 §13.5; spec/07 §7.0.1 |
| У-27 | Нормализованные числа | `normalized_value`, `metric_value`, `value`, `corrected_value` приходят числами JSON. Сервер считает в `Decimal` и не добавляет лишних знаков (`1.197`, а не `1.1970000001`). `spread_value` — в единицах статьи, без пересчёта (как в эталоне: `305 mV ± 32` → `normalized_value 0.305`, `spread_value 32`) | REQ-046; dev_gold D021 |
| У-28 | Дубли файлов и статей | Тот же sha256 в корпусе → `skipped_duplicate`, `duplicate_of` = `file_id` первого файла. При `reuse_existing` статья из другого корпуса связывается без разбора: файл `done`, `paper_id` — существующей статьи. Смысловой дубль (тот же DOI, затем название + год + первый автор) ставит у статьи `dup_of` и `dup_kind: "doi" \| "title"`, данные не сливаются | spec/13 §13.4; BACKEND «Остаётся открытым» п.11; REQ-074 |
| У-29 | Сдача через API — асинхронно | `POST /exports {format: "submission"}` **всегда** отвечает `202 Export` со `status: "processing"`: проверка `check_submission.py` идёт 15–90 с и больше. По готовности приходит событие `export.ready`; `GET /exports/{id}` отдаёт итог проверки в `check`, файл — `GET /exports/{id}/file` с `X-Check-Status`. Если валидатор нашёл ошибки — `status: "failed"`, `error.code: "submission_invalid"`, `error.details: {errors, warnings}`; ZIP с `check_report.txt` всё равно доступен для разбора | spec/04 §4.16, spec/14 §14.6 (там синхронно) |
| У-30 | `POST /papers` и «один прогон на корпус» | Поштучные загрузки идут в служебный корпус «Загруженные вручную». Если у него уже идёт прогон, новые файлы **добавляются в этот прогон** (`counts.total` растёт), иначе запускается прогон `selected`. 409 `conflict` для `POST /papers` не бывает | spec/04 §4.6; spec/13 §13.5 (REQ-076) |
| У-31 | Удалённый режим CLI и ключи API | Ключей API в волне 1 нет. `faktura extract --server URL` входит через `POST /auth/login` (почта и пароль из `FAKTURA_EMAIL` и `FAKTURA_PASSWORD`) и вызывает `POST /corpora`, `POST /corpora/{id}/files`, `POST /corpora/{id}/runs`, `GET /runs/{id}`, `POST /exports`, `GET /exports/{id}`, `GET /exports/{id}/file`. Когда появятся ключи, их права расширят на эти вызовы (BACKEND §2.5 сейчас даёт ключу только `GET` и `POST /papers`) | spec/13 §13.6; BACKEND §2.5 |
| У-32 | Выходная папка прогона | `GET /runs/{id}/output` есть только у прогонов, запущенных `POST /extract`: папка собирается последним шагом прогона, до `run.done`. Пока прогон идёт — 409 `run_not_finished`; у обычного прогона `output: null` и 404 `not_found` (для них есть `POST /exports`). Форма `Run.output` — §3.4 | spec/13 §13.6 |
| У-33 | Недоступный ML-1 | Если `/health` ML-1 отвечает 503 или связи нет после ретраев, прогон встаёт на паузу: `status` остаётся `running`, `paused: true`, `pause_reason: "ml_unavailable"`, файлы остаются `queued`, приходит событие `run.paused`. После восстановления — `run.resumed`. Если ML не поднялся за 30 мин, оставшиеся файлы получают `failed` с `ml_unavailable`, прогон — `done_with_errors` | spec/07 §7.0.1; REQ-065 |
| У-34 | Слияние замечаний бэка и ML | Одно замечание на пару (`check_name`, множество `record_ids`). Если правило бэка и находка ML совпали или пересеклись, остаётся одна запись: `computed_by: "backend"`, `merged: true`, `record_ids` и цитата — от ML, числа и текст — от бэка. Межстатейная сопоставимость — только фильтр, замечаний `comparability` бэк по умолчанию не создаёт (такие замечания от ML показываются) | К-15; REQ-049, REQ-056, REQ-059 |
| У-35 | Ручной факт в сдаче | Ручной факт-показатель выгружается строкой `results` (`result_id` вида `RM1`, `extraction_status: extracted`), если у него есть `source.page` и `source.quote` или `source.ref`. Без них он виден в API с `exportable: false` | REQ-041; spec/04 §4.9 |

---

## 3. Схемы сущностей

Типы: `string`, `int`, `number`, `bool`, `datetime`, `id`; `?` — может быть `null`. Полный перечень полей с источниками — spec/03. Здесь — то, что отдаёт API волны 1.

### 3.0. Ключ id у сущностей

Имена ключей взяты из спеки. Где спека использует `id`, контракт добавляет такой же ключ с префиксом сущности, чтобы фронту было удобно.

| Сущность | Ключ | Дополнительно | Источник |
|---|---|---|---|
| `User` | `id` | `user_id` (то же значение) | spec/04 §4.2 |
| `Paper` | `id` | `paper_id` (то же значение) | spec/04 §4.7 |
| `Corpus` | `corpus_id` | — | spec/13 §13.2 |
| `CorpusFile` | `file_id` | — | spec/13 §13.2 |
| `Run` | `run_id` | — | spec/13 §13.5 |
| `Profile` | `profile_id` | — | spec/13 §13.3 |
| `Fact` | `fact_id` | — | spec/04 §4.8 |
| `Anchor` | `anchor_id` | — | spec/03 §3.5 |
| `Issue` | `issue_id` | — | spec/04 §4.10 |
| `Action`, `HistoryEntry` | `action_id` | — | spec/04 §4.15 |
| `Export` | `export_id` | — | spec/04 §4.16 |

### 3.1. `User` (BACKEND §2.1, spec/04 §4.2)
```json
{ "id": "usr_01", "user_id": "usr_01", "email": "team@faktura.ai", "name": "Исследователь", "role": "Материаловед", "team": "Команда хакатона",
  "color": "graphite", "focus": "Перовскитные и тонкоплёночные солнечные элементы", "is_admin": false }
```
В `/admin/users` добавляется `disabled: bool`.

### 3.2. `Corpus` (spec/13 §13.2)
| Поле | Тип | Примечание |
|---|---|---|
| `corpus_id` | id | `crp_…` |
| `name` | string | |
| `kind` | `regular \| manual \| extract` | `manual` — служебный «Загруженные вручную» (У-30); `extract` — созданный `POST /extract` |
| `collection` | `"solar"` | У-21 |
| `profile_id` | id | по умолчанию `prf_solar_tz_v1` |
| `project_id` | id? | |
| `files_total`, `papers_done`, `papers_failed`, `facts`, `open_issues` | int | |
| `last_run_id` | id? | |
| `active_run` | `{run_id, progress, eta_s, paused}`? | для экрана «Корпус», как в `/corpus/overview` |
| `created_at`, `updated_at` | datetime | |
| `deleted_at` | datetime? | |

### 3.3. `CorpusFile` (spec/13 §13.2)
| Поле | Тип | Примечание |
|---|---|---|
| `file_id` | id | `cf_…` |
| `corpus_id` | id | |
| `name` | string | путь внутри папки или ZIP: `pdfs/2023/cigs_flex.pdf` |
| `publication_id` | string | имя файла без `.pdf` |
| `source` | `upload \| zip` | `url`, `server_path` — W2 |
| `size`, `page_count` | int? | |
| `sha256` | string? | |
| `status` | `pending \| downloading \| queued \| processing \| done \| failed \| skipped_duplicate \| needs_ocr \| cancelled` | |
| `stage` | string? | `Текст \| Таблицы \| Рисунки \| Формулы \| Нормализация \| Проверка` |
| `progress` | int | 0…100 |
| `paper_id` | id? | |
| `title` | string? | название статьи после разбора |
| `duplicate_of` | id? | `file_id` (У-28) |
| `facts`, `issues` | int | найдено фактов и открытых замечаний |
| `error` | `{code, message}`? | коды — §1.5 |
| `attempts` | int | |
| `model_version` | string? | |
| `from_cache` | bool | ответ ML взят из кеша |
| `updated_at` | datetime | |

### 3.4. `Run` — прогон (spec/13 §13.2, §13.5)
```json
{ "run_id": "run_12", "corpus_id": "crp_solar", "kind": "full", "status": "running", "progress": 41,
  "paused": false, "pause_reason": null,
  "counts": { "total": 148, "queued": 80, "processing": 4, "done": 60, "failed": 2, "skipped": 2, "cancelled": 0 },
  "facts_found": 931, "issues_found": 57, "concurrency": 4, "keep_review": true,
  "profile_id": "prf_solar_tz_v1", "profile_version": 3, "model_version": "extract-0.3", "rules_version": "1", "dictionary_version": "2026-11-20",
  "eta_s": 610, "started_at": "2026-11-20T09:00:00Z", "finished_at": null, "created_by": "usr_01", "output": null }
```
- `kind`: `full | new_only | selected | failed_only | renormalize | recheck`. `status`: `queued | running | done | done_with_errors | cancelling | cancelled | failed`.
- `paused` и `pause_reason` (`"ml_unavailable"`) — У-33.
- `output` (У-32) — `null` или:
```json
{ "available": true, "url": "/api/v1/runs/run_12/output", "csv_files": 7, "check_status": "ok", "built_at": "2026-11-20T09:20:00Z" }
```
`check_status`: `ok | warnings | failed | null` (`null` — скрипт проверки на сервере не настроен).

### 3.5. `Profile` и `ProfileField` (spec/13 §13.3)
```json
{ "profile_id": "prf_solar_tz_v1", "version": 1, "name": "ТЗ ВИНИТИ — солнечные, v1", "collection": "solar", "builtin": true,
  "fields": [ { "key": "pce", "entity": "result", "tz_name": "metric_value", "metric_name": "PCE", "label": "КПД", "type": "number",
                "unit": "%", "units": { "%": 1, "fraction": 100 }, "enum_values": null, "required": true, "synonyms": ["PCE", "efficiency", "КПД"], "enabled": true } ],
  "rules": [ { "check_name": "jv_consistency", "enabled": true, "params": { "threshold_pct": 3 } },
             { "check_name": "eqe_consistency", "enabled": true, "params": { "threshold_pct": 5 } },
             { "check_name": "comparability", "enabled": false, "params": {} } ],
  "flags": { "export_absent": false, "ml_issue_min_confidence": 0.5, "backend_rules": true } }
```
`flags` добавлены в волне 1: выгрузка `absent`, порог находок ML (У-13), выключатель правил бэка.

### 3.6. `Paper` (spec/04 §4.7, spec/03 §3.4)
Поля `GET /papers/{id}`:

| Поле | Тип | Примечание |
|---|---|---|
| `id`, `paper_id` | id | `pap_…`, одно значение (§3.0) |
| `publication_id` | string | `P042` (У-2) |
| `corpus_id` | id | |
| `title` | string | до разбора — имя файла |
| `authors` | string[] | |
| `authors_text` | string | |
| `year` | int? | |
| `doi` | string? | без `https://doi.org/` |
| `journal` | string? | |
| `collection` | `"solar"` | |
| `keywords` | string[] | |
| `page_count` | int? | |
| `main_page`, `main_table`, `main_figure` | ? | от ML; у мока `null` |
| `in_scope` | bool? | `null` у упавшей (У-14) |
| `exclusion_reason` | string? | при `in_scope = false` |
| `file` | `{name, size, sha256}` | |
| `mine` | bool | |
| `added_at` | datetime | |
| `chat_id` | id? | P2: `null`, пока чатов нет |
| `processing` | `{status, stage, progress, error, started_at, finished_at}` | `status`: `queued \| processing \| done \| failed` |
| `topic_by` | `project \| text \| guess \| manual` | в волне 1 всегда `project`: коллекция берётся из корпуса, классификатор ML-2 не вызывается |
| `dup_of`, `dup_kind` | id?, `file \| doi \| title`? | У-28 |
| `model_version` | string? | |
| `rows` | `[{row_id, label, order}]` | приборы; `*` — общие факты |
| `claims` | `[]` | от ML; в волне 1 пусто |
| `quality` | `{total, sourced_pct, normalized_pct, accepted, issues}` | |
| `coverage_summary` | `{filled, total}` | |
| `open_issues` | int | |
| `top_level` | `conflict \| doubt`? | |
| `facts_count` | int | |

### 3.7. `Fact` (spec/04 §4.8, spec/03 §3.5)
```json
{ "fact_id": "fct_4kq9w2m1z8ab", "paper_id": "pap_7d1x0c2b9qre", "publication_id": "D021",
  "row_id": "DV-B4", "row_label": "B4", "param": "pce", "param_label": "КПД", "metric_name": "PCE",
  "target": { "entity": "result", "id": "R003", "field": "metric_value" },
  "raw_value": "5.25", "raw_unit": "%",
  "normalized_value": 5.25, "normalized_unit": "%", "metric_value": 5.25, "metric_unit": "%", "normalized_text": null,
  "norm_status": "ok", "extraction_status": "extracted",
  "review_status": "new", "corrected_value": null, "corrected_raw": null, "value": 5.25, "display": "5,25 %",
  "confidence": 0.9, "added_by": "auto", "result_origin": "primary", "cited_publication_id": null,
  "aggregation": "champion", "spread_value": null, "n_devices": null, "time_point": null, "time_unit": null,
  "measurement_id": "MS1", "material_id": "M-RFMS", "scan_direction": null, "duplicate_of": null,
  "source_page": 13, "source_section": "3.4", "source_table": null, "source_figure": "Figure 10",
  "source_fragment": "exhibited PCE = 5.25%", "source_kind": "figure", "source_ref": "Figure 10", "source_where": "B4",
  "quote_found": true, "anchor_id": "anc_p13_0007", "cross": null, "note": null, "exportable": true,
  "issue_ids": ["iss_d021_contradiction_r002"], "model_version": "extract-0.3",
  "created_at": "2026-11-20T09:05:00Z", "updated_at": "2026-11-20T09:05:00Z" }
```
- `value` = `corrected_value ?? normalized_value`; `display` — `value` по-русски с единицей.
- `norm_status`: `ok | converted | failed | not_applicable | passthrough` (`passthrough` — безразмерный нестандартный показатель без единицы, например `CRI`).
- `source_kind`: `table | text | figure | abstract | supplementary | manual`.
- `duplicate_of` — `fact_id` основной записи (в CSV — её `result_id`). Фронт группирует повторы под основной записью.
- `normalized_text` — для текстовых фактов (`conditions`), иначе `null`.
- `exportable` — попадёт ли факт в сдачу (У-13, У-35).
- Служебные поля (`confidence`, `quote_found`, `review_status` и др.) в CSV сдачи не выгружаются (spec/14 §14.5).

### 3.8. `Anchor` и `Rect` (spec/03 §3.5, spec/04 §4.7)
```json
{ "anchor_id": "anc_p13_0007", "kind": "text", "page": 13, "rects": [ { "x": 0.598, "y": 0.52, "w": 0.059, "h": 0.017 } ],
  "text": "exhibited PCE = 5.25%", "fact_ids": ["fct_4kq9w2m1z8ab"], "issue_ids": ["iss_d021_contradiction_r002"] }
```
`kind`: `cell | text | figure | manual | caption | reference`.

### 3.9. `Issue` (spec/04 §4.10, spec/03 §3.8, spec/05 §5.1)
```json
{ "issue_id": "iss_d021_jv_r010", "check_name": "jv_consistency", "plugin": "physics", "scope": "paper", "collection": "solar",
  "paper_id": "pap_7d1x0c2b9qre", "publication_id": "D021",
  "severity": "warn", "ui_level": "doubt", "title": "КПД не сходится с Voc × Jsc × FF",
  "text": "PCE 2,36 % не согласуется с Voc·Jsc·FF/Pin = 3,11 % (расхождение 31,7 %)",
  "description": "PCE 2,36 % не согласуется с Voc·Jsc·FF/Pin = 3,11 % (расхождение 31,7 %)",
  "record_ids": ["R010", "R007", "R008", "R009"],
  "row_id": "DV-A1", "row_label": "A1", "param": "pce", "param_label": "КПД", "value": "2,36 %",
  "fact_ids": ["fct_…", "fct_…", "fct_…", "fct_…"], "main_fact_id": "fct_…", "anchor_ids": ["anc_…"],
  "related": [ { "entity": "result", "id": "R010" } ],
  "numbers": { "calc": 3.11, "reported": 2.36, "diff_pct": 31.7, "threshold_pct": 3 },
  "source_page": 6, "source_fragment": "Table 2, строка A1",
  "status": "open", "resolved_by": null, "resolved_at": null, "touched_fact_ids": [],
  "computed_by": "backend", "merged": false, "confidence": null, "rule_version": "1", "stale": false,
  "created_at": "2026-11-20T09:05:00Z", "updated_at": "2026-11-20T09:05:00Z" }
```
- `severity`: `error | warn | info`; `ui_level`: `conflict | doubt | warn`; `status`: `open | fixed | ok`; `computed_by`: `backend | ml`; `merged` — запись объединяет правило бэка и находку ML (У-34).
- `scope` у замечания: `paper` (проверки внутри статьи — все в волне 1).
- `record_ids` — `result_id` из CSV. Могут быть пустыми: замечание о статье в целом.
- `source_page`, `source_fragment` — у замечаний бэка всегда заполнены (из записи-якоря). У находок ML могут быть `null`.
- `check_name` — только каталог spec/05 §5.2. `severity`, `ui_level`, `plugin` и `title` берутся из каталога.

### 3.10. `HistoryEntry` и `Action` (spec/04 §4.15, spec/03 §3.9)
```json
{ "action_id": "act_95", "at": "2026-11-20T08:57:00Z", "who": "Исследователь", "user_id": "usr_01", "action": "edit", "label": "исправление",
  "paper_id": "pap_…", "paper_title": "…", "fact_id": "fct_…", "param_label": "Voc", "row_label": "образец A",
  "from": "1100 мВ", "to": "1,08 В", "count": null, "undone": false }
```
`action` в журнале: `accept | reject | restore | edit | add | acceptAll`. Служебные действия (`issue_resolve`, `corpus_create`, `profile_update`, `export`…) в журнале не показываются; отменяемые из них отменяются через `/actions/{id}/undo`.

### 3.11. `Export` (spec/04 §4.16)
```json
{ "export_id": "exp_3k9m2q7x1aa", "format": "submission", "status": "done",
  "scope": { "corpus_id": "crp_solar" }, "papers": 90, "facts": 1412,
  "url": "/api/v1/exports/exp_3k9m2q7x1aa/file", "filename": "submission_crp_solar_20261120.zip",
  "expires_at": "2026-11-21T09:30:00Z",
  "check": { "status": "warnings", "errors": 0, "warnings": 3, "quotes_checked": 251, "quotes_not_found": 1, "script": "configured" },
  "error": null, "action_id": "act_120", "created_at": "2026-11-20T09:28:00Z", "finished_at": "2026-11-20T09:29:31Z" }
```
- `status`: `processing | done | failed`. `url` пуст, пока `processing`.
- `check` — только у `submission` с `validate: true`. `script: "not_configured"`, если на сервере нет `check_submission.py`: тогда `status` считается только по строгому валидатору бэка.
- `error` при `failed`: `{code: "submission_invalid" | "nothing_to_export" | "internal_error", message, details}`.

---

## 4. Эндпоинты

Каждая группа: таблица (метод, путь, приоритет, раздел спеки) и ключевые запросы и ответы. Номер `№` — строка сводной таблицы spec/04 §4.22.

### 4.1. Служебные (spec/04 §4.21)

| № | Метод и путь | P | Ответ |
|---|---|---|---|
| 95 | `GET /health` | P1 | `200 {"status":"ok","db":"ok","storage":"ok","ml":"ok\|degraded","ml_model_version":"extract-0.3"}`. Без авторизации. `ml: degraded`, если `/health` ML-1 отвечает 503 или недоступен (у мока — `ok`); `status` при этом `ok`. Если недоступна БД или хранилище — `503` с `status: "error"` и `db`/`storage: "error"` |

### 4.2. Вход (BACKEND §2, spec/04 §4.1)

| № | Метод и путь | P | Тело → ответ | Ошибки |
|---|---|---|---|---|
| 1 | `POST /auth/login` | P1 | `{email, password, remember}` → `{access_token, token_type:"Bearer", expires_in:900, user}` + cookie refresh | 400 `validation_error`, 401 `invalid_credentials`, 403 `account_disabled`, 429 `too_many_attempts` |
| 2 | `POST /auth/refresh` | P1 | cookie (или `{refresh_token}`) → `{access_token, token_type, expires_in}` + новая cookie | 401 `refresh_invalid` |
| 3 | `POST /auth/logout` | P1 | `{all?: bool}` → 204 | — |
| 4 | `GET /auth/me` | P1 | → `User` | 401 |
| 5 | `POST /auth/register` | P1 | → 403 `registration_disabled`. При `FAKTURA_ALLOW_SIGNUP=true`: `{name, email, password}` → как login | 400 (`field: "password"`, короче 8), 409 `email_taken` |
| — | `/auth/oauth/*`, `/auth/password/*` | — | позже, маршрутов в волне 1 нет | |

Пример:
```http
POST /api/v1/auth/login
Content-Type: application/json

{ "email": "team@faktura.ai", "password": "s3cret-pass", "remember": true }
```
```http
200 OK
Set-Cookie: faktura_refresh=rt_5Yk3…; HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth; Max-Age=2592000
X-Request-Id: 3f2a…

{ "access_token": "eyJhbGciOi…", "token_type": "Bearer", "expires_in": 900,
  "user": { "id": "usr_01", "user_id": "usr_01", "email": "team@faktura.ai", "name": "Исследователь", "role": "Материаловед", "team": "Команда хакатона", "color": "graphite", "focus": "…", "is_admin": false } }
```
При `remember: false` — `Max-Age=43200` (12 ч).

### 4.3. Профиль пользователя и коллекции (spec/04 §4.2–4.3)

| № | Метод и путь | P | Примечание |
|---|---|---|---|
| 6 | `GET /me` | P1 | `User` |
| 7 | `PATCH /me` | P1 | `name` (1..80), `role`, `team`, `color`, `focus`; `email` → 400 `field: "email"` |
| 8 | `GET /me/stats` | P2 | `{papers, mine, facts, accepted, edited, rejected, issues, projects, questions}` |
| 9 | `/me/api-keys` | — | позже (У-31) |
| 10 | `GET /collections` | P1 | форма spec/04 §4.3; у `solar` параметры дополнены `rs, rsh, hi, jsc_eqe, stabilized_pce, t80, retained_pce, area` (У-6); `rag` — задел |
| 11 | `GET /collections/{id}/stats` | P2 | `{papers, facts, processing, open_issues}` |

### 4.4. Корпус (spec/13 §13.2, §13.4, §13.8)

| № | Метод и путь | P | Тело / параметры → ответ | Ошибки |
|---|---|---|---|---|
| 99 | `GET /corpus/overview` | P1 | `collection?` → форма spec/13 §13.8; `empty: true` без корпусов. Из кеша агрегатов, до 100 мс | — |
| 100 | `POST /corpora` | P1 | `{name, collection:"solar", profile_id?, project_id?}` → 201 `Corpus` + `action_id` | 400 (У-21) |
| 101 | `GET /corpora` | P1 | `collection?`, `limit`, `cursor` → список `Corpus` | |
| 102 | `GET /corpora/{id}` | P1 | → `Corpus` | 404 |
| 103 | `PATCH /corpora/{id}` | P1 | `name`, `profile_id`, `project_id` → `Corpus` + `action_id` | 400, 404 |
| 104 | `DELETE /corpora/{id}` | P1 | → `200 {action_id}`; мягко, вместе со статьями | 404, 409 (идёт прогон) |
| 105 | `POST /corpora/{id}/restore` | P1 | → `Corpus` + `action_id` | 410 `gone` |
| 106 | `POST /corpora/{id}/files` | P1 | multipart `file` (до 200 шт., 1 ГБ) **или** `archive` (ZIP до 2 ГБ) → 202 `{accepted, duplicates, rejected[], files[]}` | 413, 415, 422 `zip_too_large`/`zip_bomb`; 400 для `urls`/`upload_id`/`server_path` (W2, У-11) |
| 107 | `POST/PATCH /uploads…` | W2 | 400 `validation_error` | |
| 108 | `GET /corpora/{id}/files` | P1 | `status` (повт.), `q`, `has_errors`, `sort` (`name\|status\|facts\|issues\|updated_at`), `dir`, `limit ≤ 500`, `cursor` → список `CorpusFile` | 404 |
| 109 | `POST /corpora/{id}/files/{file_id}/retry` | P1 | → 202 `Run` (вид `selected`) | 404, 409 |
| 116 | `GET /corpora/{id}/summary` | P1 | форма spec/13 §13.8 | 404 |

Пример загрузки ZIP:
```http
POST /api/v1/corpora/crp_solar/files
Authorization: Bearer …
Content-Type: multipart/form-data; boundary=…

--…
Content-Disposition: form-data; name="archive"; filename="test.zip"
Content-Type: application/zip
…
```
```json
202
{ "accepted": 89, "duplicates": 1,
  "rejected": [ { "name": "readme.txt", "code": "unsupported_file", "message": "Можно загрузить только PDF" },
                { "name": "copy/P001.pdf", "code": "duplicate_name", "message": "Файл с именем P001 уже есть в корпусе" } ],
  "files": [ { "file_id": "cf_001", "name": "P001.pdf", "publication_id": "P001", "status": "pending", "sha256": "9f2c…" } ] }
```
Файлы после приёма — `pending`. Разбор начинается только прогоном (`POST /corpora/{id}/runs`). Исключение — `POST /papers` (У-30).

Сводка `GET /corpora/{id}/summary` — форма spec/13 §13.8. Уточнения: `issues.by_rule[].check_name` — только каталог (У-18); `coverage[]` и `not_found[]` — по полям профиля (считаются тем же кодом, что `/papers/{id}/coverage`); `errors[]` — коды из §1.5.

### 4.5. Прогоны (spec/13 §13.5–13.6)

| № | Метод и путь | P | Тело → ответ | Ошибки |
|---|---|---|---|---|
| 110 | `POST /corpora/{id}/runs` | P1 | `{kind, file_ids?, profile_id?, model_version?:"latest", concurrency?:4, keep_review?:true}` → 202 `Run` | 400, 404, 409 `conflict` «Прогон уже идёт» |
| 111 | `GET /corpora/{id}/runs` | P1 | список `Run`, новые сверху | |
| 112 | `GET /runs/{run_id}` | P1 | `Run` (опрос раз в 2–5 с, если SSE недоступен) | 404 |
| 113 | `POST /runs/{run_id}/cancel` | P1 | → 202 `Run` со `status: "cancelling"` | 404, 409 (уже завершён) |
| 114 | `GET /runs/{run_id}/output` | P1 | ZIP выходной папки: 7 CSV + `_meta/` (как у CLI), `Content-Disposition: attachment; filename="run_12.zip"`, `X-Check-Status` | 404 (прогон не из `/extract`, У-32), 409 `run_not_finished` |
| 115 | `POST /extract` | P1 | multipart `archive` + `domain=solar` + `profile_id?` → 202 `{run_id, corpus_id}`. Создаёт корпус `kind: "extract"` «Извлечение <дата>» и сразу запускает `full` со сборкой выходной папки | 400, 413, 415, 422 |

Пример:
```json
POST /api/v1/corpora/crp_solar/runs
{ "kind": "new_only", "concurrency": 4, "keep_review": true }

202
{ "run_id": "run_12", "corpus_id": "crp_solar", "kind": "new_only", "status": "queued", "progress": 0, "paused": false, "pause_reason": null,
  "counts": { "total": 90, "queued": 90, "processing": 0, "done": 0, "failed": 0, "skipped": 0, "cancelled": 0 },
  "facts_found": 0, "issues_found": 0, "model_version": "extract-0.3", "profile_version": 1, "eta_s": null, "started_at": null, "output": null }
```

### 4.6. Профили и паспорт (spec/13 §13.3, spec/04 §4.17)

| № | Метод и путь | P | Примечание |
|---|---|---|---|
| 118 | `GET /profiles?collection=` | P1 | `{data:[Profile]}` |
| 119 | `GET /profiles/{id}` | P1 | `Profile`; 404 |
| 120 | `POST /profiles` | P1 | `{name, collection, base_profile_id, fields?, rules?, flags?}` → 201 `Profile` + `action_id` |
| 121 | `PATCH /profiles/{id}` | P1 | → `{profile, rerun_hint: "full"\|"renormalize"\|"recheck"\|null, action_id}`; встроенный → 403 `forbidden` |
| 91 | `GET /passport?collection=&profile_id=&format=json\|md\|xlsx` | P1 (json, md), P2 (xlsx) | форма spec/04 §4.17, все 11 метрик (У-19); `md` → `text/markdown; charset=utf-8` |

### 4.7. Статьи (spec/04 §4.6–4.7)

| № | Метод и путь | P | Примечание |
|---|---|---|---|
| 37 | `GET /papers` | P1 | `corpus_id`, `collection`, `status`, `mine`, `q`, `ids`, `in_scope`, `sort` (`added_at\|year\|title\|publication_id`), `dir`, `limit`, `cursor` |
| 38 | `GET /papers/{id}` | P1 | `Paper` (§3.6); 404 |
| 39 | `GET /papers/{id}/pages/{n}` | P1 | `scale` (1, 1.5, 2), `format` (`webp\|png`; без Pillow на сервере — всегда `png`) → изображение, `ETag`, `Cache-Control: private, max-age=86400`; 304 при `If-None-Match`; 404 |
| 40 | `GET /papers/{id}/file` | P1 | `application/pdf`, `Content-Disposition: inline`, поддержка `Range` |
| 41 | `GET /papers/{id}/layout` | P1 | `pages=4` или `3-5`. В волне 1 заполнены только `pages[]` (`n, width_pt, height_pt, image_url, has_text_layer`); `blocks`, `tables`, `figures` — пустые массивы, пока нет `/ml/parse` ML-2 |
| 42 | `GET /papers/{id}/anchors` | P1 | `page?` → `{data:[Anchor]}` |
| 43 | `GET /papers/{id}/coverage` | P1 | форма spec/04 §4.7; `params` — поля профиля |
| 44 | `GET /papers/{id}/structure` | P1 | `variants[].layers` из `layers` (подложка не пишется — BACKEND «Остаётся открытым» п.7), `conditions` из `measurements`, `pipeline: null` |
| 45 | `GET /papers/{id}/links` | P2 | `{references:[], summary:{refs:0, unparsed:0, cited_by:0}, cited_by:[], entities:[]}` |
| 46 | `GET /papers/{id}/cited-by` | P2 | `{data:[]}` |
| 33 | `GET /papers/{id}/processing` | P1 | форма spec/04 §4.6 |
| 34 | `PUT /papers/{id}/collection` | P2 | `{collection}`. В волне 1 есть только `solar` (У-21): `"solar"` → 202 `Paper` без изменений с `X-Faktura-Stub: 1`; другое значение → 400 `validation_error`, `field: "collection"` |
| 35 | `POST /papers/{id}/reprocess` | P1 | `{keep_review?: true}` → 202 `Paper` (прогон `selected` по файлу статьи; 409, если у корпуса идёт прогон, кроме служебного — У-30) |
| 36 | `DELETE /papers/{id}` | P1 | → `{action_id}`; мягко |
| — | `POST /papers/{id}/restore` | P1 | → `Paper` + `action_id`; 410 `gone` |
| 32 | `POST /papers` | P1 | multipart `file` (до 20), `collection?` → 202 `{data:[{paper, chat: null, action_id}]}`. Статья попадает в служебный корпус «Загруженные вручную» и разбирается в его активном прогоне или новом (У-30). Ошибки по отдельным файлам — 207 `{data, errors:[{name, code, message}]}` |

### 4.8. Факты (spec/04 §4.8, spec/13 §13.7)

| № | Метод и путь | P | Примечание |
|---|---|---|---|
| 47 | `GET /facts` | P1 | параметры spec/04 §4.8 (+ `corpus_id`, `metric_name`, `result_origin`, `exportable`); по умолчанию без `rejected` |
| 48 | `GET /papers/{id}/facts` | P1 | все факты статьи, включая отклонённые; без пагинации: `{data:[Fact]}` |
| 49 | `GET /facts/{id}` | P1 | `Fact`; 404 |
| 50 | `GET /facts/{id}/source` | P1 | `{fact_id, paper_id, paper, page, kind, ref, where, section, anchor_id, rects, quote}` |
| 51 | `GET /facts/{id}/trace` | P1 | `{steps:[{k, v}]}` |
| 52–54 | `POST /facts/{id}/accept\|reject\|restore` | P1 | тело `{group_id?}` → `{fact, action_id, issues_changed, paper}`; 404; 422 `paper_not_ready` |
| 55 | `PATCH /facts/{id}` | P1 | `{raw}` → как accept; 422 `fact_value_unrecognized` (`field: "raw"`). `extraction_status` не меняется (У-5) |
| 56 | `POST /facts/bulk` | P1 | `{action, fact_ids}` или `{action, filter}` → `{count, action_id, fact_ids}`; 400 без обоих или при > 5000 |
| 117 | `GET /facts/table` | P1 | параметры spec/13 §13.7 → `{data, total, next_cursor, filters, unparsed, facets?}` |

Пример `GET /facts/table`:
```http
GET /api/v1/facts/table?corpus_id=crp_solar&q=КПД%20выше%2020%20после%202022&has_issues=true&sort=value&dir=desc&limit=100&facets=true
```
```json
{ "data": [ { "fact_id": "fct_…", "param": "pce", "label": "КПД", "metric_name": "PCE", "raw_value": "21.3", "raw_unit": "%",
              "normalized_value": 21.3, "unit": "%", "display": "21,3 %",
              "paper_id": "pap_…", "publication_id": "P017", "paper_title": "…", "year": 2023, "row_id": "DV2", "row_label": "PSC-2",
              "source_page": 7, "source_kind": "table", "source_ref": "Table 2", "source_where": "строка PSC-2", "anchor_id": "anc_…",
              "review_status": "new", "extraction_status": "extracted", "confidence": 0.95, "aggregation": "champion", "result_origin": "primary",
              "exportable": true,
              "issues": [ { "issue_id": "iss_…", "check_name": "jv_consistency", "severity": "warn" } ] } ],
  "total": 37, "next_cursor": "eyJrIjpbMjEuMywiZmN0XyJdfQ",
  "filters": [ { "id": "num:pce:min", "label": "КПД > 20 %", "match": "КПД выше 20", "kind": "num", "param": "pce", "op": ">", "value": 20 },
               { "id": "year:min", "label": "после 2022 года", "match": "после 2022", "kind": "year", "op": ">", "value": 2022 } ],
  "unparsed": "",
  "facets": { "param": { "pce": 37 }, "review_status": { "new": 30, "accepted": 7 }, "severity": { "warn": 5 }, "year": { "2023": 12, "2024": 25 } } }
```
`unparsed` добавлено к форме §13.7 по аналогии с `/data/rows` (spec/04 §4.11): так фронт видит, что из строки запроса не понято.

Пример правки:
```json
PATCH /api/v1/facts/fct_…   { "raw": "1100 мВ" }

200   (X-Action-Id: act_96)
{ "fact": { "fact_id": "fct_…", "param": "voc", "raw_value": "1.08", "raw_unit": "V", "normalized_value": 1.08,
            "corrected_raw": "1100 мВ", "corrected_value": 1.1, "value": 1.1, "display": "1,10 В",
            "review_status": "accepted", "extraction_status": "extracted", "…": "…" },
  "action_id": "act_96", "issues_changed": { "opened": ["iss_…"], "closed": [] },
  "paper": { "quality": { "total": 18, "sourced_pct": 100, "normalized_pct": 100, "accepted": 1, "issues": 3 }, "open_issues": 3, "top_level": "doubt" } }
```

### 4.9. Ручные факты и предпросмотр (spec/04 §4.9)

| № | Метод и путь | P | Примечание |
|---|---|---|---|
| 57 | `POST /papers/{id}/facts/guess` | P1 | тело и ответ — spec/04 §4.9 |
| 58 | `POST /normalize/preview` | P1 | `{collection, param, raw}` → `{ok, value, unit, text}`; всегда 200 |
| 59 | `POST /papers/{id}/facts` | P1 | тело spec/04 §4.9 → 201 `{fact, action_id, issues_changed, paper}`; `manual`: `accepted`, confidence 1; `figure`: `new`, 0.7. Для выгрузки в сдачу нужны `source.page` и `source.quote` (или `source.ref`), иначе `fact.exportable: false` (У-35). Ошибки: 400, 422 `fact_value_unrecognized`, 422 `paper_not_ready` |

### 4.10. Замечания и проверки (spec/04 §4.10, §4.13)

| № | Метод и путь | P | Примечание |
|---|---|---|---|
| 60 | `GET /issues` | P1 | `corpus_id`, `paper_id`, `collection`, `papers`, `status` (`open` по умолчанию `\|fixed\|ok\|resolved\|all`), `check_name`, `plugin`, `severity`, `ui_level`, `fact_id`, `computed_by`, `sort` (`severity\|created_at\|page`), `limit`, `cursor` → список `Issue` |
| 61 | `GET /papers/{id}/issues` | P1 | `status=all` по умолчанию; `{data:[Issue]}` |
| 62 | `POST /issues/resolve` | P1 | `{issue_ids, how:"fixed"\|"ok", accept_fact_id?, merge_action_ids?}` → `{resolved, accepted_fact_id, action_id, message, issues_changed, paper}`; 409 `already_resolved` |
| 63 | `POST /issues/{id}/reopen` | P1 | → `{issue, restored_fact_ids, action_id, message, issues_changed, paper}` |
| 76 | `GET /checks?collection=` | P1 | форма spec/04 §4.13; `rules[].id` = `check_name` |
| 77 | `GET /checks/rules` | P1 | `{data:[{id, title, what, level, collections, computed_by, tz_ref, enabled_by_default}]}` — каталог spec/05 §5.2 |
| 78 | `GET /checks/accuracy?collection=&corpus_id=` | P2-R | форма spec/04 §4.13, подсчёт по У-20; 404 `not_found` «Эталонная разметка не загружена» |

### 4.11. Журнал и отмена (spec/04 §4.15)

| № | Метод и путь | P | Примечание |
|---|---|---|---|
| 85 | `GET /history` | P1 | `action`, `paper_id`, `user_id` (`me`), `from`, `to`, `limit` (100), `cursor` → `{data:[HistoryEntry], next_cursor}` |
| 86 | `POST /history/undo` | P1 | → `{undone_action_id, label, message, fact_id}`; если нечего — `200 {undone_action_id: null, message: "Отменять нечего"}` |
| 87 | `POST /actions/{id}/undo` | P1 | → `{undone_action_id, label, message, fact_id, issues_changed}`; 409 `conflict`, 409 `already_undone`, 404 |

Глубина отмены — 50 последних действий пользователя за 24 часа. Удаления (корпус, статья) возвращаются через `…/restore` в течение 30 суток.

### 4.12. Экспорт (spec/04 §4.16, spec/14 §14.6)

| № | Метод и путь | P | Примечание |
|---|---|---|---|
| 88 | `POST /exports/preview` | P2 | `ExportOptions` → `{head, rows, facts, papers}` (первые 50 строк) |
| 89 | `POST /exports` | P1 (`submission`), P2 (`csv\|xlsx\|json\|tsv`) | см. ниже |
| 90 | `GET /exports/{id}` | P1 | `Export` (§3.11) |
| — | `GET /exports/{id}/file` | P1 | файл выгрузки; для `submission` — `X-Check-Status`; 409 `conflict`, пока `processing`; 410 `gone` после 24 ч |

**Сдача** — главный формат, всегда асинхронно (У-29):
```json
POST /api/v1/exports
Idempotency-Key: 7c1e…
{ "format": "submission", "scope": { "corpus_id": "crp_solar" }, "validate": true }

202   (X-Action-Id: act_120)
{ "export_id": "exp_3k9m2q7x1aa", "format": "submission", "status": "processing", "url": null, "check": null, "error": null, "action_id": "act_120", "…": "…" }
```
Далее событие `export.ready` `{export_id, status, url}` → `GET /exports/{id}` (форма §3.11) → `GET /exports/{id}/file`:
```http
200 OK
Content-Type: application/zip
Content-Disposition: attachment; filename="submission_crp_solar_20261120.zip"
X-Check-Status: warnings
```
ZIP: `publications.csv`, `materials.csv`, `devices.csv`, `layers.csv`, `measurements.csv`, `results.csv`, `issues.csv` и `check_report.txt`. CSV — UTF-8 с BOM, `,`, `\r\n`, точка в числах (spec/14 §14.2). Выгрузка собирается тем же кодом, что `faktura extract`.

`scope` для `submission`: `corpus_id` (обязательно в волне 1) или `paper_ids`. Правила выборки — spec/14 §14.5, У-13, У-35. Без SSE — опрос `GET /exports/{id}` раз в 2–5 с.

Форматы для людей (`csv` — `;`, CRLF, десятичная запятая; `xlsx`; `json` `flat|relational`; `tsv`) — P2, форма spec/04 §4.16. Синхронно до 50 тыс. фактов, больше — как сдача, через 202.

### 4.13. События SSE (spec/04 §4.20, spec/13 §13.5)

| № | Метод и путь | P | Примечание |
|---|---|---|---|
| 93 | `GET /events` | P1 | `text/event-stream`; авторизация — `Authorization` или `?token=`; `Last-Event-ID`; пинг `: ping` каждые 25 с |
| 94 | `POST /events/token` | P1 | → `{token, expires_in: 60}` (У-22) |

Формат кадра:
```
id: 1042
event: run.progress
data: {"run_id":"run_12","corpus_id":"crp_solar","progress":41,"counts":{"total":90,"queued":49,"processing":4,"done":36,"failed":1,"skipped":0,"cancelled":0},"facts_found":512,"issues_found":31}

```

| Событие | Данные | P |
|---|---|---|
| `run.progress` | `{run_id, corpus_id, progress, counts, facts_found, issues_found, eta_s}` — не чаще раза в секунду | P1 |
| `run.file` | `{run_id, corpus_id, file_id, paper_id, status, stage, facts, issues, error}` | P1 |
| `run.paused` | `{run_id, corpus_id, reason: "ml_unavailable", retry_in_s}` (У-33) | P1 |
| `run.resumed` | `{run_id, corpus_id}` | P1 |
| `run.done` | `{run_id, corpus_id, status, counts, output}` | P1 |
| `paper.progress` | `{paper_id, stage, progress}` | P1 |
| `paper.updated` | `{paper_id, fields}` | P1 |
| `paper.done` | `{paper_id, chat_id: null, title, facts, issues}` | P1 |
| `paper.failed` | `{paper_id, code, message}` | P1 |
| `facts.changed` | `{paper_id, fact_ids, by_user}` | P1 |
| `issues.changed` | `{paper_id, opened, closed}` | P1 |
| `export.ready` | `{export_id, status, url}` | P1 |
| `dictionary.renormalized` | `{collection, papers}` | P2 |
| `stream.reset` | `{reason: "history_lost"}` — фронт перечитывает состояние (У-23) | P1 |

Запасной вариант без SSE: опрос `GET /runs/{id}` раз в 2–5 с, `GET /papers/{id}/processing`, `GET /exports/{id}`.

### 4.14. Вторичные экраны (P2 и P2-R)

| № | Метод и путь | P | Форма ответа (у заглушки — `X-Faktura-Stub: 1`) |
|---|---|---|---|
| 64 | `GET /data/rows` | P2 | `{query, filters, unparsed, metric, data: [], total: 0, next_cursor: null, stats: {rows: 0, papers: 0, metric: null}}`; `filters` — уже настоящий разбор запроса |
| 65 | `GET /data/filter-options` | P2 | `{data: []}` |
| 66 | `GET /data/query/parse` | P1 | `{filters, unparsed, metric}` — настоящий разбор (нужен таблице фактов); `strict=true` → 400 `query_unparsed` |
| 67 | `GET /data/compare` | P2 | `{collection, metric, columns: [], params: [], conditions: [], comparable: {ok: true, reasons: []}, conclusions: []}`; 400 при < 2 или > 3 id |
| 68–72 | `/views…` | P2 | `{data: []}`; `POST` → 201 с переданными полями, `id` и `action_id` |
| 73 | `GET /graph` | P2 | `{nodes: [], edges: [], default_root: null}` |
| 74 | `GET /graph/edges.csv` | P2 | CSV только с заголовком `source,target,type,source_label,target_label` |
| 75 | `GET /entities/{id}/papers` | P2 | `{entity: {entry_id, canon, name}, data: []}`; 404 для неизвестного |
| 79 | `GET /dictionary` | P2-R | `{params: [], units: [], materials: [], conditions: []}` — из словаря нормализатора |
| 80–84 | `POST/DELETE /dictionary/…` | P2 | заглушка `POST` → 201 `{synonym, renormalize: {papers: 0, job_id: null}, action_id}`; `DELETE` встроенного → 403 |
| 92 | `GET /search` | P2 | `{q, layout_fixed: null, groups: []}` |
| 12–18 | `/projects…` | P2 | `{data: []}`; `POST` → 201 проект; `GET /projects/{id}` → 404 |
| 19–31 | `/chats…`, `/turns…` | P2 | `GET /chats` → `{data: [], total: 0, next_cursor: null}`; `POST /chats/ask` → 201 чат с одной репликой и потоком событий `step` → `outro` («Ответы в чате появятся позже») → `done` |
| 97 | `POST /admin/gold` | P2-R (`dir`), P2 (`items`) | `{dataset, dir: "dev_gold"}` — загрузить 7 CSV эталона из каталога кейса на сервере (только админ) → `{dataset, papers, results}`. Вариант `{dataset, items}` (spec/04 §4.21) — заглушка |
| 98 | `POST /admin/reprocess` | P2-R | `{model_version?, corpus_id?}` → 202 `Run` (`kind: "full"`, `keep_review: true`) |

### 4.15. Администрирование (BACKEND §2.2, spec/04 §4.21)

| № | Метод и путь | P | Примечание |
|---|---|---|---|
| 96 | `GET /admin/users` | P1 | только `is_admin`; список `User` + `disabled` |
| 96 | `POST /admin/users` | P1 | `{email, name, role?, team?, is_admin?, password? \| generate: true}` → 201 `{user, password?}` (пароль — один раз, только при `generate`); 400 (`field: "password"`, короче 8); 409 `email_taken` |
| 96 | `PATCH /admin/users/{id}` | P1 | `{disabled?, is_admin?, name?, password?}`; 400 для короткого пароля |

---

## 5. Сценарии фронта

**Вход и первый экран.**
`POST /auth/login` → `GET /corpus/overview` → при `empty: true` — пустое состояние «Загрузите папку или ZIP с PDF».

**Импорт и прогон.**
`POST /corpora` → `POST /corpora/{id}/files` (пачками до 200 файлов или ZIP) → `POST /corpora/{id}/runs {kind: "new_only"}` → `POST /events/token` → `GET /events?token=…` (`run.progress`, `run.file`, `run.paused`/`run.resumed`, `run.done`) → `GET /corpora/{id}/summary`, `GET /corpora/{id}/files`. При `paused: true` фронт показывает «Сервис разбора временно недоступен — продолжим автоматически».

**Загрузка по одному файлу.**
`POST /papers` (каждый файл отдельно, с `Idempotency-Key`) → `paper.done` / `paper.failed` по каждому. 409 не бывает (У-30).

**Таблица фактов и источник.**
`GET /facts/table?corpus_id=…&q=…` → по строке: `GET /papers/{paper_id}/anchors?page=N` + `GET /papers/{paper_id}/pages/{N}` → подсветка `rects` найденного `anchor_id`.

**Разбор статьи.**
`GET /papers/{id}` → `GET /papers/{id}/facts` → `GET /papers/{id}/issues` → действия `POST /facts/{id}/accept|reject`, `PATCH /facts/{id}`, `POST /issues/resolve` → обновить шапку из `paper` в ответе → «Отменить» из уведомления: `POST /actions/{action_id}/undo`.

**Экспорт сдачи.**
`POST /exports {format: "submission", scope: {corpus_id}, validate: true}` → 202 → ждать `export.ready` (или опрашивать `GET /exports/{id}`) → при `status: done` скачать `GET /exports/{id}/file`, показать `check.status`; при `failed` показать `error.message` и дать скачать ZIP с `check_report.txt`. Паспорт: `GET /passport?collection=solar&format=md`.

---

## 6. Сводная таблица и статус в волне 1

| № (spec/04 §4.22) | Группа | P |
|---|---|---|
| 1–5 | Вход | P1 |
| 6–7 | Профиль пользователя | P1 |
| 8, 11 | Статистика пользователя и коллекции | P2 |
| 9 | API-ключи | позже |
| 10 | Коллекции | P1 |
| 12–31 | Проекты, чаты, реплики | P2 (заглушки) |
| 32–44 | Загрузка, статья, страницы, якоря, покрытие, структура | P1 (34 — P2) |
| 45–46 | Связи статьи | P2 |
| 47–59 | Факты, ручные факты, предпросмотр | P1 |
| 60–63 | Замечания | P1 |
| 64–65, 67–75 | Обзор, сравнение, виды, граф | P2 |
| 66 | Разбор запроса | P1 |
| 76–77 | Проверки | P1 |
| 78 | Точность извлечения | P2-R |
| 79 | Чтение словаря | P2-R |
| 80–84 | Правка словаря | P2 |
| 85–87 | Журнал и отмена | P1 |
| 88 | Предпросмотр экспорта | P2 |
| 89–91 | Экспорт (`submission`), статус, паспорт | P1 (форматы для людей и xlsx — P2) |
| 92 | Поиск | P2 |
| 93–95 | События, health | P1 |
| 96 | Пользователи (админ) | P1 |
| 97–98 | Эталон из каталога, перезапуск | P2-R |
| 99–106, 108–121 | Корпус, файлы, прогоны, `/extract`, сводка, таблица фактов, профили | P1 |
| 107 | Resumable-загрузка | W2 |

Внутренние эндпоинты ML (`/internal/ml/*`, вебхуки, spec/07 §7.3) в волне 1 не нужны: ML-1 вызывается синхронно (`POST /ml/extract`). Фронт их никогда не вызывает.

---

## 7. Журнал изменений контракта

| Версия | Дата | Что |
|---|---|---|
| 1.0 | 2026-11-20 | Первая редакция: сведены эндпоинты spec/04 §4.22 и spec/13, приоритеты волны 1, уточнения У-1…У-28 |
| 1.1 | 2026-11-20 | До интеграции с фронтом. **Несовместимо с 1.0:** `POST /exports {submission}` теперь всегда 202 (У-29); `GET /runs/{id}/output` для незавершённого прогона — 409 `run_not_finished` вместо 422 (У-32); `submission_invalid` — код в `Export.error`, а не HTTP 422. **Добавлено:** §3.0 (ключи id), `paper_id`/`user_id` рядом с `id`; `Corpus.kind`; `Run.paused`, `pause_reason`, типизированный `Run.output`; `CorpusFile.from_cache`; `Fact.exportable`, `norm_status: passthrough`; `Issue.merged`; схема `Export` (§3.11); события `run.paused`, `run.resumed`; `POST /papers/{id}/restore`; метка P2-R (точность, эталон из каталога, чтение словаря, перезапуск); уточнения У-29…У-35 (асинхронная сдача, `POST /papers` без 409, удалённый CLI без ключей, выходная папка, пауза ML, слияние замечаний, ручные факты в сдаче); лимит ZIP 2 ГБ и TTL refresh при `remember: false` |
