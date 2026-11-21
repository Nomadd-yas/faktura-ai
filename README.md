# Фактура AI

Сервис, который превращает научные статьи в PDF в проверяемый набор данных: 7 связанных таблиц CSV, где у каждого
значения есть номер страницы и дословная цитата из статьи. Сделан для кейса ВИНИТИ № 5 в двух предметных областях:

| Область | Код | Таблицы |
|---|---|---|
| Тонкоплёночные солнечные элементы | `solar` | publications, materials, devices, layers, measurements, results, issues |
| Retrieval-Augmented Generation | `rag` | publications, tasks, datasets, configurations, experiments, results, issues |

Набор проходит официальный валидатор кейса `check_submission.py` и выгружается одной кнопкой (`submission.zip`).

## Что умеет

- **Разбор PDF:** текстовый слой или блочный OCR (GLM-OCR, layout + отдельные запросы на текст и таблицы), извлечение
  таблиц моделью DeepSeek, значения с графиков — моделью со зрением.
- **Нормализация:** единицы и шкалы (доля ↔ %, мВ ↔ В, мА/см²), канонические названия метрик и материалов, свой словарь
  синонимов.
- **Проверки качества:** правила области (согласованность J–V, единицы, champion vs mean, производные значения, повторы,
  @k у метрик поиска и др.) дают замечания (`issues`) со ссылкой на строки.
- **Правки человека:** принять, исправить, отклонить значение; состояния замечаний с автозакрытием; готовые предложения
  исправления у замечаний; журнал и отмена.
- **Вопросы по разбору (агент):** чат по данным и полному тексту статей разбора; каждое число в ответе проверяется по
  данным до отправки, сноски ведут на строку таблицы или страницу статьи; история чатов с возобновлением; внешние
  источники (OpenAlex): похожие и цитирующие работы.
- **Выгрузки:** `submission.zip` с паспортом набора, `dataset.json`, `dataset.xlsx`, набор по всем разборам области.
- **Живой прогресс:** SSE-события разбора, процент и оставшееся время.

## Качество (dev-выборка кейса, 21.11.2026)

| | Метрика | Значение |
|---|---|---|
| solar | F1 по 11 показателям results, dev / отложенные статьи | 96.1 / 97.1 |
| rag | F1 мягкая / строгая (с названием метрики), 3 контрольные статьи | 86.6 / 70.1 |

Подробности и слабые места — [docs/eval/RESULTS_2026-11-21.md](docs/eval/RESULTS_2026-11-21.md).

## Архитектура

```
 браузер (фронтенд, отдельный репозиторий)
        │  REST + SSE, JWT
        ▼
 ┌──────────────┐   HTTP (NDJSON)   ┌──────────────────────┐   OpenAI API   ┌──────────────┐
 │ backend      │ ────────────────► │ ml (pdf-dataset)     │ ─────────────► │ vLLM         │
 │ FastAPI      │                   │ layout PP-DocLayout  │                │ GLM-OCR      │
 │ SQLAlchemy   │ ◄──────────────── │ блочный OCR, LLM     │                │ (GPU)        │
 └──────┬───────┘                   └──────────┬───────────┘                └──────────────┘
        │                                      │ RouterAI (DeepSeek, Gemini Flash-Lite),
   PostgreSQL / SQLite                         │ провайдер всегда закреплён
   файлы PDF (local / S3)                      ▼
                                        внешние LLM
```

- `backend/` — API, очередь разборов, нормализация, правила, правки v2, выгрузки, агент чата, полнотекстовый индекс
  (FTS5 в SQLite, tsvector + GIN в PostgreSQL).
- `ml/pdf-dataset/` — сервис извлечения: OCR, сборка markdown по страницам, извлечение таблиц, графики, `/llm/chat`
  для агента, `/ocr` для блочного OCR.
- `ml/eval/` — оценщики против эталона кейса (`score.py`, `score_rag.py`, `ocr_eval.py`).
- `docs/` — API для фронтенда, устройство агента, домены, восстановление на новом сервере.

## Быстрый старт (локально, без GPU и ключей)

Нужны Python 3.12 и [uv](https://docs.astral.sh/uv/).

```bash
cd backend
uv venv && uv pip install -e ".[dev]"
uv run pytest -q                       # ~140 тестов, ~2 минуты
uv run faktura serve                   # API на :8000, SQLite, мок ML (данные из эталона dev)
```

Документация API — `http://localhost:8000/docs`. Пользователь создаётся командой
`uv run faktura users create --email you@example.com --generate --admin`.

Разбор из командной строки, без сервера:

```bash
uv run faktura extract --domain solar --in path/to/pdfs --out ./out --check path/to/check_submission.py
```

## Развёртывание (Docker)

Сервер: Ubuntu, Docker + NVIDIA Container Toolkit, GPU от 12 ГБ.

```bash
# OCR-модель (первый запуск скачивает веса); FP16 по умолчанию, VLLM_QUANT=fp8_per_tensor - Q8
bash backend/deploy/vllm-run.sh

# бэкенд + ML: собрать образы на сервере (uv) и поднять compose
bash backend/deploy/deploy.sh --ml --remote-build <ssh-host>

# дальнейшие обновления кода (~1 минута)
git push <remote> main && ssh <ssh-host> "bash ~/garden/backend/deploy/server-deploy.sh --ml"
```

Секреты (`POSTGRES_PASSWORD`, `FAKTURA_JWT_SECRET`, `ROUTERAI_API_KEY`, `FAKTURA_OPENALEX_KEY` …) лежат только в
`backend/deploy/.env` на сервере; `init-env.sh` создаёт недостающие. Перенос на другой сервер с данными —
[docs/deploy/RESTORE.md](docs/deploy/RESTORE.md).

Основные настройки:

| Переменная | Что |
|---|---|
| `FAKTURA_EXTRACT_URL` | адрес ML-сервиса (без него - мок) |
| `FAKTURA_RUN_CONCURRENCY`, `ML_CONCURRENCY` | параллельные статьи |
| `ROUTERAI_PROVIDER`, `RAG_VISION_MODEL`, `RAG_VISION_PROVIDER` | модели и закреплённые провайдеры |
| `FAKTURA_AGENT_MODE` | `off` - чат без агента (по шаблонам) |
| `FAKTURA_OPENALEX_KEY` | ключ OpenAlex для внешних источников (без ключа тоже работает, медленнее) |
| `FAKTURA_CORS_ORIGINS` | адреса фронтенда |

## Документация

| Документ | Для кого |
|---|---|
| [docs/api/CORE_API.md](docs/api/CORE_API.md) | контракт API для фронтенда: разборы, таблицы, правки, выгрузки, SSE, чат, история, источники |
| [docs/api/BACKEND_REPLY.md](docs/api/BACKEND_REPLY.md) | что сделано по спеке фронтенда и где бэк её уточнил |
| [docs/design/AGENT.md](docs/design/AGENT.md) | устройство агента: инструменты, маркеры сносок, проверка чисел, стоимость |
| [docs/design/DOMAINS.md](docs/design/DOMAINS.md) | как устроены предметные области и как добавить новую |
| [docs/design/TEXT_INDEX.md](docs/design/TEXT_INDEX.md), [docs/design/ML_NDJSON.md](docs/design/ML_NDJSON.md) | индекс текста, потоковый ответ ML |
| [ml/pdf-dataset/README.md](ml/pdf-dataset/README.md) | ML-сервис: запуск, ручки OCR, HTTP API |
| [docs/eval/](docs/eval/) | замеры качества и скорости |

## Структура репозитория

```
backend/            FastAPI-бэкенд (пакет faktura), тесты, deploy/ (compose, скрипты, оверлей-образы)
ml/pdf-dataset/     сервис извлечения (OCR + LLM), Dockerfile
ml/eval/            оценка против эталона кейса
docs/               API, дизайн, оценки, развёртывание
```

Данные кейса (PDF, эталон) в репозиторий не входят.
