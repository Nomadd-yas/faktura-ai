# Восстановление «Фактуры» на новом сервере

Бэкап сервера лежит у разработчика в `backups/server-<дата>/` (папка в `.gitignore`, внутри секреты). Состав:

| Файл | Что |
|---|---|
| `faktura.pgdump` | дамп Postgres (`pg_dump -Fc`) — разборы, строки таблиц, правки, чат, индекс текста |
| `files.tar.gz` | том `faktura_files` — загруженные PDF |
| `case.tar.gz` | `deploy/case`, `deploy/case_rag` — данные кейсов для `check_submission.py` и мока |
| `env.secret` | `deploy/.env` — пароли, JWT, ключ RouterAI (**не открывать в чате, не коммитить**) |
| `git-main.txt` | коммит, который работал на сервере |
| `vllm-image.txt`, `vllm-env.txt` | образ vLLM (по digest) и его переменные |
| `SHA256.txt` | контрольные суммы файлов бэкапа |

Что не входит (восстанавливается само): Docker-образы бэкенда и ML (`deploy.sh` собирает их из репозитория), образ и веса vLLM (качаются), том `faktura_ml-out` (кеши OCR/LLM, пересчитываются).

## Требования к серверу
Ubuntu с Docker и NVIDIA Container Toolkit, GPU от 12 ГБ (GLM-OCR в Q8 занимает ~10 ГБ), ~60 ГБ диска (образ vLLM ~36 ГБ). Доступ по ssh под псевдонимом (у нас `hima-gpu`, на новом — свой, поправьте в командах).

## Шаги
1. **Код.** На сервере: `mkdir -p ~/garden && git init --bare ~/garden/faktura.git`. Локально: `git remote set-url server <alias>:garden/faktura.git && git push server main`.
2. **Каталог деплоя.** На сервере: `mkdir -p ~/garden/backend && git -C ~/garden/faktura.git archive main:backend | tar -x -C ~/garden/backend`.
3. **Секреты и данные кейсов.** Локально из папки бэкапа:
   ```bash
   scp env.secret <alias>:garden/backend/deploy/.env && ssh <alias> chmod 600 garden/backend/deploy/.env
   scp case.tar.gz faktura.pgdump files.tar.gz <alias>:/tmp/
   ssh <alias> "tar -xzf /tmp/case.tar.gz -C ~/garden/backend/deploy"
   ```
   Если адреса сервисов изменились — поправьте в `.env` `FAKTURA_EXTRACT_URL`, `FAKTURA_CORS_ORIGINS`.
4. **Образы.** Локально: `bash backend/deploy/deploy.sh --ml` — собирает образы бэкенда и ML, отправляет на сервер, ставит метки `:shipped`, поднимает compose.
5. **База.** На сервере, после того как поднялся `postgres`:
   ```bash
   cd ~/garden/backend/deploy
   docker compose stop api ml ml-mock
   docker exec -i faktura-postgres-1 pg_restore -U faktura -d faktura --clean --if-exists --no-owner < /tmp/faktura.pgdump
   docker run --rm -v faktura_files:/v -v /tmp:/b alpine sh -c "tar -xzf /b/files.tar.gz -C /v"
   docker compose up -d
   ```
6. **OCR (vLLM).** На сервере: `bash ~/garden/backend/deploy/vllm-run.sh` (первый запуск качает веса модели). Проверка: `curl -s localhost:8123/v1/models` → `zai-org/GLM-OCR`, `max_model_len` 16384.
7. **Проверка.** `docker compose ps` — все `healthy`; `GET /api/v1/health`; индекс текста для чата: `docker exec faktura-api-1 faktura textindex rebuild --only-missing`.
8. **Дальнейшие выкатки** — как обычно: `git push server main` и `ssh <alias> "bash ~/garden/backend/deploy/server-deploy.sh --ml"`.

## Как сделать свежий бэкап
Команды — в истории разработки (сборка на сервере во `/tmp/faktura-backup-<дата>`, скачивание `scp -r`, удаление временной копии на сервере). Главное: `pg_dump -Fc`, архив тома `faktura_files`, `deploy/.env`, `deploy/case*`.
