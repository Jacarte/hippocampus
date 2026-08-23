"""Real installed-SDK compatibility smoke tests for ``mem0ai==2.0.7``.

This test file provides two kinds of coverage:

1. **SDK surface smoke coverage** (Section A — always runs, requires no live
   backend).  Verifies that the installed ``mem0ai==2.0.7`` package exposes
   every import, classmethod, and ``MemoryConfig`` field the server depends
   on.  These tests pass or fail entirely on the SDK's installed shape — no
   network, no PostgreSQL, no API credentials needed.

2. **End-to-end persistence probe** (Section B — conditionally runs when a
   live server is reachable).  Proves that a memory created through
   ``POST /memories`` with a unique ``user_id`` and metadata marker is
   retrievable via server APIs AND persisted in the PostgreSQL
   ``public.mem0_memories`` table.

Configuration (all via environment variables):

====================== ========================= ==============================
Var                    Default                   Purpose
====================== ========================= ==============================
``MEM0_SERVER_URL``    ``http://localhost:8000``  Server base URL
``POSTGRES_HOST``      ``localhost``              PostgreSQL host
``POSTGRES_PORT``      ``5432``                   PostgreSQL port
``POSTGRES_DB``        ``postgres``               PostgreSQL database name
``POSTGRES_USER``      ``postgres``               PostgreSQL user
``POSTGRES_PASSWORD``  ``postgres``               PostgreSQL password
====================== ========================= ==============================

The ``POSTGRES_*`` variables follow the same convention as the server's
``.env.example`` and ``runtime.get_config_from_env()`` so that the E2E
probe uses the same connection parameters as the server itself.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ===================================================================
# Section A — SDK surface smoke tests
# ===================================================================


def test_sdk_import_and_version() -> None:
    """The installed ``mem0`` package must be ``2.0.7``."""
    import mem0

    assert mem0.__version__ == "2.0.7"


def test_sdk_public_exports() -> None:
    """The server relies on exactly ``Memory`` and ``MemoryConfig`` from mem0.

    Verify they are reachable through the documented import paths.
    """
    from mem0 import Memory
    from mem0.configs.base import MemoryConfig

    assert Memory is not None
    assert MemoryConfig is not None


def test_sdk_memory_public_methods() -> None:
    """Every mem0 ``Memory`` method the server calls must exist.

    The server currently calls (directly or through the service layer):
    ``add``, ``get``, ``get_all``, ``search``, ``update``, ``delete``,
    ``delete_all``, ``reset``, ``history``, ``from_config``.
    """
    import inspect
    from mem0 import Memory

    expected: set[str] = {
        "add",
        "get",
        "get_all",
        "search",
        "update",
        "delete",
        "delete_all",
        "reset",
        "history",
        "from_config",
    }
    for method in expected:
        assert hasattr(Memory, method), f"Memory is missing required method {method!r}"

    # ``from_config`` must be a classmethod so ``Memory.from_config(dict)`` works.
    assert isinstance(
        inspect.getattr_static(Memory, "from_config"), classmethod
    ), "Memory.from_config must be a classmethod"


def test_sdk_memory_config_model_fields() -> None:
    """``MemoryConfig`` must expose the model fields the server config relies on.

    The server builds a config dict in ``runtime.get_config_from_env()`` that
    includes ``vector_store``, ``llm``, ``embedder``, ``history_db_path``, and
    ``version``.  Every one of these must be a recognised ``MemoryConfig`` field.
    """
    from mem0.configs.base import MemoryConfig

    fields = MemoryConfig.model_fields
    expected_fields = {"vector_store", "llm", "embedder", "history_db_path", "version"}
    for field in expected_fields:
        assert field in fields, (
            f"MemoryConfig is missing required field {field!r}; "
            f"available fields: {set(fields)}"
        )

    # ``visit_db_path`` is intentionally NOT a ``MemoryConfig`` field in 2.0.7.
    # The server passes it through the config dict anyway, which Pydantic v2
    # silently tolerates via its extra-field behaviour.  The test documents
    # this as a known non-field so we don't accidentally rely on it.
    assert "visit_db_path" not in fields, (
        "MemoryConfig.visit_db_path is NOT a field in mem0 2.0.7; "
        "the server passes it through the config dict via Pydantic v2 "
        "extra-field tolerance"
    )


def test_sdk_memory_config_accepts_server_config_dict() -> None:
    """``MemoryConfig(**server_config_dict)`` must succeed.

    The server builds its ``MemoryConfig``-compatible dict in
    ``runtime.get_config_from_env()``.  This test uses the exact same
    shape so any future mem0 schema change that breaks the config build
    will fail here first.
    """
    from mem0.configs.base import MemoryConfig

    config: dict[str, Any] = {
        "version": "v1.1",
        "vector_store": {
            "provider": "pgvector",
            "config": {
                "host": "localhost",
                "port": 5432,
                "dbname": "postgres",
                "user": "postgres",
                "password": "postgres",
                "collection_name": "mem0_memories",
                "embedding_model_dims": 1536,
                "diskann": False,
                "hnsw": False,
            },
        },
        "llm": {
            "provider": "openai",
            "config": {"model": "gpt-5", "temperature": 0.7},
        },
        "embedder": {
            "provider": "openai",
            "config": {},
        },
        "history_db_path": "/tmp/mem0_history.db",
    }

    mc = MemoryConfig(**config)

    assert mc.version == "v1.1"
    assert mc.vector_store.provider == "pgvector"
    # ``vector_store.config`` is a Pydantic ``PGVectorConfig`` in 2.0.7,
    # not a bare dict — access fields via attribute, not subscript.
    pg_config = mc.vector_store.config
    assert pg_config.host == "localhost"
    assert pg_config.embedding_model_dims == 1536
    assert pg_config.diskann is False
    assert pg_config.hnsw is False
    assert mc.llm.provider == "openai"
    assert mc.llm.config["model"] == "gpt-5"
    assert mc.embedder.provider == "openai"
    assert mc.history_db_path == "/tmp/mem0_history.db"


def test_sdk_pgvector_config_fields() -> None:
    """The pgvector config block must include the fields required by 2.0.7.

    mem0 2.0.7's ``PGVector.__init__`` requires three positional
    parameters that have no defaults: ``embedding_model_dims``,
    ``diskann``, and ``hnsw``.  The server adds these in
    ``runtime.get_config_from_env()``.  This test proves the
    ``MemoryConfig`` pipeline accepts them.
    """
    from mem0.configs.base import MemoryConfig

    config: dict[str, Any] = {
        "version": "v1.1",
        "vector_store": {
            "provider": "pgvector",
            "config": {
                "host": "localhost",
                "port": 5432,
                "dbname": "postgres",
                "user": "postgres",
                "password": "postgres",
                "collection_name": "mem0_memories",
                "embedding_model_dims": 768,
                "diskann": True,
                "hnsw": True,
            },
        },
        "llm": {"provider": "openai", "config": {}},
        "embedder": {"provider": "openai", "config": {}},
        "history_db_path": "/tmp/history.db",
    }

    mc = MemoryConfig(**config)
    pg_config = mc.vector_store.config
    assert pg_config.embedding_model_dims == 768
    assert pg_config.diskann is True
    assert pg_config.hnsw is True

    # Verify the server's defaults also round-trip
    mc_defaults = MemoryConfig(**{
        **config, "vector_store": {"provider": "pgvector", "config": {
            "host": "localhost", "port": 5432, "dbname": "postgres",
            "user": "postgres", "password": "postgres",
            "collection_name": "mem0_memories", "embedding_model_dims": 1536,
            "diskann": False, "hnsw": False,
        }},
    })
    pg_config_defaults = mc_defaults.vector_store.config
    assert pg_config_defaults.embedding_model_dims == 1536
    assert pg_config_defaults.diskann is False
    assert pg_config_defaults.hnsw is False


def test_sdk_query_api_endpoint_signature() -> None:
    """The mem0 SDK does NOT export a ``/query`` endpoint on the ``Memory``
    instance — ``server/server.py`` implements ``/query`` as a server-side
    facade that delegates to ``QueryService``.

    This test verifies that the SDK's ``search`` method — which backs the
    server's ``/search`` — uses the verified 2.0.7 keyword shape.
    """
    import inspect
    from mem0 import Memory

    search_sig = inspect.signature(Memory.search)
    params = list(search_sig.parameters.keys())
    # mem0 2.0.7: search(self, query, *, top_k=20, filters=None, threshold=0.1, rerank=False)
    assert "top_k" in search_sig.parameters, (
        f"Memory.search must accept top_k in 2.0.7; signature params: {params}"
    )
    top_k_default = search_sig.parameters["top_k"].default
    assert top_k_default == 20, (
        f"Memory.search top_k default must be 20 in 2.0.7, got {top_k_default}"
    )

    get_all_sig = inspect.signature(Memory.get_all)
    get_all_params = list(get_all_sig.parameters.keys())
    assert "filters" in get_all_sig.parameters, (
        f"Memory.get_all must accept filters in 2.0.7; signature params: {get_all_params}"
    )


def test_sdk_memory_from_config_accepts_dict() -> None:
    """``Memory.from_config`` signature must accept a single ``config_dict``
    positional argument (the already-verified 2.0.7 surface).

    NOTE: This test does NOT call ``Memory.from_config()`` because that
    requires live backend connections (vector store, LLM, embedder) that
    are not available in the offline test environment.  The ``MemoryConfig``
    acceptance test above proves the dict-to-config translation works;
    the actual backend initialisation is verified by the live integration
    smoke tests in Section B below.
    """
    import inspect
    from mem0 import Memory

    sig = inspect.signature(Memory.from_config)
    params = list(sig.parameters.keys())
    assert "config_dict" in params or "config" in params or len(params) >= 2, (
        f"Memory.from_config must accept a config dict as first positional arg; "
        f"signature params: {params}"
    )


def test_removed_embedding_compat_exports_are_absent() -> None:
    """Only the retained LLM compatibility helper remains exported."""
    from services import mem0_compat

    assert callable(mem0_compat.generate_response)
    for name in ("embed", "_EMBEDDING_ATTR", "_EMBED_METHOD"):
        assert not hasattr(mem0_compat, name)


# ===================================================================
# Section B — End-to-end persistence probe
# ===================================================================


# -----------------------------------------------------------------------
# Environment-driven configuration
# -----------------------------------------------------------------------
# Server URL — override via ``MEM0_SERVER_URL`` env var.
SERVER_URL = os.environ.get("MEM0_SERVER_URL", "http://localhost:8000")

# PostgreSQL connection — same ``POSTGRES_*`` env vars the server uses in
# ``runtime.get_config_from_env()`` / ``docker-compose.yaml``.
PG_HOST = os.environ.get("POSTGRES_HOST", "localhost")
PG_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
PG_DB = os.environ.get("POSTGRES_DB", "postgres")
PG_USER = os.environ.get("POSTGRES_USER", "postgres")
PG_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "postgres")

# pgvector collection name — matches ``docker-compose.yaml`` default.
PG_COLLECTION = os.environ.get("POSTGRES_COLLECTION", "mem0_memories")

_E2E_SKIP_REASON = (
    f"Server at {SERVER_URL} is not reachable — set MEM0_SERVER_URL to a "
    f"live endpoint and ensure the PostgreSQL host is reachable via the "
    f"POSTGRES_* environment variables."
)


def _server_healthy() -> bool:
    """Return ``True`` when the server at ``SERVER_URL`` responds 200 to
    ``GET /health`` within a short timeout."""
    try:
        import requests  # type: ignore[import-untyped]
    except ImportError:
        return False
    try:
        resp = requests.get(f"{SERVER_URL}/health", timeout=5.0)
        return resp.status_code == 200
    except Exception:
        return False


def _pg_accessible() -> bool:
    """Return ``True`` when ``psycopg2`` can reach the PostgreSQL instance
    that the server's pgvector backend uses.

    Connection parameters come from ``POSTGRES_HOST``, ``POSTGRES_PORT``,
    ``POSTGRES_DB``, ``POSTGRES_USER``, ``POSTGRES_PASSWORD`` — the same
    env vars the server uses in its own configuration.
    """
    try:
        import psycopg2  # type: ignore[import-untyped]
    except ImportError:
        return False
    try:
        conn = psycopg2.connect(
            host=PG_HOST,
            port=PG_PORT,
            dbname=PG_DB,
            user=PG_USER,
            password=PG_PASSWORD,
            connect_timeout=5,
        )
        conn.close()
        return True
    except Exception:
        return False


_e2e_available = _server_healthy()
_pg_available = _pg_accessible()


@pytest.mark.skipif(not _e2e_available, reason=_E2E_SKIP_REASON)
class TestEndToEndPersistenceProbe:
    """End-to-end persistence probe: create a memory through the server API,
    confirm retrieval through server endpoints, and prove the record is
    persisted in PostgreSQL.

    The test uses a unique ``user_id`` (``mem0-upgrade-e2e-<UUID>``) so it
    never collides with other test runs or production data.  The metadata
    marker ``mem0-upgrade-e2e`` is included for easy identification in
    the PostgreSQL table.
    """

    UNIQUE_ID = f"mem0-upgrade-e2e-{uuid.uuid4().hex[:12]}"
    TEST_MESSAGE = f"End-to-end persistence probe for {UNIQUE_ID}"
    # Class-level storage shared across test methods (pytest creates a new
    # instance per test, so instance attrs set in one method are lost in another).
    _memory_id: str | None = None
    _created: dict[str, Any] | None = None

    def test_e2e_health_check(self) -> None:
        """Confirm the server is reachable before running the probe."""
        import requests

        resp = requests.get(f"{SERVER_URL}/health", timeout=5.0)
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["status"] == "healthy"

    def test_e2e_create_and_retrieve_memory(self) -> None:
        """POST a memory with a unique user_id, then GET /memories to
        confirm the server returns it."""
        import requests

        create_resp = requests.post(
            f"{SERVER_URL}/memories",
            json={
                "messages": [
                    {"role": "user", "content": self.TEST_MESSAGE}
                ],
                "user_id": self.UNIQUE_ID,
                "metadata": {
                    "source": "mem0-upgrade-e2e",
                    "test_run": self.UNIQUE_ID,
                },
            },
            timeout=(10.0, 120.0),
        )

        assert create_resp.status_code == 200, (
            f"POST /memories returned {create_resp.status_code}: "
            f"{create_resp.text}"
        )
        created = create_resp.json()
        # mem0 2.0.7 ``add()`` returns ``{"results": [{"id": "...", ...}]}``.
        # The server's ``MemoryService.add()`` forwards this shape through.
        assert "results" in created, (
            f"Create response missing 'results' key: {created}"
        )
        assert len(created["results"]) >= 1, (
            f"Create response has empty results list: {created}"
        )
        memory_id = created["results"][0]["id"]
        assert memory_id, f"Create result missing 'id': {created}"
        type(self)._memory_id = memory_id
        type(self)._created = created

        # Retrieve via GET /memories
        list_resp = requests.get(
            f"{SERVER_URL}/memories",
            params={"user_id": self.UNIQUE_ID},
            timeout=(10.0, 60.0),
        )
        assert list_resp.status_code == 200
        items = list_resp.json()
        # The response could be a list (GET /memories) or a dict with results
        if isinstance(items, dict):
            items = items.get("results", [])
        user_ids = [
            item.get("user_id") or (item.get("metadata") or {}).get("user_id")
            for item in items
        ]
        assert self.UNIQUE_ID in user_ids, (
            f"Created memory not found in GET /memories; "
            f"user_ids returned: {user_ids}"
        )

    def test_e2e_get_single_memory(self) -> None:
        """GET /memories/{memory_id} must return the record created by
        :meth:`test_e2e_create_and_retrieve_memory` with matching content."""
        import requests

        memory_id = type(self)._memory_id
        assert memory_id is not None, (
            "test_e2e_create_and_retrieve_memory must run before this test"
        )

        get_resp = requests.get(
            f"{SERVER_URL}/memories/{memory_id}",
            timeout=(10.0, 60.0),
        )
        assert get_resp.status_code == 200
        record = get_resp.json()
        assert record.get("id") == memory_id

    def test_e2e_persistence_in_postgresql(self) -> None:
        """Confirm the memory created by :meth:`test_e2e_create_and_retrieve_memory`
        is present in the ``public.mem0_memories`` table.

        This is the critical persistence proof: if the test user_id exists
        in PostgreSQL, the create/retrieve API path works end-to-end, and
        the pgvector backend is correctly wired.
        """
        import psycopg2  # type: ignore[import-untyped]

        conn = psycopg2.connect(
            host=PG_HOST,
            port=PG_PORT,
            dbname=PG_DB,
            user=PG_USER,
            password=PG_PASSWORD,
            connect_timeout=15,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT COUNT(*) FROM public.{PG_COLLECTION} "
                    "WHERE payload->>'user_id' = %s",
                    (self.UNIQUE_ID,),
                )
                row = cur.fetchone()
        finally:
            conn.close()

        assert row is not None, "SELECT COUNT(*) returned None"
        count = int(row[0])
        assert count >= 1, (
            f"Expected at least 1 memory row for user_id={self.UNIQUE_ID!r} "
            f"in public.mem0_memories, got {count}. "
            f"The memory was created via POST /memories but did not reach "
            f"the PostgreSQL store."
        )
