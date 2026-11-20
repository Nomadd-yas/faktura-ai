#!/bin/sh
# Точка входа образа: `pdf-dataset serve` -> HTTP API; иначе аргументы идут в pipeline.py.
set -e
if [ "$1" = "serve" ]; then
    shift
    exec uvicorn api:app --host 0.0.0.0 --port "${PORT:-8080}" "$@"
fi
exec python pipeline.py "$@"
