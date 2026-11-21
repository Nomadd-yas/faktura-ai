# pdf-dataset — PDF → dataset.json в один прогон

Один контейнер, один процесс, без подпроцессов:

```
PDF ──glmocr (layout PP-DocLayoutV3 локально + OCR на вашем сервере)──▶ markdown
    ──strip_tail (обрезка References и т.п.)──────────────────────────▶ clean.md
    ──extract_dataset (7 вызовов RouterAI, провайдер по приоритету)──────────▶ dataset.json + meta.json
```

`extract_dataset.py` — модуль, который `pipeline.py` импортирует и вызывает напрямую
(CLI тоже работает). Конфиг glmocr — обычный файл `glmocr.config.yaml`, без кодогенерации.

## Сборка

```bash
cd pdf-dataset
docker build -t pdf-dataset .
```

Если сборщик не видит huggingface.co (для layout-модели ~1–2 ГБ):

```bash
docker build --build-arg PRELOAD_LAYOUT_MODEL=0 -t pdf-dataset .
```

и при запуске монтируйте HF-кэш: `-v hf-cache:/hf-cache`
(модель скачается при первом запуске). Ускорить можно через зеркало:
`--build-arg` + `ENV HF_ENDPOINT=https://hf-mirror.com`.

## Запуск

Linux / macOS:

```bash
docker run --rm \
  -v "$PWD/papers:/data:ro" -v "$PWD/out:/out" \
  -e ROUTERAI_API_KEY="sk-..." \
  pdf-dataset /data/D001.pdf
```

PowerShell:

```powershell
docker run --rm `
  -v "${PWD}\papers:/data:ro" -v "${PWD}\out:/out" `
  -e ROUTERAI_API_KEY="sk-..." `
  pdf-dataset /data/D001.pdf
```

## HTTP API (опционально)

Тот же контейнер умеет поднимать самый простой синхронный HTTP API вместо разового прогона:

```bash
docker run --rm -p 8080:8080 \
  -v "$PWD/out:/out" \
  -e ROUTERAI_API_KEY="sk-..." \
  pdf-dataset serve
```

Эндпоинты:

- `GET /health` → `{"status": "ok"|"down", "model_version", "ocr": "ok"|"down", "router_key": true|false}`
  ([faktura-patch] `down`, если нет `ROUTERAI_API_KEY` или не отвечает OCR-сервер из `glmocr.config.yaml`, проверка TCP ~2 с).
- `POST /extract` — multipart: файл `file` (PDF) + опционально `publication_id`, `no_strip`.
  Отвечает после завершения конвейера (минуты!) JSON-объектом:
  `{ok, publication_id, extract_error, failed_tables, counts, dataset, meta, out_dir}`.
  В ответ добавлено `model_version` = `pdf-dataset/<model>`.
  Файлы остаются и в `/out/<stem>/`. Повторная загрузка того же имени файла перезаписывает результат.

**Поток NDJSON** ([faktura-patch], спека §10; подробно — `docs/design/ML_NDJSON.md`). Для больших статей (RAG, ~28 тыс. строк
`results`): `Accept: application/x-ndjson` или поле формы `stream=ndjson` (`stream=json` — принудительно обычный JSON; по
умолчанию, без них, ответ прежний). Ответ `application/x-ndjson`, по объекту на строку: `{"type":"header", ok, model_version,
publication_id, extract_error, failed_tables, has_dataset, out_dir, meta}`, затем `{"type":"table","table":"results","rows":[…≤1000…]}`
(размер пачки — env `ML_NDJSON_ROWS`), в конце `{"type":"end","counts":{…}}`. Ошибки до начала ответа — обычный HTTP-статус и
тело `{code,message,retryable,detail}`; сбой после заголовка — строка `{"type":"error","code","message","retryable","detail"}`
и поток закрывается без `end`.

В `meta` ([faktura-patch]): `ocr_confidence` (0..1: среднее по страницам оценки надёжности текста — текстовый слой ok/poor/none =
1.0/0.5/0.2, страница glmocr — его `confidence` блоков, иначе эвристика `0.9·(1−min(1, 5·доля мусорных символов))`, пустая
страница 0.5) с пояснением в `meta.ocr_confidence_basis`; `suggested_domain` (`rag`|`solar`|`null` — модель заполняет на шаге
publications только при `in_scope=false`; в строку `publications` не попадает, шаблон фиксирован).

Ошибки ([faktura-patch]) — JSON `{"code", "message", "retryable", "detail"}` (`detail` = `message`):

| HTTP | code | когда |
|------|------|-------|
| 422 | `pdf_broken` | PDF не читается / 0 страниц (проверка pymupdf до OCR) |
| 422 | `pdf_encrypted` | PDF зашифрован |
| 503 | `ocr_unavailable` | OCR-сервер недоступен; `retryable: true`, заголовок `Retry-After: 60` |
| 503 | `not_configured` | нет `ROUTERAI_API_KEY` (или конфига glmocr); `retryable: false` |
| 500 | `ml_error` | прочие сбои |

Страницы: markdown для LLM строится постранично из `result.json_result` с маркерами
`<!-- page N -->` (N — страница PDF с 1; блоки image пропускаются). Модель берёт `source_page`
из ближайшего предшествующего маркера. Если формат `json_result` неожиданный, берётся
`markdown_result` без маркеров (в логе и `meta.page_markers=false`). `strip_tail` маркеры сохраняет и
режет только по заголовкам в последних 40% текста. Схемы 7 таблиц — 1:1 с колонками шаблона сдачи;
пустое = `null`, значения без явного указания в тексте не заполняются.

curl:

```bash
curl -X POST http://localhost:8080/extract -F "file=@D001.pdf" -F "publication_id=D001"
```

PowerShell:

```powershell
$form = @{ file = Get-Item "D001.pdf"; publication_id = "D001" }
Invoke-RestMethod -Uri "http://localhost:8080/extract" -Method Post -Form $form -TimeoutSec 900
```

Важно:
- **авторизации нет** — только внутренняя сеть / VPN / закрытый контур;
- ответы **синхронные**: статья обрабатывается 1–5 минут, у клиента должен быть большой timeout;
- запросы обрабатываются **по одному**; glmocr тяжёлый — не шлите параллельные запросы;
- порт меняется через `PORT` (`docker run -e PORT=9000 -p 9000:9000 ... serve`).

Аргументы `--no-strip` / `--publication-id` в API передаются формой: `-F "no_strip=true"`.

## Настройки

| Что            | Где                                        | Как поменять                                   |
|----------------|--------------------------------------------|------------------------------------------------|
| Адрес OCR-сервера, порт, `served-model-name`, устройство layout | `glmocr.config.yaml` (в каталоге рядом с `pipeline.py`, живёт в `/app` в образе) | правкой файла и пересборкой, либо смонтировать свой поверх: `-v "$PWD/glmocr.config.yaml:/app/glmocr.config.yaml:ro"`, либо `GLMOCR_CONFIG=/path/to/config.yaml` |
| RouterAI-ключ  | env `ROUTERAI_API_KEY` (обязательный)      | `-e`                                           |
| Модель RouterAI| env `ROUTERAI_MODEL`                       | по умолчанию `deepseek/deepseek-v4.1-flash-1017` |
| Провайдер      | env `ROUTERAI_PROVIDER`                    | по умолчанию `wafer`; несколько через запятую — по приоритету (`order` + `allow_fallbacks: false`)   |

`--publication-id D001` — явный id (по умолчанию — имя PDF без расширения).
`--no-strip` — не обрезать хвостовые разделы markdown.
Оба аргумента идут после имени файла: `pdf-dataset /data/D001.pdf --no-strip --publication-id D001`.

Аргументы запуска передаются в `pipeline.py` напрямую через `/app/entrypoint.sh` (режим `serve` — см. «HTTP API» выше).

## Артефакты (в `/out`)

| Файл                  | Содержание                                                            |
|-----------------------|-----------------------------------------------------------------------|
| `<stem>.md`           | markdown статьи (после strip_tail)                                    |
| `<stem>.dataset.json` | датасет: ключи `publications, materials, devices, layers, measurements, results, issues`; пустая ячейка = `null` |
| `<stem>.meta.json`    | метаданные прогона: `pages`, `ocr_seconds`, `markdown_chars`, `glmocr_config` + `glmocr_config_text`, `strip_tail` (строка/заголовок/сколько отрезано), `steps[]` (по вызову: `finish_reason`, `strict`, `seconds`, `usage` с токенами), `failed_tables`, `counts`, `extract_error` |
| `work/`               | glmocr-вывод (`.md` + `.json`), `<md>.clean.md`, `*.raw_response.txt` — сырой ответ модели для шага, если JSON не разобрался |
| `uploads/`            | (только API-режим) загруженные PDF как есть |

Код выхода (CLI-режим): `0` — успех (замечания валидатора, если есть, печатаются в stdout, но не роняют прогон), `2` — glmocr отработал, но извлечение оборвалось (dataset может отсутствовать; смотрите `.meta.json` → `extract_error` и `work/*.raw_response.txt`). В API-режиме вместо кода выхода смотрите поле `ok` и `extract_error` в ответе.

## extract_dataset.py сам по себе

```bash
ROUTERAI_API_KEY=sk-... python extract_dataset.py D001.clean.md --out D001.dataset.json --meta D001.meta.json
```

`--no-issues`, `--issues-with-text`, `--max-tokens`, `--no-strict`, `--issues-max-tokens` — как раньше.

## Заметки

- Ваш vLLM/SGLang должен отдавать `zai-org/GLM-OCR` (`--served-model-name`) на `:8123`, иначе поправьте `glmocr.config.yaml`.
- Layout-модель по умолчанию на CPU — GPU сервера занята vLLM. Хотите layout на GPU: `device: cuda`(или `cuda:0`) в конфиге + `docker run --gpus all ...`.
- `run_glmocr.py` больше не нужен — его роль исполняет `pipeline.py` (шаг 1).
- Первый боевой прогон: смотрите `finish_reason` по шагам в stdout и `.meta.json`; `finish_reason=length` — поднимите `--max-tokens` (передаётся только в CLI-режиме; для конвейера лимиты в `extract_dataset.py` — `max_tokens`/`issues_max_tokens` в вызове `extract()`).

## Блочный OCR: POST /ocr и ручки настройки [faktura-patch]

`POST /ocr` - multipart: `file` (PDF), опционально `pages` (`"1,3,5-7"`, с 1; по умолчанию все) и `format` = `markdown | blocks | both` (по умолчанию `both`).
Ответ: `{pages:[{page, markdown?, blocks?:[{index, label, native_label, bbox, content}], confidence?, seconds, page_size_pt}], model, seconds, cached, bbox_format}`.
`label` - text | table | formula | image; `native_label` - метка layout-модели (paragraph_title, figure_title, ...); `bbox` - `[x1,y1,x2,y2]`
в **нормализованных координатах 0..1000** от левого верхнего угла страницы (это `bbox_2d` из json_result glmocr; в pt: `x/1000*ширина`,
ширина/высота страницы - в `page_size_pt`). Блоки header/footer/footnote/number layout-модель отбрасывает (abandon), у image `content = null`.
`cached` = true, если все страницы взяты из дискового кеша `/out/ocr_cache/<sha256 pdf>/p<N>.<хеш настроек>.json` (ключ: sha256 PDF, страница,
хеш page_loader/layout/модели; адрес vLLM в хеш не входит). Кешированные страницы отдаются и при недоступном vLLM.

Ошибки `{code,message,retryable,detail}`: 400 `bad_request` (плохие `pages`/`format`), 413 `too_many_pages` (> `OCR_MAX_PAGES`, 50),
422 `pdf_broken|pdf_encrypted`, 503 `ocr_unavailable` (vLLM недоступен, `retryable`, `Retry-After: 60`), 500 `ml_error`.
Выполняется в отдельном пуле процессов (прогретый парсер glmocr, воркер перезапускается каждые `ML_TASKS_PER_CHILD`), размер пула - `OCR_CONCURRENCY` (2);
первый запрос в процессе грузит layout-модель (пул лениво, при старте не прогревается). RouterAI не используется.

Ручки (env -> эффективный конфиг во временном каталоге поверх `glmocr.config.yaml`/`GLMOCR_CONFIG`; не заданы - конфиг как есть):

| env | куда в glmocr-конфиге | заметка |
|-----|-----------------------|---------|
| `OCR_PDF_DPI` | `pipeline.page_loader.pdf_dpi` (200) | растеризация страницы перед layout и OCR |
| `OCR_MAX_TOKENS` | `pipeline.page_loader.max_tokens` (8192) | лимит ответа на любой запрос, в т.ч. на блок; раздельных лимитов по типам блоков в glmocr 0.1.5 нет |
| `OCR_LAYOUT_THRESHOLD` | `pipeline.layout.threshold` (0.3) | порог уверенности layout-модели |
| `OCR_LAYOUT_THRESHOLD_BY_CLASS` | `pipeline.layout.threshold_by_class` | JSON, напр. `{"table": 0.2}` |
| `OCR_LAYOUT_UNCLIP` | `pipeline.layout.layout_unclip_ratio` (1.0,1.0) | `1.05` или `1.05,1.1`: расширение рамки блока (отдельного padding кропа в glmocr нет - это его ближайший аналог) |
| `OCR_MAX_PAGES`, `OCR_CONCURRENCY` | - | лимит страниц запроса, число воркеров пула |

Оценка качества (A/B при смене модели/ручек): `ml/eval/ocr_eval.py` (см. docstring), запускается в контейнере.

Пробелы в ячейках таблиц: склейка вида «GEMINI1.0Pro» - **вывод модели** (glmocr отдаёт HTML таблицы как есть, `_clean_content` пробелы внутри
не трогает; наш `_ocr_page_texts`/`build_paged_markdown` содержимое не меняют). Наш код склеивал слова только при разборе HTML ячеек
(`<br>` удалялся без пробела) - исправлено в `extract_dataset.html_cell_text`: разрывающие теги -> пробел, строчные (`<sub>`) -> без пробела.

## Графики (домен rag) [faktura-patch]

`figures.py` оцифровывает графики прямо из векторной геометрии PDF (оси калибруются по подписям делений) и **знает своё
состояние**: для каждой страницы (`text_layer` ok/poor/none, `vector_paths`, `raster_frac`, `ocr_used`) и каждого
графика (`vector_ok` / `vector_partial` / `raster` / `none` + `confidence` по общим сигналам: невязка калибровки, число
делений, найдены ли обе оси, есть ли серии, покрытие легенды, доля точек внутри осей) — всё в `meta.extraction_state`.
Запасной путь: `RAG_VISION_MODEL=<модель с картинками на RouterAI>` — растровые/ненадёжные графики рисуются в PNG и
оцифровываются vision-моделью (строки помечены `approximate, digitized from raster image by vision model`); без неё или
при ошибке вызова — issue `figure_only`. Смысл серий (метрика, k, конфигурация) определяет один вызов LLM на график, строки
порождает программа. Переменные: `RAG_FIGURES=0` (выключить), `RAG_VISION_MODEL`, `RAG_FIGURE_PARTIAL=keep|drop`
(`keep` по умолчанию: частично оцифрованные графики без vision остаются с пометкой), `RAG_FIGURE_VISION_MAX` (8),
`RAG_FIGURE_MAX_ROWS` (12000 на график), `RAG_FIGURE_MAX_ROWS_TOTAL` (40000).
