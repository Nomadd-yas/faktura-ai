#!/usr/bin/env bash
# Выкладка бэкенда на сервер: код из git (HEAD) + эталон для ml-mock -> ~/garden/backend, образ собирается
# ЛОКАЛЬНО и передаётся по ssh (PyPI с сервера очень медленный), затем docker compose up.
# Запуск из корня репозитория:  bash backend/deploy/deploy.sh [ssh-host]   (по умолчанию hima-gpu)
# Выкладывается только закоммиченный код. deploy/.env и тома на сервере не перезаписываются.
set -euo pipefail
HOST="${1:-hima-gpu}"
REMOTE_DIR="garden/backend"
IMAGE="faktura-backend:latest"
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

echo "== код ($(git rev-parse --short HEAD)) -> $HOST:~/$REMOTE_DIR"
ssh "$HOST" "bash -c 'mkdir -p ~/$REMOTE_DIR/deploy/case'"
git archive --format=tar HEAD:backend | ssh "$HOST" "bash -c 'tar -x -C ~/$REMOTE_DIR'"

echo "== эталон dev_gold для ml-mock"
tar -C data/caseB_kejs5 -cf - dev_gold | ssh "$HOST" "bash -c 'rm -rf ~/$REMOTE_DIR/deploy/case/dev_gold && tar -x -C ~/$REMOTE_DIR/deploy/case'"

echo "== сборка образа локально (из HEAD) и передача"
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT
git archive --format=tar HEAD:backend | tar -x -C "$BUILD_DIR"
docker build -q -t "$IMAGE" "$BUILD_DIR"
docker save "$IMAGE" | gzip -1 | ssh -o ServerAliveInterval=30 "$HOST" "bash -c 'gunzip | docker load'"

echo "== .env и запуск"
ssh "$HOST" "bash -c 'cd ~/$REMOTE_DIR/deploy && bash init-env.sh && docker compose up -d --no-build --remove-orphans && docker compose ps --format \"table {{.Service}}\t{{.Status}}\"'"
