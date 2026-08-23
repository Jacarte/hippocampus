"""Compatibility seam for mem0 SDK-internal access.

This module is the *single* place in the server that touches mem0 SDK
internal attributes (currently ``memory_instance.llm`` and
``memory_instance.embedding_model``).  All other service-layer code MUST
route through the helpers in this module so that:

* The 2.0.0 access path is declared in one location and can be changed
  in one location if a future mem0 release renames / restructures these
  attributes.
* ``server/services/query_service.py`` uses the embedding helper instead of
  reaching into the mem0 ``Memory`` object directly.  The LLM helper remains
  available for callers that need the same compatibility boundary.
* Failures (e.g. a future mem0 release removes ``.llm`` / ``.embedding_model``
  entirely) produce a clear, descriptive ``AttributeError`` that pinpoints
  the missing attribute and the helper that needed it, rather than a
  generic ``AttributeError`` raised deep inside a service.

The helpers intentionally do NOT import from ``mem0`` — they only access
attributes on a passed-in memory instance.  This keeps the seam testable
with plain mocks and avoids hard coupling to the mem0 import surface
(the test fakes do not have a real mem0 install).
"""

from __future__ import annotations

from typing import Any

# Attributes the server currently relies on the mem0 ``Memory`` instance
# to expose.  Centralised here so the access path is declared in one place
# and a future mem0 release can be adapted by changing a single string.
_LLM_ATTR: str = "llm"
_EMBEDDING_ATTR: str = "embedding_model"

# The 2.0.0 LLM client exposes a ``generate_response(messages=...)`` method.
# We keep the surface narrow: only the kwargs the server actually uses.
_LLM_METHOD: str = "generate_response"

# The 2.0.0 embedder exposes an ``.embed(text, memory_action=...)`` method.
# The helper defaults ``memory_action`` to ``"add"`` for compatibility with
# mem0's write-oriented call shape.  Query retrieval explicitly passes
# ``None``, causing the helper to omit that keyword argument.
_EMBED_METHOD: str = "embed"


def _resolve_attr(memory_instance: Any, attr_name: str, *, kind: str) -> Any:
    """Return ``getattr(memory_instance, attr_name)`` or fail fast.

    Args:
        memory_instance: A mem0 ``Memory`` instance (or any object that
            exposes the SDK-internal attributes the server relies on).
        attr_name: Attribute name to resolve (e.g. ``"llm"`` or
            ``"embedding_model"``).
        kind: Human-readable label for the missing attribute, used in
            the error message ("LLM client" / "embedder").

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
            f"mem0_compat: memory_instance has no {kind!r} attribute {attr_name!r}; this server requires mem0 2.0.0 which exposes it on the Memory instance. ({exc})"
        ) from exc
    if value is None:
        raise AttributeError(
            f"mem0_compat: memory_instance.{attr_name} resolved to None; expected a configured mem0 2.0.0 {kind}."
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
            parent.  Fail-fast so downstream ``.embed(...)`` or
            ``.generate_response(...)`` calls do not raise an
            ambiguous ``AttributeError`` deep inside a service.
    """
    try:
        method = getattr(obj, method_name)
    except AttributeError as exc:
        raise AttributeError(
            f"mem0_compat: mem0 2.0.0 {owner} is expected to expose {method_name!r}; got {type(obj).__name__!r} which does not. ({exc})"
        ) from exc
    if not callable(method):
        raise AttributeError(
            f"mem0_compat: memory_instance.{owner}.{method_name} resolved to a non-callable of type {type(method).__name__!r}."
        )
    return method


def generate_response(memory_instance: Any, *, messages: list[dict[str, Any]]) -> str:
    """Generate an LLM chat-completion response through a mem0 Memory instance.

    Encapsulates the verified mem0 2.0.0 access path
    ``memory_instance.llm.generate_response(messages=...)``.  Callers use this
    helper rather than reaching into ``memory_instance.llm`` directly.

    Args:
        memory_instance: A mem0 ``Memory`` instance that exposes ``.llm``
            (the verified 2.0.0 access path).
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


def embed(
    memory_instance: Any,
    text: str,
    *,
    memory_action: str | None = "add",
) -> Any:
    """Embed a single text string through a mem0 Memory instance.

    Encapsulates the verified mem0 2.0.0 access path
    ``memory_instance.embedding_model.embed(text, memory_action=...)``.
    Used by :mod:`server.services.query_service` for query-time embeddings,
    with ``memory_action=None`` because the mem0 2.0.0 one-shot query path
    does not require a memory action.

    Args:
        memory_instance: A mem0 ``Memory`` instance that exposes
            ``.embedding_model`` (the verified 2.0.0 access path).
        text: The text to embed.
        memory_action: Optional memory-action label forwarded to the
            embedder.  The verified mem0 2.0.0 call shape accepts
            ``memory_action="add"``; the query path can pass
            ``memory_action=None`` to skip the kwarg entirely.  When
            ``None`` the kwarg is omitted from the call so the embedder
            uses its own default behaviour.

    Returns:
        The raw embedding result from the mem0 embedder.  The query service
        uses it as a list of floats directly.  ``None`` is a possible return
        value and callers must treat it as "no embedding available".

    Raises:
        AttributeError: If ``memory_instance.embedding_model`` or the
            ``embed`` method is unavailable — fail fast with a
            descriptive message.
        Exception: Anything raised by the underlying mem0 / embedder client
            is propagated unchanged.  Query retrieval catches the failure and
            falls back to lexical-only results.
    """
    embedder = _resolve_attr(memory_instance, _EMBEDDING_ATTR, kind="embedder")
    method = _resolve_method(embedder, _EMBED_METHOD, owner="embedder")
    if memory_action is None:
        return method(text)
    return method(text, memory_action=memory_action)
