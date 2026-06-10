from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------------------
# HTTP request metrics
# ---------------------------------------------------------------------------

http_requests_total: Counter = Counter(
    "http_requests_total",
    "Total HTTP requests received, partitioned by method, path, and response status code.",
    labelnames=["method", "path", "status_code"],
)

http_request_duration_seconds: Histogram = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds, partitioned by method and path.",
    labelnames=["method", "path"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# ---------------------------------------------------------------------------
# Memory CRUD operation metrics
# ---------------------------------------------------------------------------

memory_operations_total: Counter = Counter(
    "memory_operations_total",
    "Memory CRUD operations, partitioned by operation type and user/agent identifiers. "
    "Operation values: add, get, get_all, search, retrieve, update, delete, "
    "delete_all, history, reset, configure.",
    labelnames=["operation", "user_id", "agent_id"],
)

memory_operation_errors_total: Counter = Counter(
    "memory_operation_errors_total",
    "Failed memory CRUD operations, partitioned by operation type.",
    labelnames=["operation"],
)

# ---------------------------------------------------------------------------
# Retrieval pipeline metrics
# ---------------------------------------------------------------------------

retrieval_duration_seconds: Histogram = Histogram(
    "retrieval_duration_seconds",
    "Per-stage retrieval latency in seconds. Stages: lexical, semantic, rerank, total.",
    labelnames=["stage"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

retrieval_candidates_count: Histogram = Histogram(
    "retrieval_candidates_count",
    "Number of candidates returned per retrieval stage. Stages: lexical, semantic, rerank.",
    labelnames=["stage"],
    buckets=[0, 1, 5, 10, 25, 50, 100, 250],
)

retrieval_degradations_total: Counter = Counter(
    "retrieval_degradations_total",
    "Retrieval pipeline degradation events, partitioned by stage and reason.",
    labelnames=["stage", "reason"],
)

# ---------------------------------------------------------------------------
# Memory retrieval hit-rate metrics (reuse tracking)
# ---------------------------------------------------------------------------

memory_retrieve_total: Counter = Counter(
    "memory_retrieve_total",
    "Total number of memory retrieve/search calls, partitioned by user and agent.",
    labelnames=["user_id", "agent_id"],
)

memory_retrieve_hits: Counter = Counter(
    "memory_retrieve_hits",
    "Number of memory retrieve/search calls that returned at least one result, "
    "partitioned by user and agent. Divide by memory_retrieve_total for reuse rate.",
    labelnames=["user_id", "agent_id"],
)

# ---------------------------------------------------------------------------
# Cross-corpus query metrics
# ---------------------------------------------------------------------------

query_duration_seconds: Histogram = Histogram(
    "query_duration_seconds",
    "Cross-corpus query latency in seconds, partitioned by corpus. "
    "Corpus values: memory_store, file_corpus, all.",
    labelnames=["corpus"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

query_hits_count: Histogram = Histogram(
    "query_hits_count",
    "Number of results returned per corpus per cross-corpus query.",
    labelnames=["corpus"],
    buckets=[0, 1, 5, 10, 25, 50],
)

# ---------------------------------------------------------------------------
# Background job metrics
# ---------------------------------------------------------------------------

background_jobs_total: Counter = Counter(
    "background_jobs_total",
    "Total background jobs processed, partitioned by status (queued, completed, failed).",
    labelnames=["status"],
)

background_job_duration_seconds: Histogram = Histogram(
    "background_job_duration_seconds",
    "Background job execution duration in seconds, partitioned by job type.",
    labelnames=["job_type"],
    buckets=[1.0, 5.0, 10.0, 30.0, 60.0, 300.0],
)

background_job_queue_depth: Gauge = Gauge(
    "background_job_queue_depth",
    "Current number of queued background jobs awaiting execution.",
)

# ---------------------------------------------------------------------------
# Indexing metrics
# ---------------------------------------------------------------------------

indexing_files_total: Counter = Counter(
    "indexing_files_total",
    "Total files indexed, partitioned by operation (sync, ingest).",
    labelnames=["operation"],
)

indexing_chunks_total: Counter = Counter(
    "indexing_chunks_total",
    "Total chunks created during indexing, partitioned by operation (sync, ingest).",
    labelnames=["operation"],
)

# ---------------------------------------------------------------------------
# File corpus operation metrics
# ---------------------------------------------------------------------------

file_corpus_operations_total: Counter = Counter(
    "file_corpus_operations_total",
    "Total file corpus operations, partitioned by operation type "
    "(upsert, query, delete, reset).",
    labelnames=["operation"],
)
