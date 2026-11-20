# 4. API

[← К оглавлению](../BACKEND_SPEC.md)

Префикс `/api/v1`, форматы, ошибки и пагинация — §0 основного файла. У каждого эндпоинта строка **«Источник»** — откуда поля ответа: [ML] извлечено моделью, [BE] вычислено бэком, [USER] введено пользователем. Поля сущностей с источником по каждому полю — [§3](03-data-model.md).

Все изменяющие запросы возвращают `action_id` (и заголовок `X-Action-Id`), если действие отменяемо.

Сводная таблица всех эндпоинтов — [§4.22](#422-сводная-таблица-эндпоинтов).

> **Интерфейс вызывает эндпоинты §15** ([15-runs.md](15-runs.md)): разборы, 7 таблиц статьи (`GET /papers/{domain}/{pid}`), `GET /results`, `GET /issues`, PDF, правки, экспорт по разбору. Где описание ниже расходится с §15 (форма `GET /papers/{id}`, `GET /issues`), верен §15. Разделы 4.4, 4.5 и граф прототипа 4.12 — **[В ФОНЕ]**; вопросы внутри разбора и новый граф — §15.9, §15.10.
>
> **Пакетный поток** (корпус, прогоны, таблица фактов корпуса, сводка, CLI): эндпоинты описаны в [§13](13-corpus.md) и включены в сводную таблицу (строки 99–121). Эндпоинты этого файла — общие (статьи, факты, замечания, словарь, экспорт) и дополнительные сценарии (чат, загрузка одной статьи).

---

## 4.1. Авторизация

Подробно — §2 основного файла.

| Метод и путь | Тело | Ответ | Источник |
|---|---|---|---|
| `POST /auth/login` | `{email, password, remember}` [USER] | `{access_token, token_type, expires_in, refresh_token?, user}` | [BE] |
| `POST /auth/refresh` | `{refresh_token}` или cookie | новая пара | [BE] |
| `POST /auth/logout` | `{all?: bool}` | 204 | [BE] |
| `GET /auth/me` | — | `user` | [BE] + [USER] (поля профиля) |
| `POST /auth/register` (опц., выключен) | `{name, email, password}` | как login / `403 registration_disabled` | [BE] |
| `GET /auth/oauth/{vk|yandex}/start`, `GET .../callback` (позже) | — | 302 | [BE] |
| `POST /auth/password/forgot`, `POST /auth/password/reset` (позже) | | 204 | [BE] |

---

## 4.2. Профиль

### `GET /me`
Источник: `email`, `id`, `is_admin` — [BE]; остальное — [USER].
```json
{ "id": "usr_01", "email": "team@faktura.ai", "name": "Исследователь", "role": "Материаловед", "team": "Команда хакатона", "color": "graphite", "focus": "Перовскитные и тонкоплёночные солнечные элементы", "is_admin": false }
```

### `PATCH /me`
Тело [USER] — любые из `name` (1..80), `role`, `team`, `color` (enum), `focus`. Почту менять нельзя (`400 validation_error`, `field: "email"`). Ответ — `GET /me`. Ошибки: 400.

### `GET /me/stats`
Сводка для профиля (`profileStats()`). Источник: [BE].
```json
{ "papers": 9, "mine": 1, "facts": 112, "accepted": 14, "edited": 2, "rejected": 1, "issues": 7, "projects": 2, "questions": 5 }
```
`papers` — разобранные статьи команды; `mine` — загруженные мной; `facts`, `accepted`, `edited`, `rejected` — по всем статьям; `issues` — открытые замечания; `projects`, `questions` — мои.

### API-ключи («появится»)
`GET /me/api-keys` → `{data:[{id, name, prefix, scopes, created_at, last_used_at}]}`; `POST /me/api-keys {name, scopes}` → `{id, key}` (ключ показывается один раз); `DELETE /me/api-keys/{id}` → 204.

---

## 4.3. Коллекции

### `GET /collections`
Конфигурация обеих коллекций (§3.2). Источник: [BE]. Фронт берёт отсюда параметры, единицы, темы, оси графика вместо зашитого `COLLECTIONS`.
```json
{ "data": [ { "id": "solar", "name": "Солнечные элементы", "hint": "Тонкоплёночные приборы: слои, КПД, условия измерения", "icon": "sun",
  "row_word": { "one": "образец", "gen": "образца", "cap": "Образец", "all": "все образцы", "allCap": "Все образцы", "byRows": "По образцам" },
  "params": [
    { "key": "pce", "label": "КПД", "unit": "%", "units": { "%": 1 }, "digits": 1, "text": false, "direction": "higher", "target": { "entity": "result", "metric_name": "PCE" }, "scale": { "min": 0, "max": 100 } },
    { "key": "voc", "label": "Voc", "unit": "V", "units": { "V": 1, "mV": 0.001 }, "digits": 2, "text": false, "direction": "higher", "target": { "entity": "result", "metric_name": "Voc" } },
    { "key": "jsc", "label": "Jsc", "unit": "mA/cm2", "units": { "mA/cm2": 1, "A/m2": 0.1 }, "digits": 1, "text": false, "direction": "higher", "target": { "entity": "result", "metric_name": "Jsc" } },
    { "key": "ff", "label": "FF", "unit": "%", "units": { "%": 1, "fraction": 100 }, "digits": 1, "text": false, "direction": "higher", "target": { "entity": "result", "metric_name": "FF" }, "scale": { "min": 0, "max": 100 } },
    { "key": "bandgap", "label": "Ширина зоны", "unit": "eV", "units": { "eV": 1 }, "digits": 2, "text": false, "direction": "none", "target": { "entity": "material", "field": "bandgap_value" } },
    { "key": "thickness", "label": "Толщина поглотителя", "unit": "nm", "units": { "nm": 1, "um": 1000 }, "digits": 0, "text": false, "direction": "none", "target": { "entity": "layer", "field": "layer_thickness", "layer_function": "absorber" } },
    { "key": "conditions", "label": "Условия измерения", "unit": "", "units": {}, "digits": 0, "text": true, "direction": "none", "target": { "entity": "measurement" } }
  ],
  "table": ["voc", "jsc", "ff", "pce"], "best": "pce", "quality": ["pce", "voc", "jsc", "ff"], "plot": { "x": "voc", "y": "pce" },
  "topics": [ { "id": "perovskite", "name": "перовскит" }, { "id": "cigs", "name": "CIGS" }, { "id": "cdte", "name": "CdTe" }, { "id": "tandem", "name": "тандем" }, { "id": "leadfree", "name": "бессвинцовые" } ],
  "scope_note": "Тонкоплёночные однопереходные приборы: перовскиты, CIGS, CdTe, кестериты, Sb₂Se₃, тонкоплёночный Si; без тандемов; только экспериментальные работы" },
  { "id": "rag", "...": "аналогично: r5, r10, mrr (unit \"\", доля), chunk (ток.), retriever (text)" } ] }
```

### `GET /collections/{id}/stats`
Для главной: «N статей, M фактов». Параметр `project_id` (опц.). Источник: [BE].
```json
{ "papers": 6, "facts": 74, "processing": 0, "open_issues": 5 }
```

---

## 4.4. Проекты [В ФОНЕ]

> **[В ФОНЕ]** — в интерфейсе выключено флагом `FEATURES` (`src/features.ts`), фронт эти эндпоинты сейчас не вызывает, **бэку делать не нужно до решения команды**.

### `GET /projects`
Мои проекты. Сортировка по умолчанию: `pinned desc, created_at asc` (как в боковой панели). Без пагинации. Источник: `name, collection, note, pinned` — [USER]; счётчики, даты — [BE].
```json
{ "data": [ { "id": "pr_perov", "name": "Перовскиты", "collection": "solar", "note": "Свинцовые и бессвинцовые перовскиты, стабильность", "pinned": true, "created_at": "2026-10-31T10:00:00Z", "updated_at": "2026-11-20T08:00:00Z", "chats_count": 3, "papers_count": 3, "open_issues": 3 } ] }
```

### `POST /projects`
Тело [USER]: `{ "name": "Перовскиты", "collection": "solar", "note": "" }`. Пустое имя → «Новый проект». 201 — проект. Ошибки: 400 (`collection` не из списка).

### `GET /projects/{id}` → проект. 404.

### `PATCH /projects/{id}`
Тело [USER] — любые из `name`, `note`, `pinned`, `collection`. Смена коллекции разрешена, только если в проекте нет чатов статей другой коллекции, иначе `409 conflict` «В проекте есть статьи другой коллекции». Ответ — проект + `action_id`. Для `pinned` уведомление «Проект «…» закреплён» с «Отменить» → `POST /actions/{action_id}/undo`.

### `DELETE /projects/{id}`
Мягко удаляет проект, его чаты переносит в «Недавние» (`project_id = null`). 200 `{ "action_id": "act_…", "moved_chats": 3 }`. «Вернуть» → `POST /projects/{id}/restore` (возвращает проект и переносит чаты обратно). 404.

### `POST /projects/{id}/restore` → проект. `410 gone`, если окно истекло.

### `GET /projects/{id}/suggestions`
Подсказки вопросов по статьям проекта (`projectSuggestions()`). Источник: [BE] (в режиме `ml` — [ML]).
```json
{ "data": ["Сравни перовскит и бессвинцовые", "Где используется MAPbI₃?", "Где данные не сходятся?"] }
```

---

## 4.5. Чаты и сообщения [В ФОНЕ]

> **[В ФОНЕ]** — в интерфейсе выключено флагом `FEATURES` (`src/features.ts`), фронт эти эндпоинты сейчас не вызывает, **бэку делать не нужно до решения команды**.

### `GET /chats`
Параметры: `project_id` (id или `none` — «Недавние»), `kind` (`paper|ask`), `pinned` (bool), `q` (поиск по названию), `sort` (`updated_at` по умолчанию, `desc`), `limit`, `cursor`. Источник: `title` — [BE]|[USER]; `project_id`, `pinned` — [USER]; остальное — [BE].
```json
{ "data": [ { "id": "ch_mapbi", "kind": "paper", "project_id": "pr_perov", "paper_id": "pap_mapbi", "collection": "solar", "title": "Перовскитный солнечный элемент с КПД 18,6 %: стабильность и интерфейсы", "pinned": false, "created_at": "2026-11-20T07:00:00Z", "updated_at": "2026-11-20T07:00:00Z", "turns_count": 0,
  "paper_status": { "status": "done", "progress": 100, "stage": "Проверка", "open_issues": 2, "top_level": "conflict" } } ],
  "total": 9, "next_cursor": null }
```

### `GET /chats/{id}` → чат (как в списке). 404.

### `POST /chats/ask` — новый вопрос (главная, проект)
Создаёт чат-вопрос и первую реплику, сразу отдаёт поток ответа. Если последний чат-вопрос пользователя с тем же проектом, коллекцией и единственной репликой с тем же текстом уже есть — возвращает его (`askNew()` защищает от дублей).

Тело [USER]: `{ "question": "Какие приборы лучшие по КПД?", "project_id": null, "collection": "solar" }`.
- `Accept: application/json` → 201 `{ "chat": {...}, "turn_id": "trn_…", "stream_url": "/api/v1/turns/trn_…/stream", "existing": false }`; поток читается `GET /turns/{id}/stream`.
- `Accept: text/event-stream` → 200, сначала событие `chat` с объектом чата, далее события ответа (ниже).

Ошибки: 400 (пустой вопрос, > 2000 символов), 404 (проект), 429.

### `PATCH /chats/{id}`
Тело [USER]: любые из `title` (1..200; ставит `title_custom = true`), `pinned`, `project_id` (`null` — в «Недавние»; проект должен быть той же коллекции, иначе `409`). Ответ — чат + `action_id`. Уведомления «Чат закреплён», «Перемещено в «…»» — с «Отменить».

### `DELETE /chats/{id}`
Чат-вопрос — удаляется. **Чат статьи — удаляется и статья с фактами, замечаниями и записями ТЗ** (иначе она осталась бы в Данных и ответах). Всё мягко. 200 `{ "action_id": "act_…", "deleted_paper_id": "pap_…" | null }`. «Вернуть» → `POST /chats/{id}/restore`. Используется и в «Удалить новую» у дубликата.

### `POST /chats/{id}/restore` → чат (и статья). 410.

### `GET /chats/{id}/turns`
Все реплики чата по порядку, у каждой все версии. Без пагинации (до 200 реплик; больше — `cursor`). Источник: `question`, `rating`, `active_version` — [USER]; `answer` — [BE]|[ML]→[BE]; остальное — [BE].
```json
{ "data": [ { "id": "trn_1", "seq": 1, "active_version": 2, "rating": "up", "rating_reason": null,
  "versions": [
    { "version": 1, "question": "Какой образец лучший?", "status": "done", "steps": ["Читаю статью", "Сверяю с таблицами и текстом"], "step": 1, "generated_by": "rules", "answer": { "...": "…" } },
    { "version": 2, "question": "Какой образец лучший по Voc?", "status": "done", "steps": ["Читаю статью", "Сверяю с таблицами и текстом"], "step": 1, "generated_by": "rules",
      "answer": {
        "intro": "Лучший по КПД — образец A:",
        "items": [ { "text": "A: КПД 19,0 %", "cite": 1 }, { "text": "B: КПД 18,6 %", "cite": 2 }, { "text": "C: КПД 17,4 %", "cite": 3 } ],
        "outro": "Обратите внимание: текст статьи называет лучшим другой образец — это отмечено в замечаниях.",
        "cites": [ { "n": 1, "fact_id": "fct_mapbi_c_A_pce", "paper_id": "pap_mapbi", "title": "Перовскитный солнечный элемент с КПД 18,6 %: стабильность и интерфейсы", "where": "стр. 4 · Табл. 2, строка A", "quote": "Фотоэлектрические параметры исследованных образцов — A: 19,0 %" } ],
        "card": null, "explore": "", "followups": ["Чего не хватает?", "Из каких слоёв прибор?", "Кто цитирует статью?"] } } ] } ] }
```

### `POST /chats/{id}/turns` — вопрос в существующем чате
Тело [USER]: `{ "question": "Где данные не сходятся?" }`. Ответ — как у `POST /chats/ask` (JSON с `stream_url` или сразу поток SSE). В чате статьи вопрос отвечается только по этой статье; в чате проекта — только по статьям проекта (список берётся в момент ответа).
Ошибки: 400; `409 turn_busy` «Ответ ещё пишется — дождитесь или нажмите «Стоп»»; `422 paper_not_ready` (чат статьи в разборе); 429.

### Поток ответа (SSE)
`GET /turns/{id}/stream` (или ответ на POST с `Accept: text/event-stream`). Поддерживает `Last-Event-ID` для продолжения после обрыва; если реплика уже закончена — отдаёт все события сразу и `done`.

| Событие | Данные | Источник |
|---|---|---|
| `chat` | объект чата (только у `POST /chats/ask`) | [BE] |
| `turn` | `{turn_id, version, question, steps}` | [BE] |
| `step` | `{step: 1}` — номер текущего шага хода поиска | [BE] |
| `intro` | `{text}` | [BE]\|[ML] |
| `item` | `{index, text, cite, head?}` — пункт; сноска к нему приходит раньше в `cite` | [BE]\|[ML] |
| `cite` | `{n, fact_id, paper_id, title, where, quote, issue_id?}` | [BE] |
| `card` | `{kind, ...}` | [BE]\|[ML]→[BE] |
| `outro` | `{text}` | [BE]\|[ML] |
| `done` | `{status: "done", explore, followups[]}` | [BE] |
| `stopped` | `{status: "stopped", shown: 2}` | [BE] |
| `error` | `{code, message}` (например `ml_unavailable` «Не удалось получить ответ — попробуйте ещё раз») | [BE] |

```
event: turn
id: 1
data: {"turn_id":"trn_9","version":1,"question":"Какие приборы лучшие по КПД?","steps":["Разбираю вопрос","Ищу в 6 статьях","Сверяю с источниками"]}

event: step
id: 2
data: {"step":1}

event: intro
id: 4
data: {"text":"Лучшее — КПД 19,0 %: перовскит, образец A. В коллекции 15 приборов, ниже — лучшие 5."}

event: cite
id: 5
data: {"n":1,"fact_id":"fct_mapbi_c_A_pce","paper_id":"pap_mapbi","title":"Перовскитный солнечный элемент…","where":"стр. 4 · Табл. 2, строка A","quote":"Фотоэлектрические параметры исследованных образцов — A: 19,0 %"}

event: item
id: 6
data: {"index":0,"head":"Перовскитный солнечный элемент с КПД 18,6 %: стабильность и интерфейсы (2024)","text":"образец A: КПД 19,0 % (Voc 1,08 В, Jsc 22,9 мА/см², FF 77,0 %)","cite":1}

event: done
id: 20
data: {"status":"done","explore":"Какие приборы лучшие по КПД?","followups":["Сравни эти статьи","Где данные не сходятся?","Связи в коллекции"]}
```

Бэк хранит ответ по мере генерации; закрытие соединения клиентом генерацию **не** останавливает (только `stop`).

### `POST /turns/{id}/stop`
«Стоп» / Esc. Останавливает генерацию; сделанное сохраняется, `items` обрезаются до показанных, `outro`, `card`, `explore` сбрасываются, `status = stopped`. 200 — версия реплики. Если уже закончено — 200 без изменений.

### `POST /turns/{id}/versions` — повторить или изменить вопрос
Только для последней реплики чата. Тело [USER]: `{ "question": "Какой образец лучший по Voc?" }` (тот же текст — «Повторить»). Создаёт новую версию, делает её активной, отдаёт поток (как `POST /chats/{id}/turns`). Прежняя версия сохраняется, если была закончена и не остановлена (переключатель «1/2»). Если это первая реплика и заголовок чата был автоматическим — заголовок меняется на новый вопрос. Ошибки: `409 conflict` «Изменить можно только последний вопрос».

### `PUT /turns/{id}/active-version`
Тело [USER]: `{ "version": 1 }`. Переключает показанную версию (фронт показывает «1/2»). 200 — реплика.

### `PUT /turns/{id}/rating`
Тело [USER]: `{ "rating": "down", "reason": "Неверная цифра" }`; `{ "rating": null }` — снять. Причина допустима только при `down`. 200. В логи качества уходят вопрос, ответ, сноски, оценка и `model_version`.

---

## 4.6. Загрузка одной статьи и разбор (дополнительный сценарий)

Загрузка поштучно — частный случай импорта корпуса (§13.4): статья попадает в служебный корпус «Загруженные вручную», разбор идёт тем же конвейером.

### `POST /papers` — загрузить
`multipart/form-data`:
| Поле | Тип | Обяз. | Источник | Описание |
|---|---|---|---|---|
| `file` | файл, повторяемое (до 20) | да | [USER] | PDF |
| `project_id` | id | нет | [USER] | загрузили в проект → коллекция проекта |
| `collection` | `solar|rag` | нет | [USER] | выбранная под полем ввода — запасной вариант, если ML не уверен |

Ответ **202** сразу, разбор идёт в фоне. Источник: всё [BE].
```json
{ "data": [ {
  "paper": { "id": "pap_u5kq2x9a", "title": "perovskite passivation 2024", "authors": [], "authors_text": "Загружено вами", "collection": "solar", "topic_by": "guess", "mine": true,
    "file": { "name": "perovskite_passivation_2024.pdf", "size": 2483112, "sha256": "9f2c…" },
    "processing": { "status": "queued", "stage": "Текст", "progress": 0 }, "dup_of": null, "added_at": "2026-11-20T09:15:00Z" },
  "chat": { "id": "ch_u5kq2x9a", "kind": "paper", "project_id": null, "paper_id": "pap_u5kq2x9a", "title": "perovskite passivation 2024" },
  "action_id": "act_…" } ] }
```
Пример curl с экрана «Экспорт и API»: `curl -X POST -H "Authorization: Bearer $GEODE_KEY" -F "file=@paper.pdf" -F "collection=solar" https://api.faktura.ai/api/v1/papers`.

Порядок на бэке: проверить тип/размер → SHA-256 → если точный дубль в команде есть — `dup_of`, `dup_kind = "file"` → сохранить файл → `title` из имени файла → коллекция: проект? `project` : (выбранная, `guess`) → создать статью и чат → поставить `MlJob` → вернуть 202. После ответа ML о теме (артефакт A1) коллекция уточняется (`text`) — событие `paper.updated`.

Ошибки: 400 (нет файла), 413, 415, 422 (`pdf_encrypted`, `pdf_broken`, `too_many_pages`), 429. При нескольких файлах ошибки — по файлу: `{data:[...], errors:[{file:"x.docx", code:"unsupported_file", message:"…"}]}` со статусом 207.

### `GET /papers/{id}/processing`
Опрос хода разбора (если SSE недоступен). Источник: [BE] (этапы ML — по прогрессу от [ML]).
```json
{ "status": "processing", "stage": "Таблицы", "progress": 34, "stages": ["Текст", "Таблицы", "Рисунки", "Формулы", "Нормализация", "Проверка"], "error": null, "topic_by": "text", "collection": "solar", "dup_of": null }
```
По готовности: `{ "status": "done", "progress": 100, "summary": { "facts": 18, "issues": 2 } }` — для уведомления «"…" разобрана: 18 фактов, замечаний — 2».

### `PUT /papers/{id}/collection` — исправить тему
Тело [USER]: `{ "collection": "rag" }`. Удаляет факты, замечания и записи ТЗ статьи и перезапускает разбор по схеме другой коллекции (`topic_by = manual`); чат статьи меняет коллекцию. 202 — статья с `processing`. Ошибки: `409 conflict`, если статья в проекте другой коллекции («Статья в проекте «…» — сначала перенесите чат»).

### `POST /papers/{id}/reprocess`
Перезапустить разбор (после `failed` или с новой версией модели). Тело `{ "keep_review": true }` — перенести статусы проверки и правки на совпавшие факты (по `row_id + param + source_kind`). 202.

### `DELETE /papers/{id}`
То же, что удаление чата статьи. 200 `{action_id}`.

### Дубликаты
- Точный (SHA-256) — при загрузке.
- Смысловой — после артефакта A3 (§7): тот же `doi`; иначе нормализованное название (нижний регистр, без пунктуации, ё→е) + `year` + фамилия первого автора. Ставит `dup_of`, `dup_kind`, событие `paper.updated`.
- «Оставить обе» — [FE] (sessionStorage), бэку ничего не нужно. «Удалить новую» — `DELETE /chats/{chat_id}`.

### События разбора — см. §4.20 (`paper.progress`, `paper.updated`, `paper.done`, `paper.failed`).

---

## 4.7. Статья

> Для нового интерфейса: `GET /papers/{domain}/{pid}` отдаёт **7 таблиц шаблона** статьи (+ `reviews`, `meta`) — §15.4; PDF — `GET /papers/{domain}/{pid}/pdf` (§15.5). Расширенные эндпоинты ниже (`layout`, `anchors`, `coverage`, `structure`, `links`) — для будущих экранов; `structure` и `links` фронт сейчас не вызывает.

### `GET /papers`
Список статей команды. Параметры: `collection`, `project_id`, `status` (`processing|done|failed`), `mine` (bool), `q` (название, авторы), `ids` (через запятую), `sort` (`added_at` по умолчанию | `year` | `title`), `dir`, `limit`, `cursor`. Источник: метаданные — [ML]; счётчики, статус — [BE].
```json
{ "data": [ { "id": "pap_mapbi", "title": "Перовскитный солнечный элемент с КПД 18,6 %: стабильность и интерфейсы", "year": 2024, "journal": "Физика и химия материалов, 2024, т. 16, № 2", "collection": "solar", "facts": 18, "open_issues": 2, "top_level": "conflict", "processing": { "status": "done", "progress": 100 } } ],
  "total": 6, "next_cursor": null }
```

### `GET /papers/{id}`
Всё для шапки и панели «О статье». Источник — по полям §3.4.
```json
{ "id": "pap_mapbi", "publication_id": "pap_mapbi", "title": "…", "authors": ["Иванов А. В.", "Котова Е. С."], "authors_text": "Иванов А. В., Котова Е. С. и др.", "year": 2024, "doi": null,
  "journal": "Физика и химия материалов, 2024, т. 16, № 2", "collection": "solar", "keywords": ["перовскит", "MAPbI3", "n-i-p"], "page_count": 12, "main_page": 4,
  "main_table": { "no": 2, "caption": "Фотоэлектрические параметры исследованных образцов", "section": "3. Результаты и обсуждение", "page": 4, "anchor_id": "anc_tbl2" },
  "main_figure": { "no": 3, "caption": "Рис. 3. Вольт-амперные характеристики образцов A–C", "page": 4 },
  "in_scope": true, "file": { "name": "mapbi.pdf", "size": 1840022 }, "mine": false, "added_at": "…", "chat_id": "ch_mapbi",
  "processing": { "status": "done", "stage": "Проверка", "progress": 100 }, "topic_by": "text", "dup_of": null, "model_version": "extractor-2026.11",
  "rows": [ { "row_id": "A", "label": "A", "order": 1 }, { "row_id": "B", "label": "B", "order": 2 }, { "row_id": "C", "label": "C", "order": 3 } ],
  "claims": [ { "claim_id": "clm_best", "row_id": "B", "param": "pce", "kind": "best", "text": "Наилучшие характеристики демонстрирует образец B", "place": "text", "anchor_id": "anc_clm_best", "source_page": 4 } ],
  "quality": { "total": 18, "sourced_pct": 100, "normalized_pct": 100, "accepted": 0, "issues": 2 },
  "coverage_summary": { "filled": 21, "total": 21 }, "open_issues": 2, "top_level": "conflict", "facts_count": 18 }
```
Ошибки: 404. Пока статья разбирается — те же поля, `rows`/`claims` пустые.

### `GET /papers/{id}/pages/{n}`
Отрисованная страница. Параметры: `scale` (1, 1.5, 2; по умолчанию 1.5), `format` (`webp|png`). Ответ — изображение (`Cache-Control: private, max-age=86400`, `ETag`). Источник: [BE] (рендер PDF). 404 — нет страницы.

### `GET /papers/{id}/file`
Исходный PDF (`application/pdf`, `Content-Disposition: inline`). Поддерживает `Range` (для pdf.js). Можно отдать 302 на подписанную ссылку на 5 мин.

### `GET /papers/{id}/layout`
Разметка для текстового слоя и для отрисовки «как в прототипе» (абзацы, таблицы, рисунки). Параметр `pages` (`4` или `3-5`). Источник: [ML] (связь `spans` → якоря — [BE]).
```json
{ "pages": [ { "n": 4, "width_pt": 595, "height_pt": 842, "image_url": "/api/v1/papers/pap_mapbi/pages/4", "has_text_layer": true } ],
  "blocks": [ { "block_id": "blk_41", "page": 4, "type": "paragraph", "section": "3. Результаты и обсуждение", "order": 1, "bbox": { "x": 0.09, "y": 0.12, "w": 0.82, "h": 0.07 },
    "text": "Фотоэлектрические характеристики изготовленных элементов измеряли при AM1.5G, 100 мВт/см², 25 °C. На рис. 3 …",
    "spans": [ { "start": 67, "end": 95, "anchor_id": "anc_t_all_conditions", "kind": "fact" } ] } ],
  "tables": [ { "table_id": "tbl_2", "no": 2, "caption": "Фотоэлектрические параметры исследованных образцов", "page": 4, "section": "3. Результаты и обсуждение", "bbox": { "x": 0.09, "y": 0.45, "w": 0.82, "h": 0.16 },
    "columns": [ { "param": "voc", "header": "Voc, В", "unit": "V" }, { "param": "jsc", "header": "Jsc, мА/см²", "unit": "mA/cm2" }, { "param": "ff", "header": "FF", "unit": "" }, { "param": "pce", "header": "КПД, %", "unit": "%" } ],
    "row_labels": [ { "row_id": "A", "label": "A", "anchor_id": "anc_rl_A" } ],
    "cells": [ { "row_id": "A", "param": "voc", "text": "1,08", "anchor_id": "anc_c_A_voc" } ] } ],
  "figures": [ { "figure_id": "fig_3", "no": 3, "caption": "Рис. 3. Вольт-амперные характеристики образцов A–C", "page": 4, "bbox": { "x": 0.12, "y": 0.64, "w": 0.76, "h": 0.25 }, "kind": "jv",
    "points": [ { "row_id": "A", "param": "voc", "x": 1.07, "y": 0, "anchor_id": "anc_f_A_voc" } ] } ] }
```

### `GET /papers/{id}/anchors`
Все якоря статьи для подсветки и точек замечаний. Параметр `page`. Источник: `rects`, `text` — [ML]|[USER]; связи — [BE].
```json
{ "data": [ { "anchor_id": "anc_c_B_pce", "kind": "cell", "page": 4, "rects": [ { "x": 0.71, "y": 0.52, "w": 0.06, "h": 0.018 } ], "text": "18,6", "fact_ids": ["fct_mapbi_c_B_pce"], "issue_ids": ["pap_mapbi:jv_consistency:B", "pap_mapbi:contradiction:clm_best"] } ] }
```

### `GET /papers/{id}/coverage`
Карта полноты «образцы × параметры» (`coverageOf()`). Источник: [BE].
```json
{ "params": ["pce", "voc", "jsc", "ff", "bandgap", "thickness", "conditions"], "total": 21, "filled": 21, "conflicts": 1, "figure_only": 0, "diverge": 1,
  "cells": [ { "row_id": "A", "param": "voc", "fact_ids": ["fct_mapbi_c_A_voc", "fct_mapbi_f_A_voc"], "main_fact_id": "fct_mapbi_c_A_voc", "level": null, "diverge": false, "figure_only": false } ] }
```
Правила: факты строки и общие (`*`); отклонённые не считаются; главный — принятый, затем с большей уверенностью; `level` — худший уровень открытых замечаний фактов клетки; `diverge` — значения различаются больше чем на 0,5 % (текстовые — без учёта регистра); `figure_only` — все источники рисунки.

### `GET /papers/{id}/structure`
Структура прибора (solar) или конвейер (rag) + условия. Источник: [ML]→[BE].
```json
{ "collection": "solar",
  "variants": [ { "id": "v1", "label": "A–C", "device_ids": ["dev_A", "dev_B", "dev_C"], "row_ids": ["A", "B", "C"],
    "layers": [ { "layer_id": "lay_1", "layer_order": 1, "layer_function": "substrate", "layer_material": "Стекло", "layer_material_normalized": "Стекло", "layer_thickness": 1100000, "thickness_unit": "нм", "source_where": "Эксперимент, абзац 1", "source_page": 3, "fact_ids": [] },
      { "layer_id": "lay_4", "layer_order": 4, "layer_function": "absorber", "layer_material": "MAPbI3", "layer_material_normalized": "MAPbI₃", "layer_thickness": 450, "thickness_unit": "нм", "source_where": "Табл. 2… / текст, абзац 3", "source_page": 4, "fact_ids": ["fct_mapbi_t_all_thickness"] } ] } ],
  "conditions": [ { "key": "spectrum", "label": "Спектр", "value": "AM1.5G", "fact_id": "fct_mapbi_t_all_conditions" }, { "key": "power", "label": "Мощность освещения", "value": "100 мВт/см²", "fact_id": "fct_mapbi_t_all_conditions" }, { "key": "temp", "label": "Температура", "value": "25 °C", "fact_id": "fct_mapbi_t_all_conditions" }, { "key": "area", "label": "Площадь", "value": null, "fact_id": null }, { "key": "scan", "label": "Скорость развёртки", "value": null, "fact_id": null } ],
  "pipeline": null }
```
У RAG `variants = null`, `conditions = null`, а
```json
"pipeline": [ { "row_id": "hybrid", "nodes": [
  { "key": "data", "title": "Датасет", "value": "корпус из 12 тыс. документов", "sub": "подпись к табл. 2", "fact_id": null },
  { "key": "chunk", "title": "Разбиение на чанки", "value": "512 ток.", "sub": "Табл. 2", "fact_id": "fct_hybrid_c_hybrid_chunk" },
  { "key": "ret", "title": "Ретривер", "value": "BM25 + e5-large, слияние RRF", "sub": "текст", "fact_id": "fct_hybrid_t_hybrid_retriever" },
  { "key": "rr", "title": "Переранжирование", "value": null, "fact_id": null },
  { "key": "gen", "title": "Генератор", "value": null, "fact_id": null },
  { "key": "met", "title": "Метрики", "items": [ { "label": "Recall@5", "value": "0,74", "fact_id": "fct_hybrid_c_hybrid_r5" } ] } ] } ]
```
`value: null` — «не указано» (правило «не восстанавливать по догадке»).

### `GET /papers/{id}/links`
«Связи статьи»: литература, кто цитирует, материалы/методы. Источник: литература — [ML]; сопоставление, `cited_by`, `entities` — [BE].
```json
{ "references": [ { "n": 1, "key": "x:kojima2009", "status": "ok", "authors": "Kojima A., Teshima K., Shirai Y., Miyasaka T.", "title": "Organometal halide perovskites as visible-light sensitizers for photovoltaic cells", "venue": "J. Am. Chem. Soc., 131(17), 6050–6051", "year": 2009, "doi": "10.1021/ja809598r", "arxiv": null, "url": "https://doi.org/10.1021/ja809598r", "id_label": "DOI 10.1021/ja809598r", "target_paper_id": null, "node_id": "x:kojima2009",
    "mentions": ["Перовскиты впервые применили в солнечных элементах в 2009 г. [1].", "Базовая архитектура n-i-p описана в [1]."] },
    { "n": 9, "key": "u:9", "status": "unparsed", "raw": "Лукин П. Т. Пассивация границ в гибридных перовскитах // Тез. докл. конф. «Фотоника-2021», с. 45", "year": 2021, "id_label": "идентификатор не найден", "node_id": "u:pap_mapbi:9", "mentions": ["…"] } ],
  "summary": { "refs": 10, "unparsed": 1, "cited_by": 3 },
  "cited_by": [ { "paper_id": "pap_tandem", "title": "Тандем перовскит/кремний с КПД 28,6 %", "year": 2024 } ],
  "entities": [ { "entry_id": "mapbi", "canon": "MAPbI₃", "name": "Метиламмоний-йодид свинца" }, { "entry_id": "sno2", "canon": "SnO₂", "name": "Диоксид олова (транспортный слой)" } ] }
```

---

## 4.8. Факты

> Для нового интерфейса — `GET /results` и `PATCH /runs/{id}/papers/{domain}/{pid}/results/{rid}/review`, `POST /runs/{id}/reviews/batch` (§15.4, §15.6). Эндпоинты `/facts/*` ниже — общий слой (в т. ч. для полей материалов, слоёв и условий) и внешний API.

### `GET /facts`
Факты с фильтрами (и для внешних программ — пример `GET /api/v1/facts?param=pce&min=18&collection=solar` на экране «Экспорт»).

| Параметр | Описание |
|---|---|
| `corpus_id`, `collection`, `paper_id` (повторяемый), `project_id` | область |
| `param` (повторяемый), `row_id` | |
| `min`, `max` | по `value` (в канонической единице) |
| `review_status` | `new|accepted|rejected` (повторяемый); по умолчанию все, кроме `rejected` |
| `source_kind` | `table|text|figure|manual` |
| `added_by` | `auto|manual` |
| `has_issues` | bool — есть открытые замечания |
| `reliable` | bool — новые, без замечаний, уверенность ≥ 0,9 |
| `min_confidence` | 0…1 |
| `extraction_status` | |
| `q` | по `raw_value`, `display`, `source_fragment` |
| `sort` | `paper|row|param|value|confidence|created_at|risk` (по умолчанию `paper,row`); `risk` — сначала с замечаниями и низкой уверенностью (порядок панели фактов) |
| `limit`, `cursor` | |

Источник: §3.5 по полям.
```json
{ "data": [ { "fact_id": "fct_mapbi_c_B_pce", "publication_id": "pap_mapbi", "row_id": "B", "row_label": "B", "param": "pce", "param_label": "КПД",
  "target": { "entity": "result", "id": "res_B_pce", "field": "metric_value" },
  "raw_value": "18.6", "raw_unit": "%", "metric_value": 18.6, "metric_unit": "%", "norm_status": "ok", "normalized_value": 18.6, "normalized_unit": "%", "normalized_text": null, "extraction_status": "extracted",
  "review_status": "new", "corrected_value": null, "corrected_raw": null, "value": 18.6, "display": "18,6 %",
  "confidence": 0.957, "added_by": "auto", "result_origin": "primary", "cited_publication_id": null,
  "source_page": 4, "source_section": "3. Результаты и обсуждение", "source_table": "Табл. 2", "source_figure": null,
  "source_fragment": "Фотоэлектрические параметры исследованных образцов — B: 18,6 %", "source_kind": "table", "source_ref": "Табл. 2", "source_where": "строка B",
  "anchor_id": "anc_c_B_pce", "aggregation": null, "scan_direction": null, "cross": null,
  "issue_ids": ["pap_mapbi:jv_consistency:B", "pap_mapbi:contradiction:clm_best"], "model_version": "extractor-2026.11" } ],
  "total": 1, "next_cursor": null }
```

### `GET /papers/{id}/facts`
Все факты статьи (панель фактов) — тот же формат, без пагинации, включая отклонённые (`review_status=all` по умолчанию).

### `GET /facts/{id}` → факт. 404.

### `GET /facts/{id}/source`
«Откуда взято число» (пример на экране «Экспорт»). Источник: [ML] (+ якорь [BE]).
```json
{ "fact_id": "fct_mapbi_c_B_pce", "paper_id": "pap_mapbi", "paper": "Перовскитный солнечный элемент с КПД 18,6 %…", "page": 4, "kind": "table", "ref": "Табл. 2", "where": "строка B", "section": "3. Результаты и обсуждение", "anchor_id": "anc_c_B_pce", "rects": [ { "x": 0.71, "y": 0.52, "w": 0.06, "h": 0.018 } ], "quote": "Фотоэлектрические параметры исследованных образцов — B: 18,6 %" }
```

### `GET /facts/{id}/trace`
Цепочка нормализации для карточки «Подробнее» (`normTrace()`). Источник: [BE].
```json
{ "steps": [ { "k": "В статье", "v": "«712 мВ»" }, { "k": "Параметр", "v": "Voc (столбец «Voc»)" }, { "k": "Число", "v": "712, единица мВ" }, { "k": "Перевод", "v": "мВ → В, × 0,001" }, { "k": "Итог", "v": "0,712 В" } ] }
```
Для текстовых: «В статье» → «Параметр» → «Единицы» (`1000 Вт/м² → 100 мВт/см²`) → «Словарь» («совпало с канонической записью» / ««1 sun» → каноническая запись» / «в словаре не нашли — оставили как в статье») → «Итог». Для исправленного — последний шаг «Исправлено».

### `POST /facts/{id}/accept` · `POST /facts/{id}/reject` · `POST /facts/{id}/restore`
Тело не нужно (опц. `{ "group_id": "…" }` — объединить с предыдущим действием в одну запись истории). `restore` — вернуть в `new` и сбросить исправление. Ответ: факт + `action_id` + `issues_changed` (замечания, которые после этого открылись/закрылись — проверки пересчитываются сразу).
```json
{ "fact": { "...": "…", "review_status": "accepted" }, "action_id": "act_81", "issues_changed": { "opened": [], "closed": [] }, "paper": { "quality": { "...": "…" }, "open_issues": 2, "top_level": "conflict" } }
```
Журнал: `accept|reject|restore`, `from` / `to` — `display` до и после. Ошибки: 404; `422 paper_not_ready`.

### `PATCH /facts/{id}` — исправить значение
Тело [USER]: `{ "raw": "19,0 %" }`. Бэк нормализует: «18,6 %», «1100 мВ», «0,5 мкм»; число без единицы трактуется в канонической единице параметра («19» → 19 %). Ставит `corrected_value`, `corrected_raw`, `review_status = accepted`, `extraction_status = corrected`. Для текстовых параметров `raw` сохраняется как есть (+ словарь).
Ответ — как у accept. Ошибки: `422 fact_value_unrecognized` «Не удалось распознать «1,1 кВ»: единицы «кВ» нет в словаре для параметра «Voc»» (`field: "raw"`).

### `POST /facts/bulk`
Массовые действия одним действием (одна запись истории, одно уведомление «Принято надёжных: 9» с «Отменить»).
```json
{ "action": "accept", "fact_ids": ["fct_1", "fct_2"] }
```
или по фильтру:
```json
{ "action": "accept", "filter": { "paper_id": "pap_mapbi", "review_status": "new", "reliable": true } }
```
`filter` принимает любые параметры `GET /facts` и `GET /facts/table` (в т. ч. `corpus_id`, `q`). `action`: `accept|reject|restore`. Учитываются только факты, для которых действие что-то меняет (принимаются только `new`). Ответ `{ "count": 9, "action_id": "act_…", "fact_ids": [...] }`; журнал — `acceptAll` с `count`. «Принять все» в статье = `filter: {paper_id, review_status: "new"}`. Ошибки: 400 (ни `fact_ids`, ни `filter`), более 5000 фактов — `400`.

---

## 4.9. Ручное добавление и предпросмотр нормализации

### `POST /papers/{id}/facts/guess`
Угадать образец и параметр по выделению (`guessFact()`). Тело [USER] (что выделено): 
```json
{ "text": "напряжение 1100 мВ при", "page": 4, "cell_anchor_id": null, "block_id": "blk_41", "before": "…текст абзаца до выделения", "after": "текст после…", "row_label_text": null }
```
Ответ [BE]:
```json
{ "row_id": "B", "param": "voc", "raw": "1100 мВ", "preview": { "ok": true, "text": "1100 мВ → 1,10 В" }, "example": "1,10 В" }
```
Логика: выделение в ячейке → строка и параметр ячейки; иначе ближайший слева синоним параметра из словаря (до 90 символов), иначе по единице («мВ» → Voc, «эВ» → ширина зоны), иначе главный параметр; образец — подпись строки рядом, для условий — `*`, иначе первая строка. `example` — пример значения этого параметра из той же статьи.

### `POST /normalize/preview`
Тело [USER]: `{ "collection": "solar", "param": "voc", "raw": "1100 мВ" }`. Ответ [BE]: `{ "ok": true, "value": 1.1, "unit": "V", "text": "1100 мВ → 1,10 В" }` / `{ "ok": false, "text": "не распознано" }`. Всегда 200.

### `POST /papers/{id}/facts` — добавить факт выделением
Тело [USER]:
```json
{ "row_id": "B", "param": "voc", "raw": "1100 мВ", "quote": "напряжение холостого хода составило 1100 мВ", "kind": "manual",
  "selection": { "page": 4, "rects": [ { "x": 0.21, "y": 0.33, "w": 0.18, "h": 0.016 } ], "block_id": "blk_43", "char_range": [120, 163] } }
```
`kind`: `manual` (по умолчанию) или `figure` (значение, снятое с рисунка человеком; тогда `ref`, `where`, напр. «Рис. 3», «пересечение с осью V»). Бэк: нормализует (число без единицы — в канонической), создаёт якорь `manual`, факт с `review_status = accepted` (у `figure` — `new`), `confidence = 1` (`figure` — 0,7), `added_by = manual`, `source_ref = "выделение"`, `source_where` = подпись строки, `source_section` по блоку; пересчитывает проверки; журнал `add`.
201 — факт + `action_id`. Ошибки: `422 fact_value_unrecognized`; 400 (неизвестный `param` или `row_id`); `422 paper_not_ready`.

Удаление ручного факта — только отменой (`POST /actions/{id}/undo`) или `reject`.

---

## 4.10. Замечания

> Для нового интерфейса — `GET /issues?run=&check_name=` в форме строк `issues.csv` (§15.4). Разбор замечаний человеком (`resolve`/`reopen`) в новом интерфейсе пока не вызывается: правки идут по записям `results` (§15.6).

### `GET /issues`
| Параметр | Описание |
|---|---|
| `corpus_id` / `paper_id` / `collection` / `project_id` / `papers` (через запятую) | область |
| `status` | `open` (по умолчанию) `| fixed | ok | resolved | all` |
| `check_name`, `plugin`, `severity`, `ui_level`, `fact_id` | фильтры |
| `sort` | `severity` (по умолчанию: ошибки первыми, внутри — порядок статьи) `| created_at` |
| `limit`, `cursor` | |

Источник: всё [BE], `status` — [USER].
```json
{ "data": [ { "issue_id": "pap_mapbi:contradiction:clm_best", "check_name": "contradiction", "plugin": "claims", "scope": "paper", "collection": "solar", "paper_id": "pap_mapbi",
  "severity": "error", "ui_level": "conflict", "title": "Текст противоречит таблице",
  "text": "В тексте лучший — образец B, но по таблице КПД выше у образца A: 19,0 % против 18,6 %.",
  "row_id": "B", "row_label": "B", "param": "pce", "param_label": "КПД", "value": "18,6 %",
  "fact_ids": ["fct_mapbi_c_B_pce", "fct_mapbi_c_A_pce"], "main_fact_id": "fct_mapbi_c_B_pce", "anchor_ids": ["anc_clm_best", "anc_c_A_pce", "anc_c_B_pce"],
  "related": [ { "entity": "result", "id": "res_B_pce" }, { "entity": "result", "id": "res_A_pce" }, { "entity": "claim", "id": "clm_best" } ],
  "numbers": { "claimed_row": "B", "top_row": "A", "top": 19.0, "claimed": 18.6 },
  "status": "open", "resolved_by": null, "resolved_at": null, "touched_fact_ids": [], "computed_by": "backend", "rule_version": "1", "stale": false } ],
  "total": 2, "next_cursor": null }
```

### `GET /papers/{id}/issues` → все замечания статьи (`status=all` по умолчанию: разобранные внизу вкладки).

### `POST /issues/resolve`
«Решено» / «Не ошибка» / «Принять как есть», в том числе для нескольких замечаний сразу.
```json
{ "issue_ids": ["pap_mapbi:jv_consistency:B"], "how": "fixed", "accept_fact_id": "fct_mapbi_c_B_pce", "merge_action_ids": ["act_80"] }
```
- `how`: `fixed` — значение принято/исправлено; `ok` — не ошибка.
- `accept_fact_id` (опц.) — при `fixed` заодно принять этот факт, если он `new`.
- `merge_action_ids` (опц.) — влить ранее сделанные действия (например, исправление значения) в одну запись истории: «Ctrl+Z и «Отменить» откатывают сразу всё».

Ответ: `{ "resolved": ["…"], "accepted_fact_id": "…" | null, "action_id": "act_…", "message": "Замечание решено · КПД принят" }`. Уже разобранные пропускаются; если все — `409 already_resolved`.

### `POST /issues/{id}/reopen`
Вернуть в открытые. Если «Решено» само приняло факт (`touched_fact_ids`) — факт возвращается в `new`. Ответ `{ "issue": {...}, "restored_fact_ids": [...], "action_id": "…", "message": "Замечание снова открыто · КПД не принят" }`.

---

## 4.11. Данные: обзор, поиск с фильтрами, сравнение

### Язык запросов (разбирает бэк, `parseQuery()` прототипа)
Строка на естественном языке → фильтры. Темы объединяются через «или», остальное через «и».

| Вид | Как пишут | Фильтр | `kind` |
|---|---|---|---|
| Тема | «перовскит», «MAPbI», «CIGS», «CdTe», «тандем», «кремний», «олово», «бессвинцовые», «Sn»; RAG: «гибрид», «hybrid», «BM25», «rerank», «переранж», «кросс», «чанк», «chunk» | по названию и ключевым словам статьи (и подписи строки) | `topic` |
| Число | `<параметр> <оператор> <число>[ %]`: «КПД выше 18», «Voc не ниже 1,05», «Recall@5 от 0,7», «полнота > 70 %». Параметр — по синонимам словаря (`кпд|pce|эффективн`, `voc|напряжени`, `jsc|плотност тока`, `ff|фактор заполн`, `ширина зоны|bandgap|запрещ`, `recall@5|полнот`, `recall@10`, `mrr`, `чанк|chunk`), между параметром и оператором до 24 символов без цифр | «выше, больше, более, свыше, >» — строго больше; «от, не ниже, >=» — не меньше; «ниже, меньше, менее, <» — строго меньше; «до, не выше, <=» — не больше. Для параметров-долей число > 1 делится на 100 («Recall@5 выше 70» = 0,70) | `num` |
| Год | «после 2022» (строго позже), «с 2022», «от 2022», «не раньше 2022» (включительно); «до 2023», «раньше 2023» (включительно) | по `year` | `year` |
| Замечания | «противоречия», «не сходится», «расхождения», «сомнения», «ошибки» | строки с открытыми замечаниями (замечание без факта — на всю статью) | `issues` |

Метрика вопроса (для сортировки и ответа) — первый названный параметр качества, иначе главный (`best`).

### `GET /data/rows`
Строки-образцы коллекции (`collectionRows()` + `applyFilters()`): одна строка = образец статьи; значения по параметрам с приоритетом источника таблица → текст/вручную → рисунок; общие факты (`*`) подставляются, если у строки своего нет.

| Параметр | Описание |
|---|---|
| `collection` | обязателен |
| `q` | строка запроса |
| `project_id` / `paper_ids` | область |
| `sort` | `title|label|year|<param>`; по умолчанию главная метрика |
| `dir` | `desc` по умолчанию (для `title`, `label` — `asc`); пустые значения — в конце |
| `limit`, `cursor` | по умолчанию 500 |

Источник: значения — [ML]→[BE] (+ правки [USER]); фильтры, флаги — [BE].
```json
{ "query": "перовскит КПД выше 18 после 2022",
  "filters": [
    { "id": "topic:перовскит", "label": "перовскит", "match": "перовскит", "kind": "topic" },
    { "id": "num:pce:min", "label": "КПД > 18 %", "match": "КПД выше 18", "kind": "num", "param": "pce", "op": ">", "value": 18 },
    { "id": "year:min", "label": "после 2022 года", "match": "после 2022", "kind": "year", "op": ">", "value": 2022 } ],
  "unparsed": "",
  "metric": "pce",
  "data": [ { "key": "pap_mapbi:A", "paper_id": "pap_mapbi", "paper_title": "Перовскитный солнечный элемент с КПД 18,6 %…", "paper_tag": "Перовскитный солнечный элемент (2024)", "year": 2024, "row_id": "A", "label": "A",
    "values": { "voc": 1.08, "jsc": 22.9, "ff": 77.0, "pce": 19.0, "bandgap": 1.59, "thickness": 450, "conditions": null },
    "display": { "voc": "1,08 В", "jsc": "22,9 мА/см²", "ff": "77,0 %", "pce": "19,0 %", "bandgap": "1,59 эВ", "thickness": "450 нм", "conditions": "AM1.5G, 100 мВт/см², 25 °C" },
    "fact_ids": { "voc": "fct_mapbi_c_A_voc", "pce": "fct_mapbi_c_A_pce" }, "issue": true, "comparability_key": "perovskite|single|AM1.5G-100|…" } ],
  "total": 4, "next_cursor": null,
  "stats": { "rows": 4, "papers": 2, "metric": { "mean": 18.4, "min": 17.1, "max": 19.0 } } }
```
`unparsed` — часть строки, из которой ничего не понято (фронт может подсказать). `match` — подстрока запроса, из которой получен фильтр: «×» на таблетке вырезает её из строки и делает новый запрос.

### `GET /data/filter-options`
Готовые варианты для «+ Фильтр». Параметр `collection`. Источник: [BE].
```json
{ "data": [
  { "value": "t:перовскит", "label": "перовскит", "group": "Тема", "insert": "перовскит" },
  { "value": "n:pce", "label": "КПД выше…", "group": "Параметр", "hint": "например, выше 18,0 %", "insert": "КПД выше 18,0" },
  { "value": "y:2023", "label": "С 2023 года", "group": "Год", "insert": "с 2023" },
  { "value": "i", "label": "Есть замечания проверки", "group": "Проверка", "insert": "противоречия" } ] }
```
Число в подсказке — медиана параметра по коллекции; годы — со второго по пятый из имеющихся.

### `GET /data/query/parse`
Только разобрать запрос без выборки: `?collection=&q=` → `{filters, unparsed, metric}`. Нужен для подсказок при наборе.

### `GET /data/compare`
Сравнение 2–3 статей. Параметры: `ids` (2–3 paper_id через запятую), опц. `rows` (параллельно `ids`), `metric`. Источник: [BE].
```json
{ "collection": "solar", "metric": "pce",
  "columns": [ { "paper_id": "pap_cigs", "title": "CIGS на гибкой подложке: влияние щелочного легирования", "head": "CIGS на гибкой подложке", "year": 2023, "topic": "CIGS", "best_row": { "row_id": "K", "label": "K", "value": 18.5, "display": "18,5 %" }, "open_issues": 1 },
    { "paper_id": "pap_cdte", "...": "…" } ],
  "params": [ { "param": "pce", "label": "КПД", "values": [ { "paper_id": "pap_cigs", "row_id": "K", "value": 18.5, "display": "18,5 %", "fact_id": "…", "all_rows": [ { "row_id": "Na", "display": "17,0 %" } ] } ], "best_paper_id": "pap_cigs", "differs": true } ],
  "conditions": [ { "paper_id": "pap_cigs", "spectrum": "AM1.5G", "power": 100, "temp": 25, "raw": "AM1.5G, 1000 Вт/м², 25 °C" } ],
  "comparable": { "ok": true, "reasons": [] },
  "conclusions": ["CIGS лучше: КПД 18,5 % против 18,0 % у CdTe.", "Условия измерения совпадают: AM1.5G, 100 мВт/см², 25 °C."] }
```
`best_row` не показывается, если строки — моменты времени (`time_point`). `differs = false` — лучшее не подсвечивается. `comparable` — по правилам сравнимости ТЗ (§5.3). Ошибки: 400 (меньше 2 или больше 3 статей, разные коллекции).

### Сохранённые виды
| Метод и путь | Тело / параметры | Ответ |
|---|---|---|
| `GET /views?collection=` | | `{data:[{id, name, collection, query, created_at}]}` |
| `POST /views` | `{name, collection, query}` [USER] | 201 вид + `action_id` |
| `PATCH /views/{id}` | `{name}` [USER] | вид |
| `DELETE /views/{id}` | | `{action_id}`; «Вернуть» → `POST /views/{id}/restore` |

---

## 4.12. Граф связей прототипа (по спискам литературы) [В ФОНЕ]

> **Граф нужен, но в новом виде — §15.10**: `GET /graph?run=&domain=&kinds=` и `GET /papers/{domain}/{pid}/related` по данным 7 таблиц. Описание ниже — вариант прототипа (литература, внешние работы), он остаётся в фоне.

> **[В ФОНЕ]** — в интерфейсе выключено флагом `FEATURES` (`src/features.ts`), фронт эти эндпоинты сейчас не вызывает, **бэку делать не нужно до решения команды**. (`GET /papers/{id}/links` и литература — тоже.)

### `GET /graph`
Параметры: `collection` (обяз.), `root` (paper_id — окрестность корня + все статьи коллекции, `nearGraph()`), `project_id` (только статьи проекта). Источник: [BE] (литература — [ML]).
```json
{ "nodes": [
    { "id": "pap_mapbi", "kind": "paper", "label": "Перовскитный солнечный эл…", "full": "Перовскитный солнечный элемент с КПД 18,6 %…", "year": 2024, "weight": 18, "paper_id": "pap_mapbi" },
    { "id": "x:kojima2009", "kind": "ext", "label": "Kojima 2009", "full": "Kojima A., Teshima K., … Organometal halide perovskites…, 2009", "year": 2009, "unparsed": false },
    { "id": "u:pap_mapbi:9", "kind": "ext", "label": "Лукин П. Т. Пас…", "full": "…", "year": 2021, "unparsed": true },
    { "id": "e:mapbi", "kind": "entity", "label": "MAPbI₃", "full": "MAPbI₃ — Метиламмоний-йодид свинца", "year": null, "entity_id": "mapbi" } ],
  "edges": [ { "source": "pap_mapbi", "target": "x:kojima2009", "type": "cites" }, { "source": "pap_mapbi", "target": "e:mapbi", "type": "uses" } ],
  "default_root": "pap_mapbi" }
```
`weight` — число фактов (фронт считает радиус). `default_root` — статья, которую чаще всего цитируют. Порядок узлов и рёбер стабилен (по типу и id) — раскладка не прыгает. Раскладки — [FE].

### `GET /graph/edges.csv`
Те же параметры. CSV `source,target,type,source_label,target_label` (UTF-8). Источник: [BE].

### `GET /entities/{id}/papers`
«Где используется материал/метод» (`whereUsed()`): `{ "entity": {entry_id, canon, name}, "data": [ { "paper_id", "title", "year", "best": "КПД 19,0 %", "value": 19.0 } ] }` — по убыванию `value`. Параметр `project_id`. Источник: [BE].

### `GET /papers/{id}/cited-by` → `{data:[{paper_id, title, year}]}` (входит и в `/links`).

---

## 4.13. Проверки

### `GET /checks`
Сводка «Достоверность» по коллекции (`runChecks()`). Параметры: `collection` (обяз.), `papers` (через запятую — только эти статьи), `project_id`, `include_accepted` (bool, по умолчанию `false`). Источник: [BE].
```json
{ "collection": "solar", "summary": { "papers": 6, "facts": 74, "errors": 1, "warnings": 3, "info": 6, "ok_rules": 2 },
  "rules": [ { "id": "jv_consistency", "title": "КПД не сходится с Voc × Jsc × FF", "what": "КПД ≈ Voc × Jsc × FF / Pin при Pin = 100 мВт/см². Расхождение больше 3 % — замечание.",
    "level": "warn", "unit": ["образца", "образцов", "образцов"], "collections": ["solar"], "computed_by": "backend",
    "total": 15, "accepted": 0, "status": "warn",
    "findings": [ { "issue_id": "pap_mapbi:jv_consistency:B", "check_name": "jv_consistency", "severity": "warn", "paper_id": "pap_mapbi", "paper_tag": "Перовскитный солнечный элемент (2024)", "row_id": "B", "row_label": "B", "param": "pce", "param_label": "КПД", "value": "18,6 %",
      "text": "Voc × Jsc × FF = 1,10 В × 23,4 мА/см² × 79,0 % даёт 20,3 %, в статье 18,6 %: расхождение 9,3 %. Проверьте мощность освещения, площадь и опечатки.",
      "main_fact_id": "fct_mapbi_c_B_pce", "fact_ids": ["fct_mapbi_c_B_voc", "fct_mapbi_c_B_jsc", "fct_mapbi_c_B_ff", "fct_mapbi_c_B_pce"] } ] } ] }
```
`total` — сколько проверено (для «3 из 42 фактов»), `accepted` — сколько принято как есть (`status = ok`), `status` — худший уровень открытых находок или `ok`. Список правил и порядок — §5. Принять как есть — `POST /issues/resolve {how: "ok"}`, вернуть — `POST /issues/{id}/reopen`, отклонить факт — `POST /facts/{id}/reject`.

### `GET /checks/rules`
Описание правил (без находок): `{data:[{id, title, what, level, collections, computed_by, tz_ref}]}`. Источник: [BE].

### `GET /checks/accuracy`
«Точность извлечения» против эталона (`gold.ts`, `QualityReport.vue`): эталон — `docs/case5/dev_gold/` (21 статья dev), считает скрипт ML-2 `faktura-eval` (§14.7), учитываются только `metric_name` из списка условий. Параметры `collection`, `corpus_id` (прогон dev). Источник: эталон — [USER] (админ), метрики — [BE].
```json
{ "collection": "solar", "gold_dataset": "hackathon-v1", "model_version": "extractor-2026.11",
  "total": { "gold": 74, "extracted": 72, "correct": 66, "p": 0.917, "r": 0.892, "f1": 0.904 },
  "normalized": 0.986, "linked": 0.972,
  "by_param": [ { "param": "pce", "label": "КПД", "gold": 15, "extracted": 15, "correct": 13, "p": 0.867, "r": 0.867, "f1": 0.867 } ],
  "by_source": [ { "kind": "table", "label": "Таблица", "gold": 60, "extracted": 59, "correct": 55, "p": 0.932, "r": 0.917, "f1": 0.924 } ],
  "by_paper": [ { "paper_id": "pap_mapbi", "short": "Иванов (2024)", "gold": 15, "extracted": 15, "correct": 14, "p": 0.933, "r": 0.933, "f1": 0.933 } ],
  "errors": [ { "kind": "value", "kind_label": "Неверное значение", "paper_id": "pap_mapbi", "row_id": "B", "param": "pce", "fact_id": "fct_mapbi_c_B_pce", "gold": "18,6 %", "got": "16,8 %", "note": "цифры переставлены при распознавании таблицы" } ] }
```
Факт верен, если найдена пара в эталоне (`paper_id + row_id + param + source_kind`), `raw_value` совпадает с эталоном (без учёта пробелов) и нормализованное значение совпадает. `normalized` — доля числовых, где нормализация бэка дала то же, что эталон; `linked` — доля с верным якорем (пересечение bbox ≥ 50 %). Виды ошибок: `miss` «Пропуск», `extra` «Лишний факт», `value` «Неверное значение», `unit` «Неверная нормализация», `link` «Неверная привязка». Если эталона для коллекции нет — `404 not_found` «Эталонная разметка не загружена».

---

## 4.14. Словарь и нормализация

### `GET /dictionary`
Параметр `collection` (параметры и материалы — коллекции, условия — общие). Источник: встроенное — [BE], `custom: true` — [USER], счётчики — [BE].
```json
{ "params": [ { "param": "voc", "label": "Voc", "unit": "V", "synonyms": [ { "text": "Voc", "custom": false }, { "text": "Uхх", "custom": false }, { "text": "U_oc", "custom": true } ], "usage": { "facts": 12, "papers": 6, "total_papers": 6 } } ],
  "units": [ { "key": "voc:мВ", "param": "voc", "label": "Voc", "unit": "mV", "canon": "V", "factor": 0.001, "is_canonical": false, "count": 3, "example": "1000 мВ → 1 В" } ],
  "materials": [ { "entry_id": "mapbi", "kind": "material", "canon": "MAPbI₃", "name": "Метиламмоний-йодид свинца", "synonyms": [ { "text": "CH3NH3PbI3", "custom": false } ], "papers_count": 3 } ],
  "conditions": [ { "entry_id": "am15", "kind": "condition", "canon": "AM1.5G, 100 мВт/см², 25 °C", "name": "Стандартные условия испытаний", "synonyms": [ { "text": "1000 Вт/м²", "custom": false } ], "papers_count": 6 } ] }
```

### `POST /dictionary/params/{collection}/{param}/synonyms`
Тело [USER]: `{ "text": "U_oc" }`. 201 `{ "synonym": {...}, "renormalize": { "papers": 6, "job_id": "job_…" }, "action_id": "…" }` — бэк в фоне пересопоставляет параметры и пересчитывает статьи коллекции (уведомление «Пересчитано статей: 6»). Ошибки: `409 synonym_exists` «Такое написание уже есть в словаре» (совпадает с синонимом любого параметра коллекции или с подписью параметра); 400 (пусто).

### `DELETE /dictionary/params/{collection}/{param}/synonyms/{text}`
Удаляет только свой (`custom`) синоним. 200 `{action_id}`. Встроенный — `403 forbidden` «Встроенные написания удалить нельзя».

### `POST /dictionary/entries/{entry_id}/synonyms` · `DELETE /dictionary/entries/{entry_id}/synonyms/{text}`
То же для материалов, методов и условий. 409 — совпадает с каноном или существующим синонимом (без учёта регистра).

### `POST /dictionary/units` («появится»)
`{collection, param, unit, factor}` — своя единица. 409, если есть.

### `POST /normalize/preview` — см. §4.9.

Правила нормализации — [§6](06-normalization.md).

---

## 4.15. История и отмена

### `GET /history`
«Журнал правок». Параметры: `action` (`accept|reject|restore|edit|add|acceptAll`; `accept` включает `acceptAll`), `paper_id`, `user_id` (`me`), `from`, `to`, `limit` (по умолчанию 100), `cursor`. Только действия с фактами; сортировка — новые сверху. Источник: [BE].
```json
{ "data": [ { "action_id": "act_95", "at": "2026-11-20T08:57:00Z", "who": "Исследователь", "user_id": "usr_01", "action": "edit", "paper_id": "pap_mapbi", "paper_title": "Перовскитный…", "fact_id": "fct_mapbi_c_A_voc", "param_label": "Voc", "row_label": "образец A", "from": "1100 мВ", "to": "1,08 В", "count": null, "undone": false },
  { "action_id": "act_90", "at": "…", "who": "Котова Е.", "action": "acceptAll", "paper_id": "pap_chunks", "count": 6 } ], "next_cursor": "…" }
```

### `POST /history/undo`
«Отменить последнее» и Ctrl+Z: отменяет последнее неотменённое действие **текущего пользователя** (любого типа, в т. ч. служебное). 200 `{ "undone_action_id": "act_95", "label": "исправление", "message": "Отменено: исправление", "fact_id": "fct_…" }` — `fact_id`, чтобы экран разбора вернул на него выделение. Нечего — `200 { "undone_action_id": null, "message": "Отменять нечего" }`.

### `POST /actions/{id}/undo`
«Отменить» в уведомлении — именно это действие. Если после него этот же объект менял кто-то ещё (или он сам) — `409 conflict` «Уже изменено после этого — отмена невозможна»; если уже отменено — `409 already_undone` «Уже отменено». Групповое действие откатывается целиком. Отмена сама пишется в журнал (`undo_of`) и пересчитывает проверки.

Глубина: 50 последних действий пользователя за 24 ч (как `history.length > 50` в прототипе); удаления — через `.../restore` в течение 30 суток.

---

## 4.16. Экспорт

### Общие параметры (`ExportOptions`)
```json
{ "scope": { "collection": "solar", "project_id": null, "paper_ids": ["pap_mapbi", "pap_cigs"], "rows": ["pap_mapbi:A", "pap_mapbi:B"], "query": null },
  "only_accepted": false, "normalized": true, "with_source": true, "with_quote": false,
  "format": "csv", "layout": "flat", "include_out_of_scope": false }
```
- `scope`: корпус (`corpus_id`), выборка таблицы фактов (`table_filter` — параметры `GET /facts/table`), коллекция целиком, проект, выбранные статьи, `rows` — только эти образцы (то, что осталось после фильтра на Обзоре; общие факты `*` статьи включаются, если выбран хоть один её образец), или `query` — строка запроса Обзора.
- `only_accepted` — только принятые (иначе все, кроме отклонённых).
- `normalized` — канонические единицы или как напечатано.
- `with_source` — страница, «Табл. 2, строка B»; `with_quote` — цитата.
- `format`: `submission|csv|xlsx|json|tsv`. **`submission`** — ZIP ровно из 7 CSV шаблона сдачи (§14) для области 1; с `validate: true` бэк прогоняет `check_submission.py` и кладёт `check_report.txt` (ошибки → `422 submission_invalid`). Остальные форматы — для людей и интерфейса.
- `layout`: `flat` — одна таблица фактов (как предпросмотр); `relational` — связанные таблицы по ТЗ (для CSV — ZIP из нескольких CSV; XLSX — несколько листов; JSON — объект с массивами).

### `POST /exports/preview`
Тело — параметры. Ответ: таблица для живого предпросмотра (первые 50 строк). Источник: [BE].
```json
{ "head": ["Статья", "Год", "Образец", "Параметр", "Значение", "Единица", "Статус", "Страница", "Источник"],
  "rows": [ ["Перовскитный солнечный элемент с КПД 18,6 %…", "2024", "A", "Voc", "1,08", "В", "на проверке", "4", "Табл. 2, строка A"] ],
  "facts": 74, "papers": 6 }
```
Плоская таблица: «Статья, Год, Образец, Параметр, Значение, Единица, Статус» (+ «Страница, Источник» при `with_source`, + «Цитата» при `with_quote`); статус «принят» / «на проверке». Значения в предпросмотре — строки с запятой; в файлах — см. ниже.

### `POST /exports`
Тело — параметры. Небольшой экспорт (≤ 50 тыс. фактов) — синхронно: 200, файл в теле, `Content-Disposition: attachment; filename="faktura-facts.csv"`. Больше — 202 `{ "export_id": "exp_…", "status": "processing" }`, затем `GET /exports/{id}` → `{status, url}` (ссылка на 24 ч). Ошибки: `422 nothing_to_export` «Нечего экспортировать — выберите статьи или снимите «только принятые»». Источник: [BE] из данных [ML]/[USER].

Форматы:
- **CSV** — UTF-8 с BOM, разделитель `;`, перевод строки CRLF, числа с запятой (открывается русским Excel).
- **XLSX** — числа числами, остальное текстом; лист «Факты» (`flat`) или листы по таблицам ТЗ (`relational`).
- **TSV** — для «в буфер» (фронт может собрать и сам из предпросмотра).
- **JSON**, `flat` — как в прототипе (`buildJSON`):
```json
[ { "paper": { "id": "pap_mapbi", "title": "…", "authors": "Иванов А. В., Котова Е. С. и др.", "year": 2024, "journal": "…", "collection": "solar" },
    "facts": [ { "sample": "B", "parameter": "КПД", "as_printed": "18,6 %", "value": 18.6, "unit": "%", "status": "new", "edited": false, "confidence": 0.96, "source": { "page": 4, "ref": "Табл. 2", "where": "строка B" } } ],
    "issues": [ { "level": "conflict", "title": "Текст противоречит таблице", "text": "…" } ] } ]
```
- **Сдача (`submission`)** — 7 таблиц шаблона, колонки и словари 1:1 (§14). Это главный формат для области 1; `relational` ниже — внутренний вариант для интерфейса и области 2.
- **JSON**, `relational` — «машиночитаемый формат и связи» по ТЗ:
```json
{ "format": "faktura-dataset", "version": "1.0", "collection": "solar", "exported_at": "…", "passport_url": "/api/v1/passport?collection=solar",
  "publications": [ { "publication_id": "…", "title": "…", "year": 2024, "DOI": null, "authors": ["…"] } ],
  "materials": [ ... ], "measurements": [ ... ], "devices": [ ... ], "layers": [ ... ], "results": [ ... ],
  "claims": [ ... ], "issues": [ ... ], "references": [ ... ] }
```
Для RAG: `publications`, `datasets_tasks`, `experiments`, `rag_configs`, `metric_results`, `claims`, `issues`, `references`. Каждая запись результата содержит поля происхождения и статуса ТЗ; связи — через идентификаторы (`device_id`, `measurement_id`, `material_id`, `config_id`, …). Полный пример — [§9](09-examples.md). В CSV/XLSX `relational` — по файлу/листу на таблицу с теми же колонками.

### Экспорт одной статьи
Меню «⋯» в разборе и «Экспорт» в профиле — `POST /exports` со `scope.paper_ids = [id]`, `format: json`.

---

## 4.17. Паспорт данных

### `GET /passport`
Параметр `collection` (без него — обе), `format` (`json|md|xlsx`). «Поля, типы, размерности и правила» — что сдаётся по ТЗ. Генерируется из конфигурации [BE].
```json
{ "collection": "solar", "version": "1.0", "schema_version": "2026-11", "scope": "Тонкоплёночные однопереходные солнечные элементы…; без тандемов; только экспериментальные работы",
  "tables": [ { "name": "results", "tz_name": "Результат показателя", "key": "result_id",
    "fields": [ { "name": "metric_value", "type": "number", "required": true, "unit": "по metric_unit", "description": "Значение показателя в канонической единице", "source": "BE (нормализация raw_value от ML)" },
      { "name": "metric_name", "type": "enum", "values": ["PCE", "Voc", "Jsc", "FF", "Rs", "Rsh", "HI", "Jsc_EQE", "T80"], "required": true },
      { "name": "result_origin", "type": "enum", "values": ["primary", "cited"], "required": true, "source": "ML" } ],
    "links": [ { "field": "device_id", "to": "devices.device_id" }, { "field": "measurement_id", "to": "measurements.measurement_id" } ] } ],
  "units": [ { "quantity": "PCE, FF", "unit": "%" }, { "quantity": "Voc", "unit": "V" }, { "quantity": "Jsc", "unit": "mA/cm2" }, { "quantity": "Eg", "unit": "eV" }, { "quantity": "толщина", "unit": "nm" }, { "quantity": "площадь", "unit": "cm2" }, { "quantity": "время", "unit": "h" } ],
  "statuses": { "extraction_status": ["extracted", "present_not_extractable", "absent"], "norm_status": ["ok", "converted", "failed", "not_applicable"], "review_status": ["new", "accepted", "rejected"] },
  "rules": [ { "id": "jv_consistency", "title": "КПД не сходится с Voc × Jsc × FF", "formula": "|Voc·Jsc·FF/Pin − PCE| / PCE > 3 %", "level": "warn", "tz_ref": "стр. 6", "computed_by": "backend" } ],
  "comparability": "Совпадают класс материала, тип прибора, AM1.5G 100 мВт/см², сопоставимая площадь, агрегация, направление развёртки; сертифицированные и стабильность — отдельно" }
```

---

## 4.18. Настройки: что на сервере, что локально

| Что | Где | API |
|---|---|---|
| Профиль: имя, роль, команда, цвет, «чем занимаетесь» | сервер | `GET/PATCH /me` |
| Почта | сервер, только чтение | `GET /me` |
| Проекты, чаты, реплики, оценки | сервер | §4.4, §4.5 |
| Статьи, факты, статусы, правки, ручные факты | сервер | §4.6–4.9 |
| Разбор замечаний (Решено / Не ошибка / Принять как есть) | сервер | §4.10 |
| Словарь: свои синонимы | сервер (общий для команды) | §4.14 |
| Сохранённые виды Обзора | сервер | §4.11 |
| Журнал правок | сервер | §4.15 |
| Выбранная коллекция для нового чата (`state.collection`) | локально (по желанию дублировать в `PATCH /me {default_collection}`) | — |
| Внешний вид: тема, акцент, шрифт, анимации, контраст, плотность | **локально** | — |
| Горячие клавиши и их переопределения | **локально** | — |
| Боковая панель свёрнута, ширина панелей, раскрытые проекты | локально | — |
| Подсказки «уже показаны», демо-тур | локально | — |
| Недавние запросы палитры | локально | — |
| «Оставить обе» у дубликата | сессия браузера | — |
| Позиции узлов графа, масштаб, вкладки экранов | локально | — |

---

## 4.19. Глобальный поиск (палитра ⌘K)

### `GET /search`
| Параметр | Описание |
|---|---|
| `q` | запрос; префиксы задают область: `#` проекты, `"` текст статей (точная фраза), `=` факты, `!` замечания |
| `scope` | `all` (по умолчанию) `| project | paper | text | fact | issue | chat` |
| `limit` | для `all` — по группам: проекты 4, статьи 5, текст 4, факты 6, замечания 3, чаты 4; иначе до 50 |
| `project_id` | ограничить |

Нормализация запроса [BE]: нижний регистр, ё = е, «18,6» = «18.6»; все слова должны найтись; вес: целиком > начало строки > начало слова > внутри слова; бонус за фразу. Если ничего не найдено и запрос набран не в той раскладке — повтор с исправлением («gthjdcrbn» → «перовскит»), в ответе `layout_fixed`. Источник: [BE].
```json
{ "q": "перовскит 18,6", "layout_fixed": null,
  "groups": [
    { "kind": "paper", "title": "Статьи", "total": 1, "hits": [ { "id": "paper:pap_mapbi", "ref": "pap_mapbi", "title": "Перовскитный солнечный элемент с КПД 18,6 %…", "meta": "Иванов А. В., Котова Е. С. и др. · 2024", "score": 23, "marks": [ [0, 10], [44, 48] ], "chat_id": "ch_mapbi", "dot": "conflict", "count": 2 } ] },
    { "kind": "text", "title": "Текст статей", "total": 1, "hits": [ { "id": "text:pap_mapbi:p3", "ref": "pap_mapbi:blk_43", "paper_id": "pap_mapbi", "title": "…составила 1,59 эВ…", "meta": "стр. 4, абзац 3", "snippet": "…перовскита, определённая по спектрам…", "marks": [ [1, 10] ], "fact_id": "fct_mapbi_t_all_bandgap", "score": 8 } ] },
    { "kind": "fact", "title": "Факты", "total": 1, "hits": [ { "ref": "fct_mapbi_c_B_pce", "title": "КПД · образец B", "value": "18,6 %", "meta": "Перовскитный… · Табл. 2", "paper_id": "pap_mapbi", "score": 12 } ] } ] }
```
`marks` — отрезки `[start, end)` для подсветки в `title` / `snippet`; у текста — `fact_id` ближайшего факта. Команды и недавние — [FE].

---

## 4.20. События (SSE)

### `GET /events`
Один поток на вкладку: `Authorization` (или `?token=` одноразовым коротким токеном из `POST /events/token`, т. к. `EventSource` не шлёт заголовки), `Last-Event-ID`. Пинг каждые 25 с (`: ping`). Источник: [BE].

| Событие | Данные | Когда |
|---|---|---|
| `paper.progress` | `{paper_id, stage, progress}` | ход разбора |
| `paper.updated` | `{paper_id, fields: {...}}` | уточнилась коллекция/название, найден дубликат |
| `paper.done` | `{paper_id, chat_id, title, facts, issues}` | разобрана (««…» разобрана: 18 фактов, замечаний — 2» + «Открыть») |
| `paper.failed` | `{paper_id, code, message}` | |
| `facts.changed` | `{paper_id, fact_ids, by_user}` | правка другим пользователем команды |
| `issues.changed` | `{paper_id, opened, closed}` | |
| `dictionary.renormalized` | `{collection, papers}` | пересчёт после синонима |
| `export.ready` | `{export_id, url}` | |
| `run.progress` | `{run_id, corpus_id, progress, counts, facts_found, issues_found}` | ход прогона корпуса, не чаще 1 раза в секунду |
| `run.file` | `{run_id, file_id, status, facts, issues, error}` | файл корпуса сменил статус |
| `run.done` | `{run_id, status, counts}` | прогон закончен |

---

## 4.21. Служебные и админские

| Метод и путь | Описание |
|---|---|
| `GET /health` | 200 `{status:"ok", db:"ok", storage:"ok", ml:"ok|degraded"}` без авторизации |
| `GET /admin/users`, `POST /admin/users`, `PATCH /admin/users/{id}` | учётные записи (только `is_admin`) |
| `POST /admin/gold` | загрузить эталонную разметку (JSON: `{dataset, items:[{paper_id, row_id, param, raw_value, source_kind, page, rects}]}`) |
| `POST /admin/reprocess` | `{collection?, model_version?}` — перезапустить разбор статей новой моделью |
| `/internal/ml/*` | приём результатов ML — §7 |

---

## 4.22. Сводная таблица эндпоинтов

| № | Метод | Путь | Раздел | Источник ответа |
|---|---|---|---|---|
| 1 | POST | `/auth/login` | 4.1 | BE |
| 2 | POST | `/auth/refresh` | 4.1 | BE |
| 3 | POST | `/auth/logout` | 4.1 | BE |
| 4 | GET | `/auth/me` | 4.1 | BE/USER |
| 5 | POST | `/auth/register` (опц.) | 4.1 | BE |
| 6 | GET | `/me` | 4.2 | USER/BE |
| 7 | PATCH | `/me` | 4.2 | USER |
| 8 | GET | `/me/stats` | 4.2 | BE |
| 9 | GET/POST/DELETE | `/me/api-keys` (появится) | 4.2 | BE |
| 10 | GET | `/collections` | 4.3 | BE |
| 11 | GET | `/collections/{id}/stats` | 4.3 | BE |
| 12 | GET | `/projects`  **[В ФОНЕ]** | 4.4 | USER/BE |
| 13 | POST | `/projects`  **[В ФОНЕ]** | 4.4 | USER |
| 14 | GET | `/projects/{id}`  **[В ФОНЕ]** | 4.4 | USER/BE |
| 15 | PATCH | `/projects/{id}`  **[В ФОНЕ]** | 4.4 | USER |
| 16 | DELETE | `/projects/{id}`  **[В ФОНЕ]** | 4.4 | BE |
| 17 | POST | `/projects/{id}/restore`  **[В ФОНЕ]** | 4.4 | BE |
| 18 | GET | `/projects/{id}/suggestions`  **[В ФОНЕ]** | 4.4 | BE/ML |
| 19 | GET | `/chats`  **[В ФОНЕ]** | 4.5 | BE/USER |
| 20 | GET | `/chats/{id}`  **[В ФОНЕ]** | 4.5 | BE/USER |
| 21 | POST | `/chats/ask`  **[В ФОНЕ]** | 4.5 | USER→BE/ML |
| 22 | PATCH | `/chats/{id}`  **[В ФОНЕ]** | 4.5 | USER |
| 23 | DELETE | `/chats/{id}`  **[В ФОНЕ]** | 4.5 | BE |
| 24 | POST | `/chats/{id}/restore`  **[В ФОНЕ]** | 4.5 | BE |
| 25 | GET | `/chats/{id}/turns`  **[В ФОНЕ]** | 4.5 | USER/BE/ML |
| 26 | POST | `/chats/{id}/turns`  **[В ФОНЕ]** | 4.5 | USER→BE/ML |
| 27 | GET | `/turns/{id}/stream` (SSE)  **[В ФОНЕ]** | 4.5 | BE/ML |
| 28 | POST | `/turns/{id}/stop`  **[В ФОНЕ]** | 4.5 | USER→BE |
| 29 | POST | `/turns/{id}/versions`  **[В ФОНЕ]** | 4.5 | USER→BE/ML |
| 30 | PUT | `/turns/{id}/active-version`  **[В ФОНЕ]** | 4.5 | USER |
| 31 | PUT | `/turns/{id}/rating`  **[В ФОНЕ]** | 4.5 | USER |
| 32 | POST | `/papers` | 4.6 | USER→BE |
| 33 | GET | `/papers/{id}/processing` | 4.6 | BE/ML |
| 34 | PUT | `/papers/{id}/collection` | 4.6 | USER→BE |
| 35 | POST | `/papers/{id}/reprocess` | 4.6 | BE |
| 36 | DELETE | `/papers/{id}` | 4.6 | BE |
| 37 | GET | `/papers` | 4.7 | ML/BE |
| 38 | GET | `/papers/{id}` | 4.7 | ML/BE |
| 39 | GET | `/papers/{id}/pages/{n}` | 4.7 | BE |
| 40 | GET | `/papers/{id}/file` | 4.7 | USER (файл) |
| 41 | GET | `/papers/{id}/layout` | 4.7 | ML |
| 42 | GET | `/papers/{id}/anchors` | 4.7 | ML/USER/BE |
| 43 | GET | `/papers/{id}/coverage` | 4.7 | BE |
| 44 | GET | `/papers/{id}/structure` | 4.7 | ML→BE |
| 45 | GET | `/papers/{id}/links` | 4.7 | ML→BE |
| 46 | GET | `/papers/{id}/cited-by`  **[В ФОНЕ]** | 4.12 | BE |
| 47 | GET | `/facts` | 4.8 | ML/BE/USER |
| 48 | GET | `/papers/{id}/facts` | 4.8 | ML/BE/USER |
| 49 | GET | `/facts/{id}` | 4.8 | ML/BE/USER |
| 50 | GET | `/facts/{id}/source` | 4.8 | ML |
| 51 | GET | `/facts/{id}/trace` | 4.8 | BE |
| 52 | POST | `/facts/{id}/accept` | 4.8 | USER |
| 53 | POST | `/facts/{id}/reject` | 4.8 | USER |
| 54 | POST | `/facts/{id}/restore` | 4.8 | USER |
| 55 | PATCH | `/facts/{id}` | 4.8 | USER→BE |
| 56 | POST | `/facts/bulk` | 4.8 | USER |
| 57 | POST | `/papers/{id}/facts/guess` | 4.9 | BE |
| 58 | POST | `/normalize/preview` | 4.9 | BE |
| 59 | POST | `/papers/{id}/facts` | 4.9 | USER→BE |
| 60 | GET | `/issues` | 4.10 | BE |
| 61 | GET | `/papers/{id}/issues` | 4.10 | BE |
| 62 | POST | `/issues/resolve` | 4.10 | USER |
| 63 | POST | `/issues/{id}/reopen` | 4.10 | USER |
| 64 | GET | `/data/rows` | 4.11 | BE |
| 65 | GET | `/data/filter-options` | 4.11 | BE |
| 66 | GET | `/data/query/parse` | 4.11 | BE |
| 67 | GET | `/data/compare` | 4.11 | BE |
| 68 | GET | `/views` | 4.11 | USER |
| 69 | POST | `/views` | 4.11 | USER |
| 70 | PATCH | `/views/{id}` | 4.11 | USER |
| 71 | DELETE | `/views/{id}` | 4.11 | BE |
| 72 | POST | `/views/{id}/restore` | 4.11 | BE |
| 73 | GET | `/graph`  **[В ФОНЕ]** | 4.12 | BE (ML) |
| 74 | GET | `/graph/edges.csv`  **[В ФОНЕ]** | 4.12 | BE |
| 75 | GET | `/entities/{id}/papers`  **[В ФОНЕ]** | 4.12 | BE |
| 76 | GET | `/checks` | 4.13 | BE |
| 77 | GET | `/checks/rules` | 4.13 | BE |
| 78 | GET | `/checks/accuracy` | 4.13 | BE (эталон USER) |
| 79 | GET | `/dictionary` | 4.14 | BE/USER |
| 80 | POST | `/dictionary/params/{c}/{param}/synonyms` | 4.14 | USER |
| 81 | DELETE | `/dictionary/params/{c}/{param}/synonyms/{text}` | 4.14 | USER |
| 82 | POST | `/dictionary/entries/{id}/synonyms` | 4.14 | USER |
| 83 | DELETE | `/dictionary/entries/{id}/synonyms/{text}` | 4.14 | USER |
| 84 | POST | `/dictionary/units` (появится) | 4.14 | USER |
| 85 | GET | `/history` | 4.15 | BE |
| 86 | POST | `/history/undo` | 4.15 | BE |
| 87 | POST | `/actions/{id}/undo` | 4.15 | BE |
| 88 | POST | `/exports/preview` | 4.16 | BE |
| 89 | POST | `/exports` | 4.16 | BE |
| 90 | GET | `/exports/{id}` | 4.16 | BE |
| 91 | GET | `/passport` | 4.17 | BE |
| 92 | GET | `/search` | 4.19 | BE |
| 93 | GET | `/events` (SSE) | 4.20 | BE |
| 94 | POST | `/events/token` | 4.20 | BE |
| 95 | GET | `/health` | 4.21 | BE |
| 96 | GET/POST/PATCH | `/admin/users…` | 4.21 | BE |
| 97 | POST | `/admin/gold` | 4.21 | USER |
| 98 | POST | `/admin/reprocess` | 4.21 | BE |

| 122 | POST | `/runs` | 15.3 | USER→BE |
| 123 | POST | `/runs/{id}/files` | 15.3 | USER→BE |
| 124 | GET | `/runs` | 15.3 | BE |
| 125 | GET | `/runs/{id}` | 15.3 | BE |
| 126 | PATCH | `/runs/{id}` | 15.3 | USER |
| 127 | DELETE | `/runs/{id}` (+ `POST /runs/{id}/restore`) | 15.3 | BE |
| 128 | POST | `/runs/{id}/restart` | 15.3 | USER→BE/ML |
| 129 | POST | `/runs/{id}/stop` | 15.3 | USER→BE |
| 130 | GET | `/runs/{id}/events` (SSE) | 15.3 | BE |
| 131 | GET | `/runs/{id}/summary` | 15.3 | BE |
| 132 | GET | `/papers/{domain}/{pid}` (7 таблиц) | 15.4 | ML→BE/USER |
| 133 | GET | `/results` | 15.4 | ML→BE/USER |
| 134 | GET | `/issues` (строки `issues`) | 15.4 | BE/ML |
| 135 | GET | `/issues/stats` | 15.4 | BE |
| 136 | GET | `/compare` | 15.4 | BE |
| 137 | GET | `/papers/{domain}/{pid}/pdf` | 15.5 | USER (файл) |
| 138 | PATCH | `/papers/{domain}/{pid}/results/{rid}/review` | 15.6 | USER |
| 139 | POST | `/reviews/batch` | 15.6 | USER |
| 140 | GET | `/runs/{id}/submission.zip` | 15.7 | BE |
| 141 | GET | `/runs/{id}/dataset.json`, `/runs/{id}/dataset.xlsx` | 15.7 | BE |
| 142 | GET | `/results/summary` | 15.4 | BE |
| 143 | GET | `/runs/{id}/messages` | 15.9 | USER/BE |
| 144 | POST | `/runs/{id}/messages` (SSE) | 15.9 | USER→BE (ML опц.) |
| 145 | GET | `/runs/{id}/suggestions` | 15.9 | BE |
| 146 | GET | `/graph?run=&domain=&kinds=` | 15.10 | BE (канон — ML-2) |
| 147 | GET | `/papers/{domain}/{pid}/related` | 15.10 | BE |
| 148 | GET | `/papers/{domain}/{pid}/ocr` | 11, 15.5 | ML-2 |
| 149 | PATCH | `/papers/{domain}/{pid}/issues/{iid}` `{state}` | 15.12 | USER |
| 150 | GET | `/runs/{id}/submission/stats` | 15.7 | BE |
| 151 | POST | `/runs/{id}/submission.zip` (выбранные значения) | 15.7 | BE |
| 99 | GET | `/corpus/overview` | 13.8 | BE |
| 100 | POST | `/corpora` | 13.4 | USER |
| 101 | GET | `/corpora` | 13.4 | BE/USER |
| 102 | GET | `/corpora/{id}` | 13.4 | BE/USER |
| 103 | PATCH | `/corpora/{id}` | 13.4 | USER |
| 104 | DELETE | `/corpora/{id}` | 13.4 | BE |
| 105 | POST | `/corpora/{id}/restore` | 13.4 | BE |
| 106 | POST | `/corpora/{id}/files` (multipart / ZIP / URL) | 13.4 | USER→BE |
| 107 | POST/PATCH | `/uploads`, `/uploads/{id}` (resumable ZIP) | 13.4 | USER |
| 108 | GET | `/corpora/{id}/files` | 13.4 | BE |
| 109 | POST | `/corpora/{id}/files/{file_id}/retry` | 13.5 | USER→BE |
| 110 | POST | `/corpora/{id}/runs` | 13.5 | USER→BE/ML |
| 111 | GET | `/corpora/{id}/runs` | 13.5 | BE |
| 112 | GET | `/runs/{run_id}` | 13.5 | BE |
| 113 | POST | `/runs/{run_id}/cancel` | 13.5 | USER→BE |
| 114 | GET | `/runs/{run_id}/output` | 13.6 | BE |
| 115 | POST | `/extract` (CLI через API) | 13.6 | USER→BE/ML |
| 116 | GET | `/corpora/{id}/summary` | 13.8 | BE |
| 117 | GET | `/facts/table` | 13.7 | ML/BE/USER |
| 118 | GET | `/profiles` | 13.3 | BE/USER |
| 119 | GET | `/profiles/{id}` | 13.3 | BE/USER |
| 120 | POST | `/profiles` | 13.3 | USER |
| 121 | PATCH | `/profiles/{id}` | 13.3 | USER |

События SSE прогона (`run.progress`, `run.file`, `run.done`) идут в общем потоке `GET /events` (§4.20).

Плюс позже: OAuth (`/auth/oauth/*`), сброс пароля (`/auth/password/*`); внутренние для ML — §7 (`/internal/ml/jobs/{job_id}/progress|partial|result|failed`).
