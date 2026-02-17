---
name: mem0-memory
description: "Store and retrieve memories using local Mem0 REST API. Use when: (1) storing conversation context or user preferences, (2) searching for relevant past memories, (3) managing agent/user/session memories, (4) tracking memory history. Triggers: 'remember', 'recall', 'memory', 'store context', 'what did we discuss', 'user preference'."
compatibility: opencode
---

# Mem0 Memory Skill

Persistent memory layer for AI agents via local Mem0 REST API at `http://localhost:8000`.

## API Operations

### Store Memory
```bash
curl -X POST http://localhost:8000/memories \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}],
    "user_id": "user-123",
    "agent_id": "opencode",
    "metadata": {"key": "value"}
  }'
```

### Search Memories
```bash
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "search text",
    "user_id": "user-123",
    "agent_id": "opencode"
  }'
```

### Get All Memories
```bash
curl "http://localhost:8000/memories?user_id=user-123&agent_id=opencode"
```

### Get Specific Memory
```bash
curl http://localhost:8000/memories/{memory_id}
```

### Update Memory
```bash
curl -X PUT http://localhost:8000/memories/{memory_id} \
  -H "Content-Type: application/json" \
  -d '{"text": "updated content"}'
```

### Delete Memory
```bash
curl -X DELETE http://localhost:8000/memories/{memory_id}
```

### Delete All Memories
```bash
curl -X DELETE "http://localhost:8000/memories?user_id=user-123"
```

### Get Memory History
```bash
curl http://localhost:8000/memories/{memory_id}/history
```

## Identifier Hierarchy

- **user_id**: Isolates memories per user across sessions
- **agent_id**: Isolates memories per agent type (e.g., "opencode", "support-bot")
- **run_id**: Isolates memories per conversation session

Always specify at least `user_id` or `agent_id` when storing/retrieving.

## Workflow

1. **Before responding**: Search for relevant context with `/search`
2. **After important exchanges**: Store meaningful conversation via `/memories` POST
3. **User asks "what do you remember"**: Retrieve with `/memories` GET
4. **Outdated info**: Update with PUT or delete with DELETE

## Python Helper

Use `scripts/mem0_client.py` for programmatic access:
```python
python scripts/mem0_client.py add --user-id user-123 --content "User prefers dark mode"
python scripts/mem0_client.py search --user-id user-123 --query "preferences"
python scripts/mem0_client.py list --user-id user-123
python scripts/mem0_client.py delete --memory-id abc123
```
