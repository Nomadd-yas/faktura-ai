# Волна 2. Архитектура и план бэкенда (отдел 2), ревизия 2

Требования — `docs/wave2/01_spec_analysis.md` (номера W2-xxx, К2-x, В2-x). Контракт HTTP, CLI, ML-1 и OCR — `docs/wave2/api_contract.md`. План волны 1 (`docs/wave1/02_plan.md`) действует в части, которую здесь не меняем: стек, слои, порты, правило владения путями.

Роль — только BE. Извлечение (ML-1), разбор PDF, нормализацию словарём и OCR (ML-2) делают другие люди. Бэкенд работает через адаптеры: мок ML-1 на `dev_gold` обеих областей и мок OCR.

---

## 0. Что изменено в ревизии 2 (ответы на замечания критика)

| Замечание | Решение | Где |
|---|---|---|
| Shared не описан, нет шага | Шаг 0 стал модулем `shared` с полным ТЗ, интерфейсами и критериями приёмки. Все остальные модули зависят от него через `depends_on` | §3, §8.0 |
| `ROUTER_MODULES` | Меняет `shared` на шаге 0. `create_app` уже пропускает отсутствующий пакет с предупреждением (код волны 1, тест в `tests/api_base`). Старые `tests/api_corpus`, `tests/api_papers`, `tests/e2e` до L3 исключаются через `collect_ignore` | §3.2, §8.0 п.8 |
| `ui_page_params` и заглушки `/graph`, `/search` | `api/base` и `tests/api_base` целиком перешли в shared. `ui_page_params`, удаление заглушек и `Expose-Headers` делает шаг 0. У `api_data` больше нет скрытой зависимости от `api_runs` | §8.0 п.8 |
| `api_review` зависит от `export` | `api_review` объединён с `api_data`: модулей не больше 8, а пакетная правка использует тот же разбор параметров `GET /results`. Критерии переписаны без `export`: проверка через storage и HTTP. Выгрузку с правками проверяют `export` и `api_runs` | §8.7 |
| Фильтр пересчёта для `protocol_mismatch` | Область пересчёта (scope) rag — (`experiment_id`, `metric_name`). Строки baseline (другая конфигурация, тот же эксперимент и метрика) входят в неё. Фильтр одинаков в `rag`, `storage` и `api_data` | §8.1 п.4, §8.7 |
| `recheck` не знает про отклонённые | Сигнатура: `recheck_group(rb, *, values, rejected)`. Отклонённые записи исключаются из всех пересчитываемых проверок | §8.1 п.4 |
| Вход `recheck_group` и замер времени | На вход подаётся подмножество строк одной или нескольких областей пересчёта, а не вся статья. Время меряется на самой большой области D014: ≤ 200 мс. Вся D014 — ≤ 10 с, только как проверка линейности | §8.1 п.4 |
| W2-017 понижен до could | Возвращено как must: `EvalAdapter` rag вызывает `faktura-eval --domain rag`. Нет инструмента → `EvalToolError`. Критерии в `export`, команда `faktura eval` в `pipeline` | §8.4 п.8, §8.5 п.4 |
| Нормализация строк `absent`/`present_not_extractable` | Такие строки не нормализуются, ни одна ячейка не меняется. Список нечисел расширен: `-`, `–`, `—`, `−`, U+FFFD, `N/A` и др. Критерий: на `dev_gold` меняется 0 ячеек results | §8.1 п.1 |
| Двойное `contradiction` | Все замечания добавляются через `RowBundle.add_issue`. Он сливает одинаковые (`check_name`, множество `record_ids`) и поглощает подмножества между замечаниями сервиса. Санитайзер вызывается до проверок (стадия ingest), поэтому групповое замечание поглощает парное | §8.0 п.4, §8.1 п.2, §8.4 п.2 |
| Где `quote_found` замечаний | Поле `IssueMeta.quote_found`. Перенумерация `issue_id` выполняется только через `RowBundle.renumber_issues`, который переносит ключи `issue_meta` | §8.0 п.4 |
| Скан и режим `auto` | Набор страниц один: `needs_ocr` (оба движка пусты **или** эвристика §11.2). Именно он распознаётся в `auto`, и по нему ставится `scan`. В `pipeline` стадия OCR запускается при `force` или при непустом `needs_ocr`, а не по `doc.scan` | §8.2 п.3, §8.5 п.2 |
| Ссылка §4.11 | Грамматика языка запросов перенесена в бриф `api_data`, в spec_refs добавлен `docs/spec_src/spec/04-api.md §4.11` | §8.7 |
| `run_backend_rules` без конфига | Пересчёт solar инкапсулирован в `checks.recheck.recheck_solar` (модуль `rag` владеет `checks/`): все включённые правила `builtin_solar_profile().rules`, область — вся статья | §8.1 п.5 |
| W2-060: нет валидации схемы | Новая стадия «Проверка формата»: `export.sanitize_rows(domain, [rb], stage="ingest")` между извлечением и нормализацией, до записи в БД | §2, §8.4 п.2, §8.5 п.2 |
| W2-001: глобальный `TABLES` | Каждый модуль L1 убирает импорт глобальных констант формата из своих путей. Проверяет ast-линтер `registry_lint` из `tests/conftest.py`. Шаг 0 переводит `domain/io.py` на реестр. На интеграции координатор удаляет `TABLES` из `domain/tables.py` и снимает `xfail` с `tests/shared/test_registry.py::test_no_global_tables`. Владельцы `norm/` и `checks/` назначены (модуль `rag`) | §3.3, §8.0 п.3 |
| W2-078: правки демо чистые | Критерии в `pipeline` (п.5) и `api_runs`: в демо-разборе 0 правок, отметок и `extra`, хотя у тех же версий статей в другом разборе правки есть | §8.5, §8.6 |
| Порог «≤ 300 замечаний» | Убран. Вместо него: оракул (наивная независимая реализация) по каждой проверке, совпадение множества `metric_scale` с предупреждениями скрипта, сохранность 134 эталонных замечаний и снимок счётчиков по видам проверок | §8.1 приёмка |
| Код 2 и пустой `check.ocr` | Критерий `pipeline`: без Tesseract или с `--check-no-ocr` в manifest `check.ocr=false`, в stderr есть предупреждение и пометка «проверка без OCR» в строке причины кода 2 | §8.5 приёмка |
| Переименование `publication_id` в zip | Функция shared `rows.export_publication_ids(files)` строит отображение pid → имя в выгрузке. `SubmissionItem.export_pid`: `build_submission` переименовывает строки через `RowBundle.rename_publication`, `prepare_pdfdir` получает ключи после переименования | §8.0 п.4, §8.4 п.4, §8.6 |

---

## 1. Рамки и приоритеты

| P | Что | Требования |
|---|---|---|
| P0 (а) | CLI `faktura extract --domain solar\|rag\|auto` → `out/solar`, `out/rag` по 7 CSV своего шаблона; `check_submission.py` своей области без ошибок (rag — с `--ocr`, код 2 допустим) | W2-010…018 |
| P0 (б) | RAG-ветка: формат, валидация и санитайзер, нормализация §6.6, проверки §5.2-B, хранение до 28 649 строк на статью с keyset | W2-001…004, 020…046, 050…054, 060…063 |
| P1 (в) | API разборов §15: Run/RunFile, `/runs*`, SSE, summary, `/papers/{domain}/{pid}`, `/results`, `/results/summary`, `/issues`, `/issues/stats`, PDF с Range, правки, пакет, сброс, состояния замечаний, журнал, `submission.zip` с правками, «лучшее значение» | W2-070…106 |
| P1 (г) | Вход §2: код волны 1 готов, `tests/api_base` зелёные | W2-110…112 |
| P2 / could | Демо-разбор, `/compare`, `dataset.json`, `/passport?domain`, `/ocr`, заглушки §15.9, §15.10, `/search` | W2-055, 056, 078, 088, 105, 120…122 |
| Не делаем | Старый чат, проекты, граф по литературе, вопросы к коллекции, внешние эндпоинты корпусов §13 (кроме того, что нужно CLI) | W2-123, Коорд. п.3–4 |

### 1.1. Отличия от принятых умолчаний (решения пользователя главнее)

1. **OCR (Б2-2).** Умолчание «OCR делает локальный Tesseract-адаптер за `ParseAdapter`» **отменено**. Бэкенд не распознаёт текст сам. Он определяет страницы `needs_ocr` (§11.2), вызывает `OcrAdapter` (предварительный HTTP-контракт ML, легко заменить) или его мок, хранит `ocr_used`/`ocr_confidence`, проверяет `quote_found` по тексту OCR из ответа ML и запускает `check_submission.py --ocr`. Tesseract (`FAKTURA_TESSERACT`, `FAKTURA_OCR_LANG=eng`) нужен **только** валидатору. Если OCR недоступен, файл получает статус `ocr` (`ocr_unavailable`, §11.6).
2. **ML-1 (Б2-1).** Адаптер принимает 7 таблиц целиком (JSON), NDJSON-поток и страницы `?part=`. Формат задаёт `FAKTURA_EXTRACT_RAG_FORMAT`. Запасного экстрактора без LLM нет (Б-1).
3. **Пустой `in_scope` в rag.** Скрипт rag требует `in_scope ∈ {true,false}`. Для упавшего файла, файла без ответа ML и файла `ocr` строка `publications` пишется с `in_scope=true` и пустым `exclusion_reason`: скрипт даёт только предупреждение «in-scope publication has no results» (код 2). Для solar остаётся К-14 (`in_scope` пусто).

Остальные умолчания (список координатора и раздел 6.2 анализа) действуют без изменений.

---

## 2. Архитектура волны 2

**Единица данных API §15 и выгрузки — строки 7 таблиц своей области (`RowBundle`).** Значения — строки, как в CSV (`0.330` не становится `0.33`). Числовые теневые колонки нужны только для фильтров и сортировки. solar-код волны 1 (`PaperBundle`, `norm`, `checks`, solar-санитайзер) не переписывается. Его результат переводится в `RowBundle` функцией `domain.rows.from_paper_bundle`.

```
             API §15 (FastAPI /api/v1)                         api.base (shared): вход §2, ошибки, CORS, пагинация
  api.runs: разборы, SSE, сводка, submission.zip, заглушки §15.9
  api.data + api.review: статья, PDF, строки, сводки, замечания, лучшее значение, правки, состояния, журнал
                     │                                         CLI: faktura extract --domain solar|rag|auto, faktura eval
                     ▼                                                       │
  pipeline: RunService (UI-разбор = корпус kind=ui + один прогон), процессор файла, HANDLERS[domain]
     файл → «Разбор PDF» (needs_ocr §11.2) → «Распознавание скана» (OcrAdapter: force или needs_ocr)
          → «Извлечение» (ExtractAdapter) → «Проверка формата» (export.sanitize_rows stage=ingest, W2-060)
          → «Нормализация» → «Проверка» → storage.rows.save (пачки) → статусы RunFile, события file/run/done
                     │
  ┌──────────────────┼──────────────────┬──────────────────────┬──────────────────────┐
  rag                adapters           export                 storage
  rag: норм. §6.6,   ml: мок/HTTP/raw   CSV по шаблону,         строки 7 таблиц, keyset,
  проверки §5.2-B,   обеих областей     санитайзер (ingest      сводки, UI-разборы, правки
  ключи, пересчёт;   ocr: HTTP/мок      и export), правки →     и отметки разбора, extra,
  solar: norm,       pdfio: needs_ocr,  выгрузка, валидатор,    журнал, WAL + 1 писатель
  checks + recheck   цитаты, области    скрипты, _meta, оценка
                     ▲
                     └── shared (шаг 0): domain.domains, domain.rows, domain.ocr, domain.review_rules,
                         сущности, ошибки, config, api.base, тестовая инфраструктура
```

### 2.1. Принципы

1. **Реестр по области (W2-001).** Таблицы, колонки, словари, правила чисел, BOM и ключ переноса правок берутся из `domain.domains.get_spec(domain)`. Функции области выбираются словарём (`pipeline.domains.HANDLERS`, словари диспетчеризации в `export`), а не ветками `if domain == …`. Литералы `"solar"`/`"rag"` в сравнениях допустимы только в файлах-реестрах, которые каждый модуль перечисляет в своём линт-тесте.
2. **Статья = (`domain`, `publication_id`)** во всех ключах, кэшах, путях API и правках (W2-002).
3. **Версия статьи общая (W2-004).** Версия определяется по (`workspace`, `domain`, `sha256`, `model_version`). Файл разбора ссылается на готовую версию без повторного вызова ML. Правки, отметки и пересчитанные замечания живут в разборе: (`run_id`, `paper_id`, …).
4. **Хранятся строки «как извлечено, проверено и нормализовано».** Правки разбора строк не меняют. Они накладываются при чтении (`review` рядом со строкой) и при выгрузке (`export.apply_reviews`).
5. **Состояние замечания вычисляется** (`domain.review_rules.issue_state`): ручная отметка плюс автозакрытие. Счётчики считаются из данных.
6. **Объём (W2-061).** Запись строк — пачками `executemany` по `rows_batch` = 2000. Проверки — группировка хэшем за O(n). Пагинация keyset. `total` точный до 100 000, дальше `total_estimate`. Пересчёт после правки — только по области пересчёта.
7. **Надёжность (W2-062).** SQLite в режиме WAL, `busy_timeout`, один писатель. Воркеры завершаются детерминированно. В тестах нет реальных `sleep`. Таймаут каждого теста — 120 с.
8. **Тексты для людей** — по-русски: «сервис разбора» / «модель», без слов «бэк» и «ML» (W2-111).

---

## 3. Модули

| id | Модуль | Ур. | Зависит от | P | Пути (owned) |
|---|---|---|---|---|---|
| `shared` | Шаг 0: реестр областей, строки, OCR-типы, правила правок, сущности, ошибки, config, `api/base`, тестовая инфраструктура | 0 | — | P0 | `pyproject.toml`, `src/faktura/config.py`, `src/faktura/domain/`, `src/faktura/api/__init__.py`, `src/faktura/api/base/`, `tests/conftest.py`, `tests/fixtures/`, `tests/shared/`, `tests/api_base/` |
| `rag` | Правила областей: rag (нормализация, проверки, ключи, пересчёт) и владение solar `norm/`, `checks/` (+ пересчёт solar) | 1 | shared | P0 | `src/faktura/rag/`, `src/faktura/norm/`, `src/faktura/checks/`, `tests/rag/`, `tests/norm/`, `tests/checks/` |
| `adapters` | ML-1 обеих областей, OCR-адаптер (контракт + мок), `needs_ocr`, цитаты, распределение по областям | 1 | shared | P0 | `src/faktura/ml/`, `src/faktura/pdfio/`, `src/faktura/ocr/`, `tests/adapters/` |
| `storage` | Строки 7 таблиц, keyset, сводки, UI-разборы, правки, отметки, extra, журнал, WAL | 1 | shared | P0/P1 | `src/faktura/storage/`, `tests/storage/` |
| `export` | Писатель CSV, санитайзер (ingest/export), наложение правок, сборка и zip, валидатор, скрипты, `_meta`, паспорт, оценка | 1 | shared | P0/P1 | `src/faktura/export/`, `src/faktura/passport/`, `src/faktura/evaluation/`, `tests/export/` |
| `pipeline` | Починка зависаний, конвейер с OCR и областями, CLI, UI-разборы, события, демо, `seed_run` | 2 | shared, rag, adapters, storage, export | P0/P1 | `src/faktura/pipeline/`, `src/faktura/cli/`, `src/faktura/__main__.py`, `tests/pipeline/` |
| `api_runs` | `/runs*`, SSE, сводка, `submission.zip`, stats, `dataset.json`, `/passport`, заглушки §15.9 | 3 | shared, storage, export, pipeline | P1 | `src/faktura/api/runs/`, `src/faktura/api/corpus/`, `tests/api_runs/`, `tests/api_corpus/` |
| `api_data` | Статья, PDF, `/results`, сводки, `/issues`, `/compare`, лучшее значение, язык запросов, заглушки §15.10 и `/search`; правки, пакет, состояния замечаний, сброс, журнал, undo | 3 | shared, rag, storage, pipeline | P1 | `src/faktura/api/data/`, `src/faktura/api/review/`, `src/faktura/api/papers/`, `src/faktura/query/`, `src/faktura/review/`, `tests/api_data/`, `tests/api_review/`, `tests/api_papers/` |

Все пути — от `backend/`. `tests/e2e/` никому не принадлежит: его переписывает координатор на интеграции.

```
L0  shared (один, первым)
L1  rag · adapters · storage · export        (параллельно)
L2  pipeline                                  (сначала зависания и CLI обеих областей — веха P0, затем UI-разборы — P1)
L3  api_runs · api_data                       (параллельно)
В3  интеграция (координатор): удалить TABLES-псевдоним, переписать tests/e2e, полный прогон, CLI на dev/test обеих областей
```

### 3.1. Порядок исправления shared после шага 0
После L0 shared заморожен. Модуль, который нашёл ошибку в shared, ставит `xfail(strict=True, reason="shared_bug: …")` и описывает ошибку в отчёте. Координатор правит shared между уровнями.

### 3.2. Правило `ROUTER_MODULES`
- Шаг 0 ставит `ROUTER_MODULES = ("faktura.api.runs", "faktura.api.data", "faktura.api.review")` и обновляет `tests/api_base/test_app_factory.py`.
- `create_app` (код волны 1) подключает `ROUTERS` пакета. Если **этого** пакета нет (`ModuleNotFoundError` с именем модуля), он пропускает его с предупреждением в лог. Любая другая ошибка импорта роняет запуск. Поэтому до L3 `api.runs` и `api.data` пропускаются, а старый `api.review` (у него `ROUTERS = []`) подключается.
- `faktura.api.corpus` и `faktura.api.papers` после шага 0 не подключаются. Их тесты (`tests/api_corpus`, `tests/api_papers`) и устаревший `tests/e2e` исключаются `collect_ignore` в `tests/conftest.py`. Владельцы удаляют эти каталоги: `api_runs` — `api/corpus` (перенеся `EventTokens` и `sse_stream` в `api/runs`), `api_data` — `api/papers`. Координатор на интеграции снимает `collect_ignore`.
- Пакет роутеров, подключённый в L3, не должен объявлять пути, которые уже есть в `api/base` (после шага 0 там нет `/graph`, `/graph/edges.csv`, `/search`).

### 3.3. Удаление глобального `TABLES` (W2-001)
1. Шаг 0: `domain.domains` (реестр), `domain/io.py` переведён на `get_spec(domain)`; `domain.tables.TABLES` и прочие константы остаются как псевдонимы solar для совместимости; в `tests/conftest.py` — фикстура `registry_lint`.
2. L1: каждый модуль убирает из своих путей импорт `TABLES`, `TABLE_NAMES`, `ID_COLUMN`, `LIST_FIELDS`, `BOOL_FIELDS`, `NUMERIC_STRICT`, `NUMERIC_LOOSE`, `VOCAB` (из `faktura.domain` и `faktura.domain.tables`). Вместо них — `get_spec(domain)`. Линт-тест `tests/<id>/test_registry_lint.py` зелёный.
3. В3: координатор удаляет псевдонимы из `domain/tables.py` и `domain/__init__.py` и снимает `xfail(strict=True)` с `tests/shared/test_registry.py::test_no_global_tables`.

`CHECK_CATALOG`, `EVAL_METRICS`, `TARGET_UNITS`, `UNIT_FOR_VALUE` — смысловые константы solar. Их могут использовать solar-код (`norm/`, `checks/`) и solar-ветки реестров.

---

## 4. Вехи

| Веха | Содержание | Готово, когда |
|---|---|---|
| В0 | `shared` | `tests/shared` и `tests/api_base` зелёные; все пакеты волны 1 импортируются; L1-тесты волны 1 не сломаны (кроме исключённых `collect_ignore`) |
| В1 (P0) | L1 + `pipeline` этап P0 | `faktura extract --domain solar` на dev: 0 ошибок скрипта; `--domain rag` на dev: 0 ошибок, код скрипта 0/2; `--domain rag` на test (мок): 0 ошибок; `tests/pipeline` без зависаний (весь каталог ≤ 10 мин) |
| В2 (P1) | `pipeline` этап P1 + L3 | Стыковка BE↔FE по TASKS: `POST /runs` → `GET /runs/{id}` + SSE → `GET /papers/{domain}/{pid}` → `GET /results` → `/pdf` → `submission.zip`; затем summary, issues, правки, состояния, журнал |
| В3 | Интеграция | `TABLES` удалён; полный `pytest` зелёный; e2e на моках обеих областей; CLI на dev/test обеих областей |

---

## 5. Тестирование

- Каталог тестов = id модуля (`tests/<id>/`). Команда модуля: `python -m pytest tests/<id> -q --tb=short` (модуль `rag` дополнительно гоняет `tests/norm tests/checks`, `api_data` — `tests/api_review`).
- Маркеры: `data` (область 1), `rag_data` (область 2), `slow` (> 10 с), `perf` (по умолчанию выключен, допуск ×3).
- Таймаут 120 с на тест. Долгие проверки (скрипт rag с `--ocr`) — только `slow` + `rag_data` + `@pytest.mark.timeout(3600)`.
- Ожидания ML в тестах: задержки повторов `()` или подменённый `sleep`. Пулы закрываются в `finally`.
- Пороги perf (синтетика размера D014 = 28 649 строк results): запись строк ≤ 15 с; страница `/results` из 100 строк с фильтром ≤ 300 мс; `results/summary` ≤ 1 с; PATCH review ≤ 500 мс; нормализация + проверки rag на D014 ≤ 30 с; `recheck_group` на самой большой области пересчёта D014 ≤ 200 мс.

---

## 6. Трассировка требований

| Требования | Модуль(и) |
|---|---|
| W2-001…004 | `shared` (реестр), все L1 (линт), `storage`, `pipeline`, В3 |
| W2-010…016, 018 | `pipeline` (CLI), `export` (скрипты, `_meta`), `adapters` (области, мок) |
| W2-017 | `export` (roundtrip-тест, `EvalAdapter` rag), `pipeline` (`faktura eval`), `rag` (сохранность эталона) |
| W2-020…028 | `export` (писатель, санитайзер, валидатор), `shared` (`RAG`-спека) |
| W2-030…033 | `rag` (normalize) |
| W2-040…046 | `rag` (checks, keys, recheck) |
| W2-050…054 | `adapters` (`needs_ocr`, OCR-адаптер, цитаты), `pipeline` (статусы) |
| W2-055, 056 | `rag` (`ocr_low_confidence`), `api_data` (`/ocr`) |
| W2-060…063 | `pipeline` (стадии, зависания), `export` (ingest-санитайзер), `storage` |
| W2-070…078 | `pipeline` (сервис, демо), `api_runs` (HTTP), `storage` (сводка) |
| W2-080…089 | `api_data`, `storage` |
| W2-090…098 | `api_data` (правки), `storage`, `rag` (recheck), `shared` (`review_rules`), `pipeline` (перенос при restart) |
| W2-100…106 | `export` (наложение правок, zip), `api_runs` (HTTP) |
| W2-110…112 | `shared` (`api/base`), `api_runs` (загрузка) |
| W2-120…122 | `api_runs` (§15.9), `api_data` (§15.10, `/search`) |

---

## 7. Риски

| Риск | Мера |
|---|---|
| Зависание тестов pipeline (Р2-6) | Первая задача `pipeline` — найти причину и починить; `pytest-timeout`; без реальных `sleep` |
| Расхождение правил rag со скриптом | Константы и функции скрипта скопированы в shared и сверяются со скриптом через `ast`; санитайзер на ingest и на export; тест скрипта на выходе CLI |
| Объём D014 (Р2-5) | Пачки, теневые колонки, индексы, keyset, пересчёт только области; perf-тесты |
| Контракт OCR от ML изменится (Р2-9) | Схема только в `domain/ocr.py` и `ocr/contract.py`, мок по фикстурам |
| Повторы и правки ломают скрипт (Р2-4) | `review_rules` в shared; каскад в `export.apply_reviews`; санитайзер снимает разошедшиеся связи; дедупликация замечаний в `add_issue` |
| Shared большой и всё на нём держится | Отдельный модуль с критериями; L1 стартует только после зелёного `tests/shared`; ошибки shared — через `xfail` и координатора |

---

## 8. ТЗ модулей (brief)

Тексты совпадают с полем `brief` структурированного плана. Общие правила для всех модулей:
- идентификаторы на английском, тексты для людей по-русски;
- git-коммитов не делать; `data/` и `*.zip` не менять;
- shared-пути (`pyproject.toml`, `config.py`, `domain/`, `api/__init__.py`, `api/base/`, `tests/conftest.py`, `tests/fixtures/`, `tests/shared/`, `tests/api_base/`) правит только модуль `shared`; ошибку в shared оформить `xfail(strict=True, reason="shared_bug: …")`;
- тесты запускать узко: `python -m pytest tests/<id> -q --tb=short`; таймаут 120 с на тест, реальных `sleep` нет;
- в своих путях не импортировать `TABLES`, `TABLE_NAMES`, `ID_COLUMN`, `LIST_FIELDS`, `BOOL_FIELDS`, `NUMERIC_STRICT`, `NUMERIC_LOOSE`, `VOCAB`: вместо них `faktura.domain.domains.get_spec(domain)`. Тест `tests/<id>/test_registry_lint.py` вызывает фикстуру `registry_lint(paths, allow_files)` и ждёт пустой список нарушений (W2-001).

### 8.0. `shared` (L0) — шаг 0

Пути: `backend/pyproject.toml`, `backend/src/faktura/config.py`, `backend/src/faktura/domain/`, `backend/src/faktura/api/__init__.py`, `backend/src/faktura/api/base/`, `backend/tests/conftest.py`, `backend/tests/fixtures/`, `backend/tests/shared/`, `backend/tests/api_base/`. Модуль выполняется первым и один. Остальные 7 модулей пишутся поверх его интерфейсов.

**Совместимость.** После шага 0 должны импортироваться все пакеты волны 1 (`faktura.export`, `ml`, `storage`, `pdfio`, `checks`, `norm`, `pipeline`, `passport`, `evaluation`). Старые имена остаются: `domain.tables.TABLES` и прочие константы — как псевдонимы solar; `ExtractOutcome.bundle`; `ParsedPage.needs_ocr`/`ParsedDocument.needs_ocr`. `tests/shared` и `tests/api_base` зелёные.

**1. `pyproject.toml`.** dev-зависимость `pytest-timeout`; `[tool.pytest.ini_options]`: `timeout = 120`, `faulthandler_timeout = 150`; маркер `rag_data: нужны данные области 2 (data/caseA_drive или FAKTURA_RAG_CASE_DIR)`.

**2. `config.py` — новые поля `Settings`** (env `FAKTURA_<ИМЯ>`). Умолчания путей вычисляются в `_derive_defaults`.

| Поле | Тип и умолчание | Назначение |
|---|---|---|
| `rag_case_dir` | `Path \| None`; если None и есть каталог `BACKEND_DIR.parent/"data"/"caseA_drive"` — он | корень кейса 2 |
| `mock_gold_dir_rag` | `Path \| None`; `rag_case_dir/"dev_gold"` | эталон мока ML-1 rag |
| `check_script_rag` | `Path \| None`; `rag_case_dir/"tools"/"check_submission.py"`, если файл есть | скрипт области 2 |
| `check_timeout_rag_s` | `int = 3600` | таймаут скрипта rag с `--ocr` (W2-013) |
| `check_ocr_shards` | `int = 4` | процессы скрипта rag при разбиении |
| `tesseract` | `Path \| None`; если None: `shutil.which("tesseract")`, затем `Path.home()/"scoop/apps/tesseract/current/tesseract.exe"`, если есть; всегда абсолютный | **только** для `check_submission.py --ocr` |
| `ocr_lang` | `str = "eng"` | язык для скрипта и запроса к OCR ML |
| `ocr_dpi` | `int = 300` | |
| `ocr_impl` | `Literal["auto","http","mock","off"] = "auto"` | `auto`: `http` при `ocr_url`, иначе `mock` при `extractor == "mock"`, иначе `off` |
| `ocr_mode` | `Literal["auto","off","force"] = "auto"` | какие страницы распознавать (CLI `--ocr`) |
| `ocr_url` | `str \| None = None` | сервис OCR ML |
| `ocr_timeout_s` | `float = 600` | §11.6 |
| `ocr_mock_dir` | `Path \| None = None` | фикстуры ответов OCR (`<sha256>.json` / `<pid>.json`) |
| `ocr_low_confidence` | `float = 0.8` | порог `ocr_low_confidence` |
| `extract_rag_format` | `Literal["auto","json","ndjson","parts"] = "auto"` | формат ответа ML-1 rag |
| `rows_batch` | `int = 2000` | пачка записи строк |
| `ui_page_limit`, `ui_page_max` | `100`, `1000` | лимиты эндпоинтов §15 |
| `results_all_max` | `int = 2000` | `results=all` у rag-статьи разрешён, если строк меньше |
| `results_large_rows` | `int = 20000` | «большая статья» для `GET /results` |
| `total_exact_max` | `int = 100000` | дальше — `total_estimate` |
| `demo_enabled` | `bool = True` | ленивое создание демо-разбора |
| `sqlite_busy_timeout_ms` | `int = 30000` | |
| `eval_rag_cmd` | `str \| None = None` | команда `faktura-eval` (ML-2); None → `shutil.which("faktura-eval")` в момент вызова |
| `eval_timeout_s` | `int = 600` | |

**3. `domain/domains.py` (новый) — реестр областей.**
```python
Domain = Literal["solar", "rag"]; DOMAINS = ("solar", "rag")

@dataclass(frozen=True)
class DomainSpec:
    name: str
    tables: dict[str, tuple[str, ...]]          # 7 таблиц; порядок ключей = порядок файлов, колонки = порядок шаблона
    table_names: tuple[str, ...]
    id_column: dict[str, str]                   # первичный ключ внутри статьи
    refs: dict[tuple[str, str], str]            # (таблица, колонка) → целевая таблица той же статьи
    list_fields: dict[tuple[str, str], str]     # колонка-список → разделитель (issues.record_ids → ";")
    bool_fields: frozenset[tuple[str, str]]
    enums: dict[tuple[str, str], frozenset[str]]        # закрытые словари: значение вне словаря = ошибка скрипта
    recommended: dict[tuple[str, str], frozenset[str]]  # значение вне словаря = предупреждение
    numeric: dict[tuple[str, str], str]         # "positive_int" | "nonneg_int" | "nonneg" | "finite" | "loose"
    csv_bom: bool                               # solar True, rag False (К2-3)
    template_version: str                       # rag "rag-viniti-adaptation-1.0"; solar — версия шаблона волны 1
    scored_metrics: tuple[str, ...]             # solar: 11 EVAL_METRICS; rag: ()
    check_catalog: frozenset[str] | None        # solar: имена CHECK_CATALOG; rag: None (check_name — открытый текст)
    match_key_columns: tuple[str, ...]          # колонки ключа переноса правок, см. review_rules
SOLAR: DomainSpec; RAG: DomainSpec
def get_spec(domain: str) -> DomainSpec   # неизвестная → AppError("validation_error", "Неизвестная область: <x>", field="domain")
```
- `SOLAR` собирается из констант `domain/tables.py` (`TABLES`, `ID_COLUMN`, `LIST_FIELDS`, `BOOL_FIELDS`, `VOCAB`, `NUMERIC_STRICT` → `"finite"`, `NUMERIC_LOOSE` → `"loose"`, `EVAL_METRICS`, `CHECK_CATALOG`). `refs` solar — те связи, которые проверяет `export/validate.py` волны 1 (результаты → приборы, измерения, материалы; `duplicate_of` → results; `issues.record_ids` → results).
- `RAG` — константы в коде, 1:1 с `data/caseA_drive/submission_template/schema.json` (колонки, `keys`, `enums`, `recommended_enums`). `numeric` 1:1 со словарями скрипта `NUMBERS`/`POSITIVE_INTS`/`NONNEGATIVE_INTS`/`NONNEGATIVE`: `publications.year`, `results.source_page`, `issues.source_page` → `positive_int`; `configurations.top_k`, `results.metric_k`, `results.sample_size`, `experiments.sample_size` → `nonneg_int`; `configurations.chunk_size`, `chunk_overlap`, `experiments.latency_ms`, `tokens_per_query` → `nonneg`; `results.metric_value`, `baseline_value`, `normalized_value` → `finite`. `refs` rag: `results.experiment_id → experiments`, `config_id → configurations`, `dataset_id → datasets`, `task_id → tasks`, `duplicate_of → results`, `issues.record_ids → results`. `bool_fields`: `publications.in_scope`.
- `match_key_columns`: solar — `publication_id, device_id, measurement_id, material_id, metric_name, aggregation, source_page`; rag — `publication_id, experiment_id, config_id, dataset_id, task_id, metric_name, metric_variant, metric_k, metric_unit, source_page`.
- Копии из `check_submission.py` области 2 (без выполнения скрипта):
  - `RAG_DUP_FIELDS = ("experiment_id","config_id","dataset_id","task_id","metric_name","metric_variant","metric_k","metric_unit","metric_value","aggregation","sample_size","result_origin","cited_publication_id")`;
  - `RAG_SCALE_METRIC_RE` (используется через `fullmatch`) `(?i)(accuracy|precision|recall|f1|exact match|em|hit(?:s)?(?:@\d+)?|mrr|ndcg)(?:@\d+)?`;
  - `RAG_AT_K_RE = r"@\d+(?![\d.])\b"`; `SAFE_ID_RE = r"[A-Za-z0-9][A-Za-z0-9_-]*\Z"`;
  - `same_at_reported_precision(a, b) -> bool` и `rag_quote_norm(text) -> str` — копии функций скрипта `same_at_reported_precision` и `norm`.
- `domain/io.py`: `read_tables(directory, domain="solar")`, `parse_cell(table, column, text, domain="solar")`, `format_cell(..., domain="solar")` берут колонки из `get_spec(domain)`. Поведение для solar не меняется.

**4. `domain/rows.py` (новый) — строки 7 таблиц.**
```python
class RowMeta(BaseModel):
    ord: int                                   # порядок строки в ответе ML / в CSV (стабильный, с 0)
    confidence: float | None = None
    bbox: dict | None = None                   # {page, rects:[{x,y,w,h}]} в долях страницы от левого верхнего угла
    quote_found: bool | None = None
    norm_status: str | None = None; norm_note: str | None = None
    ocr_used: bool | None = None; ocr_confidence: float | None = None
    row_label: str | None = None; row_text: str | None = None
    extra: dict = {}                           # поля ML вне шаблона
class IssueMeta(BaseModel):
    key: str                                   # стабильный ключ К-6, см. issue_key_for
    computed_by: Literal["backend", "ml", "both"]
    confidence: float | None = None
    exported: bool = True                      # False — только интерфейс (ocr_low_confidence)
    severity: str | None = None
    quote_found: bool | None = None            # проверка цитаты source_fragment замечания
    scope: str | None = None                   # область пересчёта: rag "<experiment_id>|<metric_name>", solar "paper"; None — не пересчитывается
class RowBundle(BaseModel):
    domain: str; publication_id: str
    tables: dict[str, list[dict[str, str]]]    # ВСЕ 7 таблиц (ключи есть всегда); ячейки — строки как в CSV; "" = пусто; все колонки спеки
    meta: dict[str, dict[str, RowMeta]]        # таблица → id записи → meta (id по spec.id_column)
    issue_meta: dict[str, IssueMeta]           # issue_id → meta
    warnings: list[str] = []
```
Методы и функции (точная семантика):
- `rows(table) -> list[dict]` — список (пустой, если строк нет). `index(table) -> dict[id, row]` — по `spec.id_column`, при повторе id побеждает первая строка.
- `in_scope() -> bool | None` — по `publications[0]["in_scope"]`: `"true"` → True, `"false"` → False, иначе None.
- `RowBundle.empty(domain, pid)` — «ML ответил, но ничего не нашёл» (мок для статьи вне эталона): 7 ключей таблиц; одна строка `publications` со всеми колонками `""`, кроме `publication_id`; `in_scope=""` в обеих областях; meta и issue_meta пусты.
- `RowBundle.failed_stub(domain, pid, reason="")` — «файл упал, нет ответа ML или статус `ocr`»: как `empty`, но для rag `in_scope="true"` (раздел 1.1 п.3), для solar `in_scope=""` (К-14); `warnings=[reason]`, если задан.
- `split_list(text) -> list[str]` — `;`, обрезка пробелов, без пустых. `join_list(ids) -> str`.
- `issue_key_for(row, extra_params=None) -> str` — `ids.issue_key(check_name, split_list(record_ids), params)`, где `params = {"page": source_page, "fragment": rag_quote_norm(source_fragment)[:200], **(extra_params or {})}` (по `ids.issue_key` параметры участвуют только при пустых `record_ids`).
- `add_issue(row, meta) -> str` — единственный способ добавить замечание. Недостающие колонки — `""`; `publication_id` ставится свой; `meta.key` пуст → `issue_key_for(row)`. Слияние:
  - (а) есть замечание с тем же `check_name` и тем же множеством `record_ids` (при пустых `record_ids` — тем же `key`) → новое не добавляется. `computed_by`: {ml, backend} → `both`; описание остаётся от ML; `confidence` = max; `quote_found` — непустое; возвращается существующий `issue_id`;
  - (б) оба `computed_by == "backend"`, тот же `check_name`, непустые `record_ids`: новое множество ⊆ существующего → не добавляется; ⊇ существующего → существующее удаляется (с его meta), новое добавляется;
  - иначе добавляется с `issue_id = row["issue_id"]`, если он задан и свободен, иначе с временным `_n<k>`. Возвращает `issue_id`.
- `renumber_issues(prefix="I") -> dict[old, new]` — порядок (int(`source_page`) или 10⁹, `check_name`, `record_ids`, `description`); id `I1…In`; атомарно переписывает `tables["issues"][*]["issue_id"]` и ключи `issue_meta`. Одинаковый вход даёт одинаковые id.
- `rename_publication(new_pid) -> RowBundle` — копия, у которой `publication_id` заменён во всех строках всех 7 таблиц и в поле модели.
- `read_rows_dir(directory, domain) -> list[RowBundle]` — 7 CSV спеки (`encoding="utf-8-sig"` снимает BOM, если есть; `newline=""`; модуль `csv`). Значения как есть, без strip; недостающая колонка → `""`; лишняя колонка → `ValueError` с именем файла и колонки. Группировка по `publication_id`: порядок — как в `publications.csv`, затем pid только из других таблиц (по возрастанию). `RowMeta.ord` — номер строки внутри (статья, таблица). У замечаний: `IssueMeta(key=issue_key_for(row), computed_by="ml")`.
- `from_paper_bundle(b: PaperBundle) -> RowBundle` — через `domain.io.tables_from_bundles([b])`. `RecordMeta` → `RowMeta` (`confidence`, `bbox`, `quote_found`, `norm_status`, `ocr_*`). `IssueRecord` → `IssueMeta` (`key` — ключ волны 1; `computed_by` по `added_by`; `exported` — по флагу `CHECK_CATALOG`; `severity`; `scope="paper"` для правил из `checks.BACKEND_RULES`).
- `to_paper_bundle(rb) -> PaperBundle` — только solar (для rag — `ValueError`), через `domain.io.bundles_from_tables(rb.tables)`, meta переносится по id записи.
- `num(text) -> float | None` — теневая колонка: `float(Decimal(text.strip()))`, если это конечное число; иначе None (`""`, `"1,5"`, `"%"`, `NaN`, `inf`).
- `export_publication_ids(files: list[tuple[str, str]]) -> dict[str, str]` — вход: (`pid`, исходное имя файла). Основа имени — имя без каталогов и без `.pdf` (без учёта регистра). Если основа встречается ровно один раз среди переданных файлов и не совпадает с `pid` другого файла → основа; иначе → `pid`. Результат всегда уникален (К2-14).

**5. `domain/ocr.py` (новый) — предварительный контракт OCR от ML** (меняется только здесь и в `ocr/contract.py`):
```python
class OcrWord(BaseModel): text: str; bbox: tuple[float, float, float, float]; confidence: float   # x0,y0,x1,y1 в пунктах PDF от левого верхнего угла; 0..1
class OcrPage(BaseModel): n: int; text: str; words: list[OcrWord] = []; ocr_confidence: float | None = None
                          ocr_language: str | None = None; dpi: int | None = None; deskew_deg: float | None = None
                          width_pt: float | None = None; height_pt: float | None = None
class OcrResult(BaseModel): used: bool; engine: str; engine_version: str; pages: list[OcrPage]
                            mean_confidence: float | None = None; partial_pages: list[int] = []
class OcrUnavailable(ExtractError)   # code "ocr_unavailable", retryable=True
class OcrFailed(ExtractError)        # code "ocr_failed"
class OcrAdapter(Protocol):
    name: str; version: str
    def recognize(self, pdf_path: Path, pages: list[int], *, sha256: str, language: str, dpi: int) -> OcrResult: ...
    def health(self) -> MlHealth: ...
```

**6. `domain/review_rules.py` (новый) — чистые правила правок** (одни для API, выгрузки и счётчиков):
- `REVIEW_NOTE = "Исправлено вручную: было {old}"`. `append_note(note, text)`: пустой `note` → `text`; иначе `note + "; " + text`; если `text` уже есть среди частей `note.split("; ")` — без изменений.
- `display_status(review) -> "new"|"accepted"|"rejected"|"corrected"` — `corrected`, если статус `new`/`accepted` и `corrected_value` не None.
- `effective_value(row, review) -> str` — `review.corrected_value`, если правка есть, не `rejected` и значение не None; иначе `row["normalized_value"]`.
- `duplicate_groups(results) -> dict[root, list[dup]]` — для каждой строки с непустым `duplicate_of` идём по цепочке до строки без `duplicate_of`. Цикл или ссылка на несуществующую запись → строка не входит ни в одну группу. Повторы — в порядке строк. Ключи — только корни, у которых есть повторы.
- `effective_main(root, dups, rejected) -> str | None` — `root`, если он не отклонён; иначе первый неотклонённый повтор; None, если отклонены все.
- `review_target(rid, results_index, rejected) -> str` — если у `rid` есть `duplicate_of`: найти корень и вернуть `effective_main(...)` (или `rid`, если None); иначе `rid`.
- `issue_state(mark, record_ids, reviews) -> (state, state_auto)` — если `record_ids` не пусты и каждая запись отклонена или имеет `corrected_value` → `("fixed", True)`; иначе отметка `fixed`/`ok` → `(mark.state, False)`; иначе `("open", False)`.
- `review_match_key(domain, row) -> list[str]` — значения `get_spec(domain).match_key_columns`.
- `values_close(a, b, rel=0.005) -> bool` — оба Decimal: `|a−b| ≤ rel·max(|a|,|b|)` (оба 0 → True); иначе сравнение строк после `strip()`.
- `parse_corrected(value) -> str | None` — None → None; int или float (не bool) → `format(Decimal(str(value)), "f")`; строка: убрать пробелы, NBSP и тонкие пробелы, одну «,» заменить на «.», затем fullmatch `[+-]?(\d+(\.\d*)?|\.\d+)`, иначе `AppError("fact_value_unrecognized", field="corrected_value")` (422). Цифры не теряются: `"0,330"` → `"0.330"`.
- `diff_recheck(base, new, checks) -> (add: list[(row, IssueMeta)], suppress: list[str])` — `base` — сохранённые замечания области (`(row, meta)`). В сравнении участвуют только замечания с `computed_by == "backend"` и `check_name ∈ checks`. Сравнение по `meta.key`: есть в `new`, нет в `base` → `add`; есть в `base`, нет в `new` → `suppress` (ключ). Замечания `both` и `ml` не подавляются.

**7. Правки существующих файлов `domain/`.**
- `extraction.py`: `ParsedPage` += `ocr_used: bool = False`, `ocr_confidence: float | None = None` (`needs_ocr` уже есть — это и есть набор §11.2); `ParsedDocument` += `scan: bool = False`, `ocr: OcrResult | None = None`, `ocr_pages: list[int] = []`; `ExtractOutcome.bundle: PaperBundle | None = None`, `ExtractOutcome.rows: RowBundle | None = None` (валидатор: заполнено хотя бы одно).
- `ports.py`: реэкспорт `OcrAdapter`. `services.py`: `Services.ocr: OcrAdapter | None = None`.
- `entities.py`:
  - `RunFileStatus(StrEnum)`: `queued, running, done, out, ocr, error, nodata`; `UiRunStatus`: `queued, running, done, stopped, error`;
  - `STATUS_LABELS_RU = {queued: "в очереди", running: "разбор", done: "готово", nodata: "нет результатов", ocr: "скан без текста", error: "ошибка"}` (`out` — текст `exclusion_reason`);
  - `STAGES_RU = ("Разбор PDF", "Распознавание скана", "Извлечение", "Проверка формата", "Нормализация", "Проверка")`;
  - `Corpus` += `title`, `pinned=False`, `demo=False`, `owner_id`, `updated_at`; `kind` += `"ui"`, `"cli"`;
  - `CorpusFile` += `domain`, `scan=False`, `ocr_confidence`, `stage`, `progress=0`, `stopped=False`, `reason`, `original_name`;
  - `ResultReview(run_id, paper_id, domain, publication_id, result_id, status: "accepted"|"rejected"|"new", corrected_value: str | None, by, by_name, at, match_key: list[str] | None, unmatched: bool = False)`;
  - `IssueMark(run_id, paper_id, domain, publication_id, issue_key, issue_id, state: "open"|"fixed"|"ok", by, at, unmatched: bool = False)`;
  - `ExtraIssue(run_id, paper_id, scope, key, op: "add"|"suppress", row: dict[str,str] | None, meta: IssueMeta | None)`.
- `errors.py` (`ERROR_CATALOG`): `ocr_unavailable` (503), `ocr_failed` (502), `eval_unavailable` (503, «Инструмент оценки недоступен»), `zip_bomb` → 422 (§15.3); коды строк `rejected` (не HTTP): `duplicate`, `duplicate_name`, `unsafe_path`.

**8. `api/__init__.py` и `api/base/`.**
- `ROUTER_MODULES = ("faktura.api.runs", "faktura.api.data", "faktura.api.review")`; в `tests/api_base/test_app_factory.py` — новые имена. Пропуск отсутствующего пакета уже реализован в `create_app`, его не трогать.
- `api/base/errors.py`: коды `run_in_progress` (409, «Разбор ещё идёт — дождитесь окончания или скачайте готовые статьи (?partial=true)»), `issue_auto_fixed` (409, «Все значения замечания исправлены или отклонены — замечание закрыто автоматически»), `submission_invalid` (422), `already_undone` (409), `range_not_satisfiable` (416).
- `api/base/deps.py`: `ui_page_params = make_page_params(100, 1000)` и `make_ui_page_params()`; экспорт в `__all__`.
- `api/base/routes_stubs.py`: удалить `/graph`, `/graph/edges.csv`, `/search` и их тесты в `tests/api_base/test_stubs_p2.py` (эти пути делает `api_data`).
- `middleware.py`: `CORS_EXPOSE_HEADERS` += `X-Export-Tables, X-Export-Results, X-Export-Rejected, X-Export-Papers, X-Export-Filename, X-Check-Status, Content-Disposition`; маскировать `token=` в query при логировании.
- `app.py`: при остановке приложения (lifespan) `services.executor.shutdown(wait=True)` и `db.close()` детерминированно.

**9. Тестовая инфраструктура.**
- `tests/conftest.py`:
  - `collect_ignore = ["api_corpus", "api_papers", "e2e"]` с комментарием «устарели после смены ROUTER_MODULES; удаляет владелец модуля / координатор»;
  - фикстуры (`rag_data` — пропуск по маркеру, как у `data`): `rag_case_dir` (session; `settings.rag_case_dir` или `FAKTURA_RAG_CASE_DIR`), `rag_dev_dir`, `rag_dev_gold_dir`, `rag_test_dir`, `rag_check_script`, `rag_schema_path`; `tesseract_path` (нет — `pytest.skip`); `rag_mini_gold_dir` (без маркера, путь `tests/fixtures/rag_mini_gold`); `rag_mini_bundles` и `rag_gold_bundles` (session, `dict[pid, RowBundle]` через `read_rows_dir`); `ocr_fixture_dir`;
  - `settings` дополнительно ставит `mock_gold_dir_rag=rag_mini_gold_dir`, `ocr_impl="mock"`, `ocr_mock_dir=ocr_fixture_dir`, `demo_enabled=False`, `extract_retry_delays=""`;
  - `registry_lint` → функция `lint(paths: list[Path], allow_files: set[str] = frozenset()) -> list[str]`: ast-скан `.py`-файлов. Нарушение — импорт `TABLES`, `TABLE_NAMES`, `ID_COLUMN`, `LIST_FIELDS`, `BOOL_FIELDS`, `NUMERIC_STRICT`, `NUMERIC_LOOSE`, `VOCAB` из `faktura.domain`/`faktura.domain.tables` (и относительные варианты), а также сравнение (`==`, `!=`, `in`) со строковой константой `"solar"`/`"rag"` в файле, которого нет в `allow_files`.
- `tests/fixtures/rag_mini_gold/` — 7 CSV **без BOM** из `dev_gold` области 2: D001 целиком и около 300 строк D014. В выборке есть цепочки `duplicate_of`, замечание с пустыми `record_ids`, строки `absent` с `-` и `–`, многострочный `source_fragment`, `cited` и значения вида `0.330`. Все ссылки внутри выборки целы: скрипт rag на ней даёт 0 ошибок.
- `tests/fixtures/ocr/` — `sample_ocr.json` (ответ по контракту, 2 страницы) и `broken_ocr.json`.
- `tests/shared/`:
  - `test_registry.py`: `RAG` 1:1 со `schema.json` и константами скрипта (разбор `ast`, без выполнения); `same_at_reported_precision` и `rag_quote_norm` совпадают с функциями скрипта на 200 случаях (функции скрипта загружаются через `ast` + `exec` изолированно); `get_spec("x")` → `validation_error`; `test_no_global_tables` — `xfail(strict=True, reason="W2-001: снимается на интеграции")`;
  - `test_rows.py`: `empty`/`failed_stub`; `add_issue` (а)(б); `renumber_issues` переносит `issue_meta`; `rename_publication`; `read_rows_dir` на `rag_mini_gold` сохраняет `0.330` и многострочный текст байт в байт; `export_publication_ids`; `num`; `from_paper_bundle`/`to_paper_bundle` — roundtrip на solar `mini_gold`;
  - `test_review_rules.py`: повторы, `effective_main`, `review_target`, автозакрытие, `parse_corrected`, `values_close`, `diff_recheck`;
  - `test_ocr_types.py`: `sample_ocr.json` разбирается в `OcrResult`.

**Приёмка.** `python -m pytest tests/shared tests/api_base -q` зелёные (кроме объявленного `xfail`); `python -c "import faktura.export, faktura.ml, faktura.storage, faktura.pdfio, faktura.checks, faktura.norm, faktura.pipeline, faktura.passport, faktura.evaluation"` без ошибок; L1-тесты волны 1 (`tests/storage`, `tests/export`, `tests/adapters`, `tests/checks`, `tests/norm`) не стали краснее, чем до шага 0; скрипт rag на `tests/fixtures/rag_mini_gold` (без PDF) — 0 ошибок.

### 8.1. `rag` (L1) — правила областей

Пути: `backend/src/faktura/rag/` (новый пакет), `backend/src/faktura/norm/`, `backend/src/faktura/checks/`, `backend/tests/rag/`, `backend/tests/norm/`, `backend/tests/checks/`. Чистые функции без БД, HTTP и PDF. solar-код `norm/` и `checks/` волны 1 зелёный: его не переписывать. Модуль им владеет и добавляет только `checks/recheck.py`. `tests/norm` и `tests/checks` должны остаться зелёными.

**Вход** — `faktura.domain.rows.RowBundle` (shared): `tables[table] -> list[dict[col, str]]` (все ячейки — строки как в CSV, `""` = пусто), `meta[table][id] -> RowMeta`, `issue_meta[issue_id] -> IssueMeta(key, computed_by, confidence, exported, severity, quote_found, scope)`. Колонки — `get_spec("rag").tables`. Из shared также: `RAG_SCALE_METRIC_RE` (fullmatch), `RAG_AT_K_RE`, `same_at_reported_precision`, `rag_quote_norm`, `RowBundle.add_issue` (единственный способ добавить замечание: он сливает дубли и поглощает подмножества), `RowBundle.renumber_issues`, `issue_key_for`, `review_rules.duplicate_groups`/`effective_main`, протокол `QuoteChecker.check(page, fragment) -> bool | None`.

**Жёсткие правила:** числа не форматируются через float — исходная строка сохраняется (`0.330`), сравнение через Decimal; `raw_value`/`raw_unit` не меняются; доли, проценты и BLEU/ROUGE не пересчитываются по одному имени метрики; разные единицы одной метрики не приводятся друг к другу; строки results не удаляются.

**1. `faktura.rag.normalize`.** `NORM_VERSION_RAG`; `normalize_rows(rb) -> list[NormEvent]` (меняет rb на месте; `NormEvent(kind, result_ids, page, table, message_ru)`); `normalize_value(raw_value, raw_unit, *, metric_name, metric_variant, note, scale_known) -> RagNorm(value, unit, status, note_add, event_kind)`; `metric_k_from_name(name) -> int | None` (`NDCG@5` → 5, `OC@0.8` → None).

Для каждой строки results:
- **0.** `extraction_status ∈ {absent, present_not_extractable}` → строка **не трогается**: ни одна ячейка (включая `note`, `metric_*`, `normalized_*`) не меняется, событий нет. Так на `dev_gold` сохраняются 39 строк `absent` (`-`, `–`, `N/A`) и 8 строк `present_not_extractable`.
- **a.** ML прислал непустой `normalized_value` → не перезаписывать. Если он не согласуется с `raw_*`, записать событие `normalization` (`kind="mismatch"`). Согласованность: число из `raw_value` после снятия `%`, `+`, пробелов и дописывания ведущего нуля равно `normalized_value` по Decimal; или s → ms ×1000; или единица совпадает. `normalized_unit` тоже не меняется.
- **b.** `normalized_value` пуст (строка `extracted`) → посчитать по §6.6:
  - нечисло: после strip без учёта регистра одно из `-`, `--`, `–`, `—`, `−`, `�`, `n/a`, `na`, `nan`, `none`, `null`, `?` → пусто, в `note` (через `append_note`) — «нечисловое значение не нормализуется: `<raw>`»;
  - `.204` без единицы → `0.204` и `fraction`, только если шкала установлена: имя подходит под `RAG_SCALE_METRIC_RE`, и все значения этой метрики на той же странице и в той же `source_table` не больше 1. В `note` — «Leading zero added without scale conversion», событие `leading_zero`. Иначе — число как напечатано, единица пуста;
  - `24.8%` → `24.8` / `%`;
  - абсолютная разница со знаком % (ведущий `+`/`−`, и в имени, variant или note есть delta/difference/improvement/gain/drop) → `percentage_points` + событие `percentage_points`;
  - относительное изменение (relative в имени, variant или note) → `relative_percent`;
  - `0.8 s` → `800` / `ms`;
  - диапазон, `≈`, `~`, `>`, `<` → пусто, в `note` — «диапазон или приблизительное значение не нормализуется: `<raw>`»;
  - неизвестная шкала → число как напечатано, единица = `raw_unit` или пусто;
  - `raw_unit = "fraction (axis labeled %)"` не трогать.
- **c.** Только для строк из п. b: `metric_value := normalized_value`, `metric_unit := normalized_unit`.
- **d.** `metric_k` пуст, а в имени есть `@N` (`RAG_AT_K_RE`) → записать N, событие `metric_k_filled`. `top_k` не трогать, `metric_k` из него не брать.
- **e.** `meta.norm_status` (`kept` | `converted` | `empty` | `skipped`) и `meta.norm_note`.

**2. `faktura.rag.checks`.** `apply_checks(rb, *, quote=None, norm_events=(), ocr_low=0.8) -> CheckSummary(counts_by_check)`. Функция заполняет `rb.tables["issues"]` и `rb.issue_meta` только через `rb.add_issue`, в конце вызывает `rb.renumber_issues()`. `description` по-русски, числа с десятичной запятой. `source_page`/`source_fragment` замечания берутся у первой записи; цитата — только буквальный `source_fragment`, не адрес таблицы. Строки `absent`/`present_not_extractable` в числовых проверках не участвуют. `IssueMeta.scope` = `"<experiment_id>|<metric_name>"` первой записи для `metric_scale`, `metric_k`, `contradiction`, `protocol_mismatch`; для остальных — None.
- **`metric_scale`.** `metric_unit ∈ {fraction, %}`, имя по `RAG_SCALE_METRIC_RE` (fullmatch), значение вне [0, 1] / [0, 100]. Одно замечание на строку. Это то же условие, что у предупреждения скрипта «value outside declared metric scale».
- **`metric_k`.** (а) `metric_k` был пуст и взят из имени (событие `metric_k_filled`); (б) `metric_k` ≠ N из имени; (в) `metric_k` = `top_k` конфигурации строки и ≠ N.
- **`normalization`.** По событиям нормализации: одно замечание на (вид события, `source_page`, `source_table`); в `record_ids` не больше 50 id, остальные — числом в описании.
- **`protocol_mismatch`.** Строка с непустым `baseline_value`. Baseline-строки — тот же `experiment_id` и `metric_name`, конфигурация с `rag_variant` или `generator_model` = `experiments.baseline_name` (конфигурация **другая**, поэтому область пересчёта — (`experiment_id`, `metric_name`) без `config_id`). Baseline найден и `keys.comparability_key` различается → замечание на строку и baseline. Не найден → замечания нет.
- **`contradiction`.** Группы по `keys.contradiction_group_key` = (`experiment_id`, `config_id`, `dataset_id`, `task_id`, `metric_name`, `metric_variant`, `metric_k`, `metric_unit`, `aggregation`, `sample_size`, `result_origin`, `cited_publication_id`). Цепочки `duplicate_of` сворачиваются в действующую основную (`duplicate_groups`/`effective_main`). Если среди оставшихся есть значения, различные сверх округления (`not same_at_reported_precision`), — одно замечание на группу, не больше 50 id. Сравнение за O(n) по множеству различных значений. Парное замечание санитайзера (ingest, «повтор разошёлся с основной») поглощается групповым через правило (б) `add_issue`. Отдельно для повторов здесь замечаний не создавать.
- **`latency_scope`** (should). У эксперимента заполнены `latency_ms` или `tokens_per_query`, а в его results не меньше двух разных `config_id`. `record_ids` пусты, `add_issue` с `issue_key_for(row, {"experiment_id": e})`.
- **`ocr_low_confidence`** (should). `meta.ocr_confidence < ocr_low` при непустом `metric_value` → замечание с `IssueMeta.exported=False`.
- **Находки ML** принимаются как есть (`check_name` — открытый текст): `record_ids` фильтруются по существующим `result_id`; если записей не осталось и нет `source_fragment` с `source_page` — находка отбрасывается.
- **`quote_found`** при переданном `quote`: для каждой строки results с `source_fragment` и `source_page` → `meta["results"][rid].quote_found`; для каждого замечания → `issue_meta[iid].quote_found`. Ставить до `renumber_issues`: перенумерация переносит meta.

**3. `faktura.rag.keys`.** `comparability_key(row, rb) -> (str, bool)` — `task_type | dataset_name+dataset_version+data_split | metric_name+metric_variant+metric_k | metric_unit | evaluation_protocol`; bool — ключ полный. `comparability_reasons(row_a, rb_a, row_b, rb_b) -> list[str]`: «разные задачи», «разные наборы данных или разбиения», «разное определение метрики или k», «разные единицы», «разный протокол оценки». `contradiction_group_key(row) -> tuple`. `recheck_scope(row) -> str` = `f"{experiment_id}|{metric_name}"`.

**4. `faktura.rag.recheck`.**
```python
RECHECK_CHECKS_RAG = ("metric_scale", "metric_k", "contradiction", "protocol_mismatch")
class RecheckResult(BaseModel): scope: str; checks: tuple[str, ...]; issues: list[tuple[dict[str, str], IssueMeta]]
def recheck_group(rb: RowBundle, *, values: dict[str, str], rejected: set[str]) -> list[RecheckResult]
```
- **Вход `rb`** — **подмножество** статьи: все строки results одной или нескольких областей пересчёта (`experiment_id`, `metric_name`) и **полные** малые таблицы (`configurations`, `datasets`, `tasks`, `experiments`). Так `api_data` получает его через `storage.rows.load(paper_id, results_filter={"experiment_id": e, "metric_name": m})`. Строки baseline других конфигураций входят в эту выборку. Таблица `issues` не нужна.
- `values` — действующие значения (`corrected_value`) по `result_id`; они подставляются вместо `normalized_value`/`metric_value`. `rejected` — отклонённые записи: они **исключаются** из всех четырёх проверок, в том числе как baseline и как член группы противоречия.
- Результат — по одному `RecheckResult` на каждую область в `rb.results`, с полным набором замечаний этих четырёх проверок. Ключи замечаний (`meta.key`) — как при первичной проверке, то есть совпадают для неизменившихся случаев. `rb` не меняется (копируются только затронутые строки). Время пропорционально размеру `rb`, а не статьи.
- Сравнение с сохранённым набором делает вызывающий через `review_rules.diff_recheck`.

**5. solar: `faktura.checks.recheck`.** `RECHECK_CHECKS_SOLAR = BACKEND_RULES`; `recheck_solar(rb, *, values, rejected, rules=None) -> list[RecheckResult]` — `rb` — вся статья solar (статьи маленькие). Порядок: `to_paper_bundle`, удалить отклонённые результаты (и их id из связей), подставить `values` в `normalized_value` и `metric_value`, вызвать `run_backend_rules(bundle, rules or builtin_solar_profile().rules)` (все включённые правила), перевести замечания в `(row, IssueMeta(scope="paper", computed_by="backend"))` и вернуть один `RecheckResult(scope="paper", checks=RECHECK_CHECKS_SOLAR)`. Ключи — те же, что даёт `checks.assign_keys`.

**Тесты.** Фикстуры `rag_mini_bundles`, `rag_gold_bundles` (маркер `rag_data`), `registry_lint` (разрешённые файлы: нет — пакет `rag` работает только с rag, solar-код не сравнивает области).

**Приёмка.**
1. На `dev_gold` (все 21 статья, 37 616 строк): `normalize_rows` + `apply_checks` не меняют **ни одной** ячейки results (сравнение строк до и после — 100 %); события `mismatch` — только диагностика (их число записать в отчёт).
2. Все 134 замечания эталона сохраняются (тот же `check_name`, те же `record_ids`, описание).
3. `metric_scale`: множество строк с замечанием совпадает с множеством строк, на которых скрипт rag (функция `validate`, загруженная изолированно) даёт «value outside declared metric scale» на `dev_gold`.
4. Оракул: в `tests/rag/oracle.py` — наивная независимая реализация (O(n²)) `metric_k`, `contradiction`, `protocol_mismatch`. На всех статьях `dev_gold`, кроме D014, множества (`check_name`, frozenset(`record_ids`)) совпадают с `apply_checks`. На D014 оракул проверяется на случайных 20 областях пересчёта.
5. Снимок: `tests/rag/data/dev_gold_counts.json` — число замечаний сервиса по видам проверок и статьям. Он создаётся один раз, причины чисел описываются в отчёте, тест сравнивает с ним. Инварианты: `normalization` ≤ числа различных (вид, страница, таблица) событий; `contradiction` ≤ числа групп; нет замечаний сервиса на строках `absent`/`present_not_extractable`; нет двух замечаний с одинаковыми (`check_name`, множество `record_ids`).
6. Синтетика: `.204` + шкала → `0.204`/fraction с пометкой; без шкалы — как напечатано; `24.8%`; `+3.1%` с delta → `percentage_points`; `0.8 s` → `800 ms`; `0.330` остаётся `0.330`; `NDCG@5` с пустым `metric_k` → `5` и `metric_k`(а); `OC@0.8` → нет.
7. `recheck_group`: отклонение одной из двух противоречащих строк убирает `contradiction`; правка значения в пределах округления убирает его; правка baseline-строки другой конфигурации пересчитывает `protocol_mismatch`; отклонённая baseline не используется. Perf: самая большая область D014 (подмножество через фильтр по `experiment_id`, `metric_name`) ≤ 200 мс; вся D014 ≤ 10 с (только линейность).
8. `recheck_solar` на `mini_gold` без правок даёт те же ключи, что и `checks.check_paper`; отклонение записи снимает её замечания.
9. Нормализация и проверки на D014 ≤ 30 с (perf). `tests/norm`, `tests/checks` зелёные; `registry_lint` по `rag/`, `norm/`, `checks/` пуст.

### 8.2. `adapters` (L1) — ML-1 обеих областей, OCR-адаптер, `needs_ocr`, цитаты, области

Пути: `backend/src/faktura/ml/`, `backend/src/faktura/pdfio/`, `backend/src/faktura/ocr/` (новый), `backend/tests/adapters/`. Код волны 1 (мок solar, `HttpExtractor`, кэш, `collect_inputs`, `LocalParseAdapter`, `PypdfQuoteIndex`) зелёный: его расширить, а не переписывать. Глобальный `TABLES` в `ml/` заменить на `get_spec(domain)`.

Shared: `domain.rows` (`RowBundle`, `RowMeta`, `IssueMeta`, `read_rows_dir`, `RowBundle.empty`, `issue_key_for`), `domain.ocr` (`OcrResult`, `OcrPage`, `OcrWord`, `OcrAdapter`, `OcrUnavailable`, `OcrFailed`), `domain.domains` (`get_spec`, `rag_quote_norm`), `domain.extraction` (`ExtractOutcome.rows`, `ParsedPage.needs_ocr/ocr_used/ocr_confidence`, `ParsedDocument.scan/ocr/ocr_pages`), настройки `mock_gold_dir_rag`, `extract_rag_format`, `ocr_*`.

**1. ML-1 rag.**
- **Мок.** `MockGoldExtractor`: при `req.domain == "rag"` читает `settings.mock_gold_dir_rag` через `read_rows_dir(dir, "rag")` (кэш на экземпляр) и отдаёт `ExtractOutcome(rows=RowBundle, model_version="mock-dev_gold")`, строки как в эталоне. Статья вне эталона → `RowBundle.empty("rag", pid)`. Ключ — (`domain`, `pid`). `mock_issues=all|ml_only` действует и для rag (`ml_only` — только замечания эталона, это и есть всё, что есть в rag). solar не меняется.
- **HTTP.** `HttpExtractor` для rag — три формата (`settings.extract_rag_format`; `auto` — по `Content-Type`): `application/json` (объект `publication` и массивы `configurations`, `datasets`, `tasks`, `experiments`, `results`, `issues`); `application/x-ndjson` (строка на запись `{"table": …, …колонки}`, первая строка может быть `{"table": "_meta", "model_version": …}`; чтение потоковое, построчное); страницы `?part=N` с `next_part`.
- **Разбор** `parse_rag_response(obj | iterable_lines, publication_id) -> ExtractOutcome(rows=…)`: JSON читается с `parse_float=str, parse_int=str` (`0.330` остаётся `0.330`); `null` → `""`; bool → `"true"`/`"false"`; массив `record_ids` → `"R1;R2"`; `_confidence`, `_bbox` (пункты PDF → доли страницы `{page, rects}`), `_row_label`, `_row_text`, `_ocr_confidence` → `RowMeta`; неизвестные поля → `meta.extra`; недостающие колонки → `""`; `publication_id` ставится принудительно; `RowMeta.ord` — порядок строки. Замечания ML: `IssueMeta(key=issue_key_for(row), computed_by="ml", confidence=_confidence)`. Структурная валидация (словари, ссылки, числа) здесь **не** делается: это стадия «Проверка формата» (`export.sanitize_rows`). Битый JSON или не-объект → `ExtractError("ml_schema_mismatch")`.
- **Ошибки** — как в волне 1 (§7.0.1: 400, 413, 422, 429 + `Retry-After`, 502, 504 → `ExtractError`).
- **Кэш и raw.** Ключ `MlCache` и `RawDirExtractor` — (`domain`, `sha256`, `model_version`); raw rag хранится как JSON или NDJSON.

**2. OCR** (решение пользователя: бэкенд не распознаёт текст, контракт пришлёт ML). Пакет `faktura.ocr`:
- `contract.py` — `parse_ocr_response(obj) -> OcrResult` по предварительной схеме (`docs/wave2/api_contract.md` §10); `confidence` 0…100 → 0…1. Вся схема только здесь.
- `HttpOcrAdapter(url, timeout_s=600)` — `POST {url}/ml/ocr`, multipart `file`, `pages` (JSON), `language`, `dpi`, `sha256`. Нет соединения, 503 или таймаут → `OcrUnavailable`; 422/500 или битый ответ → `OcrFailed`; `partial_pages` пробрасывается.
- `MockOcrAdapter(gold_dir_rag, fixtures_dir)` — детерминированный: сначала фикстура `<sha256>.json` или `<pid>.json`; иначе текст страницы из `source_fragment` строк results и issues эталона rag на этой странице (слова с условными bbox, уверенность 0.9); иначе пустой текст, уверенность 0.
- `UnavailableOcrAdapter` — всегда `OcrUnavailable`.
- `OcrCache` — по (`sha256`, `engine`, `engine_version`, `pages`) в `cache_dir/ocr`.
- `build_ocr(settings) -> OcrAdapter`: `ocr_impl=auto` → `http` при `ocr_url`, `mock` при `extractor=mock`, иначе `unavailable`; `off` → `unavailable`.
- Tesseract из кода бэкенда не вызывать.

**3. `needs_ocr` по §11.2** (`pdfio/parse.py`). Страница `needs_ocr`, если: (1) текст pypdf **и** текст PyMuPDF после `rag_quote_norm` пусты оба; **или** (2) меньше 20 символов при изображении ≥ 70 % площади; **или** (3) больше 30 % символов U+FFFD/PUA. Старую эвристику «< 100 символов» убрать. `ParsedDocument.ocr_pages` = номера страниц `needs_ocr`; `ParsedDocument.scan = bool(ocr_pages)`; `needs_ocr` документа — то же самое. **Один набор** страниц служит и для OCR, и для признака скана.

`parse_with_ocr(parser, ocr, pdf_path, *, sha256, mode: "auto"|"off"|"force", language, dpi) -> ParsedDocument`:
- `auto` — распознаются ровно `doc.ocr_pages`; если набор пуст, OCR не вызывается;
- `force` — распознаются все страницы (`scan` по-прежнему = `bool(ocr_pages)`);
- `off` — OCR не вызывается, `doc.ocr = None`, предупреждение в `warnings`, если `ocr_pages` не пуст;
- заполняет `doc.ocr`, `pages[].ocr_used` (страница распознана), `pages[].ocr_confidence`; `OcrUnavailable` и `OcrFailed` пробрасывает; непустой `partial_pages` → предупреждение `ocr_partial`.

**4. Цитаты** (`pdfio/quotes.py`): `RagQuoteIndex(pdf_path, ocr: OcrResult | None)`. Варианты текста страницы: pypdf, PyMuPDF и, для распознанных страниц, текст OCR; каждый — через `rag_quote_norm`. `check(page, fragment)`: True, если нормализованная цитата — подстрока хотя бы одного варианта этой страницы (варианты и страницы не склеиваются); нет страницы или цитаты → None. `find_pages(fragment) -> list[int]`. Фабрика `quote_index(domain, pdf_path, ocr=None)` — словарь по области: solar → `PypdfQuoteIndex` (без изменений), rag → `RagQuoteIndex`.

**5. Области и имена** (`pdfio/inputs.py`):
- `assign_domains(files: list[InputFile], domain: "solar"|"rag"|"auto") -> DomainAssignment(by_domain: dict[str, list[InputFile]], rejected)`. При `auto` область — по относительному пути без учёта регистра: компонент `solar` или `caseB*` → solar, `rag` или `caseA*` → rag; не определилась → `DomainUnresolved` («укажите --domain»). Каждый файл ровно в одной области.
- `dedupe_names(..., mode="cli")`: дубль имени в области → `Rejected(code="duplicate_name")`; режим `ui`: суффикс `-2`, `-3` (как в волне 1), исходное имя сохраняется в `InputFile.original_name`.
- Пакет больше не бросает `zip_bomb` с 413: код в каталоге теперь 422, тесты обновить.

**Приёмка.**
1. Мок rag на `rag_mini_gold`: `ExtractOutcome.rows` по D001 совпадает с CSV байт в байт после записи (`0.330`, многострочные ячейки); статья вне эталона → `empty`.
2. `parse_rag_response`: JSON, NDJSON (поток из 30 000 строк без загрузки всего в память, ≤ 5 с) и `parts` дают одинаковый `RowBundle`; `_bbox` переводится в доли; неизвестное поле — в `extra`.
3. HTTP-ошибки 429/503/таймаут → `ExtractError` с `retryable`; кэш различает области.
4. OCR: `HttpOcrAdapter` на тестовом сервере (`httpx.MockTransport`) — 200, 503 → `OcrUnavailable`, 422 → `OcrFailed`, `partial_pages`; `MockOcrAdapter` детерминирован; `sample_ocr.json` разбирается, `broken_ocr.json` → `OcrFailed`.
5. `needs_ocr`: синтетические PDF (PyMuPDF) — пустая страница-картинка; страница с 10 символами и картинкой 80 %; страница с 40 % U+FFFD; обычная текстовая. Только первые три попадают в `ocr_pages`. `auto` распознаёт ровно их; `force` — все; `off` — ни одной, но `scan=True`.
6. `RagQuoteIndex`: цитата находится только на своей странице; текст OCR используется только для распознанных страниц.
7. `assign_domains`: `caseA_drive/dev/D001.pdf` → rag, `solar/x.pdf` → solar, `misc/x.pdf` → `DomainUnresolved`.
8. Старые тесты `tests/adapters` зелёные; `registry_lint` по `ml/`, `pdfio/`, `ocr/` пуст (разрешены файлы с фабриками по области, перечислить в тесте).

### 8.3. `storage` (L1) — строки, запросы, UI-разборы, правки разбора

Пути: `backend/src/faktura/storage/`, `backend/tests/storage/`. SQLAlchemy Core; SQLite (CLI и тесты) и PostgreSQL. Существующие таблицы и репозитории волны 1 не ломать (`tests/storage` зелёные, допустимы правки под новые поля). Новые таблицы — через `metadata.create_all`, без миграций. Глобальный `TABLES` в `storage/` заменить на `get_spec(domain)`.

Shared: `domain.rows` (`RowBundle`, `RowMeta`, `IssueMeta`, `num`, `split_list`), `domain.review_rules` (`issue_state`, `display_status`, `effective_value`), `domain.entities` (`ResultReview`, `IssueMark`, `ExtraIssue`, `RunFileStatus`, `UiRunStatus`, `Corpus`, `CorpusFile`), настройки `rows_batch`, `total_exact_max`, `sqlite_busy_timeout_ms`.

**Схема:**

| Таблица | Изменения или колонки |
|---|---|
| `corpora` | `+ title, pinned, demo, owner_id, updated_at`; `kind` += `ui`, `cli` |
| `corpus_files` | `+ domain, scan, ocr_confidence, stage, progress, stopped, reason, original_name` |
| `papers` | `collection` = область; `+ scan bool, ocr JSON`; индекс (`workspace_id`, `collection`, `sha256`, `model_version`) |
| `paper_rows` (новая) | `paper_id, table_name, record_id, ord, data JSON (dict str→str), meta JSON`; индекс (`paper_id`, `table_name`, `ord`) — 5 малых таблиц и `publications` |
| `result_rows` (новая) | `paper_id, domain, publication_id, ord, result_id`; индексируемые `metric_name, metric_group, metric_variant, metric_k int, metric_unit, aggregation, result_origin, extraction_status, duplicate_of, experiment_id, config_id, dataset_id, task_id, device_id, measurement_id, material_id, source_page int`; `value_num float` (= `num(normalized_value)`); `confidence, ocr_confidence, quote_found`; `search_text` (lower: `raw_value`, `source_fragment`, `metric_name`); `data JSON`; `meta JSON`. Индексы: (`paper_id`, `ord`) unique, (`paper_id`, `result_id`) unique, (`paper_id`, `experiment_id`, `metric_name`), (`paper_id`, `metric_name`), (`paper_id`, `config_id`), (`paper_id`, `dataset_id`), (`paper_id`, `duplicate_of`), (`domain`, `metric_name`, `value_num`) |
| `issue_rows` (новая) | `paper_id, issue_id, ikey, check_name, source_page, computed_by, scope, exported, data JSON, meta JSON`; индексы (`paper_id`, `ikey`), (`paper_id`, `scope`) |
| `issue_records` (новая) | `paper_id, ikey, result_id` — для автозакрытия и счётчиков в SQL |
| `run_reviews` (новая) | `run_id, paper_id, domain, publication_id, result_id, status, corrected_value str, corrected_num float, by, by_name, at, match_key JSON, unmatched`; PK (`run_id`, `paper_id`, `result_id`) |
| `run_issue_marks` (новая) | `run_id, paper_id, ikey, issue_id, state, by, at, unmatched`; PK (`run_id`, `paper_id`, `ikey`) |
| `run_issue_extra` (новая) | `run_id, paper_id, scope, ikey, op ('add' \| 'suppress'), data JSON, meta JSON`; индекс (`run_id`, `paper_id`, `scope`) |
| `actions` | `+ run_id`; `prev` хранится в `changes` |

**Интерфейс** (атрибуты `UnitOfWork`; типы — в `storage/_types.py` или `storage/rows.py`):
- **`uow.rows`:**
  - `save(paper_id, rb)` — заменяет строки статьи; `executemany` пачками по `settings.rows_batch`, без ORM-объекта на строку; одна транзакция; заполняет `issue_records` и колонки `scope`/`computed_by`/`exported` из `IssueMeta`;
  - `load(paper_id, *, tables=None, results_filter: dict[str, str | list[str]] | None = None) -> RowBundle` — `tables=None` → все 7. `results_filter` — равенство или IN по индексируемым колонкам `result_rows` (`experiment_id`, `metric_name`, `config_id`, `dataset_id`, `task_id`, `result_id`, `duplicate_of`); иначе `validation_error`. Малые таблицы всегда целиком. `issues` загружаются, только если указаны в `tables`;
  - `duplicate_chain(paper_id, rid) -> list[dict]` — корень цепочки и все строки, ведущие к нему (итеративно по индексу `duplicate_of`);
  - `results_page(paper_id, *, limit, cursor) -> (list[(row, meta)], next_cursor)`; `count_results(paper_id)`;
  - `paper_results_summary(paper_id) -> list[{metric_name, metric_group, config_id, dataset_id, experiment_id, count, min, max, pages}]`;
  - `summary(paper_ids) -> {by_metric, by_config, by_dataset, by_experiment, by_page, total}` (`GROUP BY` в БД).
- **`uow.results.query(q: ResultQuery) -> ResultPage`.** `ResultQuery`:
  - контекст: `domain`, `run_id | None`, `owner_id`, `paper_ids` (не заданы → по одной последней готовой версии на (`domain`, `pid`) из разборов владельца);
  - фильтры: `pids[]`, `metric[]`, `scored`, `config_id[]`, `dataset_id[]`, `task_id[]`, `experiment_id[]`, `metric_group`, `metric_variant`, `metric_k`, `metric_unit`, `device_id`, `measurement_id`, `material_id`, `q` (подстрока `search_text`), `num_filters: list[(metric, op, value)]`, `year_min/year_max`, `aggregation[]`, `result_origin[]`, `extraction_status[]`;
  - по действующему значению: `min`/`max` (`COALESCE(run_reviews.corrected_num, value_num)`), `review` (`new|accepted|rejected|corrected`), `has_issues` (открытые с учётом отметок, автозакрытия и `extra`); эти три требуют `run_id`;
  - `duplicates` (`include|exclude`), `sort` (`publication_id|device|metric|value|page|issues`), `dir`, `limit` ≤ 1000, `cursor`.

  `ResultPage(data: list[ResultRowOut(paper_id, domain, publication_id, ord, row, meta, review, issue_ids, device_label, paper_title)], total, total_estimate, next_cursor)`. `total` точный, пока ≤ `total_exact_max`, иначе `total=None` и `total_estimate`. Курсор — значения сортировки плюс (`domain`, `pid`, `ord`), подписанный; чужой или битый → `validation_error`. `uow.results.group(q, by) -> list[{key, count, min, max, unit, pages}]`. `uow.results.iter_keys(q) -> Iterator[(paper_id, result_id)]` — для пакетной правки по фильтру (постранично).
- **`uow.issues_view`:**
  - `list(q: IssueQuery(domain, run_id, owner_id, paper_ids, pids, check_name[], record_id, state=all|open, sort, limit, cursor)) -> IssuePage` — элементы `row`, `meta`, `state`, `state_auto`, `resolved_at`, `resolved_by`, `paper_title`. Базовые замечания объединяются с `run_issue_extra` разбора: `suppress` скрывает замечание по `ikey`, `add` добавляет. Состояние — `review_rules.issue_state`;
  - `base_for_scope(paper_id, scope) -> list[(row, IssueMeta)]` — сохранённые замечания области пересчёта;
  - `stats(run_id, domain, paper_ids) -> [{check_name, count (открытые), papers}]`;
  - `open_counts(run_id, paper_ids) -> {paper_id: n}` — в SQL через `issue_records`, `run_reviews`, `run_issue_marks`, `run_issue_extra`.
- **`uow.reviews`:** `get`, `upsert(review) -> prev | None`, `delete -> prev`, `batch_upsert(list) -> list[prev]`, `delete_all(run_id) -> list`, `list(run_id, paper_id=None, result_ids=None)`, `restore(list)`.
- **`uow.issue_marks`:** `get`, `set -> prev`, `delete`, `delete_all(run_id)`, `list`, `restore`.
- **`uow.issue_extra`:** `replace(run_id, paper_id, scope, items: list[ExtraIssue]) -> prev`, `list(run_id, paper_id)`, `delete_all(run_id)`, `restore`.
- **`uow.ui_runs`:** `create(owner_id, workspace_id, domain, title, demo=False) -> UiRunRecord` (корпус `kind=ui` + строка `runs`; `Run.id` = id корпуса с префиксом `run_`); `get(run_id, owner_id=None)` (чужой или удалённый → None); `list(owner_id, *, domain=None, q=None, limit, cursor)` — порядок `pinned desc, created_at desc, id desc`, keyset; `update`; `soft_delete(run_id) -> list[paper_id]` (статьи — только если не входят в другие разборы); `restore`; `files(run_id)`; `counts(run_id) -> {files, queued, running, done, out, ocr, error, nodata}`; `paper_versions(owner_id, domain) -> {pid: paper_id}`; `find_demo(owner_id, include_deleted=True)`.
- **`uow.papers.find_version(workspace_id, domain, sha256, model_version)`.**
- **`uow.stats.run_summary(run_id) -> dict`** (§15.3): `files`, `papers_in_scope`, `papers_out`, `files_without_results`, `files_ocr`, `files_error`; `results` (без отклонённых), `scored_results` (solar — `spec.scored_metrics`, rag = `results`); `issues_total`, `issues_open`, `reviewed`, `accepted`, `corrected`, `rejected`, `edit_count` (правки + отметки); `coverage` (solar — 11 метрик `{metric, n, total, results}`; rag — каждый `metric_group`, затем 11 самых частых `metric_name`); `issues_by_check [{check_name, total, open}]`.
- **`uow.actions.record(..., run_id=…)`** — уже есть; `prev` кладётся в `changes`.

**Надёжность:** SQLite `PRAGMA journal_mode=WAL`, `busy_timeout = sqlite_busy_timeout_ms`, `foreign_keys=ON`; `Database.write_lock` (`threading.RLock`) — пишущая UoW держит его всю транзакцию; `check_same_thread=False`; `Database.close()` закрывает всё; фоновых потоков нет.

**Приёмка.**
1. `save` + `load` на `rag_mini_bundles` и solar `mini_gold` → тот же `RowBundle` (строки байт в байт, meta, `issue_meta` с `scope` и `quote_found`).
2. `load(results_filter={"experiment_id": e, "metric_name": m})` возвращает и строки других `config_id` (baseline); неизвестная колонка фильтра → `validation_error`.
3. `query`: все фильтры, сортировки и keyset без пропусков и повторов на 5 000 строк; `review`/`min`/`max`/`has_issues` с `run_id`; `extra` (`add`/`suppress`) влияет на `has_issues`, `open_counts`, `stats`.
4. Автозакрытие: все `record_ids` отклонены или исправлены → `fixed`, `state_auto=True`; снятие правки снова открывает.
5. Две разные записи `run_reviews` для одной версии статьи в разных разборах не видят друг друга; `delete_all(run_id)` затрагивает только свой разбор.
6. Параллельно 4 потока пишут разные статьи → без `database is locked`.
7. Perf (синтетика 28 649 строк): `save` ≤ 15 с; `query` (100 строк, фильтр `config_id`) ≤ 300 мс; `summary` ≤ 1 с; `open_counts` ≤ 300 мс; `load` области пересчёта (2 000 строк) ≤ 150 мс.
8. Старые `tests/storage` зелёные; `registry_lint` по `storage/` пуст.

### 8.4. `export` (L1) — сдача по шаблону области, санитайзер, правки, скрипты, `_meta`, паспорт, оценка

Пути: `backend/src/faktura/export/`, `backend/src/faktura/passport/`, `backend/src/faktura/evaluation/`, `backend/tests/export/`. Код solar волны 1 (`sanitize`, `number_issues`, `strict_validate`, `run_check_script`, `write_meta`) зелёный: переиспользовать через `domain.rows.to_paper_bundle`/`from_paper_bundle`, а не переписывать. Глобальный `TABLES` убрать из своих путей. Диспетчеризация по области — словарями.

Shared: `domain.domains` (`get_spec`, `RAG_DUP_FIELDS`, `SAFE_ID_RE`, `same_at_reported_precision`), `domain.rows` (`RowBundle`, `add_issue`, `renumber_issues`, `rename_publication`, `split_list`), `domain.review_rules` (`REVIEW_NOTE`, `append_note`, `duplicate_groups`, `effective_main`, `issue_state`), `domain.entities` (`ResultReview`, `ExtraIssue`, `CheckReport`), настройки `tesseract`, `ocr_lang`, `check_*`, `eval_rag_cmd`.

**1. Писатель.** `write_domain_tables(domain, tables, out_dir) -> list[Path]`, `table_bytes(domain, table, rows) -> bytes`: колонки и порядок — `get_spec(domain).tables`; solar — UTF-8 с BOM, rag — без BOM (К2-3); разделитель `,`, `csv.QUOTE_MINIMAL`; перевод строки — как в файлах шаблона (сверить байты `data/caseA_drive/submission_template/*.csv` и шаблона solar); многострочные ячейки экранирует модуль `csv`.

**2. Санитайзер.** `sanitize_rows(domain, bundles, *, stage: "ingest"|"export") -> SanitizeResult(bundles, events)` — словарь по области. solar: `ingest` — без изменений (ответ ML уже проверен разбором ответа), `export` — `sanitize` + `number_issues` волны 1 через конвертацию. rag — `sanitize_rag(bundles, stage)`. Цель: на любом ответе ML скрипт rag даёт 0 ошибок. Порядок:
- **Словари.** `in_scope`: `""` → `true` (раздел 1.1 п.3); `false` без причины → `exclusion_reason` = «причина не указана моделью»; `true` с причиной → причина в лог, поле очищается. `result_origin` пуст или вне словаря → `cited`, если есть `cited_publication_id`, иначе `primary`. `extraction_status` пуст или вне словаря → `extracted`, если есть `raw_value`, `source_page` и один из `source_fragment`/`source_table`/`source_figure`, иначе `present_not_extractable`. Исходное значение пишется в `note`. Пустой `metric_name` → `unspecified`.
- **Числа** по `RAG.numeric`: `positive_int`, `nonneg_int` (только `[0-9]+`), `nonneg`, `finite`. Нечисло, NaN или inf → `""`, исходник в `note` (у таблиц без `note` — в `events`). Записи `26.2M`, `47.4k`, `↓18.4%` допустимы только в `raw_value`.
- **Метрика** (только `stage="export"`): `metric_value = normalized_value`, `metric_unit = normalized_unit`.
- **`in_scope=false`** → строки шести остальных таблиц удаляются (с событием).
- **Id** по `SAFE_ID_RE`: недопустимые символы → `_`, ссылки переименовываются каскадно; повтор первичного ключа → `X-2`.
- **Ссылки.** Висячие `experiment_id`, `config_id`, `dataset_id`, `task_id`, `duplicate_of` и id в `record_ids` очищаются с событием.
- **`cited`/`primary`.** `cited` без `cited_publication_id` → `unspecified`, в `note` — «источник цитирования не указан». `primary` с `cited_publication_id` → id переносится в `note`.
- **`extracted`.** Нет `raw_value` → взять `metric_value`; нет страницы или локатора → `present_not_extractable`.
- **`duplicate_of`.** Самоссылка и цикл → связь снимается. Хотя бы одно поле `RAG_DUP_FIELDS` расходится (`metric_value` — через `same_at_reported_precision`) → связь снимается, и через `rb.add_issue` заводится `contradiction` (`record_ids` — повтор и основная, `IssueMeta(computed_by="backend", scope="<experiment_id>|<metric_name>")`). Дублей не будет: `add_issue` сливает одинаковые и поглощает подмножества, а групповое замечание `rag.apply_checks` появляется позже и поглощает пару.
- **Замечания.** `source_page` не положительное целое → `""`; цитата без страницы → очищается; нет ни записей, ни цитаты → замечание удаляется; пустой `check_name` → `unspecified`; при неуникальных `issue_id` → `rb.renumber_issues()`.
- Стадия `ingest` на эталоне — тождество: 0 изменённых ячеек.

**3. Правки разбора** (§15.6, §15.7, §15.12):
```python
apply_reviews(rb, reviews: dict[rid, ResultReview], *, issue_states: dict[ikey, str], extra: list[ExtraIssue] = (),
              selected: set[str] | None = None, export_absent: bool) -> tuple[RowBundle, ExportCounts]
```
- Сначала `extra`: `suppress` удаляет замечание с этим `ikey`, `add` добавляет через `add_issue`.
- `rejected` → строка удаляется; её id вычищается из `record_ids`. Замечание без записей остаётся, только если у него есть `source_fragment` и `source_page`. Цепочки `duplicate_of` — через `duplicate_groups`/`effective_main`: первый неотклонённый повтор становится основной, остальные перенаправляются на него.
- Исправление: `normalized_value = metric_value = corrected_value`; `raw_*` без изменений; к `note` через `append_note` — `REVIEW_NOTE.format(old=<прежнее normalized_value>)`. Повторы основной получают то же значение и пометку.
- `accepted` и `new` выгружаются как есть.
- Замечания: состояние `ok` не выгружается; `open` и `fixed` выгружаются; `IssueMeta.exported=False` не выгружается.
- `selected` (`D001:R002,…` → множество rid этой статьи): в `results` и `record_ids` остаются только выбранные неотклонённые записи; остальные таблицы — целиком.
- Статья вне области → только `publications`. solar: `absent` не выгружается при `export_absent=False` (правило волны 1); rag: `absent` от ML выгружается.
- `ExportCounts {tables: 7, results, rejected, papers}`.

**4. Сборка.**
```python
class SubmissionItem(BaseModel): rb: RowBundle; reviews: dict[str, ResultReview] = {}; issue_states: dict[str, str] = {}
                                 extra: list[ExtraIssue] = []; export_pid: str | None = None   # имя статьи в выгрузке (К2-14)
build_submission(domain, items, out_dir, *, selected: dict[pid, set[rid]] | None = None) -> SubmissionResult(paths, counts, events, pid_map)
submission_zip(domain, items, *, selected=None, check_report: str | None = None) -> tuple[Path, ExportCounts]
dataset_json_stream(run_dict, domain, items) -> Iterator[bytes]       # {run, 7 таблиц, reviews}
submission_filename(title) -> str                                     # «Набор для сдачи — {title}.zip»
```
- Порядок: `apply_reviews` → `rb.rename_publication(export_pid)`, если `export_pid` задан и отличается → `sanitize_rows(stage="export")` → `strict_validate` → запись. `selected` задаётся ключами API (исходный `pid`). `pid_map` — {pid API → pid в выгрузке}; по нему вызывающий строит `prepare_pdfdir`.
- `submission_zip` — временный файл ZIP ровно из 7 CSV (плюс `check_report.txt`), собирается потоково, без сборки всего в памяти.

**5. Валидатор и скрипты.**
- `strict_validate(domain, tables)` — для rag повторяет правила `validate()` из `data/caseA_drive/tools/check_submission.py` (без PDF).
- `prepare_pdfdir(domain, pdfs: dict[export_pid, Path], tmp) -> Path` — ровно PDF области с именами `{export_pid}.pdf` (hardlink, иначе копия).
- `run_check(domain, script, csv_dir, pdf_dir, *, ocr: bool, tesseract: Path | None, ocr_lang="eng", json_report: Path | None, timeout_s) -> CheckReport`: команда `sys.executable -I script csv pdfdir [--ocr --tesseract <ABS> --ocr-language eng] [--json-report path]`, окружение `PYTHONUTF8=1`. rag — разбор JSON-отчёта (`errors`, `warnings`, `quotes.requested/matched/unresolved`, `exit_code`); solar — как в волне 1. Статус: 0 → ok, 2 → warnings, 1 → failed, падение или таймаут → crashed. `CheckReport.ocr` — был ли `--ocr`.
- `run_check_sharded(..., shards)` (should) — разбиение по публикациям на временные `csv` и `pdfdir`, параллельные процессы, слияние (суммы, конкатенация, код по приоритету 1 > 2 > 0).

**6. `_meta`.** `write_meta_v2(out_root, *, manifest, files_rows, facts_ui, errors, passports: dict[domain, (json, md)], check_reports: dict[domain, CheckReport])`: `manifest.json` с блоком `domains.{solar, rag}` — `result {publications, in_scope, results, issues, files_failed, files_ocr}`, `check {status, exit_code, errors, warnings, quotes_requested, quotes_matched, quotes_unresolved, ocr}`; `files.csv` (`domain, name, sha256, status, scan, ocr_confidence, publication_id, results, issues, error`); `passport_<domain>.json/.md`; `check_report_solar.txt`, `check_report_rag.json`; `facts_ui.json` (по области и pid: `confidence`, `bbox`, `quote_found`, `ocr_used`, `ocr_confidence`); `errors.log`.

**7. Паспорт.** `build_passport(domain, …)`: поля 7 таблиц и правила с `executor` «Сервис разбора» | «Модель» (§15.15); для solar добавить `executor`.

**8. Оценка (must, W2-017).** `evaluation.build_evaluator(settings, domain) -> EvalAdapter` — словарь по области: solar — локальная оценка волны 1 (без изменений); rag — `FakturaEvalRag(cmd, timeout_s)`: `subprocess` `[*shlex.split(cmd), "--domain", "rag", "--gold", <gold_dir>, "--pred", <pred_dir>, "--json-report", <tmp.json>]`, разбор JSON в `EvalReport` (предварительная схема `{metrics: {name: value}, per_publication: {...}}`, меняется в одном месте `evaluation/rag_contract.py`). Команда — `settings.eval_rag_cmd`, иначе `shutil.which("faktura-eval")`. Нет инструмента → `EvalToolError(AppError, code="eval_unavailable")` с текстом «Оценка области rag недоступна: не найден инструмент faktura-eval (задайте FAKTURA_EVAL_RAG_CMD)». Ненулевой код или битый JSON → `EvalToolError` с выводом stderr в `details`.

**Приёмка.**
1. Roundtrip (W2-017): `read_rows_dir(dev_gold rag)` → `build_submission("rag")` → файлы равны эталону по строкам (сравнение множеств строк по таблицам, порядок строк и нумерация `issue_id` не важны); `0.330` сохраняется. Скрипт rag на результате без PDF — 0 ошибок. То же для solar `dev_gold` (как в волне 1).
2. Байты: rag-CSV без BOM, solar — с BOM; перевод строки совпадает с шаблоном.
3. Санитайзер на 30 «вредных» ответах (вне словаря, `NaN`, `26.2M` в `metric_value`, висячие ссылки, цикл `duplicate_of`, разошедшийся повтор, `in_scope=false` со строками, плохие id, дубли `issue_id`) → скрипт rag 0 ошибок; на разошедшемся повторе ровно одно `contradiction`; `sanitize_rows(stage="ingest")` на `dev_gold` — 0 изменений.
4. `apply_reviews`: отклонение основной переводит повторы на первый неотклонённый; исправление пишет «Исправлено вручную: было X» и в основную, и в повторы; замечание без записей с цитатой остаётся, без цитаты — удаляется; `ok` не выгружается; `extra` `suppress`/`add` учитываются; скрипт rag после правок — 0 ошибок.
5. `export_pid`: элемент с `pid="P042-2"` и `export_pid="P042"` даёт строки с `P042` во всех 7 таблицах; `pid_map` верен; `prepare_pdfdir` с этими именами — скрипт сверяет набор PDF без ошибок.
6. `run_check` на фиктивном скрипте (`sys.executable` + временный .py) различает коды 0/1/2, таймаут → crashed; реальный скрипт rag на `rag_mini_gold` без `--ocr` — разбор JSON-отчёта (маркер `rag_data`).
7. `submission_zip`: ровно 7 CSV (+ `check_report.txt`), память не растёт линейно (поток), имя файла по-русски.
8. Оценка: фиктивный `faktura-eval` (скрипт, пишущий JSON) → `EvalReport`; нет команды → `EvalToolError("eval_unavailable")`; solar — как раньше.
9. Старые `tests/export` зелёные; `registry_lint` по `export/`, `passport/`, `evaluation/` пуст (разрешены файлы-диспетчеры, перечислить в тесте).

### 8.5. `pipeline` (L2) — конвейер обеих областей, CLI, UI-разборы, события

Пути: `backend/src/faktura/pipeline/`, `backend/src/faktura/cli/`, `backend/src/faktura/__main__.py`, `backend/tests/pipeline/`.

Зависимости (готовы до старта): `rag` (`normalize_rows`, `apply_checks`, `recheck_group`; `checks.recheck.recheck_solar`), `adapters` (`MockGoldExtractor` обеих областей, `build_ocr`, `parse_with_ocr`, `quote_index`, `assign_domains`, `dedupe_names`), `storage` (`uow.rows`, `uow.ui_runs`, `uow.reviews`, `uow.issue_marks`, `uow.issue_extra`, `uow.papers.find_version`), `export` (`sanitize_rows`, `build_submission`, `prepare_pdfdir`, `run_check`, `run_check_sharded`, `write_meta_v2`, `build_evaluator`), shared (`STAGES_RU`, `RunFileStatus`, `STATUS_LABELS_RU`, `RowBundle.failed_stub`, `review_rules.review_match_key`/`values_close`).

**Этап P0.**

**0. Зависания.** Сначала найти и устранить причину зависания `tests/pipeline`. Вероятные места: реальные `sleep` в ожидании ML (`retry_delays` 30/120/600, пауза `_MlGate`), незакрытый `ThreadExecutor`, ожидание подписки `EventSubscription`, блокировки SQLite. В тестах задержки — `()` или подменённый `sleep`; пулы закрываются в `finally`/`shutdown(wait=True)`; таймаут 120 с на тест; весь каталог ≤ 10 минут.

**1. Обработчики областей.** `pipeline/domains.py` — единственное место с выбором по области: `HANDLERS = {"solar": SolarHandler(), "rag": RagHandler()}` с общим интерфейсом `extract(req) -> ExtractOutcome`, `validate(rb) -> (rb, events)`, `normalize(rb) -> events`, `check(rb, quote, norm_events)`, `recheck(rb, *, values, rejected) -> list[RecheckResult]`.
- solar: валидация — `export.sanitize_rows("solar", stage="ingest")`; нормализация и проверки — код волны 1 (`apply_normalization`, `check_paper`, связывание повторов) на `PaperBundle`, затем `from_paper_bundle`; `recheck` → `checks.recheck.recheck_solar`.
- rag: `export.sanitize_rows("rag", stage="ingest")` → `rag.normalize_rows` → `rag.apply_checks(quote=adapters.quote_index("rag", pdf, doc.ocr), norm_events=…, ocr_low=settings.ocr_low_confidence)`; `recheck` → `rag.recheck_group`.

**2. Файл.** Стадии `STAGES_RU`: «Разбор PDF» (`ocr_pages` по §11.2) → «Распознавание скана» (выполняется, если `ocr_mode == "force"` или (`ocr_mode == "auto"` и `doc.ocr_pages` не пуст); через `adapters.parse_with_ocr` и `Services.ocr`; при `off` стадия пропускается) → «Извлечение» → «Проверка формата» (W2-060: `handler.validate`; ответ ML без 7 таблиц или не того вида → `error` «ответ модели не по формату») → «Нормализация» → «Проверка» → `uow.rows.save` и поля версии (`scan`, `ocr`, `model_version`). В БД попадают только строки, прошедшие «Проверку формата».

| Исход | Статус файла | Подробности |
|---|---|---|
| `OcrUnavailable` | `ocr` | `reason` «Распознавание скана недоступно — перезапустите файл позже»; разбор продолжается; в выгрузке `failed_stub` |
| `OcrFailed`, ошибка ML, ошибка формата | `error` | причина по-русски; в выгрузке `failed_stub` |
| `ocr_partial` | `done` | с предупреждением в `reason` |
| готово, `in_scope=false` | `out` | `reason = exclusion_reason` |
| готово, 0 строк results | `nodata` | |
| готово | `done` | скан: `scan=true`, `ocr_confidence` = `doc.ocr.mean_confidence` |

Сбой файла не останавливает прогон. Версия статьи переиспользуется: если для (`workspace`, `domain`, `sha256`, `model_version`) уже есть готовая, файл ссылается на неё без вызова ML.

**3. Конкурентность.** `run_concurrency` = 4 потока; запись через единственного писателя storage; детерминированное завершение; отмена.

**4. CLI** (spec/13 §13.6, `docs/wave2/api_contract.md` §8):
- опции волны 1 сохраняются; добавляются `--domain solar|rag|auto` (по умолчанию solar), `--check` (флаг; пути из `settings.check_script` / `check_script_rag` или `--check-solar` / `--check-rag`), `--tesseract`, `--ocr-lang`, `--check-no-ocr`, `--ocr auto|off|force`, `--ocr-url`, `--gold-dir-rag`;
- порядок: `collect_inputs` → `assign_domains` (`DomainUnresolved` → код 1 «укажите --domain») → `dedupe_names(mode="cli")` → для каждой области корпус `kind=cli` и обработка → `out/<domain>/` 7 CSV через `export.build_submission` → общий `_meta` через `export.write_meta_v2`;
- каждому принятому PDF области — строка `publications` (`RowBundle.failed_stub` для `error`/`ocr`);
- `--check`: `export.prepare_pdfdir` (только PDF области, принятые, имена = `publication_id`) и `export.run_check`. rag — с `--ocr`, если Tesseract найден (`--tesseract` или `settings.tesseract`, абсолютный путь), таймаут `check_timeout_rag_s` = 3600, можно `run_check_sharded`. Без Tesseract или с `--check-no-ocr` — без `--ocr`, в stderr предупреждение «Проверка rag выполнена без OCR: <причина>», в manifest `domains.rag.check.ocr=false`;
- коды выхода: 0 — успех; 2 — есть файлы `error`/`ocr` или скрипт rag вернул 2; 3 — любой скрипт вернул 1, упал или превысил таймаут; 1 — ошибка запуска. Приоритет 1 > 3 > 2 > 0. При коде 2 в stderr строка причины: «Код 2: файлов с ошибкой N, без OCR M; цитат требует проверки K» и, если проверка шла без `--ocr`, добавка «; проверка без OCR»;
- прогресс в stderr: `[ 61/148] done  name.pdf  results=16 issues=1`;
- `faktura eval --domain solar|rag --pred <out/domain> [--gold PATH]` → `export.build_evaluator`; `EvalToolError` → сообщение по-русски, код 1;
- `faktura import-raw`, `faktura check --domain`, `faktura users` сохраняются.

**Этап P1.**

**5. UI-разборы** (`RunService`, §15.2–15.3):
- `create_ui_run(user, *, domain, title, inputs) -> (run_id, rejected)`. Title по умолчанию: имя ZIP или папки; иначе «N статей · D мес» (1 статья, 2 статьи, 5 статей; янв…дек). Обработка стартует сама через `Services.executor`.
- `add_files(user, run_id, inputs) -> (added, rejected)` — тот же SHA-256 в разборе → `duplicate`; совпадение имени с другим содержимым → суффикс `-2`, `original_name` сохраняется.
- `stop(user, run_id, *, cancel_running)` — неначатые остаются `queued` с `stopped=True` («остановлено»); статус разбора `stopped`.
- `restart(user, run_id, *, files, keep_review=True)` — правки переносятся по `review_match_key` + `values_close` (0,5 %), а не по rid; несовпавшие сохраняются с `unmatched=True`. Отметки замечаний — по `ikey`. `extra` области пересчитывается заново через `handler.recheck`.
- `ensure_demo_run(user, *, force=False) -> run_id | None` — solar dev (21 PDF из `settings.case_dir/dev`, мок ML-1), title «Демо — dev, 21 статья с эталоном», `demo=true`. Создаётся один раз на пользователя; удалённый не пересоздаётся без `force`. Переиспользует версии статей и **никогда не копирует правки, отметки и `extra`** из других разборов (W2-078).
- `get_run_for(user, run_id)` → 404 `AppError(not_found)` для чужого.
- Статус разбора: `queued`/`running`/`done`/`stopped`/`error` (`error` — все файлы `error`).
- `export_items(run_id, *, selected=None, partial=False) -> (items: list[SubmissionItem], pdfs: dict[export_pid, Path])` — для `api_runs`: строки, правки, состояния (`issue_state`), `extra` и `export_pid = export_publication_ids(файлы разбора)[pid]`; разбор идёт и не `partial` → `AppError("run_in_progress")`.

**6. События** — на `Services.bus` (по `workspace_id`, в `data` всегда `run_id`): `file {id, status, progress, stage, results, issues, reason, scan, ocr_confidence}` — на каждой стадии; `run {status, counts}` — при смене счётчиков; `done {status, counts, results, issues}` — в конце.

**7. Представления** (`pipeline/views.py`): `run_view(uow, run_id, *, files="full"|"short"|"none") -> dict` и `run_file_view(file) -> dict` — формы Run и RunFile из `api_contract.md` §2.1–2.2 (`status_label` из `STATUS_LABELS_RU`, `open_issue_count` с учётом отметок и `extra`).

**8. Помощник для тестов API** (`pipeline/testing.py`): `seed_run(services, user, *, domain, gold_dir, pids, pdf_dir=None) -> run_id` — готовый UI-разбор на моках, обработанный синхронно. Если PDF нет, генерируется крошечный PDF (PyMuPDF). Для rag в выборку попадают часть D014 и скан.

**Приёмка.**
1. `tests/pipeline` без зависаний: весь каталог ≤ 10 мин, ни одного теста > 120 с.
2. CLI solar на dev (маркер `data`): 7 CSV с BOM, скрипт области 1 — 0 ошибок, код CLI 0.
3. CLI rag на dev (маркеры `rag_data`, `slow`, timeout 3600): 7 CSV без BOM; с Tesseract (фикстура `tesseract_path`) — команда скрипта содержит `--ocr`, `manifest.domains.rag.check.ocr=true`, ошибок скрипта 0, код скрипта 0 или 2, код CLI 0 или 2.
4. Без Tesseract или с `--check-no-ocr` (фиктивный скрипт, быстро): `manifest.domains.rag.check.ocr=false`, в stderr есть «Проверка rag выполнена без OCR», строка причины кода 2 содержит «проверка без OCR»; код CLI 2 не путается с файлами `error`: в manifest раздельные `files_failed`, `files_ocr`, `quotes_unresolved`.
5. CLI rag на test (мок, 90 PDF вне эталона): у каждого PDF строка `publications`; скрипт rag 0 ошибок.
6. `--domain auto` на дереве `solar/…` + `rag/…` → `out/solar` и `out/rag`; файл вне подпапок → код 1 «укажите --domain»; дубль имени → `duplicate_name` в `files.csv`.
7. Статусы: мок OCR `unavailable` → файл `ocr`, строка `publications` с `in_scope=true` (rag); `OcrFailed` → `error`; скан с успешным OCR → `done`, `scan=true`, `ocr_confidence`; `--ocr force` на текстовом PDF вызывает OCR (счётчик вызовов мока), `--ocr off` — нет.
8. «Проверка формата»: ответ мока с висячей ссылкой и `NaN` → в БД строки уже очищены, событие в `errors.log`.
9. Демо (W2-078): в другом разборе пользователя на тех же версиях статей есть правки и отметки → `ensure_demo_run` создаёт демо с 0 правок, 0 отметок, 0 `extra`; `run_summary(demo).edit_count == 0`; повторный вызов не создаёт второй; удалённый не пересоздаётся без `force`.
10. `restart(keep_review=True)` переносит правку по ключу значения при сменившемся rid; несовпавшая — `unmatched`.
11. `faktura eval --domain rag` без инструмента → код 1 и русское сообщение; с фиктивным инструментом → отчёт.
12. `registry_lint` по `pipeline/`, `cli/` пуст (разрешён только `pipeline/domains.py` и разбор `--domain` в `cli/`).

### 8.6. `api_runs` (L3) — разборы, SSE, сводка, набор для сдачи

Пути: `backend/src/faktura/api/runs/` (новый пакет, `ROUTERS: list[APIRouter]`), `backend/src/faktura/api/corpus/` (удалить: `EventTokens` и `sse_stream` перенести в `api/runs`), `backend/tests/api_runs/`, `backend/tests/api_corpus/` (удалить). `api/base` — shared (уже сделано на шаге 0: `ui_page_params`, `Expose-Headers`, коды ошибок, без заглушек `/graph`, `/search`). `ROUTER_MODULES` уже содержит `faktura.api.runs`.

Зависимости: `pipeline` (`RunService`: `create_ui_run`, `add_files`, `stop`, `restart`, `ensure_demo_run`, `get_run_for`, `export_items`; `views.run_view`; `testing.seed_run`), `storage` (`uow.ui_runs`, `uow.stats.run_summary`, `uow.actions`), `export` (`submission_zip`, `prepare_pdfdir`, `run_check`, `dataset_json_stream`, `submission_filename`, `build_passport`), `api.base.deps` (`current_user`, `get_services`, `get_uow`, `ui_page_params`, `mutation_response`, `stub_response`, `upload_rate_limit`).

Эндпоинты — `docs/wave2/api_contract.md` §3 и §6 (spec/15 §15.2, §15.3, §15.7):
- **`POST /runs`** — multipart `file` (повторяемое, относительные пути) или `archive` (ZIP), `domain`, `title`. `pdfio.collect_inputs`/`safe_unzip` с лимитами из настроек, сигнатура `%PDF-`, zip-slip и `..` → `rejected: unsafe_path`. Ответ `202 {id, title, domain, status: "queued", created_at, demo, files, rejected}`. Ошибки: 400 `validation_error` (нет файлов), 413 `file_too_large`, 415 `unsupported_file`, 422 `zip_bomb`, 429 `too_many_requests` (`upload_rate_limit`).
- **`POST /runs/{id}/files`** → `202 {added, rejected}`.
- **`GET /runs`** — `limit` 100 (максимум 1000), `cursor`, `q`, `domain`, `files`. При первом вызове пользователя — `ensure_demo_run`, если `settings.demo_enabled`.
- **`GET /runs/{id}`**, **`PATCH /runs/{id}`** (`{title?, pinned?}` → `{run, action_id}` + `X-Action-Id`), **`DELETE /runs/{id}`** (`{action_id}`), **`POST /runs/{id}/restore`**, **`POST /runs/{id}/restart`** (`{files, keep_review}`), **`POST /runs/{id}/stop`** (`{cancel_running}`) → 202.
- **`GET /runs/{id}/events`** — SSE: события `file`/`run`/`done` с `id:`; `Last-Event-ID` (заголовок или `last_event_id`); пинг `: ping` раз в `settings.sse_ping_s`; после `done` поток закрывается; если разбор уже завершён — сразу `run` и `done`; авторизация Bearer или `?token=` от `POST /events/token` (одноразовый, 60 с); `sse_max_events`/`sse_idle_timeout_s` закрывают поток в тестах.
- **`GET /runs/{id}/summary`** — `storage.stats.run_summary`.
- **`GET` и `POST /runs/{id}/submission.zip`**, **`GET /runs/{id}/submission/stats`:** `results=D001:R002,…`, `check`, `partial`. Сборка: `pipeline.export_items(run_id, selected, partial)` (разбор идёт без `partial` → 409 `run_in_progress`) → `export.submission_zip(domain, items, selected=…)`. Ответ — `FileResponse`. Заголовки: `Content-Disposition: attachment; filename="submission.zip"; filename*=UTF-8''…`, `X-Export-Tables`, `X-Export-Results`, `X-Export-Rejected`, `X-Export-Papers`, `X-Export-Filename`, `X-Check-Status`. `check=true`: solar — скрипт как есть; rag — без `--ocr` синхронно или кэш отчёта `--ocr` по хэшу CSV и набора PDF (К2-15); pdfdir — `export.prepare_pdfdir(domain, pdfs)` с ключами `export_pid`; первая строка `check_report.txt` — режим; код 1 → 422 `submission_invalid` с `details.report`. `publication_id` в выгрузке — `export_pid` (К2-14).
- **`GET /runs/{id}/dataset.json`** (should), **`GET /passport?domain=`** (should).
- **Заглушки §15.9** (could, `X-Faktura-Stub: 1`): `GET/POST /runs/{id}/messages`, `GET /runs/{id}/suggestions`, `POST …/messages/{mid}/stop`, `PUT …/messages/{mid}/rating` — формы по контракту §3.3.

Все пути — только для владельца разбора, чужой → 404.

**Приёмка.**
1. Сценарий стыковки на моках (`seed_run` и `POST /runs` с 3 PDF): `POST /runs` → 202 → SSE до `done` → `GET /runs/{id}` (статусы, `status_label`) → `GET /runs` (порядок, keyset, `q`, `domain`).
2. Загрузка: ZIP с `../x.pdf` → `rejected unsafe_path`; не-PDF → `unsupported_file`; повтор SHA-256 → `duplicate`; ZIP-бомба → 422; 61-я загрузка за час → 429.
3. SSE: `Last-Event-ID` продолжает поток; `?token=` одноразовый и не попадает в лог; завершённый разбор сразу отдаёт `run` + `done`.
4. `submission.zip`: solar — 7 CSV с BOM, rag — без BOM; правки разбора учтены (отклонённая строка отсутствует, исправленная с пометкой); `results=` — только выбранные; во время разбора → 409, с `partial=true` → 200; заголовки `X-Export-*` и `Content-Disposition` с русским именем; `check=true` на rag → `X-Check-Status`; файл с ключом `P042-2` при уникальном исходном имени `P042.pdf` выгружается как `P042`.
5. `summary` по контракту §3.2 для обеих областей.
6. Демо: первый `GET /runs` нового пользователя (с `demo_enabled=True`) создаёт демо-разбор; правки того же пользователя в другом разборе на тех же статьях не видны в демо (`GET /runs/{demo}/summary`: `edit_count=0`); удалённый не пересоздаётся.
7. Чужой разбор → 404 на всех путях; `tests/api_base` зелёные; `tests/api_corpus` и `api/corpus` удалены; `registry_lint` по `api/runs/` пуст.

### 8.7. `api_data` (L3) — статья, PDF, строки, сводки, замечания, правки и журнал

Пути: `backend/src/faktura/api/data/` (новый пакет, `ROUTERS`), `backend/src/faktura/api/review/` (заменить заглушки волны 1, `ROUTERS`), `backend/src/faktura/api/papers/` (удалить), `backend/src/faktura/query/` (язык запросов и «лучшее значение»), `backend/src/faktura/review/` (заменить заглушки волны 1 на `RunReviewService`), `backend/tests/api_data/`, `backend/tests/api_review/` (устаревшие тесты фактов заменить), `backend/tests/api_papers/` (удалить). `ROUTER_MODULES` уже содержит `faktura.api.data` и `faktura.api.review`. Заглушек `/graph` и `/search` в `api/base` уже нет (шаг 0).

Зависимости: `storage` (`uow.rows.load`/`duplicate_chain`/`paper_results_summary`/`summary`, `uow.results.query`/`group`/`iter_keys`, `uow.issues_view.list`/`stats`/`base_for_scope`/`open_counts`, `uow.reviews`, `uow.issue_marks`, `uow.issue_extra`, `uow.actions`, `uow.ui_runs.get`/`paper_versions`), `pipeline` (`HANDLERS[domain].recheck`, `RunService.get_run_for`, `views`, `testing.seed_run`), `rag` (`rag.keys.comparability_reasons`, `rag.keys.recheck_scope`), solar `checks.comparability_key`, shared (`review_rules.*`, `get_spec`, `ui_page_params`, `stub_response`, `mutation_response`). Модуль `export` не используется: выгрузку с правками проверяет `api_runs`.

**A. Чтение** (`docs/wave2/api_contract.md` §2.3, §4; spec/15 §15.4, §15.5, §15.13, §15.14):
- **`GET /papers/{domain}/{pid}`** — `run`, `results=summary|page|all` (по умолчанию rag → `summary`, solar → `all`), `limit` ≤ 1000 (по умолчанию 500), `cursor`. Ответ: `publication` и шесть таблиц области; `results` или `results_summary`; `issues`; `reviews` (только изменённые, только с `run`); `meta.results[rid]` (`confidence`, `bbox`, `quote_found`, `norm_status`, `issue_ids` открытые, `duplicates`, `duplicate_page`, `source_table_row_label`, `source_table_row_text`, `ocr_used`, `ocr_confidence`); `meta.issues[iid]` (`state`, `state_auto`, `computed_by`, `confidence`, `records`: solar — `device_label`, rag — `rag_variant` и `dataset_name`; `value` — действующее значение); `pdf_pages`, `run_ids`, `file_status`, `scan`, `ocr_confidence`; `paper_status`, `open_issue_count`, `next_cursor`. Без `run` — последняя готовая версия из разборов пользователя, без правок (`reviews` пуст, состояния `open`). `results=all` у rag-статьи с ≥ `results_all_max` строк → 400 «Слишком много строк: используйте results=page или GET /results». `out` → только `publication`. Нет статьи → 404.
- **`GET /papers/{domain}/{pid}/pdf`** — доступ через разборы пользователя; поток из `FileStore`. Один диапазон `Range` (`bytes=a-b`, `a-`, `-n`) → 206 + `Content-Range`; неверный → 416; `Accept-Ranges: bytes`; `ETag` = sha256 (`If-None-Match` → 304); `Cache-Control: private, max-age=86400`; S3 → можно 302 на `signed_url` (300 с); 404.
- **`GET /papers/{domain}/{pid}/ocr`** (should) — слова с bbox в долях страницы; не скан → 404.
- **`GET /results`** — все параметры контракта §4.4 → `storage.ResultQuery` (функция `query.params.to_result_query(params, user, run)` — общая для `GET /results` и пакетной правки по фильтру). `domain` обязателен без `run`; `review` и `min`/`max` без `run` → 400. Если выборка охватывает rag-статью с > `results_large_rows` строк, а фильтров `metric`/`config_id`/`dataset_id`/`task_id`/`experiment_id` и `group_by` нет → 400 с подсказкой. `group_by` → `uow.results.group`. Ответ `{data: [{row, device_label, paper_title, review, meta}], total | total_estimate, next_cursor, filters}`.
- **`q` — язык запросов** (`query/parse.py`, spec/04 §4.11). `parse_query(domain, q) -> ParsedQuery(filters: [{id, label, match, kind, …}], unparsed, metric)`:
  - **число** `<параметр> <оператор> <число>[ %]`: между параметром и оператором — до 24 символов без цифр. Синонимы параметров: solar `кпд|pce|эффективн` → PCE, `voc|напряжени` → Voc, `jsc|плотност тока` → Jsc, `ff|фактор заполн` → FF, `ширина зоны|bandgap|запрещ` → bandgap (плюс синонимы метрик из `norm`); rag `recall@5|полнот` → Recall@5, `recall@10`, `mrr`, `чанк|chunk` → chunk_size, плюс любое `metric_name` статьи дословно. Операторы: «выше, больше, более, свыше, >» — строго больше; «от, не ниже, >=» — не меньше; «ниже, меньше, менее, <» — строго меньше; «до, не выше, <=» — не больше. Десятичная запятая допустима. Для параметров-долей (`metric_unit=fraction`) число > 1 делится на 100 («Recall@5 выше 70» = 0,70). `kind=num`, `id="num:<param>:min|max"`, `label` вида «КПД > 18 %»;
  - **год**: «после 2022» — строго позже; «с 2022», «от 2022», «не раньше 2022» — включительно; «до 2023», «раньше 2023» — включительно. `kind=year`, по `publications.year`;
  - **тема**: solar «перовскит», «MAPbI», «CIGS», «CdTe», «тандем», «кремний», «олово», «бессвинцовые», «Sn»; rag «гибрид», «hybrid», «BM25», «rerank», «переранж», «кросс», «чанк», «chunk» — по названию статьи и подписи строки; темы объединяются через «или», остальное через «и». `kind=topic`;
  - **замечания**: «противоречия», «не сходится», «расхождения», «сомнения», «ошибки» → `has_issues`. `kind=issues`;
  - rag дополнительно: `config_id:<id>`, `dataset_id:<id>`, `experiment_id:<id>`;
  - остаток — подстрока по `search_text`, названию статьи, `publication_id`, `device_label`; `unparsed` — нераспознанная часть; `match` — подстрока запроса, из которой получен фильтр.
- **`GET /results/summary`** — `storage.rows.summary`.
- **`GET /issues`** (`state` по умолчанию `all`) и **`GET /issues/stats`** — через `storage.issues_view`. `computed_by` для людей: `backend` → «сервис разбора», `ml` и `both` → «модель».
- **`GET /compare`** (should) — `{metric, unit, data, comparable {ok, reasons}}`; причины solar — `checks.comparability_key`, rag — `rag.keys.comparability_reasons`.
- **`query/best.py`:** `best_value(domain, rows, reviews, metric) -> row | None` и `best_set(...) -> dict[metric, row]` по §15.13 (контракт §7). Одна функция для сравнения, ответов и сводок.
- **Заглушки** (could, `X-Faktura-Stub: 1`): `GET /graph`, `GET /papers/{domain}/{pid}/related`, `GET /search` — формы по контракту §4.8.

Все списки принимают `run` и `domain`; без `run` — объединение разборов пользователя без задвоения версий и без правок. Лимиты — `ui_page_params` (100/1000).

**B. Правки** (`review/service.py` — `RunReviewService`; контракт §5; spec/15 §15.6, §15.12, §15.17):
- **`PATCH /runs/{id}/papers/{domain}/{pid}/results/{rid}/review`**, `{status: accepted|rejected|new, corrected_value?}`. Порядок:
  1. проверки доступа (чужой разбор, статья не в разборе, нет записи → 404); `corrected_value` → `review_rules.parse_corrected` (422 `fact_value_unrecognized`, `field=corrected_value`); единица — `normalized_unit`;
  2. цель: `chain = uow.rows.duplicate_chain(paper_id, rid)`; `rejected` разбора по цепочке; `target = review_target(rid, chain_index, rejected)` → в ответе `applied_to`;
  3. `uow.reviews.upsert` (или `delete` при `new` без значения), `match_key = review_match_key(domain, row)`;
  4. **пересчёт сразу**: rag — `scope = (experiment_id, metric_name)` строки `target`; `rb = uow.rows.load(paper_id, tables=["configurations","datasets","tasks","experiments","results"], results_filter={"experiment_id": e, "metric_name": m})` (в выборку входят baseline-строки других конфигураций); `values` и `rejected` — из `uow.reviews.list(run_id, paper_id, result_ids=<rid выборки>)`. solar — `rb = uow.rows.load(paper_id)` целиком. Затем `HANDLERS[domain].recheck(rb, values=…, rejected=…)`; для каждого `RecheckResult`: `base = uow.issues_view.base_for_scope(paper_id, r.scope)`, `add, suppress = review_rules.diff_recheck(base, r.issues, r.checks)`, `uow.issue_extra.replace(run_id, paper_id, r.scope, …)`. Добавленное замечание получает `issue_id = meta.key` (стабилен, проходит `SAFE_ID_RE`);
  5. `issues_changed {opened, closed}` — замечания статьи, у которых изменилось состояние (`issue_state` до и после, с учётом автозакрытия, `add`/`suppress`);
  6. журнал: `uow.actions.record(kind="review", run_id, prev=…)`.

  Ответ `{review: {status, corrected_value, by, at}, action_id, issues_changed, applied_to}`. Цель — ≤ 500 мс на D014.
- **`POST /runs/{id}/reviews/batch`** — `{items: [{domain, pid, rid, status}]}` или `{filter: {…параметры GET /results}, status}`. Фильтр → `query.params.to_result_query` → `uow.results.iter_keys` постранично. Ответ `{count, action_id}`. Одна запись журнала с компактным `prev` (список (`paper_id`, `rid`, `prev_status`, `prev_corrected`)); пересчёт — по одному разу на каждую затронутую область; отмена атомарна на десятках тысяч строк.
- **`PATCH /runs/{id}/papers/{domain}/{pid}/issues/{iid}`** `{state: open|fixed|ok}` → `{issue: {…row, state, state_auto, resolved_at, resolved_by}, action_id, open_issue_count}`. `open` на автозакрытом → 409 `issue_auto_fixed`; `open` снимает ручную отметку. Отметка хранится по `ikey`.
- **`DELETE /runs/{id}/reviews`** → `{removed_reviews, removed_issue_marks, action_id}`; удаляет и `extra` разбора; одна запись журнала (`prev` — всё удалённое).
- **`GET /history?run=`** (should) → `{data: [{action_id, at, who, label, kind, run_id, count, undone}], next_cursor}`.
- **`POST /history/undo`** `{run?}` — своё последнее действие за 24 ч, глубина 50. **`POST /actions/{action_id}/undo`**; повторная отмена → 409 `already_undone`. Отмена восстанавливает `prev` точно (правки, отметки, `extra`).
- **Подписи журнала** по-русски, с десятичной запятой: «Принято значение R002», «Исправлено значение R002: 16,8 → 18,7», «Отклонено значение R002», «Замечание I3: исправлено» / «всё верно» / «открыто снова», «Массовая правка: N значений», «Сброс правок разбора».

Чужой разбор, статья или запись → 404. Ключ правки — (`run_id`, `paper_id`, `result_id`).

**Приёмка** (без модуля `export`; состояние проверяется через HTTP и `storage`):
1. `GET /papers/rag/D014?run=…` (seed_run): `results_summary` по умолчанию; `results=page` с keyset; `results=all` → 400; без `run` — `reviews` пуст.
2. `/pdf`: 200, 206 (`bytes=0-99`, `bytes=-100`), 416, 304 по `ETag`; чужой → 404.
3. `/results`: все фильтры контракта; `review`/`min`/`max` без `run` → 400; большая rag-статья без фильтров → 400; `group_by`; keyset без пропусков; `q`: «КПД выше 18 после 2022» → два фильтра (`num:pce:min`, `year:min`), «Recall@5 выше 70» → 0,70; rag `config_id:C01`.
4. Правка: `PATCH` с `"0,330"` → `corrected_value "0.330"`; затем `GET /results?run&review=corrected` возвращает строку с `review.corrected_value`, а `uow.stats.run_summary(run).edit_count` вырос на 1; «abc» → 422.
5. Пересчёт rag: отклонение одной из двух противоречащих строк закрывает `contradiction` (`issues_changed.closed`); правка baseline-строки другой конфигурации меняет `protocol_mismatch`; снятие правки возвращает исходное состояние (`extra` области пуст).
6. Автозакрытие: исправлены все записи замечания → `state=fixed`, `state_auto=true`; `PATCH issues {open}` → 409 `issue_auto_fixed`.
7. Повтор: правка на повтор применяется к основной (`applied_to`); если основная отклонена — к первому неотклонённому повтору.
8. Изоляция: правки разбора A не видны в разборе B на тех же версиях статей и без `run`.
9. Пакет по фильтру на 10 000 строк — одна запись журнала, undo атомарен; `DELETE /runs/{id}/reviews` и его отмена восстанавливают правки, отметки и `extra`; повторная отмена → 409.
10. Perf: PATCH review на синтетике 28 649 строк ≤ 500 мс.
11. `api/papers`, `tests/api_papers` удалены; `registry_lint` по `api/data/`, `api/review/`, `query/`, `review/` пуст (разрешены словари синонимов по области в `query/`).
