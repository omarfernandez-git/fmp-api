#!/usr/bin/env bash
# Compila la web en local y sube API + web al servidor por SSH/rsync.
# Uso: deploy/deploy.sh [usuario@servidor]      (por defecto apache02)
# Requiere: rsync en local y en el servidor, y que el usuario SSH pueda escribir en /opt/fmp y /var/www/html/fmp
# (o tener sudo sin contraseña para rsync: ver README). No sube data/ ni .env: la base de datos se sube aparte la primera vez.
set -euo pipefail
HOST="${1:-apache02}"
API_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WEB_DIR="$(cd "$API_DIR/../fmp" && pwd)"

echo "== Compilando la web (Angular producción)"
(cd "$WEB_DIR" && npm run build)

echo "== Subiendo la API a $HOST:/opt/fmp"
rsync -az --delete --rsync-path="sudo rsync" \
  --exclude '.env' --exclude 'data/' --exclude '.venv/' --exclude '__pycache__/' --exclude '.git/' --exclude 'web/' \
  "$API_DIR/" "$HOST:/opt/fmp/"

echo "== Subiendo la web a $HOST:/var/www/html/fmp"
rsync -az --delete --rsync-path="sudo rsync" "$WEB_DIR/dist/frontend/browser/" "$HOST:/var/www/html/fmp/"

echo "== Dependencias y reinicio en el servidor"
ssh "$HOST" 'sudo bash -c "cd /opt/fmp && (test -x .venv/bin/python || python3 -m venv .venv) && .venv/bin/pip install -q -r requirements.txt && chown -R www-data:www-data /opt/fmp /var/www/html/fmp && systemctl restart fmp && sleep 2 && systemctl is-active fmp && curl -s -o /dev/null -w \"API local: %{http_code}\n\" http://127.0.0.1:8000/api/refresh"'
echo "== Listo: https://mv.greensysit.net"
