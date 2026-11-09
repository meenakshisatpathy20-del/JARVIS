#!/usr/bin/env bash
# Used by .devcontainer/devcontainer.json:
#   setup -> create .env with random secrets (first creation only)
#   start -> wait for Docker, then start the hosted demo on port 8080 (every start / wake-up)
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  for k in POSTGRES_PASSWORD APP_DB_PASSWORD CHAOS_DB_PASSWORD CHAOS_TOKEN INGEST_TOKEN DIAGNOSIS_SIGNING_KEY; do
    echo "$k=$(openssl rand -hex 16)"
  done > .env
  printf 'GEMINI_API_KEY=\nSITE_ADDRESS=:80\nPUBLIC_HTTP_PORT=8080\n' >> .env
  echo ".env created with random secrets"
fi
[ "${1:-start}" = "setup" ] && exit 0

echo "waiting for Docker..."
for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
docker compose -f docker-compose.yml -f docker-compose.deploy.yml up -d --build
echo
echo "J.A.R.V.I.S. is starting on port 8080. In the PORTS tab, set port 8080 to Public and open its URL."
echo "Wait ~60 s for warm-up before breaking anything in /lab."
