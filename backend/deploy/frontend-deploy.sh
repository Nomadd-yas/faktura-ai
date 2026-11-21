#!/usr/bin/env bash
# Выкладка фронтенда (репозиторий hack-lock, приватный) на сервер: исходники клонируются ЛОКАЛЬНО (там есть доступ к
# GitHub), передаются по ssh, собираются на сервере в контейнере node:22 и подменяют /var/www/faktura атомарно.
# nginx не устанавливается и не трогается (его ставит ML-2); конфиг сайта - backend/deploy/nginx/factura.conf.
#   bash backend/deploy/frontend-deploy.sh [ssh-host] [git-ref]     (по умолчанию faktura-gpu, main)
set -euo pipefail
HOST="${1:-faktura-gpu}"
REF="${2:-main}"
REPO="${FRONTEND_REPO:-https://github.com/Hahora/hack-lock.git}"
SRC="$(mktemp -d)"
trap 'rm -rf "$SRC"' EXIT
git clone -q --depth 1 --branch "$REF" "$REPO" "$SRC/hack-lock"
echo "== фронтенд $(git -C "$SRC/hack-lock" log --oneline -1)"
ssh "$HOST" "bash -c 'rm -rf /root/frontend-src && mkdir -p /root/frontend-src'"
tar -C "$SRC/hack-lock" --exclude=.git --exclude=node_modules -cf - . | ssh "$HOST" "bash -c 'tar -x -C /root/frontend-src'"
ssh "$HOST" "bash -c 'set -e; cd /root/frontend-src
  printf \"VITE_DATA_SOURCE=api\nVITE_API_URL=/api/v1\n\" > .env.production
  docker run --rm -v /root/frontend-src:/app -w /app node:22-alpine sh -c \"npm ci --no-audit --no-fund --loglevel=error && npm run build\" | tail -3
  rm -rf /var/www/faktura.new && cp -r dist /var/www/faktura.new && chown -R www-data:www-data /var/www/faktura.new
  rm -rf /var/www/faktura.old; if [ -d /var/www/faktura ]; then mv /var/www/faktura /var/www/faktura.old; fi
  mv /var/www/faktura.new /var/www/faktura && echo выложено: /var/www/faktura'"
