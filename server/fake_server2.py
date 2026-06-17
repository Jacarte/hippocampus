import sys, os, traceback
sys.path.insert(0, '/Users/javcab/Documents/hippocampus/server')
from typing import Any

class _FakeMemory:
    def __init__(self, config=None):
        self.config = config; self.records = {}
    def add(self, *, messages, metadata=None, user_id=None, agent_id=None, run_id=None):
        rid = f"mem-{len(self.records)+1}"
        r = {"id": rid, "memory": messages[0]["content"] if messages else "",
             "messages": messages, "metadata": dict(metadata or {}),
             "user_id": user_id, "agent_id": agent_id, "run_id": run_id}
        self.records[rid] = r; return r
    def get(self, memory_id): return self.records.get(memory_id)
    def get_all(self, **kw): return list(self.records.values())
    def update(self, memory_id, data):
        if memory_id in self.records: self.records[memory_id].update(data); return self.records[memory_id]
    def delete(self, memory_id=None, **kw):
        mid = memory_id or kw.get('memory_id')
        if mid: self.records.pop(mid, None)

os.environ['OPENAI_API_KEY'] = 'fake'
os.environ['MEM0_VISIT_DB_PATH'] = ':memory:'
os.environ['MEM0_HISTORY_DB_PATH'] = ':memory:'

from server import create_app
app = create_app(memory_factory=_FakeMemory, startup_enabled=False)

from fastapi.testclient import TestClient
c = TestClient(app)
c.post("/configure", json={"version":"v1.1","vector_store":{"provider":"pgvector","config":{"host":"x"}},
    "llm":{"provider":"openai","config":{"model":"gpt-5","api_key":"fake"}},
    "embedder":{"provider":"openai","config":{"api_key":"fake"}},"history_db_path":":memory:"})

c.post("/admin/memories", json={
    "scope":"user","scope_id":"alice",
    "messages":[{"role":"user","content":"test content"}],
    "metadata":{"type":"procedure","project":"infra"}
})
print(f"READY: {len(app.state.memory.records)} records")

# Test update
r = c.put("/admin/memories/mem-1", json={
    "messages": [{"role":"user","content":"updated content"}],
    "metadata": {"type":"stable-fact","project":"infra","decay_half_life_days":120}
})
print(f"UPDATE status={r.status_code}")
if r.status_code!=200:
    print(r.text[:500])
