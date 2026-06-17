# End-to-End Testing Guide

This document explains how to test the full Hippocampus stack (server + CMS) end-to-end using `docker-compose`, seeded with production-like data.

## Prerequisites

- Docker Desktop running
- An OpenAI API key with access to `gpt-4` and `text-embedding-3-small`

## Quick Start

### 1. Get your OpenAI key

```bash
read -sp "OpenAI API key: " OPENAI_KEY
```

### 2. Create `.env`

```bash
cp .env.example .env
sed -i '' "s/sk-your-api-key-here/$OPENAI_KEY/" .env
```

### 3. Build and start

```bash
docker compose build --no-cache mem0-server
docker compose up -d
```

Wait for all services to become healthy (~30 seconds):

```bash
docker compose ps
# mem0-postgres: healthy
# mem0-server:   healthy
# mem0-cms:      running
```

### 4. Verify health

```bash
curl http://localhost:8000/admin/health
# {"status":"ok","service":"admin-cms","visit_db_path":"/var/lib/mem0/visits.db"}
```

## Seeding Test Data

The fresh stack starts with zero memories. Seed production-like data by copying rows from the production PostgreSQL instance or creating them via the admin API.

### Option A: Seed from production PostgreSQL

```bash
# Export a sample from production
pg_dump -h 192.168.0.160 -U postgres -d postgres \
  -t mem0_memories --data-only --column-inserts \
  --rows-per-insert=10 --where="payload ? 'data'" \
  | head -200 > seed.sql

# Import into local docker postgres
docker exec -i mem0-postgres psql -U postgres -d postgres < seed.sql
```

### Option B: Create via API

```bash
curl -s -X POST http://localhost:8000/admin/memories \
  -H 'Content-Type: application/json' \
  -d '{
    "scope":"user",
    "scope_id":"alice",
    "messages":[{"role":"user","content":"Alice prefers dark mode in all applications"}],
    "metadata":{"type":"stable-fact","project":"test","decay_half_life_days":120}
  }'
```

## Test Scenarios

### 1. List memories (unscoped)

```bash
curl -s 'http://localhost:8000/admin/memories?page=1&page_size=20' | python3 -m json.tool
```

**Expected**: `total_items` matches seeded count. Every item has non-empty `memory_id` and `content`.
Cards in the CMS at `http://localhost:8080` render with visible text and scope tags.

### 2. List memories (scoped)

```bash
curl -s 'http://localhost:8000/admin/memories?page=1&page_size=5&scope=user&scope_id=alice' | python3 -m json.tool
```

**Expected**: Returns only memories for the given user. Each has a UUID `memory_id`.

### 3. View memory detail

```bash
curl -s http://localhost:8000/admin/memories/<memory_id> | python3 -m json.tool
```

**Expected**: Returns full detail with `content`, `metadata`, `popularity`, `freshness`, and `audit` blocks. All metadata keys visible.

### 4. Update a memory

```bash
curl -s -X PUT http://localhost:8000/admin/memories/<memory_id> \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"UPDATED: new content here"}],"metadata":{"type":"decision","project":"new-project"}}'
```

**Expected**: Returns 200 with updated `content`, `metadata.type`, and `metadata.project`. No 500 error.

### 5. Delete a memory

```bash
curl -s -X DELETE http://localhost:8000/admin/memories/<memory_id>
```

**Expected**: 200 OK. Memory removed from subsequent list calls.

### 6. Copy a memory

```bash
curl -s -X POST http://localhost:8000/admin/memories/<memory_id>/copy \
  -H 'Content-Type: application/json' \
  -d '{"target_scope":"user","target_scope_id":"bob"}'
```

**Expected**: Returns 200 with `target_memory_id` and `copied_from` provenance.

### 7. Search/filter

```bash
# Text search
curl -s 'http://localhost:8000/admin/memories?page=1&page_size=20&query=dark+mode'

# Type filter
curl -s 'http://localhost:8000/admin/memories?page=1&page_size=20&type=stable-fact'
```

**Expected**: Results filtered to matching memories. Count badge reflects filtered count.

## CMS UI Testing

Open `http://localhost:8080` in a browser. Verify:

| Feature | What to check |
|---------|---------------|
| Memory list | Cards visible with content, scope tags (`u:`, `a:`, `r:`), heat count |
| Count badge | Shows correct total matching list response |
| Impersonation | Set a user, create a memory — appears in list with correct scope tag |
| Search | Type a query — list filters, count updates |
| Type filter | Select "stable-fact" — only matching cards shown |
| Detail page | Click Edit — content editable, all metadata fields shown |
| Metadata editor | Change any field, Save — field updated, no error |
| Delete | Click Delete on a card — card removed, count decrements |

## Verify the Update Fix

The critical regression test:

```bash
# Create a memory
ID=$(curl -s -X POST http://localhost:8000/admin/memories \
  -H 'Content-Type: application/json' \
  -d '{"scope":"user","scope_id":"test","messages":[{"role":"user","content":"original"}],"metadata":{"type":"stable-fact"}}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['memory_id'])")

# Update it — must return 200, not 500
curl -s -X PUT "http://localhost:8000/admin/memories/$ID" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"updated"}],"metadata":{"type":"procedure"}}'

# Expected output:
# {"memory_id":"...","content":"updated","metadata":{"type":"procedure",...},...}
```

## Viewing Logs

```bash
# Server logs (includes tracebacks on errors)
docker compose logs mem0-server --tail 50

# CMS access logs
docker compose logs mem0-cms --tail 20
```

## Teardown

```bash
docker compose down
```

To also remove volumes (fresh start):

```bash
docker compose down -v
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Server container restarting | OpenAI key invalid or missing | Check `OPENAI_API_KEY` in `.env` |
| Update returns 500 | mem0 API mismatch | Rebuild with `--no-cache` |
| Empty cards in CMS | Fallback records not normalized | Verify `admin_service.py` has `_load_postgres_fallback_records` fix |
| `'dict' object has no attribute 'replace'` | mem0 2.x `update()` received dict instead of string | Verify `admin_service.py:542` passes `data=str` to `memory_instance.update()` |
| Postgres not healthy | Volume mount missing | `mkdir -p data/postgres data/mem0` |
