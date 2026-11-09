#!/bin/bash
# Runs once when the Postgres container is first created.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
  CREATE ROLE app LOGIN PASSWORD '${APP_DB_PASSWORD}';
  CREATE ROLE chaos LOGIN PASSWORD '${CHAOS_DB_PASSWORD}';
  GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO app, chaos;
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f /docker-entrypoint-initdb.d/schema.sql.txt
