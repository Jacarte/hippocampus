"""Compatibility seam for mem0 SDK-internal access.

This module is the *single* place in the server that touches the mem0 SDK's
internal ``memory_instance.llm`` attribute. All other service-layer code MUST
route through the helpers in this module so that:

* The 2.0.7 access path is declared in one location and can be changed
  in one location if a future mem0 release renames / restructures these
  attributes.
* Callers use one compatibility boundary instead of reaching into the mem0
  ``Memory`` object directly.
* Failures (e.g. a future mem0 release removes ``.llm`` entirely) produce a
  clear, descriptive ``AttributeError`` that pinpoints
  the missing attribute and the helper that needed it, rather than a
  generic ``AttributeError`` raised deep inside a service.

SDK imports are lazy: plain adapters remain testable without constructing a
real Memory client. The strict write observer additionally uses the pinned
SDK's JSON utilities to preserve its accepted extraction response formats.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import json
from threading import RLock
from typing import Any

from .memory_policy import EXTRACTED_MEMORY_TYPES

# Attributes the server currently relies on the mem0 ``Memory`` instance
# to expose.  Centralised here so the access path is declared in one place
# and a future mem0 release can be adapted by changing a single string.
_LLM_ATTR: str = "llm"

# The 2.0.7 LLM client exposes a ``generate_response(messages=...)`` method.
# We keep the surface narrow: only the kwargs the server actually uses.
_LLM_METHOD: str = "generate_response"


def _resolve_attr(memory_instance: Any, attr_name: str, *, kind: str) -> Any:
    """Return ``getattr(memory_instance, attr_name)`` or fail fast.

    Args:
        memory_instance: A mem0 ``Memory`` instance (or any object that
            exposes the SDK-internal attributes the server relies on).
        attr_name: Attribute name to resolve (for example, ``"llm"``).
        kind: Human-readable label for the missing attribute, used in
            the error message.

    Returns:
        The resolved attribute value.

    Raises:
        AttributeError: With a descriptive message that names the
            attribute, the helper that needed it, and the mem0 release
            surface that was expected.  No silent ``None`` fallback —
            the seam fails closed so downstream code never accidentally
            calls a method on ``None``.
    """
    try:
        value = getattr(memory_instance, attr_name)
    except AttributeError as exc:
        raise AttributeError(
            f"mem0_compat: memory_instance has no {kind!r} attribute {attr_name!r}; this server requires mem0 2.0.7 which exposes it on the Memory instance. ({exc})"
        ) from exc
    if value is None:
        raise AttributeError(
            f"mem0_compat: memory_instance.{attr_name} resolved to None; expected a configured mem0 2.0.7 {kind}."
        )
    return value


def _resolve_method(obj: Any, method_name: str, *, owner: str) -> Any:
    """Return ``getattr(obj, method_name)`` or fail fast with context.

    Args:
        obj: The parent object (e.g. the resolved LLM client).
        method_name: The method name to resolve on ``obj``.
        owner: Human-readable label for ``obj``, used in the error
            message (e.g. ``"LLM client"``).

    Returns:
        The resolved method (bound method on the parent object).

    Raises:
        AttributeError: When the expected method is missing on the
            parent.  Fail-fast so downstream ``.generate_response(...)`` calls
            do not raise an
            ambiguous ``AttributeError`` deep inside a service.
    """
    try:
        method = getattr(obj, method_name)
    except AttributeError as exc:
        raise AttributeError(
            f"mem0_compat: mem0 2.0.7 {owner} is expected to expose {method_name!r}; got {type(obj).__name__!r} which does not. ({exc})"
        ) from exc
    if not callable(method):
        raise AttributeError(
            f"mem0_compat: memory_instance.{owner}.{method_name} resolved to a non-callable of type {type(method).__name__!r}."
        )
    return method


def generate_response(memory_instance: Any, *, messages: list[dict[str, Any]]) -> str:
    """Generate an LLM chat-completion response through a mem0 Memory instance.

    Encapsulates the verified mem0 2.0.7 access path
    ``memory_instance.llm.generate_response(messages=...)``.  Callers use this
    helper rather than reaching into ``memory_instance.llm`` directly.

    Args:
        memory_instance: A mem0 ``Memory`` instance that exposes ``.llm``
            (the verified 2.0.7 access path).
        messages: Chat-completion messages in the standard
            ``[{"role": ..., "content": ...}, ...]`` shape.  Forwarded
            verbatim as the ``messages`` kwarg to the LLM.

    Returns:
        The raw LLM response (typically a ``str``; the caller is
        responsible for ``.strip()``-ing and validating it).

    Raises:
        AttributeError: If ``memory_instance.llm`` or the
            ``generate_response`` method is unavailable — fail fast with
            a descriptive message.
        Exception: Anything raised by the underlying mem0 / LLM client is
            propagated unchanged for the caller to handle.
    """
    llm = _resolve_attr(memory_instance, _LLM_ATTR, kind="LLM client")
    method = _resolve_method(llm, _LLM_METHOD, owner="LLM client")
    return method(messages=messages)


class MemoryWriteError(RuntimeError):
    """An add failed, including an error swallowed by the pinned SDK.

    ``retryable`` is true only when no vector insertion was attempted. Once
    persistence starts, a transport failure may have happened after a commit.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass
class _WriteObservation:
    extraction_failed: bool = False
    failed_embeddings: set[str] = field(default_factory=set)
    failed_inserts: set[str] = field(default_factory=set)
    persistence_started: bool = False
    # Extracted memory text -> valid extractor label. The pinned SDK copies only
    # ``text`` and ``attributed_to`` into the payload, so the insert proxy adds it.
    extracted_types: dict[str, str] = field(default_factory=dict)


class _ForwardingProxy:
    def __init__(self, target: Any, observation: _WriteObservation) -> None:
        self._target = target
        self._observation = observation

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)


class _ExtractionProxy(_ForwardingProxy):
    def generate_response(self, *args: Any, **kwargs: Any) -> str:
        # The SDK catches both provider and parse errors and returns []. Validate
        # before that boundary so only a valid {"memory": []} means no selection.
        from mem0.memory.utils import extract_json, remove_code_blocks

        try:
            response = self._target.generate_response(*args, **kwargs)
            cleaned = remove_code_blocks(response)
            try:
                parsed = json.loads(cleaned, strict=False)
            except json.JSONDecodeError:
                parsed = json.loads(extract_json(cleaned), strict=False)
            if not isinstance(parsed, dict) or not isinstance(parsed.get("memory"), list):
                raise ValueError("Invalid memory extraction response.")
            for item in parsed["memory"]:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("text"), str)
                    or not item["text"].strip()
                ):
                    raise ValueError("Invalid extracted memory text.")
            for item in parsed["memory"]:
                label = item.get("type")
                if isinstance(label, str) and label.strip().lower() in EXTRACTED_MEMORY_TYPES:
                    # The SDK keeps the first of duplicate texts; keep its label.
                    self._observation.extracted_types.setdefault(
                        item["text"], label.strip().lower()
                    )
            return json.dumps(parsed)
        except Exception:
            self._observation.extraction_failed = True
            raise


class _EmbeddingProxy(_ForwardingProxy):
    def embed_batch(self, texts: list[str], *args: Any, **kwargs: Any) -> Any:
        observing = not self._observation.persistence_started
        try:
            vectors = self._target.embed_batch(texts, *args, **kwargs)
            if observing and (len(vectors) != len(texts) or any(v is None for v in vectors)):
                raise ValueError("Embedding batch did not cover every memory.")
        except Exception:
            if observing:
                self._observation.failed_embeddings.update(texts)
            raise
        if observing:
            self._observation.failed_embeddings.difference_update(texts)
        return vectors

    def embed(self, text: str, *args: Any, **kwargs: Any) -> Any:
        observing = not self._observation.persistence_started
        try:
            vector = self._target.embed(text, *args, **kwargs)
            if observing and vector is None:
                raise ValueError("Embedding is missing.")
        except Exception:
            if observing:
                self._observation.failed_embeddings.add(text)
            raise
        if observing:
            self._observation.failed_embeddings.discard(text)
        return vector


class _InsertProxy(_ForwardingProxy):
    def insert(self, *, vectors: Any, ids: list[str], payloads: Any, **kwargs: Any) -> Any:
        self._observation.persistence_started = True
        if self._observation.extracted_types:
            payloads = [self._with_extracted_type(payload) for payload in payloads]
        try:
            result = self._target.insert(vectors=vectors, ids=ids, payloads=payloads, **kwargs)
        except Exception:
            self._observation.failed_inserts.update(ids)
            raise
        # A successful per-record fallback resolves that record's batch error.
        self._observation.failed_inserts.difference_update(ids)
        return result

    def _with_extracted_type(self, payload: Any) -> Any:
        # Client metadata is already in the payload; its type always wins.
        if not isinstance(payload, dict) or "type" in payload or "memory_type" in payload:
            return payload
        text = payload.get("data")
        label = self._observation.extracted_types.get(text) if isinstance(text, str) else None
        return {**payload, "type": label} if label else payload


_ENTITY_STORE_INIT_LOCK = RLock()


class _WriteViewMixin:
    __slots__ = ()

    @property
    def entity_store(self) -> Any:
        # The SDK initializes this lazily. Keep its cache and connection-pool
        # ownership on the long-lived Memory, not on a disposable write view.
        with _ENTITY_STORE_INIT_LOCK:
            return self._hippocampus_write_owner.entity_store


@lru_cache(maxsize=16)
def _write_view_type(base: type) -> type:
    return type(f"{base.__name__}WriteView", (_WriteViewMixin, base), {"__slots__": ()})


def add_memories(memory_instance: Any, *, messages: list[dict[str, Any]], **params: Any) -> Any:
    """Call the 2.0.7 add pipeline without acknowledging swallowed failures.

    Use a shallow per-call view, never replace clients on the shared Memory
    instance. Connections/configuration remain shared as in normal SDK use;
    observation state and proxies are local to this request. Entity enrichment
    happens after primary persistence and remains best-effort in the SDK.
    Test doubles without SDK internals keep their original public add contract.
    """
    internals = ("llm", "embedding_model", "vector_store")
    if not all(hasattr(memory_instance, name) for name in internals):
        if any(base.__module__.startswith("mem0.") for base in type(memory_instance).__mro__):
            raise MemoryWriteError(
                "The mem0 instance is missing required write-observation internals.",
                retryable=True,
            )
        return memory_instance.add(messages=messages, **params)

    observation = _WriteObservation()
    view = object.__new__(_write_view_type(type(memory_instance)))
    view.__dict__.update(vars(memory_instance))
    view._hippocampus_write_owner = memory_instance
    if params.get("infer", True):
        view.llm = _ExtractionProxy(memory_instance.llm, observation)
    view.embedding_model = _EmbeddingProxy(memory_instance.embedding_model, observation)
    view.vector_store = _InsertProxy(memory_instance.vector_store, observation)
    try:
        response = view.add(messages=messages, **params)
    except Exception as exc:
        raise MemoryWriteError(
            "Memory processing failed; no successful write acknowledgement is available.",
            retryable=not observation.persistence_started,
        ) from exc
    if observation.extraction_failed or observation.failed_embeddings or observation.failed_inserts:
        raise MemoryWriteError(
            "Memory extraction or persistence failed; the capture was not acknowledged.",
            retryable=not observation.persistence_started,
        )
    return response
