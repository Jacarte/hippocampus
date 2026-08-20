# `min_score` Threshold for `/query` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `min_score` parameter (default `0.5`) to the `/query` endpoint that filters out low-relevance hits server-side before returning the response.

**Architecture:** `UnifiedQueryRequest` gains a `min_score: float` field; `QueryService.query()` gains a matching parameter and applies the filter after sorting but before truncating to `limit`. Filtering is entirely server-side.

**Tech Stack:** Python 3.11 + Pydantic v2

---

## File Map

| File | Change |
|---|---|
| `server/api_models.py` | Add `min_score: float = 0.5` to `UnifiedQueryRequest` |
| `server/services/query_service.py` | Add `min_score` param to `query()`; filter hits after sort |
| `server/server.py` | Pass `min_score` from request down to `query_service.query()` |
| `server/tests/test_query_api.py` | Add threshold test cases |

---

## Task 1: Add `min_score` to `UnifiedQueryRequest` and `QueryService`

**Files:**
- Modify: `server/api_models.py`
- Modify: `server/services/query_service.py`

### Step 1.1 — Write the failing tests

In `server/tests/test_query_api.py`, add **after** the existing tests:

```python
def test_query_filters_hits_below_min_score() -> None:
    """Hits with score below min_score must be excluded from results."""
    corpus = FileCorpusService()
    corpus.upsert_chunks(
        root="/repo",
        file_path="bar.py",
        chunks=[
            {
                "language": "python",
                "symbol_name": "low_score_func",
                "symbol_kind": "function",
                "line_start": 1,
                "line_end": 3,
                "content": "threshold test low",
            },
        ],
    )
    # Memory hit with score below threshold
    low_mem = {"id": "mem-low", "memory": "threshold test low", "_retrieval": {"score": 0.3}, "metadata": None}
    # Memory hit with score at threshold (inclusive)
    ok_mem = {"id": "mem-ok", "memory": "threshold test ok", "_retrieval": {"score": 0.5}, "metadata": None}

    retrieval = FakeRetrieval([low_mem, ok_mem])
    svc = QueryService(corpus=corpus, retrieval_service=retrieval)

    result = svc.query("threshold test", corpora=["memory_store"], min_score=0.5, memory_instance=object())

    scores = [h["score"] for h in result["hits"]]
    assert all(s >= 0.5 for s in scores), f"Expected all scores >= 0.5, got {scores}"
    assert any(h["memory_id"] == "mem-ok" for h in result["hits"])
    assert not any(h["memory_id"] == "mem-low" for h in result["hits"])


def test_query_all_filtered_returns_empty_hits() -> None:
    """When every hit is below min_score, hits must be an empty list."""
    retrieval = FakeRetrieval([
        {"id": "m1", "memory": "low", "_retrieval": {"score": 0.1}, "metadata": None},
    ])
    svc = QueryService(corpus=FileCorpusService(), retrieval_service=retrieval)

    result = svc.query("low", corpora=["memory_store"], min_score=0.9, memory_instance=object())

    assert result["hits"] == []
    assert result["total"] == 1  # total reflects pre-filter count


def test_query_min_score_zero_returns_all_hits() -> None:
    """min_score=0.0 must not filter anything."""
    corpus = _make_corpus_with_chunks()
    retrieval = FakeRetrieval([_fake_memory_result()])
    svc = QueryService(corpus=corpus, retrieval_service=retrieval)

    result = svc.query("hello", corpora=["all"], min_score=0.0, memory_instance=object())

    assert len(result["hits"]) == 3  # all three survive


def test_query_default_min_score_is_0_5() -> None:
    """Calling query() without min_score must apply the 0.5 default."""
    low_mem = {"id": "low", "memory": "hello low", "_retrieval": {"score": 0.2}, "metadata": None}
    high_mem = {"id": "high", "memory": "hello high", "_retrieval": {"score": 0.8}, "metadata": None}
    retrieval = FakeRetrieval([low_mem, high_mem])
    svc = QueryService(corpus=FileCorpusService(), retrieval_service=retrieval)

    result = svc.query("hello", corpora=["memory_store"], memory_instance=object())

    ids = [h["memory_id"] for h in result["hits"]]
    assert "high" in ids
    assert "low" not in ids
```

- [ ] **Step 1.1 — Write the failing tests** (paste the block above into `server/tests/test_query_api.py`)

---

- [ ] **Step 1.2 — Run tests to confirm they fail**

```bash
cd server && python -m pytest tests/test_query_api.py::test_query_filters_hits_below_min_score tests/test_query_api.py::test_query_all_filtered_returns_empty_hits tests/test_query_api.py::test_query_min_score_zero_returns_all_hits tests/test_query_api.py::test_query_default_min_score_is_0_5 -v
```

Expected: all four tests FAIL (TypeError or assertion error because `min_score` doesn't exist yet).

---

- [ ] **Step 1.3 — Add `min_score` to `UnifiedQueryRequest` in `server/api_models.py`**

Locate `UnifiedQueryRequest` and add one field:

```python
class UnifiedQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Search query.")
    corpora: list[CorpusType] = Field(default=["all"])
    limit: int = Field(10, ge=1, le=50)
    path_filter: str | None = None
    language_filter: str | None = None
    scope_filter: str | None = None
    user_id: str | None = Field(
        default=None,
        description=(
            "Optional user identifier forwarded to the memory corpus for "
            "per-user scoping.  When omitted the server applies no user filter."
        ),
    )
    min_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum relevance score threshold.  Hits with a score strictly "
            "below this value are excluded from the response.  Defaults to "
            "0.5.  Set to 0.0 to disable filtering."
        ),
    )
```

---

- [ ] **Step 1.4 — Add `min_score` param and filtering logic to `QueryService.query()`**

In `server/services/query_service.py`, update the `query` method signature and body:

```python
def query(
    self,
    query_text: str,
    corpora: list[str],
    limit: int = 10,
    path_filter: str | None = None,
    language_filter: str | None = None,
    scope_filter: str | None = None,
    user_id: str | None = None,
    chunk_memory_enabled: bool = False,
    query_embedding: list[float] | None = None,
    memory_instance: Any | None = None,
    min_score: float = 0.5,
) -> dict[str, Any]:
```

Then replace the two lines that sort and truncate:

```python
# before
all_hits.sort(key=lambda h: h.score, reverse=True)
truncated = all_hits[:limit]
```

with:

```python
all_hits.sort(key=lambda h: h.score, reverse=True)
filtered = [h for h in all_hits if h.score >= min_score]
truncated = filtered[:limit]
```

And update the `UnifiedQueryResponse` construction to use `total=len(all_hits)` (pre-filter count, so callers can see how many were filtered):

```python
return UnifiedQueryResponse(
    hits=truncated,
    total=len(all_hits),      # total = pre-filter, pre-limit count
    corpora_queried=corpora_queried,
    degraded=degraded,
    degradation_reasons=degradation_reasons,
).model_dump()
```

Also update the docstring for `query()` — add this entry to the `Args:` block:

```
min_score: Minimum score threshold applied after sorting.  Hits with
    a score strictly below *min_score* are excluded before the
    result is truncated to *limit*.  Defaults to ``0.5``.  Set to
    ``0.0`` to return all hits regardless of score.
```

---

- [ ] **Step 1.5 — Run the four new tests to confirm they pass**

```bash
cd server && python -m pytest tests/test_query_api.py::test_query_filters_hits_below_min_score tests/test_query_api.py::test_query_all_filtered_returns_empty_hits tests/test_query_api.py::test_query_min_score_zero_returns_all_hits tests/test_query_api.py::test_query_default_min_score_is_0_5 -v
```

Expected: all four PASS.

---

- [ ] **Step 1.6 — Run the full server test suite to confirm no regressions**

```bash
cd server && python -m pytest -v
```

Expected: all pre-existing tests still PASS.

---

- [ ] **Step 1.7 — Commit**

```bash
git add server/api_models.py server/services/query_service.py server/tests/test_query_api.py
git commit -m "feat(server): add min_score threshold filtering to /query"
```

---

## Task 2: Wire `min_score` through the `/query` route in `server.py`

**Files:**
- Modify: `server/server.py`

- [ ] **Step 2.1 — Pass `min_score` from the request to `query_service.query()`**

Locate the `unified_query` route handler (search for `@app.post("/query"`). The lambda inside `_execute_service_call` currently calls `query_service.query(...)` without `min_score`. Add it:

```python
return _execute_service_call(
    "unified_query",
    lambda: request.app.state.query_service.query(
        query_text=query_req.query,
        corpora=query_req.corpora,
        limit=query_req.limit,
        path_filter=query_req.path_filter,
        language_filter=query_req.language_filter,
        scope_filter=query_req.scope_filter,
        chunk_memory_enabled=chunk_memory_enabled,
        memory_instance=memory_instance,
        user_id=query_req.user_id,
        min_score=query_req.min_score,
    ),
)
```

- [ ] **Step 2.2 — Run the full server test suite**

```bash
cd server && python -m pytest -v
```

Expected: all tests PASS.

- [ ] **Step 2.3 — Commit**

```bash
git add server/server.py
git commit -m "feat(server): forward min_score from /query route to QueryService"
```

---

## Task 4: Docstring / JSDoc final pass

**Files:**
- Review: `server/api_models.py` — `UnifiedQueryRequest.min_score` field description
- Review: `server/services/query_service.py` — `query()` docstring `Args:` block for `min_score`

- [ ] **Step 4.1 — Check every new/changed symbol**

For each file listed above, verify:
1. Every new field or parameter has a doc comment that says **what it is**, **what the default means**, **valid range**, and **what happens at the boundary (0.0, exactly threshold, above threshold)**.
2. No stale references (e.g. old docstrings that don't mention `min_score` where they should).
3. Docstring accuracy: the described behavior matches the implementation.

Checklist:
- [ ] `UnifiedQueryRequest.min_score` Field description covers range `[0.0, 1.0]`, default `0.5`, and that `0.0` disables filtering.
- [ ] `QueryService.query()` `Args:` entry for `min_score` — describes filter semantics (strictly below = excluded).

- [ ] **Step 4.2 — Commit docstring fixes (if any)**

```bash
git add server/api_models.py server/services/query_service.py
git commit -m "docs: final docstring pass for min_score threshold feature"
```

(Skip commit if no changes were needed.)

---

## Self-Review

### Spec coverage
| Requirement | Task |
|---|---|
| `min_score: float = 0.5` on `UnifiedQueryRequest` | Task 1.3 |
| Server filters hits `< min_score` after sort, before `limit` | Task 1.4 |
| Route passes `min_score` to service | Task 2.1 |
| Empty `hits: []` when all filtered | Task 1 (test in 1.1, impl in 1.4) |
| `total` = pre-filter count | Task 1.4 |
| Docstring final pass | Task 4 |

### Placeholder scan
None found.

### Type consistency
- `min_score: float` is consistent across the request model and query service.
- `total` stays `len(all_hits)` (pre-filter) throughout
