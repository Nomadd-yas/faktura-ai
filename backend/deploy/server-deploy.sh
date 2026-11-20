#!/usr/bin/env bash
# Быстрый деплой НА СЕРВЕРЕ из git-ремоута ~/garden/faktura.git. Локально:
#   git push server main && ssh hima-gpu "bash ~/garden/backend/deploy/server-deploy.sh [--ml] [ref]"
# Образ собирается без сети поверх последнего отправленного с локальной машины (<image>:shipped):
# меняется только слой с кодом (deploy/overlay/*.Dockerfile). Если с момента отправки поменялись зависимости
# или Dockerfile — скрипт останавливается: тогда один раз  bash backend/deploy/deploy.sh [--ml]  с локальной машины.
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

# overlay <name> <context> <deps-paths...>
overlay() {
  local name="$1" ctx="$2"; shift 2
  local stamp="$DST/deploy/.shipped-$name"
  if ! docker image inspect "faktura-$name:shipped" >/dev/null 2>&1 || [[ ! -f "$stamp" ]]; then
    echo "!! нет отправленного образа faktura-$name:shipped — сначала с локальной машины: bash backend/deploy/deploy.sh $( [[ $name == ml ]] && echo --ml )" >&2
    exit 1
  fi
  local base; base="$(cat "$stamp")"
  if ! git -C "$REPO" diff --quiet "$base" "$REF" -- "$@"; then
    echo "!! с отправки образа $name ($base) поменялись зависимости/Dockerfile:" >&2
    git -C "$REPO" diff --stat "$base" "$REF" -- "$@" >&2
    echo "   отправьте образ с локальной машины: bash backend/deploy/deploy.sh $( [[ $name == ml ]] && echo --ml )" >&2
    exit 1
  fi
  echo "== faktura-$name:latest = shipped($base) + код"
  docker build -q --network=none -f "$SRC/backend/deploy/overlay/$name.Dockerfile" -t "faktura-$name:latest" "$ctx" >/dev/null
}
overlay backend "$SRC/backend" backend/pyproject.toml backend/Dockerfile
if [[ "$WITH_ML" == 1 ]]; then
  overlay ml "$SRC/ml/pdf-dataset" ml/pdf-dataset/requirements.txt ml/pdf-dataset/Dockerfile
fi

echo "== запуск"
cd "$DST/deploy"
bash init-env.sh
docker compose up -d --no-build --remove-orphans
docker compose ps --format "table {{.Service}}\t{{.Status}}"
