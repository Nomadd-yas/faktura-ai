# 3. Модель данных

[← К оглавлению](../BACKEND_SPEC.md)

Колонки таблиц: **Тип**; **Обяз.** — `да` (NOT NULL) или `нет` (может быть `null`); **Ист.** — источник значения:
- **[ML]** — извлечено/сгенерировано ML-сервисом;
- **[BE]** — вычислено или присвоено бэком (идентификаторы, время, нормализация, проверки, счётчики);
- **[USER]** — введено или выбрано пользователем;
- `[ML]→[BE]` — сырьё от ML, итог считает бэк; `[ML]|[USER]` — от ML, у ручных записей — от человека.

Пометка «ТЗ» в описании — поле взято из ТЗ с тем же именем; «появится» — есть в ТЗ, в интерфейсе пока не показано.

Правило ТЗ для всех предметных полей: **фиксируются только параметры, приведённые в публикации; неизвестное не восстанавливается по догадке.** Почти все предметные поля необязательны (`null` = «в статье не указано»); бэк не подставляет значения по умолчанию (§6.4).

## 3.0. Общая схема

```
Workspace ─┬─ Corpus ─┬─ CorpusFile ── Paper           (основной поток, §13)
           │          ├─ BatchJob (прогон)
           │          └─ ExtractionProfile (список полей)
           ├─ User ─┬─ Project ── Chat ── Turn ── TurnVersion (вопрос + ответ)
           │        └─ SavedView
           ├─ Paper (= publication ТЗ) ─┬─ Row (образец: прибор / конфигурация)
           │                            ├─ Fact (значение + происхождение + статус)  ← ядро
           │                            ├─ Claim (утверждение текста)
           │                            ├─ Issue (замечание)
           │                            ├─ Anchor (место в PDF, bbox)
           │                            ├─ Page / Block / Table / Figure (разметка PDF)
           │                            ├─ Reference (список литературы)
           │                            ├─ solar: material, measurement, device, layer, result
           │                            └─ rag:   dataset_task, experiment, rag_config, metric_result
           ├─ Dictionary (синонимы параметров, единицы, материалы/методы, условия)
           ├─ Action (журнал действий и отмена)
           ├─ GoldFact (эталонная разметка)
           └─ MlJob (задача разбора)
```

**Факт интерфейса и таблицы ТЗ.** Интерфейс работает с плоским списком фактов «образец × параметр». ТЗ описывает нормализованные таблицы. В API оба слоя:

- **Таблицы ТЗ** (область 1 — §3.6, область 2 — §3.7; обе = 7 таблиц сдачи) — машиночитаемый набор данных для экспорта и внешних программ, имена полей ровно как в ТЗ.
- **Факт** — одно извлечённое значение одного поля одной записи ТЗ с происхождением, исходной и нормализованной записью и статусом. Поле `target` говорит, какую клетку таблицы ТЗ он заполняет:

| Параметр интерфейса (`param`) | Коллекция | Куда пишется в модели ТЗ |
|---|---|---|
| `pce` | solar | `result`, `metric_name = "PCE"`, `metric_value` |
| `voc` | solar | `result`, `metric_name = "Voc"` |
| `jsc` | solar | `result`, `metric_name = "Jsc"` |
| `ff` | solar | `result`, `metric_name = "FF"` |
| `bandgap` | solar | `material.bandgap_value` (+ `bandgap_unit`, `bandgap_method`) |
| `thickness` | solar | `layer.layer_thickness` слоя с `layer_function = "absorber"` |
| `conditions` | solar | `measurement` (`spectrum`, `light_intensity`, `temperature`, …) — текстовый факт, разбирается на поля |
| `r5` | rag | `metric_result`, `metric_name = "Recall"`, `metric_k = 5` |
| `r10` | rag | `metric_result`, `metric_name = "Recall"`, `metric_k = 10` |
| `mrr` | rag | `metric_result`, `metric_name = "MRR"` |
| `chunk` | rag | `rag_config.chunk_size` (+ `chunk_unit`) |
| `retriever` | rag | `rag_config.retrieval_method` / `retriever_model` / `reranker_model` (текстовый факт, разбирается по словарю) |
| прочие (`rs`, `rsh`, `hi`, `eqe_consistency`, `t80`, `latency_ms`, …) | — | «появится»: новые `metric_name` или поля ТЗ; набор параметров коллекции расширяем (§3.2) |

Таблицы ТЗ — **производные от фактов** [BE]: при изменении факта (принят, исправлен, отклонён) бэк пересобирает затронутые записи. Отклонённый факт клетку не заполняет; исправленный заполняет `corrected_value`.

---

## 3.1. Пользователи и доступ

### Workspace — команда
| Поле | Тип | Обяз. | Ист. | Описание |
|---|---|---|---|---|
| `id` | id | да | [BE] | |
| `name` | string | да | [BE] (админ) | «Команда хакатона» |
| `created_at` | datetime | да | [BE] | |

### User
| Поле | Тип | Обяз. | Ист. | Описание |
|---|---|---|---|---|
| `id` | id | да | [BE] | |
| `workspace_id` | id | да | [BE] | |
| `email` | string | да | [BE] (админ/CLI) | уникальна, нижний регистр; пользователь сам не меняет |
| `password_hash` | string | да | [BE] | наружу не отдаётся |
| `name` | string(1..80) | да | [USER] | «Исследователь»; инициалы аватара — на фронте |
| `role` | string(0..80) | нет | [USER] | «Материаловед» — подпись профиля, не права |
| `team` | string(0..80) | нет | [USER] | по умолчанию — имя Workspace |
| `color` | enum(`sapphire`,`violet`,`emerald`,`amber`,`rose`,`graphite`) | да | [USER] | по умолчанию `graphite` |
| `focus` | string(0..300) | нет | [USER] | «Чем занимаетесь» |
| `is_admin`, `is_active` | bool | да | [BE] (админ) | |
| `created_at`, `last_login_at` | datetime | да / нет | [BE] | |

### RefreshSession [BE]
`id`, `user_id`, `token_hash`, `parent_id` (цепочка ротации), `expires_at`, `revoked_at`, `user_agent`, `ip`, `created_at` — всё [BE].

### UserIdentity («появится», OAuth) [BE]
`user_id`, `provider` enum(`vk`,`yandex`), `subject`, `email`, `linked_at`.

### ApiKey («появится»)
`id` [BE], `user_id` [BE], `name` [USER], `prefix` [BE], `key_hash` [BE], `scopes` (`read`, `upload`) [USER], `created_at`, `last_used_at`, `revoked_at` [BE].

---

## 3.2. Коллекция (предметная область)

Справочник в конфиге бэка (сейчас дублирует `COLLECTIONS` из `src/data.ts`). Все поля — **[BE]** (конфигурация). Отдаётся `GET /collections`; фронт берёт оттуда параметры, единицы, темы.

### Collection
| Поле | Тип | Обяз. | Ист. | Описание |
|---|---|---|---|---|
| `id` | enum(`solar`,`rag`) | да | [BE] | |
| `name` | string | да | [BE] | «Солнечные элементы» / «RAG и поиск» |
| `hint` | string | да | [BE] | подпись под названием |
| `icon` | string | да | [BE] | `sun` / `graph` |
| `row_word` | json | да | [BE] | `{one:"образец", gen:"образца", cap:"Образец", all:"все образцы", allCap:"Все образцы", byRows:"По образцам"}`; у RAG — «конфигурация» |
| `params` | Param[] | да | [BE] | |
| `table` | string[] | да | [BE] | параметры таблицы статьи и Обзора: solar `voc,jsc,ff,pce`; rag `chunk,r5,r10,mrr` |
| `best` | string | да | [BE] | главная метрика: `pce` / `r5` |
| `quality` | string[] | да | [BE] | метрики с понятием «лучше»: solar `pce,voc,jsc,ff`; rag `r5,r10,mrr` |
| `plot` | `{x, y}` | да | [BE] | оси графика Обзора |
| `topics` | `{id, name, pattern}`[] | да | [BE] | темы фильтров: перовскит, CIGS, CdTe, тандем, бессвинцовые / гибридный поиск, BM25, переранжирование, размер чанка |
| `scope_note` | string | да | [BE] | границы корпуса по ТЗ |

### Param
| Поле | Тип | Обяз. | Ист. | Описание |
|---|---|---|---|---|
| `key` | string | да | [BE] | `pce`, `voc`, … |
| `label` | string | да | [BE] | «КПД», «Voc», «Ширина зоны», «Recall@5» |
| `unit` | string | да | [BE] | каноническая единица (§6.1), **ASCII**: `%`, `V`, `mA/cm2`, `Ohm*cm2`, `h`, `eV`, `nm`, `cm2` (по-русски — только в `display` для интерфейса) |
| `units` | `Record<string, number>` | да | [BE] | встроенные единицы → множитель (`{"В":1,"мВ":0.001}`); пополняется словарём |
| `digits` | int | да | [BE] | знаков после запятой при показе |
| `text` | bool | да | [BE] | текстовый параметр |
| `direction` | enum(`higher`,`lower`,`none`) | да | [BE] | куда «лучше» |
| `target` | json | да | [BE] | `{entity:"result", metric_name:"PCE"}` … |
| `metric_k` | int | нет | [BE] | для @k |
| `scale` | `{min, max}` | нет | [BE] | допустимая шкала: PCE 0…100 %, Recall 0…1 |

---

## 3.3. Проекты и чаты [В ФОНЕ]

> Выключены в интерфейсе флагом `FEATURES` (`src/features.ts`); бэку делать не нужно до решения команды. Сущность интерфейса вместо них — **разбор** `Run` (§15.2).

### Project
| Поле | Тип | Обяз. | Ист. | Описание |
|---|---|---|---|---|
| `id` | id | да | [BE] | |
| `owner_id` | id | да | [BE] | |
| `name` | string(1..120) | да | [USER] | пустое → «Новый проект» [BE] |
| `collection` | enum(`solar`,`rag`) | да | [USER] | коллекция проекта; статьи, загруженные в проект, получают её |
| `note` | string(0..500) | да | [USER] | описание, `""` по умолчанию |
| `pinned` | bool | да | [USER] | |
| `created_at`, `updated_at` | datetime | да | [BE] | порядок: `pinned desc, created_at asc` |
| `deleted_at` | datetime | нет | [BE] | мягкое удаление |
| `chats_count`, `papers_count`, `open_issues` | int | да | [BE] | вычисляемые в ответе |

Поле прототипа `open` (раскрыт в боковой панели) — [FE].

### Chat
| Поле | Тип | Обяз. | Ист. | Описание |
|---|---|---|---|---|
| `id` | id | да | [BE] | |
| `owner_id` | id | да | [BE] | |
| `project_id` | id | нет | [USER] | `null` — «Недавние»; при загрузке PDF — проект, где загружали |
| `kind` | enum(`paper`,`ask`) | да | [BE] | `paper` создаётся загрузкой PDF, `ask` — первым вопросом |
| `paper_id` | id | для `paper` | [BE] | у статьи ровно один чат |
| `collection` | enum | да | [USER]→[BE] | выбранная под полем ввода; действующая = проекта, иначе статьи, иначе эта |
| `title` | string(1..200) | да | [BE]\|[USER] | [BE]: «Новый вопрос» → первые 48 символов первого вопроса; у `paper` — название статьи. [USER]: переименование |
| `title_custom` | bool | да | [BE] | переименовали вручную — автоматически больше не меняем |
| `pinned` | bool | да | [USER] | |
| `created_at`, `updated_at` | datetime | да | [BE] | `updated_at` растёт при каждой реплике |
| `deleted_at` | datetime | нет | [BE] | |
| `paper_status` | json | нет | [BE] | у `paper` в списках: `{progress, top_level, open_issues}` для точки и полоски в боковой панели |

### Turn — реплика
| Поле | Тип | Обяз. | Ист. | Описание |
|---|---|---|---|---|
| `id` | id | да | [BE] | |
| `chat_id` | id | да | [BE] | |
| `seq` | int | да | [BE] | |
| `active_version` | int | да | [USER] | какая версия показана («1/2»); после новой версии — [BE] ставит последнюю |
| `versions` | TurnVersion[] | да | — | |
| `rating` | enum(`up`,`down`) | нет | [USER] | повторное нажатие снимает |
| `rating_reason` | enum(`Неверная цифра`,`Не тот источник`,`Неполный ответ`) | нет | [USER] | |

### TurnVersion
| Поле | Тип | Обяз. | Ист. | Описание |
|---|---|---|---|---|
| `version` | int | да | [BE] | 1, 2, … |
| `question` | string(1..2000) | да | [USER] | |
| `status` | enum(`pending`,`streaming`,`done`,`stopped`,`error`) | да | [BE] | `stopped` — по «Стоп» [USER], сделанное сохраняется |
| `steps` | string[] | да | [BE] | «Разбираю вопрос», «Ищу в 6 статьях проекта», «Сверяю с источниками»; в чате статьи — «Читаю статью», «Сверяю с таблицами и текстом» |
| `step` | int | да | [BE] | |
| `answer` | Answer | нет | [BE]\|[ML]→[BE] | см. ниже |
| `error` | `{code, message}` | нет | [BE] | |
| `generated_by` | enum(`rules`,`ml`) | да | [BE] | §7.6 |
| `model_version` | string | нет | [ML] | |
| `created_at`, `finished_at` | datetime | да / нет | [BE] | |

### Answer
В режиме `rules` все поля — [BE]. В режиме `ml` текст — [ML], сноски и карточки проверяет и дополняет [BE].

| Поле | Тип | Обяз. | Ист. | Описание |
|---|---|---|---|---|
| `intro` | string | да | [BE]\|[ML] | вступление |
| `items` | AnswerItem[] | да | [BE]\|[ML] | пункты |
| `outro` | string | да | [BE]\|[ML] | итог, `""` если нет |
| `cites` | Cite[] | да | [BE] | бэк строит сноски по `fact_id` (у ML — по присланным `fact_id`, после проверки) |
| `card` | Card | нет | [BE]\|[ML]→[BE] | мини-экран |
| `explore` | string | да | [BE] | запрос для «Открыть в обзоре данных», `""` — нет кнопки |
| `followups` | string[] | да | [BE]\|[ML] | до 3; уже заданное (и то же намерение) не предлагается — фильтрует [BE] |

`AnswerItem`: `text` [BE]|[ML], `cite` (int, `0` — без сноски) [BE], `head` (необяз., название статьи над группой) [BE].

`Cite` (всё [BE]): `n`, `fact_id`, `paper_id`, `title`, `where` («стр. 4 · Табл. 2, строка B»), `quote` (строка таблицы «подпись — образец: значение» или предложение текста), `issue_id` (необяз.).

`Card` (`kind` и параметры — [BE]|[ML]→[BE]; данные карточки фронт берёт отдельными запросами):
| `kind` | Поля | Что показывает |
|---|---|---|
| `compare` | `collection`, `ids` (paper_id[]), `rows` (row_id[], параллельно), `metric` | таблица сравнения 2–3 строк |
| `checks` | `collection`, `papers` (необяз.), `skip` (fact_id[] уже в тексте) | сводка проверок |
| `coverage` | `paper_id` | карта полноты |
| `structure` | `paper_id` | стек слоёв / конвейер RAG |
| `graph` | `collection`, `root` (необяз.), `focus` (entity_id, необяз.), `title` | граф связей |

---

## 3.4. Статья (publication)

### Paper
Поля ТЗ «Публикация»: `publication_id; title; year; DOI; authors`.

| Поле | Тип | Обяз. | Ист. | Описание |
|---|---|---|---|---|
| `publication_id` (= `id`) | string | да | [BE] | ТЗ; **имя файла без расширения** (`D001`, `P042`) — так требует формат сдачи |
| `domain` | enum(`solar`,`rag`) | да | [BE]\|[USER] | **ключ статьи — пара (`domain`, `publication_id`)**: коды D001… совпадают в двух областях; пути `/papers/{domain}/{pid}` |
| `title` | string | да | [BE]→[ML] | до разбора — имя файла без `.pdf`, `_`/`-` → пробел [BE]; после разбора — извлечённое [ML], если найдено. ТЗ |
| `year` | int | нет | [ML] | ТЗ |
| `DOI` | string | нет | [ML] | ТЗ, без `https://doi.org/` |
| `authors` | string[] | да | [ML] | ТЗ; до разбора `[]` |
| `authors_text` | string | да | [BE] | склейка: «Иванов А. В., Котова Е. С. и др.»; до разбора «Загружено вами» |
| `journal` | string | нет | [ML] | «Физика и химия материалов, 2024, т. 16, № 2» |
| `collection` | enum(`solar`,`rag`) | да | [BE]\|[USER] | правило §7.2-A1; смена человеком |
| `keywords` | string[] | да | [ML] | `tags` прототипа |
| `page_count` | int | нет | [BE] | число страниц PDF |
| `main_page` | int | нет | [ML] | страница главной таблицы |
| `main_table` | `{no, caption, section, page, anchor_id}` | нет | [ML] | `tableNo`, `caption`, `section` |
| `main_figure` | `{no, caption, page}` | нет | [ML] | `figure` |
| `in_scope` | bool | да | [ML]\|[USER] | статья в области (раздел 3.1 условий); `false` → в остальных таблицах строк нет |
| `exclusion_reason` | string | при `in_scope=false` | [ML]\|[USER] | почему исключена |
| `file` | `{name, size, sha256, mime}` | нет | [USER]→[BE] | имя — от пользователя, остальное считает бэк |
| `uploaded_by` | id | нет | [BE] | |
| `mine` | bool | да | [BE] | `uploaded_by == me` |
| `added_at` | datetime | да | [BE] | |
| `processing` | Processing | да | [BE] | |
| `topic_by` | enum(`project`,`text`,`guess`,`manual`) | да | [BE] | |
| `topic_confidence` | number 0..1 | нет | [ML] | |
| `dup_of`, `dup_kind` | id, enum(`file`,`doi`,`title`) | нет | [BE] | |
| `model_version` | string | нет | [ML] | |
| `deleted_at` | datetime | нет | [BE] | |
| `chat_id` | id | да | [BE] | |
| `quality` | `{total, sourced_pct, normalized_pct, accepted, issues}` | да | [BE] | `qualityOf()` |
| `coverage_summary` | `{filled, total}` | да | [BE] | «найдено X из Y» |
| `open_issues` | int | да | [BE] | |
| `top_level` | enum(`conflict`,`doubt`) | нет | [BE] | точка в списках |
| `facts_count` | int | да | [BE] | |

`Processing` (всё [BE], этап и процент первых этапов — по прогрессу от [ML]):
| Поле | Тип | Описание |
|---|---|---|
| `status` | enum(`queued`,`processing`,`done`,`failed`) | `done` ⇔ `progress = 100` |
| `stage` | enum(`Текст`,`Таблицы`,`Рисунки`,`Формулы`,`Нормализация`,`Проверка`) | первые четыре — работа ML, последние два — бэка |
| `progress` | int 0..100 | 0–80 % — ML, 80–100 % — бэк |
| `error` | `{code, message}` | при `failed` |
| `started_at`, `finished_at` | datetime | |
| `ml_job_id` | id | |

### Row — образец
| Поле | Тип | Обяз. | Ист. | Описание |
|---|---|---|---|---|
| `row_id` | string | да | [ML]→[BE] | ML даёт локальный ключ, бэк делает уникальным в статье |
| `paper_id` | id | да | [BE] | |
| `label` | string | да | [ML] | как в таблице: «A», «Без обработки», «BM25», «512» |
| `order` | int | да | [ML] | |
| `entity` | json | да | [BE] | solar `{device_id, measurement_id, material_id}`; rag `{config_id, experiment_id, dataset_id, task_id}` |
| `time_point`, `time_unit` | number, string | нет | [ML]→[BE] | строка — момент времени («1000 ч»), единица — ч |

Особая строка **`*`** — «все образцы статьи» [BE]: факт с `row_id = "*"` относится ко всем строкам («ширина зоны 1,59 эВ во всех образцах»); в модели ТЗ заполняет общую запись (`material`, `measurement`).

### Claim — утверждение в тексте
| Поле | Тип | Обяз. | Ист. | Описание |
|---|---|---|---|---|
| `claim_id` | id | да | [BE] | |
| `paper_id` | id | да | [BE] | |
| `row_id` | string | да | [ML] | о какой строке |
| `param` | string | да | [ML]→[BE] | параметр (ML даёт название, бэк сопоставляет по словарю) |
| `kind` | enum(`best`,`equals`) | да | [ML] | |
| `value` | number | для `equals` | [ML]→[BE] | ML — как написано, бэк нормализует |
| `value_raw` | string | для `equals` | [ML] | «0,76» |
| `text` | string | да | [ML] | фраза, как в статье |
| `place` | enum(`text`,`abstract`,`conclusion`) | да | [ML] | |
| `anchor_id` | id | да | [ML]→[BE] | |
| `source_page`, `source_section` | int, string | да / нет | [ML] | |
| `confidence` | number | да | [ML] | |

---

## 3.5. Факт — ядро модели

Факт — одно извлечённое значение одного поля одной записи ТЗ с происхождением. Для показателей (`metric_name`) факт **один к одному** соответствует строке `results.csv` (включая повторы с `duplicate_of`); для полей материалов, приборов, слоёв и измерений (ширина зоны, толщина, площадь, условия) факт — служебная запись с местом в источнике, а значение переносится в соответствующую колонку таблицы.

Поля ТЗ и шаблона сдачи — с теми же именами. **Правки и проверка человеком хранятся отдельными полями** и не меняют `extraction_status`.

| Поле | Тип | Обяз. | Ист. | Описание |
|---|---|---|---|---|
| `fact_id` | id | да | [BE] | для показателя = `result_id` строки `results` |
| `publication_id` | id | да | [BE] | имя файла без расширения: `D001`, `P042` |
| `row_id` | string | да | [ML]\|[USER] | образец (= `device_id`); `"*"` — все образцы; пусто у `cited` |
| `param` | string | да | [ML]→[BE]\|[USER] | ключ поля профиля: `PCE`, `Voc`, …, `bandgap_value`, `layer_thickness`, `active_area`, `light_intensity` |
| `target` | json | да | [BE] | клетка таблицы сдачи: `{table:"results", id:"R004"}` или `{table:"materials", id:"M1", column:"bandgap_value"}` |
| `raw_value` | string | нет | [ML]\|[USER] | дословно как в статье: `1123`, `0.49` |
| `raw_unit` | string | нет | [ML]\|[USER] | дословно: `mV`, `fraction`, `%`, `W/m2` |
| `normalized_value` | number | нет | [BE] | в целевой единице шаблона |
| `normalized_unit` | string | нет | [BE] | ASCII: `%`, `V`, `mA/cm2`, `Ohm*cm2`, `h`, `eV`, `nm`, `cm2` |
| `extraction_status` | enum(`extracted`,`present_not_extractable`,`absent`) | да | [ML] | **только эти три** (шаблон): `extracted` — значение извлечено; `present_not_extractable` — есть, но извлечь нельзя (только в supplementary, только на графике), значения пустые, место указано; `absent` — поле профиля в статье не найдено (такие строки в выгрузку `results` по умолчанию не пишутся) |
| `norm_status` | enum(`ok`,`converted`,`failed`,`not_applicable`) | да | [BE] | результат нормализации: `ok` — единица уже целевая; `converted` — пересчитано (`mV → V`, `fraction → %`); `failed` — единица не распознана или шкала неизвестна, `normalized_value` пусто → замечание `unit_inconsistency`; `not_applicable` — текстовое поле |
| `review_status` | enum(`new`,`accepted`,`rejected`) | да | [USER] | проверка человеком; по умолчанию `new` |
| `corrected_value` | number | нет | [USER]→[BE] | исправление человека в целевой единице; в выгрузку идёт как `normalized_value`/`metric_value` (а `raw_value` остаётся из статьи) |
| `corrected_raw` | string | нет | [USER] | как человек ввёл («19 %») |
| `added_by` | enum(`model`,`user`) | да | [BE] | `user` — добавлено выделением в PDF |
| `added_by_user`, `reviewed_by`, `reviewed_at` | id, id, datetime | нет | [BE] | |
| `value` | number | нет | [BE] | действующее: `corrected_value ?? normalized_value` |
| `display` | string | нет | [BE] | для интерфейса по-русски: «1,123 В», «71,0 %» (в API и выгрузке единицы — ASCII) |
| `confidence` | number 0..1 | да | [ML] | у ручных — 1 |
| `aggregation` | enum(`champion`,`mean`,`median`) | нет | [ML] | **пусто**, если в статье не сказано |
| `spread_value`, `n_devices` | number, int | нет | [ML]→[BE] | |
| `time_point`, `time_unit` | number, string | нет | [ML] | для стабильности |
| `measurement_id`, `material_id` | id | нет | [ML]→[BE] | |
| `result_origin` | enum(`primary`,`cited`) | да | [ML] | |
| `cited_publication_id` | string | нет | [ML] | для `cited` — ссылка, как в статье: `[12]`, `[19–21]`, «Bai et al., 2018» |
| `cited_paper_id` | id | нет | [BE] | если цитируемая статья есть в корпусе (по DOI из списка литературы) — для графа |
| `source_page` | int | да* | [ML]\|[USER] | страница **PDF-файла** с 1. *Обязательна при `extracted` и `present_not_extractable` |
| `source_section` | string | нет | [ML] | «Results and Discussion», «Abstract» |
| `source_table` | string | нет | [ML] | «Table 4», «TABLE 1» |
| `source_figure` | string | нет | [ML] | «Figure 10», «FIGURE 5B» |
| `source_fragment` | string | нет | [ML]\|[USER] | **дословная** короткая цитата со страницы (или «Table 2, строка B»); проверяется как в `check_submission.py` |
| `source_table_row_label`, `source_table_row_text` | string | нет | [ML] | метка строки таблицы и сырой текст строки — для «места в таблице», когда `source_fragment` — адрес «Table 1, строка X» |
| `quote_found` | bool | да | [BE] | цитата найдена на `source_page` (та же нормализация, что в `check_submission.py`) |
| `duplicate_of` | id | нет | [ML]\|[BE] | `result_id` основной записи для повторного упоминания того же значения того же прибора **внутри статьи** (§3.6.1) |
| `note` | string | нет | [ML]\|[USER] | свободное примечание (колонка `note` сдачи) |
| `source_kind` | enum(`table`,`text`,`figure`,`abstract`,`supplementary`) | да | [BE] | для интерфейса — из `source_*` |
| `anchor_id` | id | нет | [BE] | bbox для подсветки (из `bbox` ML или выделения человека) |
| `issue_ids` | id[] | да | [BE] | |
| `model_version` | string | нет | [ML] | |
| `created_at`, `updated_at` | datetime | да | [BE] | |

Прежние значения прототипа (`raw_only`, `text`, `manual`, `corrected`, `not_reported`) в `extraction_status` **не используются**: нераспознанная единица — `norm_status = failed`; ручной факт — `added_by = user`; исправление — `corrected_value`; текстовое поле — `norm_status = not_applicable`.

### Anchor — место в PDF
| Поле | Тип | Обяз. | Ист. | Описание |
|---|---|---|---|---|
| `anchor_id` | id | да | [BE] | |
| `paper_id` | id | да | [BE] | |
| `kind` | enum(`cell`,`text`,`figure`,`manual`,`caption`,`reference`) | да | [ML]\|[BE] | `manual` — [BE] |
| `page` | int | да | [ML]\|[USER] | |
| `rects` | Rect[] | да | [ML]\|[USER] | |
| `text` | string | нет | [ML]\|[USER] | |
| `block_id` | id | нет | [ML]\|[BE] | |
| `char_range` | `[start, end]` | нет | [ML]\|[BE] | |

`Rect`: `{x, y, w, h}` в **долях страницы** 0…1 от левого верхнего угла.

### Разметка PDF (все поля — [ML], идентификаторы и URL — [BE])
`Page`: `paper_id`, `n`, `width_pt`, `height_pt` [ML], `image_url` [BE], `has_text_layer` [ML].

`Block`: `block_id` [BE], `paper_id`, `page`, `type` enum(`heading`,`paragraph`,`table`,`caption`,`figure`,`formula`,`reference`,`abstract`), `section`, `order`, `bbox`, `text`, `spans` — `[{start, end, anchor_id, kind}]`.

`Table`: `table_id` [BE], `paper_id`, `no`, `caption`, `page`, `section`, `bbox`, `columns`, `cells` (`[{row, col, text, bbox, anchor_id}]`), `row_labels`.

`Figure`: `figure_id` [BE], `paper_id`, `no`, `caption`, `page`, `bbox`, `kind`.

---

## 3.6. Предметная область 1 — солнечные элементы: 7 таблиц сдачи

Сущности **1:1 с шаблоном** `docs/case5/submission_template/` (поля, порядок, словари) — см. также [§14](14-submission.md). Ниже — каждое поле с источником; служебные поля API (не выгружаются) — в конце каждой таблицы, курсивом.

Общее: пустое значение = «в статье нет» (`null` в API, пустая ячейка в CSV); логические — `true`/`false`; несколько значений — массив в API, через `;` в CSV; id уникальны внутри публикации.

### publications
| Поле | Тип | Обяз. | Ист. | Описание |
|---|---|---|---|---|
| `publication_id` | string | да | [BE] | имя файла без `.pdf`: `D007`, `P042` |
| `title` | string | нет | [ML] | |
| `year` | int | нет | [ML] | |
| `DOI` | string | нет | [ML] | без `https://doi.org/` |
| `authors` | string[] | нет | [ML] | в CSV через `;` («Qiao H.W.; Chen M.»), «et al.» — как в статье |
| `in_scope` | bool | да | [ML] | статья в области (раздел 3.1 условий): экспериментальная работа с изготовленным однопереходным тонкоплёночным прибором; расчётные/модельные работы — `false` |
| `exclusion_reason` | string | при `in_scope=false` | [ML] | по-русски, со ссылкой на раздел 3.1: «Только численное моделирование…; изготовленных образцов и измеренных характеристик нет» |

У статьи с `in_scope=false` **в остальных таблицах строк нет**: бэк не создаёт факты и записи, даже если ML что-то вернул; статья видна в корпусе с пометкой «вне области». Человек может переопределить (`PATCH /papers/{id} {in_scope, exclusion_reason}` [USER]) → перезапуск разбора.

### materials
| Поле | Тип | Обяз. | Ист. | Значения / описание |
|---|---|---|---|---|
| `material_id` | string | да | [ML]→[BE] | `M1`… |
| `material_name_raw` | string | да | [ML] | как в статье: «0.001 M SrCl2–doped CsPbI2Br» |
| `formula_normalized` | string | нет | [ML]→[BE] | для перовскита — A-B-X с раскрытыми MA, FA, Cs (`CsPbI2Br`, `CH3NH3PbI3`); бэк сверяет со словарём материалов |
| `material_class` | enum | нет | [ML] | `perovskite`, `CIGS`, `CdTe`, `kesterite`, `Sb2Se3`, `thin-film Si`, `other` |
| `dimensionality` | enum | нет | [ML] | `3D`, `2D`, `2D-3D` |
| `additives` | string[] | нет | [ML] | в CSV через `;` |
| `bandgap_value` | number | нет | [ML]→[BE] | в eV |
| `bandgap_unit` | enum | нет | [BE] | `eV` |
| `bandgap_method` | enum | нет | [ML] | `Tauc`, `EQE`, `PL`, `cited` (взято из литературы) |
| `note` | string | нет | [ML]\|[USER] | |
| *`fact_ids`, `bandgap_raw`* | | | [BE]/[ML] | служебные |

### devices
| Поле | Тип | Обяз. | Ист. | Значения / описание |
|---|---|---|---|---|
| `device_id` | string | да | [ML]→[BE] | `DV1`, `DV-B4` |
| `absorber_material_id` | string | нет | [ML] | → `materials` |
| `device_label` | string | да | [ML] | подпись в статье: «pristine», «0.001 M SrCl2», «B4» |
| `device_role` | enum | нет | [ML] | `control`, `target`, `record` |
| `device_type` | enum | нет | [ML] | `cell`, `minimodule`, `module` |
| `architecture` | enum | нет | [ML] | `n-i-p`, `p-i-n`, `substrate`, `superstrate` |
| `substrate_type` | enum | нет | [ML] | `rigid`, `flexible` |
| `active_area` | number | нет | [ML]→[BE] | в cm2 |
| `area_unit` | enum | нет | [BE] | `cm2` |
| `area_definition` | enum | нет | [ML] | `designated illumination`, `aperture`, `total` |
| `encapsulation` | bool | нет | [ML] | |
| `note` | string | нет | [ML]\|[USER] | нестандартная формулировка: «n-i-p (planar); aperture (metal mask)» |
| *`row_id`* | | | [BE] | = `device_id`; строка интерфейса |

### layers
| Поле | Тип | Обяз. | Ист. | Значения / описание |
|---|---|---|---|---|
| `layer_id` | string | да | [ML]→[BE] | `DV1-L1` |
| `device_id` | string | да | [ML] | → `devices` |
| `layer_order` | int | да | [ML] | от подложки: 1, 2, …; **подложка не пишется** — первым идёт контакт на ней |
| `layer_function` | enum | да | [ML] | `front_contact`, `ETL`, `absorber`, `passivation`, `HTL`, `interlayer`, `recombination`, `back_contact`. CdS и аналогичные n-слои → `ETL`; металлическая сетка → `front_contact`; просветляющее и прочие → `interlayer`; исходное название — в `note` |
| `layer_material` | string | да | [ML] | как в статье: «FTO (glass, 8 Ω/sq)», «compact TiO2» |
| `layer_thickness` | number | нет | [ML]→[BE] | в nm («2.1 µm» → 2100) |
| `thickness_unit` | enum | нет | [BE] | `nm` |
| `deposition_method` | string | нет | [ML] | |
| `annealing_temperature` | string | нет | [ML] | в °C с единицей: «500 °C» |
| `annealing_time` | string | нет | [ML] | в единицах статьи с явной единицей: «60 min» |
| `atmosphere` | string | нет | [ML] | |
| `note` | string | нет | [ML]\|[USER] | |

### measurements
| Поле | Тип | Обяз. | Ист. | Значения / описание |
|---|---|---|---|---|
| `measurement_id` | string | да | [ML]→[BE] | `MS1` |
| `device_id` | string[] | нет | [ML] | **несколько приборов через `;`**: одно измерение — для всех приборов `DV1;DV2;DV3` |
| `measurement_type` | enum | нет | [ML] | `J-V`, `MPP tracking`, `EQE`, `stability` (темновая ВАХ — `J-V` с `note`) |
| `spectrum` | string | нет | [ML] | `AM1.5G` |
| `light_intensity` | number | нет | [ML] | как в статье: `100` |
| `intensity_unit` | string | нет | [ML] | как в статье: `mW/cm2`, `W/m2` (не пересчитывается; для расчёта Pin бэк переводит сам) |
| `scan_direction` | enum | нет | [ML] | `reverse`, `forward`, `average`; прямой и обратный ход — **разные** измерения |
| `scan_rate` | string | нет | [ML] | с единицей: «0.15 V/s» |
| `mask_used` | bool | нет | [ML] | |
| `certified` | bool | нет | [ML] | |
| `certification_lab` | string | нет | [ML] | |
| `temperature` | string | нет | [ML] | |
| `stability_protocol` | enum | нет | [ML] | `ISOS-D`, `ISOS-L`, `ISOS-T`, `other` (не назван → `other` + `stress_conditions`) |
| `stress_conditions` | string | нет | [ML] | |
| `note` | string | нет | [ML]\|[USER] | «В статье обратный ход назван FB–SC» |

### results
Одна строка — один показатель для одного прибора при одних условиях (включая направление развёртки и момент времени). Лучший прибор и среднее — разные строки. Каждая строка — это факт (§3.5); здесь — колонки сдачи.

| Поле | Тип | Обяз. | Ист. | Значения / описание |
|---|---|---|---|---|
| `result_id` | string | да | [ML]→[BE] | `R001`, повторы — `R008-1` |
| `device_id` | string | нет | [ML] | пусто у `cited` |
| `measurement_id` | string | нет | [ML] | пусто у `cited` |
| `material_id` | string | нет | [ML] | |
| `metric_name` | string | да | [ML] | оцениваемые: `PCE`, `Voc`, `Jsc`, `FF`, `Rs`, `Rsh`, `HI`, `Jsc_EQE`, `stabilized_PCE`, `T80`, `retained_PCE`; прочие (`Voc_deficit`, `Jmpp`, `Vmpp`, `AVT`…) — допустимы, в оценке не участвуют |
| `metric_value` | number | нет | [BE] | **= `normalized_value`** (или исправление человека) |
| `metric_unit` | string | нет | [BE] | **= `normalized_unit`** |
| `aggregation` | enum | нет | [ML] | `champion`, `mean`, `median`; **пусто**, если не сказано |
| `spread_value` | number | нет | [ML] | ± или стандартное отклонение |
| `n_devices` | int | нет | [ML] | |
| `time_point`, `time_unit` | number, string | нет | [ML] | для стабильности: `1000`, `h` |
| `raw_value`, `raw_unit` | string | нет | [ML] | дословно: `0.49` + `fraction`, `1123` + `mV` |
| `normalized_value` | number | нет | [BE] | |
| `normalized_unit` | string | нет | [BE] | PCE, FF, HI, retained_PCE, stabilized_PCE — `%`; Voc — `V`; Jsc, Jsc_EQE — `mA/cm2`; Rs, Rsh — `Ohm*cm2`; T80 — `h` |
| `result_origin` | enum | да | [ML] | `primary`, `cited` |
| `cited_publication_id` | string | для `cited` | [ML] | ссылка как в статье |
| `source_page`, `source_section`, `source_table`, `source_figure`, `source_fragment` | | | [ML] | §3.5 |
| `extraction_status` | enum | да | [ML] | `extracted`, `present_not_extractable`, `absent` |
| `duplicate_of` | string | нет | [ML]\|[BE] | §3.6.1 |
| `note` | string | нет | [ML]\|[USER] | «Тип агрегации в таблице не указан» |
| *`review_status`, `corrected_value`, `added_by`, `confidence`, `anchor_id`, `quote_found`, `comparability_key`* | | | | служебные (§3.5), не выгружаются |

### issues
См. §3.8 и §5.1: `issue_id`, `record_ids` (через `;`), `check_name`, `description`, `source_page`, `source_fragment`.

### 3.6.1. Повторы значения внутри статьи (`duplicate_of`)

Одно и то же значение одного прибора часто встречается в статье несколько раз: в аннотации, в тексте, в таблице, на рисунке, в выводах. Правило шаблона:
- основная запись — одна (обычно таблица или самое полное место), `duplicate_of` пусто;
- **каждое** повторное упоминание — **отдельная строка `results`** со своими `source_page`, `source_fragment` (и `source_table`/`source_figure`), с теми же `device_id`, `measurement_id`, `metric_name`, `aggregation`, значением и `duplicate_of = <result_id основной>`;
- если повтор расходится со значением основной (5.25 % в аннотации против 3.85 % в таблице) — это **не** повтор, а отдельная запись + замечание `contradiction`;
- «ловушки»: одинаковые значения у **разных** приборов — не повтор (замечание `dedup_trap` при необходимости); одна строка в двух таблицах — один прибор — повтор.

Кто: ML-1 размечает повторы при извлечении (`duplicate_of` в ответе); бэк проверяет (одинаковые прибор, показатель, измерение, агрегация, значение в пределах округления) и, если ML не связал явные повторы, связывает сам — с пометкой `note: "связано бэком"`. В интерфейсе повторы показываются под основной записью («ещё 2 упоминания»), принятие основной не принимает повторы автоматически.

Это **не** дубликаты статей в корпусе (одна статья загружена дважды) — те описаны в §13.4 и решаются на уровне `CorpusFile`/`Paper`.

### 3.6.2. Параметры интерфейса → таблицы сдачи

| Параметр прототипа | Куда |
|---|---|
| `pce`, `voc`, `jsc`, `ff` | `results` с `metric_name` `PCE`, `Voc`, `Jsc`, `FF` |
| `bandgap` | `materials.bandgap_value` |
| `thickness` | `layers.layer_thickness` слоя `absorber` |
| `conditions` | `measurements` (`spectrum`, `light_intensity`, `intensity_unit`, `temperature`) |
| новые (появятся в интерфейсе) | `Rs`, `Rsh`, `HI`, `Jsc_EQE`, `stabilized_PCE`, `T80`, `retained_PCE`; `active_area`, `scan_direction`, `aggregation`, `device_role`, `result_origin` |

## 3.7. Предметная область 2 — RAG: 7 таблиц сдачи

Сущности **1:1 с шаблоном** `docs/case5_area2/submission_template/` (`README.md`, `schema.json`, версия `rag-viniti-adaptation-1.0`): `publications`, `configurations`, `datasets`, `tasks`, `experiments`, `results`, `issues`. Формат — [§14.B](14-submission.md). Общие правила те же, что в области 1: пустое = нет в статье (не `NaN`, `null`, `unknown`; ноль — `0`), id уникальны **внутри публикации**, `publication_id` = имя PDF, списки через `;` только в `authors` и `issues.record_ids`.

**Закрытые словари — только три**: `publications.in_scope` (`true`/`false`), `results.result_origin` (`primary`/`cited`), `results.extraction_status` (`extracted`/`present_not_extractable`/`absent`). Рекомендуемые значения: `results.metric_direction` — `higher_is_better`, `lower_is_better`, `target` (иное — предупреждение, неизвестно — пусто). Всё остальное (`retrieval_method`, `task_type`, `metric_group`, `metric_unit`, `aggregation`, `check_name`) — **открытый текст по статье**.

Объём: в эталоне dev 21 статья, **37 616 результатов**, 4 733 конфигурации, 241 датасет, 90 задач, 142 эксперимента, 134 замечания; одна статья (D014) — 28 649 строк, из них `Answer F1` — 12 074. Поэтому API строк `results` — только постранично и с фильтрами (§15.4).

### publications
Как в области 1 (§3.6): `publication_id`, `title`, `year`, `DOI`, `authors` (через `;`), `in_scope`, `exclusion_reason` (обязательна при `false`, **пуста** при `true` — иначе ошибка скрипта). Граница области: система находит сведения во внешней коллекции и использует контекст при ответе, статья описывает конфигурацию и результаты оценки; решение по содержанию, а не по слову «RAG». Источник: [ML].

### configurations
| Поле | Тип | Ист. | Правило |
|---|---|---|---|
| `config_id` | string | [ML]→[BE] | `C01`… |
| `rag_variant` | string | [ML] | название системы или baseline из статьи |
| `retrieval_method` | string | [ML] | как в статье: `BM25`, `dense`, `hybrid` |
| `retriever_model` | string | [ML] | |
| `embedding_model`, `embedding_version` | string | [ML] | версия — только если указана отдельно |
| `chunking_method` | string | [ML] | |
| `chunk_size`, `chunk_overlap` | number ≥ 0 | [ML] | только числом; **диапазон → пусто + `note`** |
| `chunk_unit` | string | [ML] | `tokens`, `characters`… по статье |
| `index_type`, `vector_store` | string | [ML] | одно не выводится из другого |
| `top_k` | int ≥ 0 | [ML] | число извлекаемых элементов; **≠ `metric_k`**; `0` — абляция без контекста |
| `reranker_model` | string | [ML] | |
| `generator_model`, `generator_version` | string | [ML] | **разные генераторы — разные конфигурации** |
| `prompt_strategy`, `context_strategy` | string | [ML] | |
| `note` | string | [ML]\|[USER] | строка таблицы, доп. параметры, неоднозначности, источник |

Разные генераторы, абляции и настройки — отдельные конфигурации (в D001 строка `RA-CoT` с двумя генераторами — `C01` и `C02`).

### datasets
`dataset_id`, `dataset_name`, `dataset_version`, `language`, `data_split`, `scientific_domain`, `note` — все [ML], по статье. Разные версии или разбиения в разных сравнениях не сливаются. Размер выборки не выводится из размера датасета.

### tasks
`task_id`, `task_type` (открытый текст: «open-domain multi-hop question answering»), `note` — [ML]. Связь задачи с набором — только в `results`.

### experiments
| Поле | Тип | Ист. | Правило |
|---|---|---|---|
| `experiment_id` | string | [ML]→[BE] | `E1`… |
| `evaluation_protocol` | string | [ML] | протокол, способ расчёта, ограничения сравнения |
| `baseline_name` | string | [ML] | если указан; несколько — через `;` (как в эталоне) |
| `hardware` | string | [ML] | |
| `latency_ms` | number ≥ 0 | [ML]→[BE] | при известной исходной единице («0.8 s» → 800); исходная запись — в `note`. **Не распространять на все конфигурации**: если задержка зависит от конфигурации — отдельные строки `results` (метрика задержки) или разные эксперименты |
| `tokens_per_query` | number ≥ 0 | [ML] | общий расход не делить на запросы без основания |
| `sample_size` | int ≥ 0 | [ML] | размер оценочной выборки, не всего датасета |
| `note` | string | [ML] | |

### results
Одна строка — одно значение одной метрики в одном эксперименте для одной конфигурации, набора и задачи.

| Поле | Тип | Ист. | Правило |
|---|---|---|---|
| `result_id` | string | [ML]→[BE] | `R001`… |
| `experiment_id`, `config_id`, `dataset_id`, `task_id` | string | [ML] | **по одному id** в каждом; у `primary` — желательно все четыре (иначе предупреждение) |
| `metric_group` | string | [ML] | свободный текст: «answer quality», «retrieval quality», «efficiency» |
| `metric_name` | string | [ML] | как в статье: `EM`, `F1`, `Answer F1`, `NDCG@5`, `Recall@5` |
| `metric_variant` | string | [ML] | определение: token-level F1, macro, judge-based |
| `metric_direction` | string | [ML]\|[BE] | `higher_is_better` / `lower_is_better` / `target`; неизвестно — пусто |
| `metric_k` | int ≥ 0 | [ML] | k метрики @k (`NDCG@5` → 5); **не `top_k`**; `OC@0.8` — не k |
| `metric_value`, `metric_unit` | number, string | [BE] | **= `normalized_value`, `normalized_unit`** |
| `baseline_value` | number | [ML] | значение baseline **только** при сопоставимых задаче, наборе, определении метрики, шкале и протоколе |
| `aggregation` | string | [ML] | открытое поле, **только при явном указании**: `mean`, `mean across queries`, `upper_95CI`… |
| `sample_size` | int | [ML] | если для этого результата указан отдельно |
| `raw_value`, `raw_unit` | string | [ML] | исходная запись дословно, со знаками и обозначениями: `.204`, `+4.4`, `24.8%` |
| `normalized_value`, `normalized_unit` | number, string | [BE] | §6.6: единица — открытый текст (`fraction`, `%`, `percentage_points`, `ms`, `score`…), пересчёт только при установленной шкале |
| `result_origin`, `cited_publication_id` | | [ML] | `cited` — число перенесено из другой работы (ссылка как в статье: `[12]`, «Lewis et al., 2020»; не ключ к `publications`); воспроизведённый авторами baseline — `primary`; у `primary` `cited_publication_id` пуст (иначе ошибка) |
| `source_page` | int ≥ 1 | [ML] | обязателен для `extracted` |
| `source_section`, `source_table`, `source_figure` | string | [ML] | хотя бы один из `source_fragment`/`source_table`/`source_figure` обязателен |
| `source_fragment` | string | [ML] | **дословный** фрагмент страницы (может быть многострочным, как строка таблицы в PDF). Запись вида «таблица 3, строка X» — **не** сюда, а в `source_table`/`note` |
| `extraction_status` | enum | [ML] | `extracted` требует `raw_value`, `source_page` и указатель |
| `duplicate_of` | string | [ML]\|[BE] | повтор того же значения в другом месте; совпадают эксперимент, конфигурация, набор, задача, метрика, вариант, k, единица, агрегация, выборка, происхождение; значение — допускается более грубое округление. Циклы и ссылки на себя запрещены; разные числа — не дубли (обе записи + замечание) |
| `note` | string | [ML]\|[USER] | столбец/кривая, способ нормализации, способ оцифровки графика |
| *служебные*: `review`, `confidence`, `bbox`, `quote_found`, `ocr_used`, `ocr_confidence` | | | отдельно от строки (§15.4) |

**Не создавать декартово произведение** метрик × конфигураций с искусственными `absent`: строка заводится только для значения, которое есть (или явно обозначено) в статье.

### issues
`issue_id`, `record_ids` (через `;`), `check_name` — **открытое имя**, `description` (числа и условия), `source_page`, `source_fragment` — см. §5.2-B.

---

## 3.8. Замечание (Issue)

Единая сущность для замечаний статьи (`Issue` в `store.ts`) и находок коллекции (`Finding` в `checks.ts`). Всё вычисляет [BE], кроме разбора [USER].

| Поле | Тип | Обяз. | Ист. | Описание |
|---|---|---|---|---|
| `issue_id` | id | да | [BE] | детерминированный `I-{check_name}-{result_id}`; в выгрузке — `I1`, `I2`… |
| `check_name` | string | да | [BE]\|[ML] | имя из каталога §5.2 (`jv_consistency`, `contradiction`, …) — колонка `issues.check_name` |
| `record_ids` | string[] | да | [BE]\|[ML] | `result_id` записей (колонка `issues.record_ids`) |
| `description` | string | да | [BE]\|[ML] | колонка `issues.description` (= `text`) |
| `source_page`, `source_fragment` | int, string | да | [BE]\|[ML] | где это видно в статье; дословная цитата |
| `confidence` | number | нет | [ML] | у находок ML |
| `plugin` | enum(`physics`,`claims`,`conditions`,`metrics`,`units`,`plausibility`,`reading`) | да | [BE] | группа для интерфейса (вкладки, фильтр); по `check_name` |
| `scope` | enum(`paper`,`collection`) | да | [BE] | |
| `collection` | enum | да | [BE] | |
| `paper_id` | id | да | [BE] | |
| `severity` | enum(`error`,`warn`,`info`) | да | [BE] | |
| `ui_level` | enum(`conflict`,`doubt`,`warn`) | да | [BE] | красная / янтарная / предупреждение |
| `title`, `text` | string | да | [BE] | по-русски, с числами |
| `row_id`, `row_label`, `param`, `param_label`, `value` | string | нет | [BE] | |
| `fact_ids` | id[] | да | [BE] | может быть пустым |
| `main_fact_id` | id | нет | [BE] | |
| `anchor_ids` | id[] | да | [BE] | |
| `related` | `{entity, id}`[] | да | [BE] | связанные записи ТЗ |
| `numbers` | json | нет | [BE] | `{calc, reported, diff_pct, threshold_pct}` |
| `status` (в API интерфейса — `state`) | enum(`open`,`fixed`,`ok`) | да | [USER]\|[BE] | по умолчанию `open`; **автоматически `fixed`**, когда все значения `record_ids` исправлены или отклонены человеком (§15.12) |
| `state_auto` | bool | да | [BE] | закрыто автоматически — «Открыть снова» не действует (`409 issue_auto_fixed`) |
| `resolved_by`, `resolved_at` | id, datetime | нет | [BE] | |
| `touched_fact_ids` | id[] | да | [BE] | |
| `computed_by` | enum(`backend`,`ml`) | да | [BE] | |
| `rule_version` | string | да | [BE] | |
| `stale` | bool | да | [BE] | правило больше не срабатывает |
| `created_at`, `updated_at` | datetime | да | [BE] | |

Исходная запись при замечании **не удаляется и не меняется** (ТЗ, стр. 6, 10).

---

## 3.9. Журнал действий (Action)

| Поле | Тип | Обяз. | Ист. | Описание |
|---|---|---|---|---|
| `action_id` | id | да | [BE] | |
| `workspace_id`, `user_id` | id | да | [BE] | |
| `who` | string | да | [BE] | имя на момент действия |
| `at` | datetime | да | [BE] | |
| `action` | enum | да | [BE] | по запросу [USER] |
| `label` | string | да | [BE] | «принятие», «разбор замечания» — для «Отменено: …» |
| `paper_id`, `fact_id` | id | нет | [BE] | |
| `from`, `to` | string | нет | [BE] | «18,6 %» → «19,0 %» |
| `count` | int | нет | [BE] | для `acceptAll` |
| `group_id` | id | нет | [BE] | |
| `changes` | json | да | [BE] | до/после, наружу не отдаётся |
| `undoable` | bool | да | [BE] | |
| `undone_at`, `undone_by`, `undo_of` | | нет | [BE] | |

`action`: показываются в журнале — `accept`, `reject`, `restore`, `edit`, `add`, `acceptAll`. Служебные (отменяются, в журнале не показываются): `issue_resolve`, `issue_reopen`, `chat_create`, `chat_rename`, `chat_pin`, `chat_move`, `chat_delete`, `project_create`, `project_update`, `project_pin`, `project_delete`, `paper_upload`, `paper_collection`, `paper_delete`, `view_create`, `view_rename`, `view_delete`, `dict_add`, `dict_remove`, `profile_update`, `undo`.

---

## 3.10. Словарь нормализации

### ParamSynonym
| Поле | Ист. | Описание |
|---|---|---|
| `collection`, `param` | [USER]\|[BE] | |
| `text` | [USER]\|[BE] | «Uхх»; встроенные — [BE] |
| `custom` | [BE] | добавлен пользователем |
| `created_by`, `created_at` | [BE] | |

Уникальность `(workspace, collection, lower(text))`.

### UnitRule
`collection`, `param`, `unit` («мВ»), `factor` (0.001), `is_canonical` — [BE] (встроенные) или [USER] (свои, «появится»); `builtin` [BE]; вычисляемые `count`, `example` [BE].

### DictEntry — материалы, методы, условия
| Поле | Тип | Обяз. | Ист. | Описание |
|---|---|---|---|---|
| `entry_id` | id | да | [BE] | `mapbi`, `bm25`, `am15` |
| `kind` | enum(`material`,`method`,`condition`) | да | [BE] | |
| `collection` | enum | нет | [BE] | `null` у условий |
| `canon` | string | да | [BE] | «MAPbI₃» |
| `name` | string | да | [BE] | расшифровка |
| `material_class` | string | нет | [BE] | |
| `synonyms` | `{text, custom}`[] | да | [BE]\|[USER] | |
| `papers_count` | int | да | [BE] | |

---

## 3.11. Прочее

### SavedView
`id` [BE], `owner_id` [BE], `collection` [USER], `name` [USER], `query` [USER], `created_at` [BE], `deleted_at` [BE].

### Reference — список литературы
| Поле | Тип | Обяз. | Ист. | Описание |
|---|---|---|---|---|
| `paper_id` | id | да | [BE] | |
| `n` | int | да | [ML] | номер [n] |
| `status` | enum(`ok`,`unparsed`) | да | [ML]→[BE] | `unparsed`, если ML не разобрал или нет ни DOI, ни arXiv, ни уверенного названия |
| `key` | string | да | [BE] | `p:{paper_id}` / `x:{external_id}` / `u:{n}` |
| `target_paper_id` | id | нет | [BE] | сопоставление со статьёй коллекции |
| `external_id` | id | нет | [BE] | |
| `authors`, `title`, `venue` | string | да | [ML] | |
| `year` | int | нет | [ML] | |
| `doi`, `arxiv` | string | нет | [ML] | |
| `raw` | string | нет | [ML] | строка, как в PDF |
| `mentions` | string[] | да | [ML] | предложения, где стоит «[n]» |
| `anchor_id` | id | нет | [BE] | |

`ExternalWork` — [BE] (сведение ссылок разных статей по DOI/arXiv/названию; поля — от [ML]).
`PaperEntity` (`paper_id`, `entry_id`, `found_in`) — [BE] по словарю.

### GoldFact — эталонная разметка
`gold_id` [BE], `paper_id`, `row_id`, `param`, `raw_value`, `normalized_value`, `anchor_id`, `source_kind` — [USER] (админ загружает), `dataset` [USER], `created_by` [BE].

### MlJob — см. §7.

---

## 3.11а. Корпус, прогон, профиль извлечения

Полные таблицы полей с источниками — [§13.2–13.3](13-corpus.md): `Corpus`, `CorpusFile`, `BatchJob`, `ExtractionProfile`, `ProfileField`. У `Paper` добавляется `corpus_id` [BE] и `file_id` [BE]. В прототипе этих сущностей нет (в интерфейсе появится).

## 3.12. Таблица «поле интерфейса → поле API»

### Paper (`src/data.ts: PaperSeed`, `src/store.ts: Paper`)
| Интерфейс | API | Ист. |
|---|---|---|
| `id` | `publication_id` (`id`) | [BE] |
| `collection` | `collection` | [BE]\|[USER] |
| `title` | `title` | [ML] |
| `authors` (строка) | `authors_text`; массив — `authors` | [BE] / [ML] |
| `year` | `year` | [ML] |
| `journal` | `journal` | [ML] |
| — | `doi` (появится) | [ML] |
| `pages` | `page_count` | [BE] |
| `page` | `main_page` | [ML] |
| `tags` | `keywords` | [ML] |
| `tableNo`, `caption`, `section` | `main_table.no`, `.caption`, `.section` | [ML] |
| `figure` | `main_figure.caption` | [ML] |
| `rows` | `rows[]` | [ML]→[BE] |
| `paragraphs` (разметка `[[…]]`) | `GET /papers/{id}/layout`: `blocks[].text` + `spans[]` | [ML] |
| `claims` | `claims[]` | [ML] |
| `addedDays` / `addedAt` | `added_at` | [BE] |
| `processing` | `processing.progress` (+ `status`, `stage`) | [BE] |
| `mine` | `mine` | [BE] |
| `seed` | — (только прототип) | |
| `file` | `file.name` | [USER] |
| `dupOf` | `dup_of` | [BE] |
| `topicBy` | `topic_by` | [BE] |

### Row
| Интерфейс | API | Ист. |
|---|---|---|
| `id` | `row_id` | [ML]→[BE] |
| `label` | `label` | [ML] |
| `values` | ячейки `tables[].cells` в `layout`; значения — факты `source_kind = table` | [ML] |
| — | `entity`, `order`, `time_point` | [BE] / [ML] |

### Fact
| Интерфейс | API | Ист. |
|---|---|---|
| `id` | `fact_id` | [BE] |
| `paperId` | `publication_id` | [BE] |
| `row` | `row_id` | [ML]\|[USER] |
| `param` | `param` (+ `target`) | [ML]→[BE] |
| `raw` | `raw_value` (+ `raw_unit`) | [ML]\|[USER] |
| `base` | `normalized_value` (+ `normalized_unit`, `normalized_text`) | [BE] |
| `edited` | `corrected_value` (+ `corrected_raw`) | [USER]→[BE] |
| `valueOf(f)` | `value` | [BE] |
| `display(f)` | `display` | [BE] |
| `status` | `review_status` | [USER] |
| `confidence` | `confidence` | [ML] |
| `source.page` | `source_page` | [ML]\|[USER] |
| `source.kind` | `source_kind` | [ML]\|[BE] |
| `source.ref` | `source_ref` (+ `source_table`, `source_figure`) | [BE] / [ML] |
| `source.where` | `source_where` | [ML]→[BE] |
| `source.anchor` | `anchor_id` | [BE] |
| `quote` | `source_fragment` | [ML]\|[USER] |
| `by` | `added_by` | [BE] |
| — | `extraction_status` [ML], `norm_status` [BE], `result_origin` [ML], `cited_publication_id` [ML], `duplicate_of` [ML]\|[BE], `quote_found` [BE], `source_section` [ML], `aggregation` [ML], `scan_direction` [ML], `model_version` [ML] | |

### Source (`src/data.ts`)
| Интерфейс | API | Ист. |
|---|---|---|
| `page` | `source_page` | [ML]\|[USER] |
| `kind` | `source_kind` | [ML]\|[BE] |
| `ref` | `source_ref`; `source_table` / `source_figure` | [BE] / [ML] |
| `where` | `source_where` | [ML]→[BE] |
| `anchor` | `anchor_id` → `Anchor.rects` | [BE] / [ML]\|[USER] |

### Issue (`src/store.ts`)
| Интерфейс | API | Ист. |
|---|---|---|
| `id` | `issue_id` | [BE] |
| `paperId` | `paper_id` | [BE] |
| `level` (`doubt`/`conflict`/`warn`) | `ui_level` (+ `severity`) | [BE] |
| `plugin` | `plugin` (+ `check_name`) | [BE] |
| `title`, `text` | `title`, `text` | [BE] |
| `anchors` | `anchor_ids` | [BE] |
| `factIds` | `fact_ids` | [BE] |
| `state.resolved[id]` | `status` | [USER] |
| `touchedBy` | `touched_fact_ids` | [BE] |

### Finding (`src/checks.ts`)
| Интерфейс | API | Ист. |
|---|---|---|
| `id` | `issue_id` | [BE] |
| `ruleId` | `check_name` | [BE] |
| `level` (`error`/`warn`/`info`) | `severity` | [BE] |
| `paperId`, `row`, `rowLabel`, `param`, `paramLabel`, `value`, `text` | `paper_id`, `row_id`, `row_label`, `param`, `param_label`, `value`, `text` | [BE] |
| `factId` | `main_fact_id` | [BE] |
| `factIds` | `fact_ids` | [BE] |
| `acceptedFindings` (localStorage) | `status = ok` | [USER] |
| `RuleResult.total / accepted / status` | `GET /checks` → `rules[]` | [BE] |

### Claim (`src/data.ts`)
| Интерфейс | API | Ист. |
|---|---|---|
| `id` | `claim_id` | [BE] |
| `row`, `param`, `kind`, `value` | `row_id`, `param`, `kind`, `value` | [ML] (→[BE] для `param`, `value`) |
| текст `[[@best|…]]` | `text`, `anchor_id` | [ML] |

### Chat, Turn, Answer, Cite, Card (`src/store.ts`, `src/query.ts`)
| Интерфейс | API | Ист. |
|---|---|---|
| `Chat.id, createdAt, updatedAt, kind, paperId` | `id, created_at, updated_at, kind, paper_id` | [BE] |
| `Chat.projectId, pinned` | `project_id, pinned` | [USER] |
| `Chat.title` | `title` | [BE]\|[USER] |
| `Chat.collection` | `collection` | [USER]→[BE] |
| `Turn.q` | `version.question` | [USER] |
| `Turn.id, steps, step, done` | `turn.id`, `version.steps`, `version.step`, `version.status` | [BE] |
| `Turn.shown` | — (поток) | |
| `Turn.answer` | `version.answer` | [BE]\|[ML]→[BE] |
| `TurnMeta.stopped` | `version.status = stopped` | [USER]→[BE] |
| `TurnMeta.rating, reason` | `turn.rating`, `turn.rating_reason` | [USER] |
| `TurnMeta.alt, ver` | `turn.versions[]`, `turn.active_version` | [BE] / [USER] |
| `Answer.*`, `followups()` | `answer.*`, `answer.followups` | [BE]\|[ML] |
| `Cite.*` | `cites[]` | [BE] |
| `Card.*` | `card.*` | [BE]\|[ML]→[BE] |

### Project, Profile, LogEntry, SavedView
| Интерфейс | API | Ист. |
|---|---|---|
| `Project.id, createdAt` | `id, created_at` | [BE] |
| `Project.name, collection, pinned, note` | то же | [USER] |
| `Project.open` | — | [FE] |
| `Profile.name, role, team, color, focus` | `User.*` | [USER] |
| `Profile.email` | `User.email` | [BE] |
| `profileStats()` | `GET /me/stats` | [BE] |
| `LogEntry.*` | `Action.*` | [BE] |
| `SavedView.id` / `name, collection, query` | то же | [BE] / [USER] |

### Словарь (`src/dictionary.ts`)
| Интерфейс | API | Ист. |
|---|---|---|
| `dictionary.params[c][k]` | `ParamSynonym` | [BE]\|[USER] |
| `MaterialEntry` | `DictEntry` (`material` / `method`) | [BE] (+ синонимы [USER]) |
| `ConditionEntry` | `DictEntry` (`condition`) | [BE] (+ синонимы [USER]) |
| `Param.units`, `unitRows()` | `UnitRule`, `GET /dictionary` → `units` | [BE] |

### Структура и связи (`src/structure.ts`, `src/refs.ts`, `src/graph.ts`)
| Интерфейс | API | Ист. |
|---|---|---|
| `Variant {id, label, layers}` | `GET /papers/{id}/structure` → `variants[]` (приборы с одинаковым стеком сгруппированы) | [BE] |
| `Layer.role, raw, nm, where, page` | `layer_function, layer_material, layer_thickness, source_where, source_page` | [ML]→[BE] |
| `Layer.name` | `layer_material_normalized` | [BE] |
| `Layer.fact` | `fact_ids` | [BE] |
| `Cond` (спектр, мощность, температура, площадь, развёртка) | `measurement.spectrum, light_intensity, temperature`, `device.active_area`, `measurement.scan_rate` | [ML]→[BE] |
| `PipeNode` | `structure.pipeline[]` из `dataset_task`, `rag_config`, `metric_result` | [BE] (данные [ML]) |
| `Reference` | `Reference` | [ML]→[BE] |
| `GNode`, `GEdge` | `GET /graph` | [BE] |
| `coverageOf()` | `GET /papers/{id}/coverage` | [BE] |
| `qualityOf()` | `paper.quality` | [BE] |
| `normTrace()` | `GET /facts/{id}/trace` | [BE] |
| `crossOf()` | `fact.cross` | [BE] |
