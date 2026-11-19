-- Read-only Postgres role for Grafana (role name: grafana_ro).
--
-- Grafana uses it to look up the current memory text (payload->>'data') for the
-- memory_id values counted from Loki memory_hit events (Hot Memories dashboard).
-- The role can only connect and SELECT the columns id and payload of
-- public.mem0_memories. It cannot write, create objects, or read the vector column.
--
-- Idempotent: safe to run again (re-sets the password and the grants).
-- Applied automatically by the one-shot compose service grafana-pg-init on every
-- `docker compose up`; manual fallback (reads exported shell vars only, not .env):
--   ./monitoring/postgres/create-grafana-ro.sh
-- Requires the psql variable ro_password (the wrapper passes GRAFANA_PG_PASSWORD).

\set ON_ERROR_STOP on

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro') THEN
    CREATE ROLE grafana_ro;
  END IF;
END
$$;

ALTER ROLE grafana_ro WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT
  NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 5 PASSWORD :'ro_password';

-- Start from zero: drop any privilege granted earlier, then grant the minimum.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM grafana_ro;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM grafana_ro;
REVOKE ALL ON SCHEMA public FROM grafana_ro;
SELECT format('REVOKE ALL ON DATABASE %I FROM grafana_ro', current_database()) \gexec

SELECT format('GRANT CONNECT ON DATABASE %I TO grafana_ro', current_database()) \gexec
GRANT USAGE ON SCHEMA public TO grafana_ro;
-- Column-level grant: id and payload only (no vector column, no SELECT *).
GRANT SELECT (id, payload) ON public.mem0_memories TO grafana_ro;

-- Defence in depth on top of the privileges above.
ALTER ROLE grafana_ro SET default_transaction_read_only = on;
ALTER ROLE grafana_ro SET statement_timeout = '5s';
