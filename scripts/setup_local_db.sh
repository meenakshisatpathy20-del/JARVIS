#!/usr/bin/env bash
# Creates the payments DB, roles and seed data on a local Postgres 16 (max_connections=20).
set -euo pipefail
cd "$(dirname "$0")/.."
H=${PGHOST:-127.0.0.1}
psql -h "$H" -U postgres -tAc "SHOW max_connections" | grep -qx 20 || echo "WARNING: set max_connections=20 in postgresql.conf and restart Postgres"
psql -h "$H" -U postgres -qc "CREATE DATABASE payments" || true
psql -h "$H" -U postgres -d payments -qc "DO \$\$BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app') THEN CREATE ROLE app LOGIN PASSWORD '${APP_DB_PASSWORD:-app}'; END IF; IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='chaos') THEN CREATE ROLE chaos LOGIN PASSWORD '${CHAOS_DB_PASSWORD:-chaos}'; END IF; END\$\$;"
psql -h "$H" -U postgres -d payments -q -f db/schema.sql.txt
echo "local DB ready"
