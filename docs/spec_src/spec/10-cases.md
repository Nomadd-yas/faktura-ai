# 10. Кейсы: сквозные сценарии

[← К оглавлению](../BACKEND_SPEC.md)

Кто действует: **ПОЛЬЗ** — человек, **ФРОНТ**, **БЭК**, **ML**. JSON сокращён («…»); полные форматы — §4, §7.

## 10.1. Кейс 1 (основной): пакетный прогон корпуса солнечных элементов

Организаторы дали корпус области 1 (кейс 5): `dev/` — 21 PDF с эталоном `dev_gold/`, `test/` — 90 PDF (`P001`…`P090`), по ним оценка. Сначала прогоняем dev и меряем качество, затем test и сдаём 7 CSV.

| № | Кто | Действие | Эндпоинт → и ← (коротко) |
|---|---|---|---|
| 1 | ПОЛЬЗ → ФРОНТ → БЭК | вход | → `POST /auth/login {"email":"team@faktura.ai","password":"••••"}` ← `200 {"access_token":"eyJ…","user":{…}}` |
| 2 | ФРОНТ → БЭК | первый экран «Корпус» | → `GET /corpus/overview` ← `{"corpora":[],"empty":true}` → пустое состояние «Загрузите папку или ZIP с PDF» |
| 3 | ПОЛЬЗ → ФРОНТ → БЭК | создать корпус | → `POST /corpora {"name":"Кейс 5 — test","collection":"solar","profile_id":"prf_solar_tz_v1"}` ← `201 {"corpus_id":"crp_test",…}` |
| 4 | ПОЛЬЗ → ФРОНТ → БЭК | загрузить ZIP (или папку пачками) | → `POST /uploads` + `PATCH /uploads/{id}` (resumable), → `POST /corpora/crp_test/files {"upload_id":"upl_1"}` ← `202 {"accepted":90,"duplicates":0,"rejected":[{"name":"readme.txt","code":"unsupported_file"}]}` |
| 5 | БЭК | дедупликация | SHA-256: дублей нет (иначе — `skipped_duplicate`) |
| 6 | ПОЛЬЗ → ФРОНТ → БЭК | «Прогнать всё» | → `POST /corpora/crp_test/runs {"kind":"new_only","concurrency":4}` ← `202 {"run_id":"run_12","status":"queued","counts":{"total":90}}` |
| 7 | БЭК → ML-2 | разбор каждого PDF (по 4 одновременно) | → `POST /ml/parse` (multipart `file`, `domain_hint=solar`) ← `{"document":{pages, blocks, tables…},"topic":{"collection":"solar","confidence":0.93},"needs_ocr":false,"references":[…]}` |
| 8 | БЭК → ML-1 | извлечение по списку полей профиля | → `POST /ml/extract` (multipart `file`, `domain=solar`, `fields=[{"key":"pce"},{"key":"voc"},…]`, `document`) ← `{"publication":{…},"devices":[{"ref":"d1","label":"A"},…],"results":[{"field":"pce","device_ref":"d2","raw_value":"18,6","raw_unit":"%","page":4,"source_type":"table","source_ref":"Табл. 2","bbox":[…],"confidence":0.93,"result_origin":"primary"},…],"model_version":"extract-0.3"}` (предварительный контракт, §7.0) |
| 9 | БЭК | по каждому файлу | валидация → сопоставление полей → нормализация (`faktura_norm` от ML-2) (FF `0.77 fraction` → `77 %`, `712 mV` → `0.712 V`) → факты и таблицы ТЗ → проверки (`jv_consistency`, `contradiction`, `missing_conditions`, …) |
| 10 | БЭК → ФРОНТ | прогресс по файлам | SSE `run.file {"file_id":"cf_061","status":"done","facts":16,"issues":1}`, `run.progress {"progress":41,"counts":{"done":60,"failed":1,…}}`; статьи вне области — `done` с `in_scope=false` |
| 11 | БЭК → ФРОНТ | прогон закончен | SSE `run.done {"run_id":"run_12","status":"done_with_errors","counts":{"done":90,"failed":0}}` |
| 12 | ФРОНТ → БЭК | сводка | → `GET /corpora/crp_test/summary` ← `{"facts":{"total":2310},"issues":{"open":211,"by_rule":[{"check_name":"physics","open":23},{"check_name":"contradiction","open":9}]},"coverage":[{"field":"pce","pct":96.6},{"field":"scan_direction","pct":26.0}],"not_found":[{"field":"certified"}]}` |
| 13 | ПОЛЬЗ → ФРОНТ → БЭК | таблица фактов, фильтр «КПД > 20 %, перовскиты» | → `GET /facts/table?corpus_id=crp_test&q=КПД > 20 % перовскиты&sort=value&dir=desc&limit=100&facets=true` ← `{"filters":[{"label":"КПД > 20 %"},{"label":"перовскит"}],"data":[{"fact_id":"fct_stab_c_h0_pce","display":"21,3 %","raw_value":"21,3 %","paper_title":"Стабильность перовскитных элементов при 85 °C","source_page":7,"source_ref":"Табл. 2","review_status":"new","issues":[]},…],"total":37}` |
| 14 | ПОЛЬЗ → ФРОНТ → БЭК | открыть источник | → `GET /papers/pap_stab/anchors?page=7`, `GET /papers/pap_stab/pages/7` ← `rects` ячейки → подсветка на странице 7 |
| 15 | ПОЛЬЗ → ФРОНТ → БЭК | замечания | → `GET /issues?corpus_id=crp_test&severity=error` ← «Текст противоречит таблице» ×9, «КПД выше подтверждённого рекорда» ×2; по каждому — «Решено» / «Не ошибка» `POST /issues/resolve`, или исправить значение `PATCH /facts/{id} {"raw":"18,7"}`, или отклонить `POST /facts/{id}/reject` |
| 16 | ПОЛЬЗ → ФРОНТ → БЭК | массово принять надёжные | → `POST /facts/bulk {"action":"accept","filter":{"corpus_id":"crp_test","reliable":true}}` ← `{"count":1840,"action_id":"act_…"}` |
| 17 | ПОЛЬЗ → ФРОНТ → БЭК | экспорт для сдачи | → `POST /exports {"scope":{"corpus_id":"crp_test"},"format":"submission","validate":true}` ← ZIP: `publications.csv`, `materials.csv`, `devices.csv`, `layers.csv`, `measurements.csv`, `results.csv`, `issues.csv` + `check_report.txt` (`X-Check-Status: ok`); паспорт — `GET /passport` |

**То же без интерфейса** (проверка на скрытых примерах):
```
$ faktura extract --domain solar --in ./test --out ./out --check docs/case5/tools/check_submission.py
[  1/90] done    P001.pdf   results=31 issues=2
…
[ 90/90] done    P090.pdf   results=27 issues=1
Готово: 90 файлов, 84 в области, 6 вне области. check_submission: ошибок 0, предупреждений 12. Набор: ./out (exit 0)
```
Результат — папка §13.6: ровно 7 CSV шаблона в `out/` (+ служебное `out/_meta/`). Проверка: `python docs/case5/tools/check_submission.py out test` → `ошибок: 0`. Качество на dev: `faktura-eval --gold docs/case5/dev_gold --pred ./out_dev` (скрипт ML-2).

**Повторный прогон.** Добавили в профиль поле `scan_direction` (`PATCH /profiles/prf_solar_tz_v1` ← `{"version":4,"rerun_hint":"full"}`) → `POST /corpora/crp_solar/runs {"kind":"full","keep_review":true}`: ML разбирает заново, принятые и исправленные человеком значения переносятся. Изменили порог правила `jv_consistency` → `{"kind":"recheck"}` — без ML, за секунды.

---

## 10.2. Кейс 2 (дополнительный): одна статья о перовските, разбор и чат

Статья «Перовскитный солнечный элемент с КПД 18,6 %: стабильность и интерфейсы» (Иванов А. В., Котова Е. С. и др., 2024). В таблице 2 три образца A, B, C; в тексте сказано, что лучший — B, хотя по таблице КПД выше у A.

### Шаг 1. Вход
| Кто | Что |
|---|---|
| ПОЛЬЗ | вводит почту и пароль на `/login` |
| ФРОНТ → БЭК | `POST /api/v1/auth/login` `{"email":"team@faktura.ai","password":"••••••••","remember":true}` |
| БЭК | проверяет хеш, выдаёт пару токенов (refresh — в cookie) |
| БЭК → ФРОНТ | `200 {"access_token":"eyJ…","expires_in":900,"user":{"id":"usr_01","name":"Исследователь",…}}` |
| ФРОНТ | хранит access в памяти, открывает `/corpus` и запрашивает `GET /corpus/overview`; фоном — `GET /collections`, `GET /projects`, `GET /chats` (боковая панель), поток `GET /events` |

### Шаг 2. Загрузка PDF
| Кто | Что |
|---|---|
| ПОЛЬЗ | в проекте «Перовскиты» нажимает «Загрузить PDF», выбирает `mapbi.pdf` |
| ФРОНТ → БЭК | `POST /api/v1/papers` (multipart: `file=@mapbi.pdf`, `project_id=pr_perov`), прогресс передачи — XHR |
| БЭК | PDF ли, размер, SHA-256 (дубля нет), сохраняет файл; коллекция `solar` (`topic_by: project`); создаёт статью и чат; ставит задачу ML |
| БЭК → ML | `POST {ML}/v1/jobs` `{"job_id":"job_7Hk2","paper_id":"pap_mapbi","file_url":"…","collection_hint":{"collection":"solar","by":"project"},"callback_url":"…/internal/ml/jobs/job_7Hk2"}` |
| БЭК → ФРОНТ | `202 {"data":[{"paper":{"id":"pap_mapbi","title":"mapbi","processing":{"status":"queued","stage":"Текст","progress":0}},"chat":{"id":"ch_mapbi"}}]}` |
| ФРОНТ | открывает `/c/ch_mapbi`, показывает этапы разбора и очередь загрузки |

### Шаг 3. ML разбирает

> Ниже показан асинхронный вариант с вебхуками (§7.3). В основном режиме бэк вызывает синхронно `POST /ml/parse` (ML-2), затем `POST /ml/extract` (ML-1), как в кейсе 1, шаги 7–8; содержимое ответов то же.
| Кто | Что |
|---|---|
| ML → БЭК | `POST /internal/ml/jobs/job_7Hk2/progress` `{"stage":"Таблицы","progress":45}` |
| БЭК → ФРОНТ | SSE `paper.progress` `{"paper_id":"pap_mapbi","stage":"Таблицы","progress":36}` |
| ML → БЭК | `POST …/partial` с A1 (`topic`) и A3 (`publication`: название, авторы, год 2024) → БЭК обновляет название, SSE `paper.updated` |
| ML → БЭК | `POST …/result` — полный `ExtractionResult`: A2 разметка (стр. 4: абзацы, табл. 2, рис. 3), A4 строки `r1..r3` = A, B, C, A5 — 12 ячеек таблицы, 3 значения из текста (`*`: условия, ширина зоны 1,59 эВ, толщина 450 нм), 2 значения с рис. 3 (Voc, Jsc образца A), A6 материал MAPbI3, условия, слои, A7 утверждение «Наилучшие характеристики демонстрирует образец B» (`kind: best`, `row_ref: r2`), A8 10 ссылок |

Фрагмент A5:
```json
{ "ref": "v4", "row_ref": "r2", "param_raw": "КПД, %", "raw_value": "18,6", "raw_unit": "%", "result_origin": "primary", "confidence": 0.96,
  "provenance": { "source_kind": "table", "source_table": "Табл. 2", "where": "строка B", "place": { "page": 4, "rects": [ { "x": 0.71, "y": 0.52, "w": 0.06, "h": 0.018 } ] } } }
```

### Шаг 4. Бэк нормализует и проверяет
| Кто | Что |
|---|---|
| БЭК | этап «Нормализация»: «КПД, %» → `pce` по словарю; «18,6» + «%» → 18,6 %; FF «0,77» (доля, шкала известна) → 77,0 %, `raw_value` сохраняется; условия «AM1.5G, 100 мВт/см², 25 °C» → канон словаря + поля `measurement`; MAPbI3 → «MAPbI₃»; собирает `material`, `measurement`, `device` ×3, `layer` ×6, `result` ×12 |
| БЭК | этап «Проверка»: `jv_consistency` у B: 1,10 × 23,4 × 0,79 = 20,3 % против 18,6 % → 9,3 % > 3 % → замечание `warn`; `contradiction`: по таблице лучший A (19,0 %), а текст называет B → замечание `error` «Текст противоречит таблице»; `voc_limit` 1,10 < 1,59 — ок; `unit_conversion` для FF — справочно |
| БЭК → ФРОНТ | SSE `paper.done` `{"paper_id":"pap_mapbi","chat_id":"ch_mapbi","title":"Перовскитный солнечный элемент с КПД 18,6 %…","facts":17,"issues":2}` → уведомление «…разобрана: 17 фактов, замечаний — 2» |

### Шаг 5. Фронт показывает факты и замечание
| Кто | Что |
|---|---|
| ФРОНТ → БЭК | `GET /papers/pap_mapbi`, `GET /papers/pap_mapbi/facts`, `GET /papers/pap_mapbi/issues`, `GET /papers/pap_mapbi/layout?pages=4`, `GET /papers/pap_mapbi/anchors?page=4`, `GET /papers/pap_mapbi/pages/4` |
| БЭК → ФРОНТ | замечание: |

```json
{ "issue_id": "pap_mapbi:contradiction:clm_best", "check_name": "contradiction", "severity": "error", "ui_level": "conflict", "title": "Текст противоречит таблице",
  "text": "В тексте лучший — образец B, но по таблице КПД выше у образца A: 19,0 % против 18,6 %.",
  "fact_ids": ["fct_mapbi_c_B_pce", "fct_mapbi_c_A_pce"], "main_fact_id": "fct_mapbi_c_B_pce", "anchor_ids": ["anc_clm_best", "anc_c_A_pce", "anc_c_B_pce"], "status": "open" }
```
| ФРОНТ | шапка «Замечаний: 2», красная точка; во вкладке «Замечания» — оба; при наведении — подсветка фразы в абзаце 2 и ячеек A и B в табл. 2 по `rects` |

### Шаг 6. Пользователь принимает или исправляет факт
Вариант А — значение в таблице верное, ошибается текст:
| Кто | Что |
|---|---|
| ПОЛЬЗ | на замечании нажимает «Не ошибка в данных → Решено» |
| ФРОНТ → БЭК | `POST /issues/resolve` `{"issue_ids":["pap_mapbi:contradiction:clm_best"],"how":"fixed","accept_fact_id":"fct_mapbi_c_B_pce"}` |
| БЭК → ФРОНТ | `{"resolved":["…"],"accepted_fact_id":"fct_mapbi_c_B_pce","action_id":"act_81","message":"Замечание решено · КПД принят"}` |

Вариант Б — ошибка распознавания, человек исправляет КПД:
| Кто | Что |
|---|---|
| ПОЛЬЗ | в карточке факта вводит «18,7» |
| ФРОНТ → БЭК | `PATCH /facts/fct_mapbi_c_B_pce` `{"raw":"18,7"}` |
| БЭК | «18,7» без единицы → в канонической (%), `corrected_value = 18.7`, `review_status = accepted`, пересчёт проверок, журнал `edit` «18,6 %» → «18,7 %» |
| БЭК → ФРОНТ | `{"fact":{"value":18.7,"display":"18,7 %","review_status":"accepted","extraction_status":"corrected"},"action_id":"act_82","issues_changed":{"opened":[],"closed":[]}}` |
| ПОЛЬЗ / ФРОНТ | «Отменить» в уведомлении → `POST /actions/act_82/undo` |

### Шаг 7. Вопрос в чате
| Кто | Что |
|---|---|
| ПОЛЬЗ | в боковой панели «Новый вопрос» (`/app/ask`): «Сравни КПД перовскитов после 2022» |
| ФРОНТ → БЭК | `POST /chats/ask` (`Accept: text/event-stream`) `{"question":"Сравни КПД перовскитов после 2022","project_id":null,"collection":"solar"}` |
| БЭК | режим `rules`: фильтры `topic:перовскит`, `year > 2022`; намерение — сравнение, метрика `pce`; строки: перовскитные статьи 2023–2024; две-три лучшие строки из разных статей; вывод по разнице |
| БЭК → ФРОНТ | поток: |

```
event: chat
data: {"id":"ch_q1","kind":"ask","title":"Сравни КПД перовскитов после 2022"}
event: step
data: {"step":1}
event: intro
data: {"text":"Тандем лучше: КПД 28,6 % против 21,3 % у перовскита."}
event: cite
data: {"n":1,"fact_id":"fct_tandem_c_T2_pce","paper_id":"pap_tandem","title":"Тандем перовскит/кремний с КПД 28,6 %","where":"стр. 6 · Табл. 3, строка T2","quote":"Параметры тандемных элементов — T2: 28,6 %"}
event: item
data: {"index":0,"head":"Тандем перовскит/кремний с КПД 28,6 % (2024)","text":"образец T2: КПД 28,6 % (Voc 1,95 В, Jsc 19,8 мА/см², FF 74,0 %)","cite":1}
event: card
data: {"kind":"compare","collection":"solar","ids":["pap_tandem","pap_stab"],"rows":["T2","h0"],"metric":"pce"}
event: done
data: {"status":"done","explore":"Сравни КПД перовскитов после 2022","followups":["Связи этих статей","Где данные не сходятся?","Какие приборы лучшие по КПД?"]}
```
| ФРОНТ | пункты со сносками `[1]`; нажатие на сноску → `/c/ch_tandem?view=facts&fact=fct_tandem_c_T2_pce`, подсветка ячейки по `anchor_id` |

### Шаг 8. Сравнение
| Кто | Что |
|---|---|
| ПОЛЬЗ | в карточке ответа «Открыть ›» |
| ФРОНТ → БЭК | `GET /data/compare?ids=pap_tandem,pap_stab` |
| БЭК → ФРОНТ | колонки, лучший образец, параметры, условия, `comparable: {"ok": false, "reasons": ["тандем — вне границ корпуса ТЗ (не однопереходный)"]}`, выводы |
| ФРОНТ | показывает колонки и предупреждение о сравнимости |

### Шаг 9. Экспорт
| Кто | Что |
|---|---|
| ПОЛЬЗ | «Экспорт»: коллекция «Солнечные элементы», только принятые, нормализованные, с источником, JSON по ТЗ |
| ФРОНТ → БЭК | `POST /exports/preview` (предпросмотр), затем `POST /exports` `{"scope":{"collection":"solar"},"only_accepted":true,"normalized":true,"with_source":true,"format":"json","layout":"relational"}` |
| БЭК → ФРОНТ | `200`, файл `faktura-facts.json`: `{"format":"faktura-dataset","publications":[…],"materials":[…],"measurements":[…],"devices":[…],"layers":[…],"results":[…],"issues":[…]}` (полный пример — §9.1) |

---

## 10.3. Кейс 3: статья о RAG (коротко)

Статья «Гибридный поиск BM25 + плотные эмбеддинги для RAG» (Соколов М. И., Дьячкова А. Р., 2024). В табл. 2 — BM25, Dense, Hybrid; в аннотации «Recall@5 гибридного поиска достигает 0,76», а в таблице 0,74.

| № | Кто | Действие | Эндпоинт и данные |
|---|---|---|---|
| 1 | ПОЛЬЗ → ФРОНТ → БЭК | загрузка вне проекта, под полем выбрано «Солнечные элементы» | `POST /papers` (`file=@hybrid_search.pdf`, `collection=solar`) → `202`, `topic_by: "guess"` |
| 2 | ML → БЭК | тема | A1 `{"collection":"rag","confidence":0.95,"topics":["hybrid","bm25"]}` → БЭК: `collection = rag`, `topic_by = text`, SSE `paper.updated` |
| 3 | ML → БЭК | извлечение | A4 строки BM25 / Dense / Hybrid; A5 `chunk` «512 ток.», `R@5` «0,74» (`metric_k: 5`), `R@10`, `MRR`; текст «BM25 + e5-large, слияние RRF» → `retriever`; A6 `datasets_tasks` `{"dataset_name":null,"dataset_description":"корпус из 12 тыс. документов"}`; A7 `{"kind":"equals","row_ref":"hybrid","param_raw":"Recall@5","value_raw":"0,76","where":"abstract"}` |
| 4 | БЭК | нормализация и проверки | Recall — доли (0,74); `retriever` → `retrieval_method = hybrid`, `retriever_model = "BM25 + e5-large"`, `fusion_method = "RRF"`; `recall_k` ок (0,74 ≤ 0,85); `contradiction`: 0,76 ≠ 0,74 → `error` «Аннотация расходится с таблицей»; язык, протокол, генератор — `null` (не указаны) |
| 5 | ФРОНТ | разбор | `GET /papers/pap_hybrid/structure` → конвейер: датасет «корпус из 12 тыс. документов», чанки 512 ток., ретривер, переранжирование «не указано», генератор «не указано», метрики |
| 6 | ПОЛЬЗ | «Не ошибка» по замечанию (в аннотации округлено по другому прогону) | `POST /issues/resolve {"issue_ids":["pap_hybrid:contradiction:clm_eq"],"how":"ok"}` |
| 7 | ПОЛЬЗ | вопрос «Какие конфигурации лучшие по Recall@5?» | `POST /chats/ask {"collection":"rag",…}` → поток: «Лучшее — Recall@5 0,77: конфигурация «С reranker»…», сноски на факты `r5` |
| 8 | ПОЛЬЗ | экспорт RAG | `POST /exports {"scope":{"collection":"rag"},"format":"xlsx","layout":"relational"}` → листы `publications`, `datasets_tasks`, `experiments`, `rag_configs`, `metric_results`, `issues` |
