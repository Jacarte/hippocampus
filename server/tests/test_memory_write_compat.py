"""Exercise write acknowledgement against the real, offline mem0 2.0.7 pipeline.

Only external providers, persistence, telemetry, and optional NLP enrichment are
stubbed. ``Memory.add`` and ``Memory._add_to_vector_store`` remain the SDK methods.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sys
from threading import Barrier
from types import SimpleNamespace
from typing import Any

import pytest
from mem0 import Memory
import mem0.memory.main as sdk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.mem0_compat import MemoryWriteError, add_memories


class StubLLM:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def generate_response(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        if callable(self.response):
            return self.response(**kwargs)
        return self.response


class StubEmbeddings:
    def __init__(
        self,
        *,
        batch_failure: bool = False,
        short_batch: bool = False,
        failed_texts: set[str] | None = None,
    ) -> None:
        self.batch_failure = batch_failure
        self.short_batch = short_batch
        self.failed_texts = failed_texts or set()
        self.individual_adds: list[str] = []

    def embed_batch(self, texts, memory_action=None):
        if self.batch_failure or self.failed_texts.intersection(texts):
            raise RuntimeError("batch embedding unavailable")
        vectors = [[1.0, float(len(text))] for text in texts]
        return vectors[:-1] if self.short_batch else vectors

    def embed(self, text, memory_action=None):
        if memory_action == "add":
            self.individual_adds.append(text)
            if text in self.failed_texts:
                raise RuntimeError("individual embedding unavailable")
        return [1.0, float(len(text))]


class StubVectorStore:
    def __init__(
        self,
        *,
        batch_failure: bool = False,
        failed_texts: set[str] | None = None,
    ) -> None:
        self.batch_failure = batch_failure
        self.failed_texts = failed_texts or set()
        self.records: dict[str, dict[str, Any]] = {}
        self.insert_calls: list[list[str]] = []

    def search(self, **kwargs):
        return []

    def insert(self, *, vectors, ids, payloads):
        self.insert_calls.append(list(ids))
        if self.batch_failure and len(ids) > 1:
            raise RuntimeError("batch insert unavailable")
        if any(payload["data"] in self.failed_texts for payload in payloads):
            raise RuntimeError("insert unavailable")
        for memory_id, payload in zip(ids, payloads):
            self.records[memory_id] = deepcopy(payload)


class StubHistory:
    def __init__(self) -> None:
        self.saved_messages: list[Any] = []

    def get_last_messages(self, *args, **kwargs):
        return []

    def save_messages(self, messages, scope):
        self.saved_messages.append((deepcopy(messages), scope))

    def batch_add_history(self, records):
        pass

    def add_history(self, *args, **kwargs):
        pass


@pytest.fixture(autouse=True)
def disable_external_sdk_services(monkeypatch):
    # These are incidental enrichment/telemetry paths, not the write pipeline.
    monkeypatch.setattr(sdk, "capture_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(sdk, "lemmatize_for_bm25", lambda text: text)
    monkeypatch.setattr(sdk, "extract_entities_batch", lambda texts: [[] for _ in texts])
    monkeypatch.setattr(sdk, "detect_temporal_usage_from_metadata", lambda value: None)
    monkeypatch.setattr(sdk, "detect_scale_threshold_from_add_result", lambda *args: None)
    monkeypatch.setattr(sdk, "display_first_run_notice", lambda *args: None)


def memory_with(response, *, embeddings=None, vector_store=None):
    # Skip provider construction, but exercise the installed class's real methods.
    memory = Memory.__new__(Memory)
    memory.config = SimpleNamespace(llm=SimpleNamespace(config={}))
    memory.llm = StubLLM(response)
    memory.embedding_model = embeddings or StubEmbeddings()
    memory.vector_store = vector_store or StubVectorStore()
    memory.db = StubHistory()
    memory.api_version = "v1.1"
    memory.custom_instructions = "Only supported durable knowledge."
    memory._entity_store = None
    return memory


def extracted(*texts):
    return json.dumps({"memory": [{"text": text} for text in texts]})


def write(memory, *, infer=True, text="Observed supported knowledge."):
    return add_memories(
        memory,
        messages=[{"role": "user", "content": text}],
        user_id="write-compat-tests",
        infer=infer,
    )


def stored_texts(memory):
    return {record["data"] for record in memory.vector_store.records.values()}


def test_real_sdk_swallows_extractor_failure_but_guard_surfaces_it():
    memory = memory_with(RuntimeError("provider unavailable"))

    assert memory.add("Observed supported knowledge.", user_id="test") == {"results": []}
    with pytest.raises(MemoryWriteError) as caught:
        write(memory)

    assert caught.value.retryable is True
    assert memory.vector_store.insert_calls == []


@pytest.mark.parametrize(
    "response",
    [
        None,
        "",
        "   ",
        "not JSON",
        "{}",
        "[]",
        '{"memory": null}',
        '{"memory": "some text"}',
        '{"memory": ["some text"]}',
        '{"memory": [{"text": 123}]}',
        '{"memory": [{"text": "  "}]}',
    ],
)
def test_invalid_extraction_is_not_acknowledged_as_no_selection(response):
    memory = memory_with(response)

    with pytest.raises(MemoryWriteError) as caught:
        write(memory)

    assert caught.value.retryable is True
    assert memory.vector_store.insert_calls == []


def test_valid_empty_selection_remains_successful():
    memory = memory_with('{"memory": []}')

    assert write(memory) == {"results": []}
    assert memory.vector_store.insert_calls == []
    assert len(memory.db.saved_messages) == 1


def test_code_fenced_extraction_preserves_text_and_attribution():
    response = '```json\n{"memory": [{"text": "Adopted Redis", "attributed_to": "user"}]}\n```'
    memory = memory_with(response)

    result = write(memory)

    assert [item["memory"] for item in result["results"]] == ["Adopted Redis"]
    assert next(iter(memory.vector_store.records.values()))["attributed_to"] == "user"


@pytest.mark.parametrize("mode", ["batch_failure", "short_batch"])
def test_embedding_batch_failure_or_short_result_recovers_individually(mode):
    embeddings = StubEmbeddings(**{mode: True})
    memory = memory_with(extracted("Decision A", "Decision B"), embeddings=embeddings)

    result = write(memory)

    assert len(result["results"]) == 2
    assert stored_texts(memory) == {"Decision A", "Decision B"}
    assert embeddings.individual_adds == ["Decision A", "Decision B"]


def test_unresolved_embedding_failure_before_any_insert_is_retryable():
    memory = memory_with(
        extracted("Decision A"),
        embeddings=StubEmbeddings(failed_texts={"Decision A"}),
    )

    with pytest.raises(MemoryWriteError) as caught:
        write(memory)

    assert caught.value.retryable is True
    assert memory.vector_store.insert_calls == []


def test_partial_embedding_success_is_not_acknowledged_as_complete():
    memory = memory_with(
        extracted("Decision A", "Decision B"),
        embeddings=StubEmbeddings(failed_texts={"Decision B"}),
    )

    with pytest.raises(MemoryWriteError) as caught:
        write(memory)

    assert caught.value.retryable is False
    assert stored_texts(memory) == {"Decision A"}


def test_batch_insert_failure_can_recover_all_ids_individually():
    store = StubVectorStore(batch_failure=True)
    memory = memory_with(extracted("Decision A", "Decision B"), vector_store=store)

    result = write(memory)

    assert len(result["results"]) == 2
    assert stored_texts(memory) == {"Decision A", "Decision B"}
    assert [len(ids) for ids in store.insert_calls] == [2, 1, 1]
    assert set(store.insert_calls[0]) == set(store.records)


@pytest.mark.parametrize("failed", [{"Decision B"}, {"Decision A", "Decision B"}])
def test_unresolved_insert_failures_override_sdk_add_results(failed):
    store = StubVectorStore(batch_failure=True, failed_texts=failed)
    memory = memory_with(extracted("Decision A", "Decision B"), vector_store=store)

    with pytest.raises(MemoryWriteError) as caught:
        write(memory)

    assert caught.value.retryable is False
    assert stored_texts(memory) == {"Decision A", "Decision B"} - failed


def test_infer_false_preserves_exact_text_without_using_the_llm():
    memory = memory_with(AssertionError("LLM must not run for an exact write"))
    text = '  canonical fact\n{"literal": "text"}\n'
    original_clients = (memory.llm, memory.embedding_model, memory.vector_store)

    result = write(memory, infer=False, text=text)

    assert result["results"][0]["memory"] == text
    assert stored_texts(memory) == {text}
    assert memory.llm.calls == []
    assert (memory.llm, memory.embedding_model, memory.vector_store) == original_clients


def test_optional_entity_embedding_failure_does_not_reject_a_saved_memory(monkeypatch):
    monkeypatch.setattr(sdk, "extract_entities_batch", lambda texts: [[("ORG", "Acme")]])
    memory = memory_with(
        extracted("Decision A"),
        embeddings=StubEmbeddings(failed_texts={"Acme"}),
    )

    result = write(memory)

    assert result["results"][0]["memory"] == "Decision A"
    assert stored_texts(memory) == {"Decision A"}


def test_concurrent_adds_have_separate_failure_observations():
    barrier = Barrier(2, timeout=5)

    def response(*, messages, **kwargs):
        barrier.wait()
        if "UNSUCCESSFUL_OBSERVATION" in messages[-1]["content"]:
            raise RuntimeError("one request's extractor failed")
        return extracted("Successful request's fact")

    memory = memory_with(response)
    original_clients = (memory.llm, memory.embedding_model, memory.vector_store)

    with ThreadPoolExecutor(max_workers=2) as executor:
        failed = executor.submit(write, memory, text="UNSUCCESSFUL_OBSERVATION")
        successful = executor.submit(write, memory, text="SUCCESSFUL_OBSERVATION")
        with pytest.raises(MemoryWriteError) as caught:
            failed.result(timeout=10)
        result = successful.result(timeout=10)

    assert caught.value.retryable is True
    assert result["results"][0]["memory"] == "Successful request's fact"
    assert stored_texts(memory) == {"Successful request's fact"}
    assert (memory.llm, memory.embedding_model, memory.vector_store) == original_clients


def test_repeated_writes_cache_lazy_entity_store_on_original_memory(monkeypatch):
    class EntityStore(StubVectorStore):
        def search_batch(self, *, queries, **kwargs):
            return [[] for _ in queries]

    entity_store = EntityStore()
    created = []

    def create_entity_store(provider, config):
        created.append((provider, deepcopy(config)))
        return entity_store

    monkeypatch.setattr(sdk.VectorStoreFactory, "create", create_entity_store)
    monkeypatch.setattr(sdk, "extract_entities_batch", lambda texts: [[("ORG", "Acme")]])
    memory = memory_with(extracted("Decision A"))
    memory.config.vector_store = SimpleNamespace(
        provider="pgvector", config={"collection_name": "offline_memories"}
    )
    memory.collection_name = "offline_memories"
    original_attributes = dict(vars(memory))

    first = write(memory)
    second = write(memory)

    assert first["results"][0]["memory"] == "Decision A"
    assert second["results"][0]["memory"] == "Decision A"
    assert len(created) == 1
    assert created[0][0] == "pgvector"
    assert created[0][1]["collection_name"] == "offline_memories_entities"
    assert memory._entity_store is entity_store
    assert len(entity_store.insert_calls) == 2
    assert type(memory) is Memory
    assert set(vars(memory)) == set(original_attributes)
    for name, original in original_attributes.items():
        if name != "_entity_store":
            assert getattr(memory, name) is original


@pytest.mark.parametrize("missing", ["llm", "embedding_model", "vector_store"])
def test_real_sdk_missing_required_internals_fails_closed(missing):
    memory = memory_with(extracted("Decision A"))
    original_store = memory.vector_store
    delattr(memory, missing)

    with pytest.raises(MemoryWriteError, match="missing required write-observation internals"):
        write(memory)

    assert original_store.insert_calls == []
