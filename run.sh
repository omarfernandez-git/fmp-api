#!/usr/bin/env bash
# Arranca la API. Sirve también la web Angular si está compilada (repo fmp: npm run build).
# Uso: ./run.sh [puerto]
cd "$(dirname "$0")"
exec python3 -m uvicorn fmp.api:app --host ${HOST:-0.0.0.0} --port "${1:-8000}"
