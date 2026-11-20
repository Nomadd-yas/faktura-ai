#!/usr/bin/env bash
# Деплой НА СЕРВЕРЕ из git-ремоута ~/garden/faktura.git (локально: git push server main).
#   ssh hima-gpu "bash ~/garden/backend/deploy/server-deploy.sh [--ml] [ref]"
# Образы собираются на сервере поверх уже загруженных (кеш слоёв): пересобирается только слой с кодом.
# RUN-шаги идут без сети (--network=none): если поменялись зависимости и кеша нет, сборка сразу падает
# (PyPI с сервера очень медленный) — тогда один раз отправить образы с локальной машины: bash backend/deploy/deploy.sh [--ml]
set -euo pipefail
WITH_ML=0
REF="main"
for a in "$@"; do
  case "$a" in
    --ml) WITH_ML=1 ;;
    *) REF="$a" ;;
  esac
done
REPO="$HOME/garden/faktura.git"
DST="$HOME/garden/backend"
SRC="$(mktemp -d)"
trap 'rm -rf "$SRC"' EXIT

echo "== код $(git -C "$REPO" rev-parse --short "$REF") -> $DST"
git -C "$REPO" archive "$REF" | tar -x -C "$SRC"
mkdir -p "$DST"
git -C "$REPO" archive "$REF:backend" | tar -x -C "$DST"   # deploy/.env и deploy/case не в git — не трогаются

build() {  # build <image> <context>
  echo "== сборка $1 (кеш слоёв, без сети)"
  if ! docker build -q --network=none --cache-from "$1" --build-arg BUILDKIT_INLINE_CACHE=1 -t "$1" "$2"; then
    echo "!! $1 не собрался из кеша (вероятно, поменялись зависимости). Отправьте образ с локальной машины:" >&2
    echo "   bash backend/deploy/deploy.sh $( [[ $1 == faktura-ml* ]] && echo --ml )" >&2
    exit 1
  fi
}
build faktura-backend:latest "$SRC/backend"
if [[ "$WITH_ML" == 1 ]]; then
  build faktura-ml:latest "$SRC/ml/pdf-dataset"
fi

echo "== запуск"
cd "$DST/deploy"
bash init-env.sh
docker compose up -d --no-build --remove-orphans
docker compose ps --format "table {{.Service}}\t{{.Status}}"
