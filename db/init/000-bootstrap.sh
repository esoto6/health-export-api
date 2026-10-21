#!/bin/bash
set -e

psql \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 <<SQL
CREATE EXTENSION IF NOT EXISTS timescaledb;

DO \$\$
BEGIN
  IF NOT EXISTS (
    SELECT FROM pg_roles WHERE rolname = 'grafana_reader'
  ) THEN
    CREATE ROLE grafana_reader LOGIN PASSWORD '${GRAFANA_DB_PASSWORD}';
  END IF;
END
\$\$;
SQL

psql \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 \
  -f /docker-entrypoint-initdb.d/001_schema.sql