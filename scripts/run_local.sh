#!/usr/bin/env bash
# No-Docker fallback (playbook "Docker is slow or broken"): runs every component with uvicorn
# against a local Postgres 16 started with max_connections=20.
#   1. Create DB + roles once:  bash scripts/setup_local_db.sh
#   2. Start everything:        bash scripts/run_local.sh
#   3. Stop everything:         bash scripts/run_local.sh stop
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
RUN=$ROOT/.local/run
mkdir -p "$RUN"
PY=${PYTHON:-python3}

stop() {
  for f in "$RUN"/*.pid; do [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null || true; rm -f "$f"; done
  echo "stopped"
}
[ "${1:-}" = "stop" ] && { stop; exit 0; }
if [ "${1:-}" = "start-auth" ]; then
  APP_DSN="host=127.0.0.1 port=5432 dbname=payments user=app password=${APP_DB_PASSWORD:-app}"
  export JARVIS_URL=http://127.0.0.1:8000
  ( cd "$ROOT/platform/auth"; SERVICE_NAME=auth-service PYTHONPATH="$ROOT/platform/common" DB_DSN="$APP_DSN" \
      setsid $PY -m uvicorn main:app --host 127.0.0.1 --port 8002 --log-level warning > "$RUN/auth-service.log" 2>&1 < /dev/null & echo $! > "$RUN/auth-service.pid" )
  exit 0
fi

export CHAOS_TOKEN=${CHAOS_TOKEN:-local-chaos-token}
export JARVIS_URL=http://127.0.0.1:8000
APP_DSN="host=127.0.0.1 port=5432 dbname=payments user=app password=${APP_DB_PASSWORD:-app}"

start_svc() {  # name dir port extra-env...
  local name=$1 dir=$2 port=$3; shift 3
  ( cd "$ROOT/platform/$dir"; exec env SERVICE_NAME="$name" PYTHONPATH="$ROOT/platform/common" "$@" \
      $PY -m uvicorn main:app --host 127.0.0.1 --port "$port" --log-level warning \
      > "$RUN/$name.log" 2>&1 & echo $! > "$RUN/$name.pid" )
}

start_auth() { start_svc auth-service auth 8002 DB_DSN="$APP_DSN"; }
export -f start_svc start_auth 2>/dev/null || true

( cd "$ROOT"; exec env MONITOR_DB_DSN="host=127.0.0.1 port=5432 dbname=payments user=postgres password=${POSTGRES_PASSWORD:-postgres}" \
    CHAOS_PUBLIC_URL=http://localhost:8090 \
    GATEWAY_HEALTH=http://127.0.0.1:8001/health AUTH_HEALTH=http://127.0.0.1:8002/health PAYMENT_HEALTH=http://127.0.0.1:8003/health \
    $PY -m uvicorn jarvis.app:app --host 0.0.0.0 --port 8000 --log-level warning > "$RUN/jarvis.log" 2>&1 & echo $! > "$RUN/jarvis.pid" )
start_auth
start_svc payment-service payment 8003 DB_DSN="$APP_DSN" AUTH_URL=http://127.0.0.1:8002
start_svc gateway gateway 8001 AUTH_URL=http://127.0.0.1:8002 PAYMENT_URL=http://127.0.0.1:8003
( cd "$ROOT/chaos"; exec env CHAOS_DATA_DIR="$ROOT/.local/chaos-data" PAYMENT_URL=http://127.0.0.1:8003 \
    CHAOS_DB_DSN="host=127.0.0.1 port=5432 dbname=payments user=chaos password=${CHAOS_DB_PASSWORD:-chaos}" \
    DOCKER_SOCK=/nonexistent \
    CHAOS_AUTH_STOP_CMD="kill \$(cat $RUN/auth-service.pid)" \
    CHAOS_AUTH_START_CMD="bash $ROOT/scripts/run_local.sh start-auth" \
    $PY -m uvicorn main:app --host 127.0.0.1 --port 8090 --log-level warning > "$RUN/chaos.log" 2>&1 & echo $! > "$RUN/chaos.pid" )
sleep 3
( cd "$ROOT/platform/loadgen"; exec env GATEWAY_URL=http://127.0.0.1:8001 $PY run.py > "$RUN/loadgen.log" 2>&1 & echo $! > "$RUN/loadgen.pid" )
echo "J.A.R.V.I.S. dashboard: http://localhost:8000   lab: http://localhost:8000/lab   (logs in .local/run)"
