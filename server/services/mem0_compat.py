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
