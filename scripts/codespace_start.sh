#!/usr/bin/env bash
# Used by .devcontainer/devcontainer.json:
#   setup -> create .env with random secrets (first creation only)
#   start -> wait for Docker, then start the hosted demo on port 8000 (every start / wake-up)
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  for k in POSTGRES_PASSWORD APP_DB_PASSWORD CHAOS_DB_PASSWORD CHAOS_TOKEN INGEST_TOKEN DIAGNOSIS_SIGNING_KEY; do
    echo "$k=$(openssl rand -hex 16)"
  done > .env
  printf 'GEMINI_API_KEY=\nSITE_ADDRESS=:80\n' >> .env
  echo ".env created with random secrets"
fi
# lab key: protects Break/Heal/Reveal and fix approval on the public link
grep -q '^LAB_KEY=.\+' .env 2>/dev/null || { sed -i '/^LAB_KEY=/d' .env 2>/dev/null; echo "LAB_KEY=jarvis-$(openssl rand -hex 3)" >> .env; }
# the hosted demo is served on port 8000
if grep -q '^PUBLIC_HTTP_PORT=' .env; then sed -i 's/^PUBLIC_HTTP_PORT=.*/PUBLIC_HTTP_PORT=8000/' .env; else echo "PUBLIC_HTTP_PORT=8000" >> .env; fi
[ "${1:-start}" = "setup" ] && exit 0

echo "waiting for Docker..."
for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
docker compose -f docker-compose.yml -f docker-compose.deploy.yml down --remove-orphans >/dev/null 2>&1 || true
docker compose -f docker-compose.yml -f docker-compose.deploy.yml up -d --build
echo
echo "J.A.R.V.I.S. is starting on port 8000. In the PORTS tab, set port 8000 to Public and open its URL."
echo "Wait ~60 s for warm-up before breaking anything in /lab."
echo "LAB KEY (give it to judges): $(grep '^LAB_KEY=' .env | cut -d= -f2)"
