# Mem0 monitoring

Two telemetry paths feed Grafana (http://localhost:3000):

| Path | Source | Store | Dashboards |
| --- | --- | --- | --- |
| Metrics | `mem0-server` `/metrics` | Prometheus | Overview, Retrieval Pipeline, Memory Operations |
| Query events | `mem0-server` stdout (JSON lines) | Loki | **Query Misses**, **Hot Memories** |
| Memory text (display only) | `public.mem0_memories` | Postgres (read-only role `grafana_ro`) | **Hot Memories** (*Top memories by hits*) |

This guide covers the query-event path (Loki + Alloy) and the read-only Postgres lookup of memory text.

## Pipeline

```
mem0-server  --stdout-->  Docker json-file log  --docker.sock (ro)-->  Alloy  --push-->  Loki  <--query--  Grafana
 (query_event,                                    discovery.docker       keep lines            14d          dashboards
  memory_hit JSON                                 only "mem0-server"     starting with         retention    Query Misses
  + plain text logs)                                                     {"event":             (compactor)  Hot Memories
                                                                         extract 6 labels
```

Emitter: `server/services/query_events.py` (logger `mem0.events`). One compact JSON object per line:

* `query_event` - one per `/search`, `/retrieve`, `/query`: `ts, request_id, endpoint, query, query_truncated, filters, scopes, limit, user_id, agent_id, run_id, project, hits, lexical, semantic, rerank, degraded, degradation_reasons, latency_ms`. `/query` events carry one extra key, `candidates`: the number of retrieval results *before* the `min_score_memory` threshold (`candidates>0` with `hits=0` means results were filtered by the score threshold). The key is absent on `/search` and `/retrieve`. `project` is the readable id when the client sends one (e.g. `git:github.com/org/repo`), else the hash parsed from `user_id`; it is truncated to 200 chars.
* `memory_hit` - one per returned memory (max 50 per request): `ts, request_id, endpoint, memory_id, rank, score, user_id, agent_id, project`.

Memory text is never logged. Missing ids are `""`; `lexical`, `semantic`, `rerank` are `null` when a stage did not run. `/query` takes `lexical`/`semantic`/`rerank`/`degraded`/`degradation_reasons` from the underlying retrieval response (plus its own `memory_store: ...` reason when retrieval failed).

**Hard failures.** When the backend call of `/search` or `/retrieve` raises, the request still fails as before (same exception, same HTTP error, same `memory_operation_errors_total`), and a best-effort `query_event` is also emitted with `hits=0`, `degraded=true` and `degradation_reasons=[<exception type name>]` (no `memory_hit` lines). These show up in *Zero-hit while degraded*, not in *True misses*. Failures of other operations (add, update, delete, get, history, ...) emit no query event; only the existing Prometheus error panels (Memory Operations dashboard, `memory_operation_errors_total`) cover them.

## Label scheme

Loki labels are low-cardinality only; everything else stays in the log line and is read with `| json`.

| Label | Source | Notes |
| --- | --- | --- |
| `service` | static | always `mem0-server` |
| `event` | JSON `event` | `query_event` or `memory_hit` |
| `endpoint` | JSON `endpoint` | `search`, `retrieve`, `query` |
| `user_id` | JSON `user_id` | omitted when empty |
| `agent_id` | JSON `agent_id` | omitted when empty |
| `project` | JSON `project` | omitted when empty |

Never made labels: `query`, `memory_id`, `request_id` (unbounded cardinality). Use `| json` for them.

Because empty values drop the label, the dashboards use `allValue=".*"` for the variables, so events without a `user_id`/`agent_id`/`project` still match "All".

## Start

```bash
mkdir -p data/loki          # bind-mount target (see DATA_PATH); start.sh also creates it
docker compose up -d loki alloy grafana
```

On Linux the Loki container runs as uid 10001; make the directory writable for it (`sudo chown 10001:10001 data/loki`). Docker Desktop needs nothing.

Grafana does not depend on Loki: the Prometheus-based dashboards start even if Loki fails to start. Only the Loki dashboards (Query Misses, Hot Memories) stay empty until Loki is healthy. Alloy waits for Loki to be healthy.

The read-only Postgres role for the memory text is created automatically: `docker compose up -d` (and `start.sh`) also runs the one-shot service `grafana-pg-init` (see [Memory text](#memory-text-postgres-read-only)). No manual step is needed.

Dashboards (folder root): **Mem0 - Query Misses** (`mem0-query-misses`) and **Mem0 - Hot Memories** (`mem0-hot-memories`). Every Mem0 dashboard has a "Mem0 dashboards" dropdown to jump between them.

Retention is **14 days** (`limits_config.retention_period: 336h` and `compactor.retention_enabled: true` in `loki/loki-config.yaml`). Compaction removes data roughly 2h after it expires. Loki is not published on a host port; only Grafana and Alloy reach it.

## Memory text (Postgres, read-only)

The logs never contain memory text. The *Top memories by hits* table on Hot Memories shows it anyway by joining, at display time, the Loki hit counts with Postgres:

* Query A (Loki): hits per `memory_id` (see below). Query B (datasource **Mem0 Postgres (read-only)**, uid `mem0-pg`):
  ```sql
  SELECT id::text AS memory_id, left(regexp_replace(payload->>'data', '\s+', ' ', 'g'), 100) AS memory
  FROM public.mem0_memories LIMIT 20000
  ```
  Grafana cannot pass the Loki ids into the SQL, so B reads all memories (id + first 100 chars, whitespace collapsed), capped at 20000 rows. Above 20000 memories, some hot memories show an empty `memory`; raise the cap in the dashboard JSON.
* Transformations: *Labels to fields* (query A), *Merge* (rows with the same `memory_id` are combined; `memory_id` is the only field both queries share, so this is an outer join), *Filter data by values* (drop rows without hits), *Organize fields* (`memory_id`, `memory`, `Hits`), *Sort by* `Hits`.
* The text is the **current** text in Postgres, not the text at the time of the hit. Deleted memories still appear (they have hits) with an empty `memory`.
* The heatmap *Hot memories over time* keeps `memory_id` as row labels: Grafana 11.3 has no per-series lookup that could rename heatmap rows from another query. Use the table for the text.

**Role `grafana_ro`.** Defined in `postgres/create-grafana-ro.sql` (idempotent): `LOGIN`, no superuser/createdb/createrole/replication, `CONNECTION LIMIT 5`, `CONNECT` on the database, `USAGE` on schema `public`, and a column-level `SELECT (id, payload)` on `public.mem0_memories` only (no `vector` column, no `SELECT *`, no other table). It also sets `default_transaction_read_only = on` and `statement_timeout = 5s`. Writes to real tables are refused by the privileges even if a session turns read-only off (the session can still create temp tables).

**Creation.** Postgres init scripts only run on an empty data dir, so the role is applied by the one-shot compose service `grafana-pg-init` (container `mem0-grafana-pg-init`, image `pgvector/pgvector:pg16`, `restart: "no"`). It starts after `postgres` and `mem0-server` are healthy, waits up to 5 minutes for `mem0-server` to create `public.mem0_memories` (fresh databases), applies the SQL and exits 0. On timeout it exits 1 with an error in `docker compose logs grafana-pg-init`. It re-runs harmlessly on every `docker compose up`. Grafana does **not** depend on it or on Postgres: if the role is missing or Postgres is down, Grafana and every other panel work; the table shows `memory_id` and `Hits` without a `memory` column and an error icon (`password authentication failed for user "grafana_ro"` or a connection error).

Fallback (manual, same SQL): `./monitoring/postgres/create-grafana-ro.sh` (uses `docker exec mem0-postgres psql`; honours exported `POSTGRES_USER`, `POSTGRES_DB`, `GRAFANA_PG_PASSWORD` shell variables, not `.env`; prefer `docker compose up grafana-pg-init` when the password is in `.env`).

**Password.** `GRAFANA_PG_PASSWORD` (default `grafana_ro`) is read by both `grafana-pg-init` and Grafana (datasource provisioning, `secureJsonData`). To change it: set it in `.env`, then `docker compose up -d grafana-pg-init grafana` (Grafana must be recreated, not just restarted, to see a new env value).

## Symptom -> panel -> action

| Symptom | Panel | Action |
| --- | --- | --- |
| Agents say "I found nothing" | Query Misses: *Zero-hit rate*, *True misses* | Open *True misses*; read `query`, `filters`, scope, and (for `/query`) `candidates`: `candidates>0` means results existed but the `min_score_memory` threshold filtered them out. Look the expected memory up in the CMS under the same user/agent. Missing: store it. Present: scope/filter mismatch or wording. |
| Zero-hit rate jumped on all endpoints at once | *Zero-hit queries by endpoint*, then *Zero-hit while degraded* | Read `degradation_reasons`; fix the failing stage (embedder/Ollama, reranker, vector store); re-run the query. |
| Zero-hit rate jumped on one endpoint only | *Zero-hit queries by endpoint* | Look at a client/skill using that endpoint; check its filters and scopes. |
| Results come back but keyword search never helps | *Partial misses* (`lexical=0`, `hits>0`, not degraded) | Memories lack the literal terms agents use (identifiers, rare words, language). Reword/add terms to the memory. |
| One memory is returned all the time | Hot Memories: *Top memories by hits*, *Hot memories over time* | Read the `memory` column (current text); check it is accurate and current; outdated + hot is the most harmful case. Edit it in the CMS by `memory_id`. |
| Good memories are ranked low | *Memory hits by rank* (rank 11+ high) | Compare with the Retrieval Pipeline dashboard (rerank); consider narrower queries or filters. |
| Few distinct memories ever hit | *Distinct memories hit* vs memory count in CMS | Review never-hit memories for cleanup or rewording. |

## LogQL per panel

Grafana replaces `$endpoint`, `$project`, `$agent_id`, `$user_id`. When pasting in Explore, replace each with `.*` (or a concrete value). Write the stream selector once:

```
{service="mem0-server",event="query_event",endpoint=~"$endpoint",project=~"$project",agent_id=~"$agent_id",user_id=~"$user_id"}
```

`| __error__=""` is required after numeric filters: events with a `null` stage count (for example `lexical`) fail the numeric comparison and would otherwise pass through with an `__error__` label.

**Query Misses**

* Zero-hit rate (stat, instant):
  ```
  (sum(count_over_time({service="mem0-server",event="query_event",endpoint=~"$endpoint",project=~"$project",agent_id=~"$agent_id",user_id=~"$user_id"} | json hits="hits" | hits=0 | __error__="" [$__range])) or vector(0))
  / sum(count_over_time({service="mem0-server",event="query_event",endpoint=~"$endpoint",project=~"$project",agent_id=~"$agent_id",user_id=~"$user_id"} [$__range]))
  ```
* Query events (stat):
  ```
  sum(count_over_time({service="mem0-server",event="query_event",endpoint=~"$endpoint",project=~"$project",agent_id=~"$agent_id",user_id=~"$user_id"} [$__range])) or vector(0)
  ```
* True misses (stat; the table panel uses the same filter without the `sum(count_over_time(... [$__range]))` wrapper):
  ```
  sum(count_over_time({service="mem0-server",event="query_event",endpoint=~"$endpoint",project=~"$project",agent_id=~"$agent_id",user_id=~"$user_id"} | json hits="hits", degraded="degraded" | hits=0 and degraded="false" | __error__="" [$__range])) or vector(0)
  ```
* Zero-hit queries by endpoint (time series):
  ```
  sum by (endpoint) (count_over_time({service="mem0-server",event="query_event",endpoint=~"$endpoint",project=~"$project",agent_id=~"$agent_id",user_id=~"$user_id"} | json hits="hits" | hits=0 | __error__="" [$__interval]))
  ```
* True misses (table, logs query):
  ```
  {service="mem0-server",event="query_event",endpoint=~"$endpoint",project=~"$project",agent_id=~"$agent_id",user_id=~"$user_id"} | json hits="hits", degraded="degraded" | hits=0 and degraded="false" | __error__=""
  ```
* Partial misses (table):
  ```
  {service="mem0-server",event="query_event",endpoint=~"$endpoint",project=~"$project",agent_id=~"$agent_id",user_id=~"$user_id"} | json hits="hits", lexical="lexical", degraded="degraded" | lexical=0 and hits>0 and degraded="false" | __error__=""
  ```
* Zero-hit while degraded (table):
  ```
  {service="mem0-server",event="query_event",endpoint=~"$endpoint",project=~"$project",agent_id=~"$agent_id",user_id=~"$user_id"} | json hits="hits", degraded="degraded" | hits=0 and degraded="true" | __error__=""
  ```

The table panels show the JSON line fields as columns via the `extractFields` (source `Line`, JSON) and `organize` transformations; `filters` and `degradation_reasons` are shown as JSON text. The *True misses* table also shows `candidates` (empty for `/search` and `/retrieve`).

Partial misses requires `degraded="false"` so outages (where `lexical` is 0 because the stage failed) are not read as vocabulary gaps; those events are listed under *Zero-hit while degraded* when `hits=0`.

**Hot Memories** (selector uses `event="memory_hit"`)

* Memory hits (stat): `sum(count_over_time({service="mem0-server",event="memory_hit",endpoint=~"$endpoint",project=~"$project",agent_id=~"$agent_id",user_id=~"$user_id"} [$__range])) or vector(0)`
* Distinct memories hit (stat): `count(sum by (memory_id) (count_over_time({service="mem0-server",event="memory_hit",endpoint=~"$endpoint",project=~"$project",agent_id=~"$agent_id",user_id=~"$user_id"} | json memory_id="memory_id" [$__range]))) or vector(0)`
* Hot memories over time (heatmap):
  ```
  topk(20, sum by (memory_id) (count_over_time({service="mem0-server",event="memory_hit",endpoint=~"$endpoint",project=~"$project",agent_id=~"$agent_id",user_id=~"$user_id"} | json memory_id="memory_id" [$__interval])))
  ```
  Grafana's Heatmap panel (`calculate=false`) renders one Loki series per `memory_id` as one row. `topk` is evaluated per time bucket, so over a long range more than 20 rows can appear.
* Top memories by hits (table, instant over the range; query A, joined with the Postgres query B, see [Memory text](#memory-text-postgres-read-only)):
  ```
  topk(20, sum by (memory_id) (count_over_time({service="mem0-server",event="memory_hit",endpoint=~"$endpoint",project=~"$project",agent_id=~"$agent_id",user_id=~"$user_id"} | json memory_id="memory_id" [$__range])))
  ```
* Memory hits by rank (stacked bars; four queries, shown for rank 1, repeat with `rank>=2 and rank<=3`, `rank>=4 and rank<=10`, `rank>=11`):
  ```
  sum(count_over_time({service="mem0-server",event="memory_hit",endpoint=~"$endpoint",project=~"$project",agent_id=~"$agent_id",user_id=~"$user_id"} | json rank="rank" | rank=1 | __error__="" [$__interval]))
  ```

Memory text is not in the logs: the *Top memories by hits* table reads it from Postgres at display time.

## Privacy and security

* `query_event` lines contain the **raw query text** (first 500 chars) and the **filters** of every search. They are stored in Loki for 14 days and shown in the Query Misses tables.
* The Hot Memories table shows **memory text** (first 100 chars) from Postgres. The datasource `mem0-pg` can read `id` and the whole `payload` (text, user/agent ids, metadata) of every memory, for all users and projects.
* Grafana runs with **anonymous access as Admin** (`GF_AUTH_ANONYMOUS_ORG_ROLE: Admin`, port 3000): anyone who can reach port 3000 can read all of it and can also open Explore on Loki and on `mem0-pg` (read-only, but full `payload`). Do not expose port 3000 beyond trusted hosts; this setup is meant for local use.
* The `alloy` container mounts **`/var/run/docker.sock` read-only**. Read-only on the socket file does not limit the Docker API: a compromised Alloy container could still control Docker. Alloy is pinned, has no published ports, and `no-new-privileges` is set; keep it that way.
* Do not log secrets in queries. If a project stores sensitive data, remove the `query` and `filters` fields at the source (`server/services/query_events.py`) rather than hiding them in dashboards.

## Troubleshooting

No data in the dashboards:

1. Is the stack up? `docker compose ps loki alloy grafana` (loki must be `healthy`).
2. Does the server emit events? `docker logs mem0-server 2>&1 | grep '^{"event":' | tail -3`. Each event must be a single line that **starts with `{"event":`** (compact separators, no leading timestamp or prefix). Wrapped or prefixed lines are dropped by Alloy. Run a search first; events only appear when queries happen. An older `mem0-server` image without the emitter prints none: rebuild (`docker compose up -d --build mem0-server`).
3. Alloy: `docker compose logs alloy | tail -50`. Look for config errors, `permission denied` on `/var/run/docker.sock`, or push errors to `http://loki:3100`. After `mem0-server` is recreated, discovery can take up to about a minute to pick up the new container.
4. Loki received something? `docker compose exec loki wget -qO- 'http://localhost:3100/loki/api/v1/label/event/values'` should list `query_event` / `memory_hit`.
5. Wrong time range: events older than 14 days are rejected/deleted; check the dashboard range.
6. Loki fails to start with `permission denied` on `/loki`: fix ownership of `data/loki` (see Start).
7. Variables show only "All": no labelled events exist yet in the range, or the events have empty `project`/`agent_id`/`user_id` (those labels are omitted).

Memory text missing in *Top memories by hits*:

1. No `memory` column and an error icon: query B failed. `docker compose logs grafana-pg-init` (did it exit 0?), `docker compose ps -a grafana-pg-init`. Re-run it: `docker compose up grafana-pg-init` (or `./monitoring/postgres/create-grafana-ro.sh`).
2. After `POST /reset` (or any change that recreates `mem0_memories`) the grants are gone: run `docker compose up grafana-pg-init`. The table name `mem0_memories` is fixed in the role SQL, the init wait and the dashboard query; a custom `POSTGRES_COLLECTION` needs those edited.
3. Datasource check: `curl -s http://localhost:3000/api/datasources/uid/mem0-pg/health` should return `Database Connection OK`. `password authentication failed`: `GRAFANA_PG_PASSWORD` differs between the role and Grafana; re-run `grafana-pg-init` and recreate Grafana (`docker compose up -d grafana`).
4. Column present but some cells empty: the memory was deleted, or the 20000-row cap of query B was reached.

Data volume note: Alloy keeps no persistent read position; logs written while Alloy is stopped are not back-filled.
