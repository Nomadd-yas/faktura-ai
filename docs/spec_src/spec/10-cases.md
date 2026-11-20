# 10. Кейсы: сквозные сценарии

[← К оглавлению](../BACKEND_SPEC.md)

Кто действует: **ПОЛЬЗ** — человек, **ФРОНТ**, **БЭК**, **ML**. JSON сокращён («…»); полные форматы — §4, §7.

## 10.1. Кейс 1 (основной): разбор пачки PDF в интерфейсе

Организаторы дали корпус области 1 (кейс 5): `dev/` — 21 PDF с эталоном `dev_gold/`, `test/` — 90 PDF (`P001`…`P090`), по ним оценка. Пользователь загружает PDF в интерфейс, смотрит результат и скачивает 7 CSV; для сдачи то же делает CLI.

| № | Кто | Действие | Эндпоинт → и ← (коротко) |
|---|---|---|---|
| 1 | ПОЛЬЗ → ФРОНТ → БЭК | вход | → `POST /auth/login {"email":"team@faktura.ai","password":"••••"}` ← `200 {"access_token":"eyJ…","user":{…}}` → фронт ведёт на **`/app`** |
| 2 | ФРОНТ → БЭК | главная-композер, недавние разборы | → `GET /runs?limit=6` ← `{"data":[{"id":"run_demo","title":"Демо — dev, 21 статья с эталоном","demo":true,"counts":{"files":21,"done":19,"out":2},"results":659,"issues":53}]}` |
| 3 | ПОЛЬЗ → ФРОНТ → БЭК | перетащил папку `test/` (90 PDF), «Разобрать 90» | → `POST /runs` (multipart: 90 × `file`, `domain=solar`) ← `202 {"id":"run_7","title":"90 статей · 20 ноя","files":[{"id":"P001","name":"P001.pdf","status":"queued"},…]}` → фронт открывает `/run/run_7` |
| 4 | БЭК → ML-2 → ML-1 | конвейер на каждый PDF (по 4 одновременно) | → `POST /ml/parse` ← документ, `needs_ocr`; → `POST /ml/extract` (`file`, `publication_id=P001`, `domain=solar`) ← строки 7 таблиц (§7.0.1) |
| 5 | БЭК | по каждому файлу | валидация → `in_scope=false` → только `publications` (статус `out` + причина) → нормализация (`0.77 fraction` → `77 %`, `1123 mV` → `1.123 V`) → проверки (`jv_consistency`, `contradiction`, `missing_conditions`, …) → сохранение |
| 6 | БЭК → ФРОНТ | ветка разбора: прогресс | SSE `GET /runs/run_7/events`: `event: file {"id":"P003","status":"running","progress":40,"stage":"Извлечение"}`, `{"id":"P017","status":"out","reason":"Только численное моделирование…"}`, `{"id":"P003","status":"done","results":31,"issues":2}` … `event: done {"counts":{"files":90,"done":84,"out":6},"results":2810,"issues":230}` |
| 7 | ФРОНТ → БЭК | сводка | → `GET /runs/run_7/summary` ← `{"papers":90,"in_scope":84,"results":2810,"issues":230,"coverage":[{"metric":"PCE","papers":83,"of":84},…]}` |
| 8 | ПОЛЬЗ → ФРОНТ → БЭК | таблица результатов, фильтр «PCE > 20 перовскит» | → `GET /results?run=run_7&q=PCE > 20 перовскит&sort=value&dir=desc&limit=100` ← `{"data":[{"row":{"publication_id":"P042","result_id":"R004","metric_name":"PCE","raw_value":"21.3","raw_unit":"%","normalized_value":"21.3","normalized_unit":"%","aggregation":"champion","source_page":"5","source_table":"Table 1",…},"device_label":"Cs0.05","review":{"status":"new"}},…],"total":37}` |
| 9 | ПОЛЬЗ → ФРОНТ → БЭК | клик по строке → статья на значении | `/paper/solar/P042?run=run_7&r=R004` → `GET /papers/solar/P042` ← 7 таблиц статьи + `reviews` + `meta`; `GET /papers/solar/P042/pdf` (Range) → pdf.js открывает стр. 5 и подсвечивает `source_fragment` (или подпись «Table 1») |
| 10 | ПОЛЬЗ → ФРОНТ → БЭК | замечания разбора | → `GET /issues?run=run_7&check_name=contradiction` ← `{"data":[{"row":{"publication_id":"P042","issue_id":"I1","record_ids":"R004;R011","check_name":"contradiction","description":"В аннотации PCE 21.3 %, в Table 1 — 20.9 %","source_page":"1","source_fragment":"efficiency of 21.3%"},"meta":{"computed_by":"ml"}}]}` → клик → `/paper/solar/P042?run=run_7&i=I1` |
| 11 | ПОЛЬЗ → ФРОНТ → БЭК | исправить значение / отклонить | → `PATCH /runs/run_7/papers/solar/P042/results/R011/review {"status":"accepted","corrected_value":21.3}` ← `{"review":{…},"action_id":"act_81","issues_changed":{"closed":["I1"]}}`; «Отменить» → `POST /actions/act_81/undo` |
| 12 | ПОЛЬЗ → ФРОНТ → БЭК | массово принять надёжные | → `POST /runs/{id}/reviews/batch {"filter":{"run":"run_7","review":"new","has_issues":false},"status":"accepted"}` ← `{"count":2410,"action_id":"act_82"}` |
| 12a | ПОЛЬЗ → ФРОНТ → БЭК | вопрос в разборе «КПД выше 20 % у перовскитов» | → `POST /runs/run_7/messages {"text":"КПД выше 20 % у перовскитов"}` ← SSE: `text` с числами-сносками, `cards: [{"kind":"results","query":{"metric":"PCE","min":20,"q":"перовскит"}}]`, `cites: [{"domain":"solar","pid":"P042","result_id":"R004","page":5}]`, `suggestions` |
| 13 | ПОЛЬЗ → ФРОНТ → БЭК | догрузить 2 PDF в тот же разбор | → `POST /runs/run_7/files` (multipart) ← `202 {"added":[{"id":"P091",…}]}` — прогресс снова в ветке |
| 14 | ПОЛЬЗ → ФРОНТ → БЭК | «Скачать набор (7 CSV)» | → `GET /runs/run_7/submission.zip?check=true` ← ZIP: `publications.csv … issues.csv` + `check_report.txt`, `X-Check-Status: ok`. Правки учтены: отклонённые строки не выгружены и убраны из `record_ids`/`duplicate_of`; у исправленной `R011` `normalized_value`/`metric_value` = 21.3, в `note` — «исправлено вручную; модель: 20.9» |

**То же без интерфейса** (сдача, проверка на скрытых примерах):
```
$ faktura extract --domain solar --in ./test --out ./out --check docs/case5/tools/check_submission.py
[  1/90] done    P001.pdf   results=31 issues=2
…
[ 90/90] done    P090.pdf   results=27 issues=1
Готово: 90 файлов, 84 в области, 6 вне области. check_submission: ошибок 0, предупреждений 12. Набор: ./out (exit 0)
```
Результат — ровно 7 CSV в `out/` (+ служебное `out/_meta/`), §13.6. Качество на dev: `faktura-eval --gold docs/case5/dev_gold --pred ./out_dev` (скрипт ML-2).

**Повторный прогон.** Новая версия модели → `POST /runs/run_7/restart {"keep_review":true}`: правки человека переносятся. Изменили порог `jv_consistency` → только перепроверка, без ML (§13.5).

---

## 10.2. Кейс 2 (дополнительный): одна статья о перовските, разбор и чат

> **[В ФОНЕ]** — сценарий прототипа с чатом и проектами. В новом интерфейсе одна статья разбирается как разбор из одного файла (кейс 1, шаги 3–11); шаг 7 (вопрос в чате) и проекты выключены `FEATURES`, бэку их делать не нужно до решения команды. Сохранено как описание на будущее.

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

## 10.3. Кейс 3: разбор корпуса RAG (область 2) со сканами и вопросом

Архив области 2: `dev/` — 21 PDF (D001–D021, из них сканы D002, D013, D021), `test/` — 90 PDF (14 сканов). Коды совпадают с областью 1 — статья везде пара (`rag`, `D001`).

| № | Кто | Действие | Эндпоинт и данные (коротко) |
|---|---|---|---|
| 1 | ПОЛЬЗ → ФРОНТ → БЭК | на `/app` выбрал область «RAG», перетащил `dev/`, «Разобрать 21» | → `POST /runs` (`domain=rag`, 21 × `file`) ← `202 {"id":"run_rag1","domain":"rag","files":[{"id":"D001","status":"queued"},…]}` |
| 2 | БЭК | проверка текстового слоя | pypdf и PyMuPDF дают пустой текст на всех страницах D002, D013, D021 → эти файлы идут в OCR |
| 3 | БЭК → ML-2 | разбор и OCR | → `POST /ml/parse` (`ocr=auto`) ← «документ» + у сканов `pages[].ocr_used=true`, `words` с bbox и уверенностью, `ocr.mean_confidence=0.91` |
| 4 | БЭК → ML-1 | извлечение по 7 таблицам области 2 | → `POST /ml/extract` (`domain=rag`) ← NDJSON: `configurations` (разные генераторы — разные `config_id`), `datasets`, `tasks`, `experiments`, `results` (у D014 — около 28 тыс. строк), `issues` |
| 5 | БЭК | нормализация и проверки | `.204` → `0.204 fraction` без пересчёта шкалы; `24.8%` остаётся `24.8 %`; `NDCG@5` → `metric_k=5`; правила `metric_scale`, `metric_k`, `normalization`, `protocol_mismatch`, `contradiction` |
| 6 | БЭК → ФРОНТ | ветка разбора | SSE `run_rag1/events`: `{"id":"D013","status":"running","stage":"Распознавание скана"}` … `{"id":"D013","status":"done","scan":true,"ocr_confidence":0.88,"results":1240}` |
| 7 | ФРОНТ → БЭК | таблица большой статьи | → `GET /results/summary?domain=rag&pid=D014` (сводка по метрикам и конфигурациям) → `GET /results?domain=rag&pid=D014&metric=Answer F1&config_id=C03&dataset_id=DS2&limit=100` |
| 8 | ПОЛЬЗ → ФРОНТ → БЭК | открыл значение со скана | `/paper/rag/D013?r=R120` → `GET /papers/rag/D013` (+ `results=summary`), `GET /papers/rag/D013/pdf`, `GET /papers/rag/D013/ocr` → подсветка по `words`; пометки «скан, распознано OCR», при `ocr_confidence < 0,8` — «сверьте цифры» |
| 9 | ПОЛЬЗ → ФРОНТ → БЭК | вопрос в разборе | → `POST /runs/run_rag1/messages {"text":"сравни EM на HotpotQA у D001 и D003"}` ← SSE: `text` + `cards: [{"kind":"compare","domain":"rag","pids":["D001","D003"],"metric":"EM"}]` + `cites: [{"domain":"rag","pid":"D001","result_id":"R001","page":6}]` + `suggestions`; сопоставимость — `comparable: {"ok": false, "reasons": ["разные определения EM"]}` |
| 10 | ПОЛЬЗ → ФРОНТ → БЭК | связи | → `GET /graph?run=run_rag1&domain=rag&kinds=dataset,generator` ← статьи, общие `HotpotQA`, `LLaMA3-8B-Instruct`… |
| 11 | ПОЛЬЗ → ФРОНТ → БЭК | скачать набор | → `GET /runs/run_rag1/submission.zip?check=true` ← 7 CSV области 2 + `check_report.json`: бэк запускает `docs/case5_area2/tools/check_submission.py … --ocr --json-report` → код `2` (часть цитат со сканов не подтверждена OCR буквально) → `X-Check-Status: warnings` |

Без интерфейса: `faktura extract --domain rag --in ./test --out ./out --check` → `out/rag/` (7 CSV) + `out/_meta/check_report_rag.json`.
