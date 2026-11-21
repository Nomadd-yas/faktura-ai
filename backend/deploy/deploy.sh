#!/usr/bin/env bash
# Выкладка бэкенда на сервер: код из git (HEAD) + эталон для ml-mock -> ~/garden/backend, образ собирается
# ЛОКАЛЬНО и передаётся по ssh (PyPI с сервера очень медленный), затем docker compose up.
# Запуск из корня репозитория:  bash backend/deploy/deploy.sh [--ml] [ssh-host]   (по умолчанию hima-gpu)
#   --ml  дополнительно собрать и передать образ прототипа ML-1 (ml/pdf-dataset -> faktura-ml:latest, несколько ГБ).
#         Запустится он только при COMPOSE_PROFILES=ml в deploy/.env на сервере.
# Выкладывается только закоммиченный код. deploy/.env и тома на сервере не перезаписываются.
# Это «тяжёлый» путь (образы целиком). Для правок кода быстрее: git push server main + deploy/server-deploy.sh.
# Здесь образ на сервере помечается :shipped, коммит — в deploy/.shipped-<name>: основа для server-deploy.sh.
set -euo pipefail
WITH_ML=0
HOST="hima-gpu"
for a in "$@"; do
  case "$a" in
    --ml) WITH_ML=1 ;;
    *) HOST="$a" ;;
  esac
done
REMOTE_DIR="garden/backend"
IMAGE="faktura-backend:latest"
ML_IMAGE="faktura-ml:latest"
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

REV="$(git rev-parse HEAD)"
echo "== git push server ($(git rev-parse --short HEAD))"
git push -q server HEAD:main

echo "== код ($(git rev-parse --short HEAD)) -> $HOST:~/$REMOTE_DIR"
ssh "$HOST" "bash -c 'mkdir -p ~/$REMOTE_DIR/deploy/case'"
git archive --format=tar HEAD:backend | ssh "$HOST" "bash -c 'tar -x -C ~/$REMOTE_DIR'"

echo "== эталон dev_gold для ml-mock и check_submission.py для api (?check=true)"
tar -C data/caseB_kejs5 -cf - dev_gold tools/check_submission.py | ssh "$HOST" "bash -c 'rm -rf ~/$REMOTE_DIR/deploy/case/dev_gold ~/$REMOTE_DIR/deploy/case/tools && tar -x -C ~/$REMOTE_DIR/deploy/case'"
echo "== RAG: dev_gold (мок), tools + submission_template (check_submission ищет schema.json рядом)"
ssh "$HOST" "bash -c 'mkdir -p ~/$REMOTE_DIR/deploy/case_rag'"
tar -C data/caseA_drive -cf - dev_gold/results.csv dev_gold/publications.csv dev_gold/configurations.csv dev_gold/datasets.csv dev_gold/tasks.csv dev_gold/experiments.csv dev_gold/issues.csv tools submission_template | ssh "$HOST" "bash -c 'rm -rf ~/$REMOTE_DIR/deploy/case_rag/* && tar -x -C ~/$REMOTE_DIR/deploy/case_rag'"

BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT

echo "== сборка образа локально (из HEAD) и передача"
mkdir -p "$BUILD_DIR/backend"
git archive --format=tar HEAD:backend | tar -x -C "$BUILD_DIR/backend"
docker build -q -t "$IMAGE" "$BUILD_DIR/backend"
docker save "$IMAGE" | gzip -1 | ssh -o ServerAliveInterval=30 "$HOST" "bash -c 'gunzip | docker load'"
ssh "$HOST" "bash -c 'docker tag $IMAGE faktura-backend:shipped && echo $REV > ~/$REMOTE_DIR/deploy/.shipped-backend'"

if [[ "$WITH_ML" == 1 ]]; then
  echo "== прототип ML-1: сборка $ML_IMAGE локально (из HEAD) и передача"
  mkdir -p "$BUILD_DIR/ml"
  git archive --format=tar HEAD:ml/pdf-dataset | tar -x -C "$BUILD_DIR/ml"
  docker build -t "$ML_IMAGE" "$BUILD_DIR/ml"
  docker save "$ML_IMAGE" | gzip -1 | ssh -o ServerAliveInterval=30 "$HOST" "bash -c 'gunzip | docker load'"
  ssh "$HOST" "bash -c 'docker tag $ML_IMAGE faktura-ml:shipped && echo $REV > ~/$REMOTE_DIR/deploy/.shipped-ml'"
fi

echo "== .env и запуск"
ssh "$HOST" "bash -c 'cd ~/$REMOTE_DIR/deploy && bash init-env.sh && docker compose up -d --no-build --remove-orphans && docker compose ps --format \"table {{.Service}}\t{{.Status}}\"'"
