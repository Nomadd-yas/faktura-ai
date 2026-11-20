#!/usr/bin/env bash
# Выкладка бэкенда на сервер: код из git (HEAD) + эталон для ml-mock -> ~/garden/backend, затем docker compose up.
# Запуск из корня репозитория:  bash backend/deploy/deploy.sh [ssh-host]   (по умолчанию hima-gpu)
# Выкладывается только закоммиченный код. deploy/.env и тома БД на сервере не перезаписываются.
set -euo pipefail
HOST="${1:-hima-gpu}"
REMOTE_DIR="garden/backend"
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

echo "== код ($(git rev-parse --short HEAD)) -> $HOST:~/$REMOTE_DIR"
ssh "$HOST" "bash -c 'mkdir -p ~/$REMOTE_DIR/deploy/case'"
git archive --format=tar HEAD:backend | ssh "$HOST" "bash -c 'tar -x -C ~/$REMOTE_DIR'"

echo "== эталон dev_gold для ml-mock"
tar -C data/caseB_kejs5 -cf - dev_gold | ssh "$HOST" "bash -c 'rm -rf ~/$REMOTE_DIR/deploy/case/dev_gold && tar -x -C ~/$REMOTE_DIR/deploy/case'"

echo "== .env, сборка и запуск"
ssh "$HOST" "bash -c 'cd ~/$REMOTE_DIR/deploy && bash init-env.sh && docker compose build && docker compose up -d && docker compose ps --format \"table {{.Service}}\t{{.Status}}\"'"
