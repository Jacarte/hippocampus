#!/bin/sh
# Entrypoint of the one-shot compose service grafana-pg-init.
# Waits (bounded) until mem0-server has created public.mem0_memories, then applies
# create-grafana-ro.sql (idempotent) to create/update the read-only role grafana_ro.
# Connection settings come from the libpq env vars PGHOST, PGPORT, PGUSER,
# PGPASSWORD, PGDATABASE; the role password from GRAFANA_PG_PASSWORD.
set -eu

timeout_seconds="${GRAFANA_PG_INIT_TIMEOUT_SECONDS:-300}"
interval_seconds=5
elapsed_seconds=0
sql_file="$(dirname "$0")/create-grafana-ro.sql"

echo "grafana-pg-init: waiting for table public.mem0_memories on ${PGHOST}:${PGPORT}/${PGDATABASE} (timeout ${timeout_seconds}s)"
while [ "$(psql -X -tAq -c "SELECT to_regclass('public.mem0_memories') IS NOT NULL" 2>/dev/null || true)" != "t" ]; do
  if [ "$elapsed_seconds" -ge "$timeout_seconds" ]; then
    echo "grafana-pg-init: ERROR: public.mem0_memories not found after ${timeout_seconds}s; role grafana_ro NOT created/updated." >&2
    echo "grafana-pg-init: mem0-server creates the table; check 'docker compose logs mem0-server', then re-run 'docker compose up grafana-pg-init'." >&2
    exit 1
  fi
  sleep "$interval_seconds"
  elapsed_seconds=$((elapsed_seconds + interval_seconds))
  echo "grafana-pg-init: table not there yet (${elapsed_seconds}s)"
done

echo "grafana-pg-init: table found, applying $(basename "$sql_file")"
psql -X -q -1 -v ro_password="${GRAFANA_PG_PASSWORD:?GRAFANA_PG_PASSWORD must be set}" -f "$sql_file"
echo "grafana-pg-init: OK: role grafana_ro ready (read-only: SELECT (id, payload) ON public.mem0_memories)"
