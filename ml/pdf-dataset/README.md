# pdf-dataset — PDF → dataset.json в один прогон

Один контейнер, один процесс, без подпроцессов:

```
PDF ──glmocr (layout PP-DocLayoutV3 локально + OCR на вашем сервере)──▶ markdown
    ──strip_tail (обрезка References и т.п.)──────────────────────────▶ clean.md
    ──extract_dataset (7 вызовов RouterAI, deepinfra pinned)──────────▶ dataset.json + meta.json
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

- `GET /health` → `{"status": "ok"}`
- `POST /extract` — multipart: файл `file` (PDF) + опционально `publication_id`, `no_strip`.
  Отвечает после завершения конвейера (минуты!) JSON-объектом:
  `{ok, publication_id, extract_error, failed_tables, counts, dataset, meta, out_dir}`.
  Файлы остаются и в `/out/<stem>/`. Повторная загрузка того же имени файла перезаписывает результат.

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
| Провайдер      | env `ROUTERAI_PROVIDER`                    | по умолчанию `deepinfra` (whitelist, `only`)   |

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
