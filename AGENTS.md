# AGENTS.md — hippocampus (Mem0 server + CMS + monitoring)

Self-hosted Mem0 REST server, admin CMS, and a Grafana/Loki/Prometheus stack.
The active client is the **pi agent** (`~/.pi/agent/extensions/hippocampus`), not
opencode. Client changes go there, not in this repo's opencode plugin.

## Layout

| Path | What |
| --- | --- |
| `server/` | FastAPI app (`server.py`, `api_models.py`, `services/`), pytest in `server/tests/` |
| `cms/` | React/Vite admin UI, served by nginx on :8080, calls `/admin/*` only |
| `monitoring/` | Prometheus, Loki, Alloy, Grafana provisioning, `grafana_ro` role scripts |
| `docker-compose.yaml` | postgres(pgvector), mem0-server, mem0-cms, prometheus, loki, alloy, grafana, grafana-pg-init |

Ports: server 8000, CMS 8080, Grafana 3000, Prometheus 9090, Postgres 15442.
Start with `./start.sh`; rebuild one service with `docker compose up -d --build <svc>`.

## Checks

- Server: `cd server && pytest tests/ -v` (offline, fakes).
- CMS: `cd cms && bun run lint && bun run test && bun run build`.
- Dashboards: validate every SQL/LogQL against the live stack before finishing.

## How memory scoping works

- The pi client builds `user_id = <userId>:project:<hash(projectId)[:24]>` and sends `project`.
  Recall reads the project `user_id` plus every id in `legacyUserIds`
  (`~/.pi/agent/settings.json`, max 10; profiles under `~/.pi/agent/profiles/*` have their own copy).
- There is no built-in global scope. A "global" memory = a memory under a fixed
  `user_id` that is added to `legacyUserIds`. It competes for the `recallLimit` slots.
- `autoCapture` writes only to the project `user_id`.
- The pi client sends no `agent_id`, so the `agent_id` label/dropdown is empty.

## Retrieval and decay

- `/retrieve` reranks with `HeuristicReranker`; decay is on by default
  (`MEM0_RETRIEVAL_DECAY_ENABLED=true`, `MEM0_RETRIEVAL_DECAY_WEIGHT=0.05`).
- `recency = 0.5 ** (age_days / half_life)`, from `created_at` (edits do not reset it);
  expired `ttl_expires_at` quarters it. `score += recency * weight` (additive, max 0.05).
- Half-life: explicit `decay_half_life_days`, else by type: decision/stable-fact 30d,
  procedure 7d, problem-fix 4d, anything else **including untyped: 30d** (was 4d until 2026-11-20;
  `_FALLBACK_HALF_LIFE_DAYS` in `retrieval_service.py` and `deriveHalfLifeDays` in `cms/src/lib/decay.ts`).
- The CMS formula (`cms/src/lib/decay.ts`) and `retrieval_service.py` must stay identical.
- Since 2026-11-20 the extraction policy labels new `infer=true` memories with a `type` (stored top-level); older rows stay untyped (no backfill).
- As of 2026-11-20 ~96% of memories were untyped; with the old 4d fallback ~47% had recency < 0.05.
- `/search` and `/query` do not apply decay.

## Observability: what emits what

- `query_event` and `memory_hit` JSON lines are emitted **only** by `/search`, `/retrieve`,
  `/query` (`server/services/query_events.py`). CMS `/admin/*` browsing emits nothing
  except generic HTTP metrics. So "I used the CMS and Grafana shows nothing" is expected;
  hot-memory panels reflect agent search traffic.
- Events must be a single stdout line starting with `{"event":`; Alloy drops anything else.
  Events carry ids, counts, scores, query (capped 500 chars) — never memory text.
- Loki labels are low-cardinality only: `service, event, endpoint, user_id, agent_id, project`.
  Never label `query`, `memory_id`, `request_id`. Empty values drop the label, so
  template variables use `allValue=".*"`.
- Memory text in dashboards comes from Postgres via the read-only `grafana_ro` role
  (datasource uid `mem0-pg`, table `mem0_memories`, SELECT on `id` and `payload` only),
  created by the `grafana-pg-init` compose service. After `POST /reset`, re-run
  `docker compose up grafana-pg-init`.
- Postgres `payload` holds `data`, `created_at`, `user_id`, and (on ~4% of rows) `type`,
  `decay_half_life_days`, `tier`, `access_count`, `last_used_at`, `anchor_*`.
- Project names in the Decay dashboard: 24-char hashes are resolved from `payload->>'project_id'`
  (sha256 of the id, first 24 hex chars); legacy 12-char hashes (sha256 of the working-directory path, no longer written)
  come from a static `names` VALUES list inside the dashboard SQL, built by hashing local directories. 8 legacy hashes
  stay `unknown <hash>`. New legacy hashes will not appear, so the list does not need updates.
- Full details and troubleshooting: `monitoring/README.md`.

## Project overview bootstrap (pi client)

On the first prompt of a session, if the project has no memory with `capture_kind: overview`
(checked with `/retrieve` and that filter) and was not asked in the last 7 days
(`~/.pi/agent/hippocampus-bootstrap.json`), the plugin appends a trusted notice asking the parent agent
to save a 3-5 line overview with `mem0_memory add kind="overview"`. It is stored as `type: stable-fact`,
`decay_half_life_days: 180`. Read-only subagents never get the notice. Code: `register.ts` (`BOOTSTRAP_INSTRUCTION`),
`core.ts` (`hasOverview`, `createCapture`).

## Grafana dashboards (MANDATORY rules)

Dashboards live in `monitoring/grafana/provisioning/dashboards/*.json`
(provisioned, reloaded every 10s; no UI-only edits, they are lost).

1. **Every dashboard must open with a text panel titled "How to read this dashboard"**
   (markdown, full width, top of the page) explaining, for a beginner: what the data is and
   where it comes from, how to read the main panels, **what to look for**, and its limits
   (e.g. "shows the corpus, not what `/retrieve` returned").
2. **Every panel must have a `description`** (the hover "i" tooltip; no extra text panels
   next to or above plots). Put the helper in the description, in markdown, in three parts:
   - **Read:** what the axes/columns/colours mean.
   - **Look for:** the pattern that signals a problem or a good state (with thresholds if any).
   - **Example:** one concrete case using real values (e.g. "2,739 untyped memories in `<0.05`").
   Keep it short (3-5 lines); separate parts with blank lines. Existing descriptive text may stay
   before the **Read:** part.
3. Datasources: Prometheus (aggregates), Loki uid `loki` (per-query events),
   Postgres uid `mem0-pg` (corpus state). Pick the one that actually holds the data.
4. Reuse the standard header: `tags: ["mem0", ...]`, the "Mem0 dashboards" links block,
   `uid: mem0-<name>`, `schemaVersion: 39`.
5. Test every query against the live source (`docker exec mem0-postgres psql ...`,
   Loki `/loki/api/v1/query`) and confirm the dashboard loads via
   `curl -u admin:admin localhost:3000/api/search?query=<title>`.
6. `fieldConfig.overrides` must be a list (`[]`), never `{}`: a dict leaves panels stuck on
   "loading plugin panel".
7. A new dashboard must be mentioned in `monitoring/README.md` (symptom -> panel -> action).

Existing: `mem0-overview`, `mem0-memory-operations`, `mem0-retrieval-pipeline`,
`mem0-hot-memories`, `mem0-query-misses`, `mem0-decay`.

## Gotchas

- Grafana only loads new datasources on start: `docker compose restart grafana` after
  datasource changes (a missing Loki datasource once came from this).
- Alloy has no persistent read position; logs written while it is stopped are not backfilled.
  After `mem0-server` is recreated, Alloy can take ~1 min to pick it up.
- `mem0-cms` healthcheck shows `unhealthy` (wget on `localhost` fails, likely IPv6); the UI
  works. Use `127.0.0.1` if fixing.
- Never commit `.env` or secrets; do not put memory text or secrets in log events or labels.
