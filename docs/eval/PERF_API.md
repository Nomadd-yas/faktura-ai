# Задержки API: замеры и правки (спека BACKEND_ONLY §11)

Замер: `cd backend && uv run --python 3.12 --all-extras python tools/bench_api.py [--rag-all] [--reps 20] [--pg URL]`.
Данные: solar - все 21 dev PDF; rag - D014, D020, D004, D007, D001 (30 602 строки results, D014 ~28k);
мок-экстрактор, 33 правки/состояния v2 (PATCH review, PATCH issues). SQLite, Windows-ноутбук, TestClient (без сети),
по 20 повторов после прогрева; p50 / p95, мс. Postgres локально не поднимался (нет сервера под рукой); скрипт принимает `--pg URL`.

Цели: `GET /runs/{id}` < 100, `/papers/{domain}/{pid}` < 200, `/results` (100 строк) < 300.

| Запрос | до p50 | до p95 | после p50 | после p95 |
|---|---:|---:|---:|---:|
| GET /runs | 132.6 | 140.5 | 35.7 | 43.3 |
| GET /runs/{id} solar | 24.3 | 27.3 | 18.4 | 23.2 |
| GET /runs/{id} rag (цель 100) | 115.2 | 128.5 | 24.5 | 32.6 |
| GET /runs/{id}/summary solar | 58.4 | 71.4 | 48.4 | 54.9 |
| GET /runs/{id}/summary rag | 237.1 | 250.2 | 50.3 | 53.4 |
| GET /papers/solar/D001 | 43.2 | 47.1 | 41.7 | 45.8 |
| GET /papers/rag/D014 summary (цель 200) | 224.6 | 245.3 | 67.6 | 78.5 |
| GET /papers/rag/D014 tables=results | 145.8 | 174.7 | 75.9 | 86.8 |
| GET /papers/rag/D014 tables=issues | 116.4 | 131.2 | 36.6 | 39.1 |
| GET /results solar, стр. 1 | 50.9 | 59.4 | 47.8 | 50.9 |
| GET /results solar, стр. 2 (cursor) | 54.3 | 61.4 | 46.5 | 53.4 |
| GET /results rag, стр. 1 (цель 300) | 242.7 | 289.5 | 44.0 | 52.6 |
| GET /results rag, стр. 2 (cursor) | 222.5 | 260.8 | 46.9 | 54.2 |
| GET /issues solar | 19.5 | 22.6 | 17.1 | 20.5 |
| GET /issues rag | 18.1 | 20.0 | 15.6 | 17.6 |
| GET /issues/stats solar | 85.4 | 110.9 | 25.0 | 29.8 |
| GET /issues/stats rag | 1215.5 | 1345.5 | 35.5 | 39.5 |

Замечание: «до» снято на более загруженной машине (шум порядка +-30%), но порядок величин от этого не меняется.
До правок за цель не проходили: `/runs/{id}` rag (>100 при 30k строк), `/papers/rag/D014` summary (>200),
`/results` rag (на грани 300), `/issues/stats` rag (>1 с). После - всё с большим запасом.

## Причины и правки

Профилирование - обёрткой с замером времени каждого метода репозиториев. Время уходило в `sub_rows` (30k строк с
крупным JSON `data`): запросы счётчиков читали строки целиком, потому что индексы их не покрывали.

1. `RunData` (api/corpus/views.py): два вызова `counts_by_paper` (results, issues) заменены одним запросом
   `SubRowsRepo.counts_by_paper_tables` (GROUP BY paper_id, table_name). Это ядро `/runs`, `/runs/{id}`, `/summary`, `/papers`.
2. Новые индексы `sub_rows` (storage/schema.py):
   - `ix_sub_rows_corpus_paper_metric (corpus_id, table_name, paper_id, metric_name)` - покрывает `counts_by_paper*` и
     `metric_counts` (summary);
   - `ix_sub_rows_corpus_id (corpus_id, table_name, id)` - keyset-страницы и `COUNT` в `list_results`/`list_issues`;
   - `ix_sub_rows_paper_metric (paper_id, table_name, metric_name)` - `paper_summary` (сводка статьи rag).
3. `/issues/stats` (review/routes_v2.py): вместо `paper_tables` на каждую статью (все таблицы, декодирование JSON всех
   строк results) - один запрос `SubRowsRepo.tables_of_corpus(corpus, domain, ("publications", "issues"))`.
   Результат тот же (нужны только publications и issues).
4. Существующие БД: `LATE_INDEXES` в schema.py + цикл в `Database.create_all` создают недостающие индексы
   идемпотентно (`checkfirst=True`; `metadata.create_all` индексы у существующих таблиц не трогает) - так же, как раньше
   `ix_sub_issues_corpus_check`. На больших Postgres-БД `CREATE INDEX` при первом старте займёт секунды.

Поведение не менялось. Оставшаяся стоимость запросов - в основном накладные расходы TestClient/FastAPI (15-20 мс на
пустой `/issues`).
