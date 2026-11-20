#!/usr/bin/env bash
# Создаёт deploy/.env со случайными секретами, если его ещё нет. Секреты не печатаются.
# Пароль админа потом смотреть на сервере: grep FAKTURA_ADMIN_PASSWORD deploy/.env
set -euo pipefail
cd "$(dirname "$0")"
if [[ -f .env ]]; then
  echo ".env уже есть — не трогаю"
  exit 0
fi
umask 077
gen() { head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c "${1:-32}"; }
cat > .env <<EOF
API_PORT=8000
POSTGRES_PASSWORD=$(gen 32)
FAKTURA_JWT_SECRET=$(gen 64)
FAKTURA_ADMIN_EMAIL=admin@faktura.local
FAKTURA_ADMIN_PASSWORD=$(gen 16)
# Без HTTPS refresh-cookie с флагом Secure браузер не сохранит
FAKTURA_COOKIE_SECURE=false
# Адрес фронта (JSON-список), добавить реальный адрес фронта
FAKTURA_CORS_ORIGINS=["http://localhost:5200"]
FAKTURA_RUN_CONCURRENCY=4
# Настоящий ML-1: раскомментировать и указать адрес
# FAKTURA_EXTRACT_URL=http://<ml-host>:<port>
EOF
echo ".env создан (секреты сгенерированы, в вывод не попадают)"
