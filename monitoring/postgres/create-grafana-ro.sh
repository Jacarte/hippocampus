#!/usr/bin/env sh
# Create or update the read-only Postgres role grafana_ro used by Grafana
# (datasource uid mem0-pg). Idempotent; run once after the stack is up and again
# after changing GRAFANA_PG_PASSWORD (then recreate Grafana: docker compose up -d grafana).
# Reads only exported shell variables, NOT .env: if GRAFANA_PG_PASSWORD lives in .env prefer
# `docker compose up grafana-pg-init`, which does read .env.
set -eu

cd "$(dirname "$0")"

docker exec -i mem0-postgres psql -X -q -1 \
  -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-postgres}" \
  -v ro_password="${GRAFANA_PG_PASSWORD:-grafana_ro}" \
  < create-grafana-ro.sql

echo "grafana_ro is ready (read-only: SELECT (id, payload) ON public.mem0_memories)."
