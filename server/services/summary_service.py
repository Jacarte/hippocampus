"""
Summary generation service for code chunks.

Generates natural-language summaries of code chunks using the server's
configured LLM, and derives embeddings for those summaries using the
server's configured embedder. Failure in either step logs a warning and
returns a no-summary result — it does not abort indexing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .mem0_compat import embed, generate_response

logger = logging.getLogger(__name__)


@dataclass
class SummaryResult:
    """Result of a summary generation attempt.

    Attributes:
        summary_text: Generated natural-language summary, or None if generation failed/was skipped.
        summary_embedding: Embedding vector for summary_text, or None if unavailable.
    """

    summary_text: Optional[str] = None
    summary_embedding: Optional[list[float]] = None


class SummaryService:
    """Generates summaries and embeddings for code chunks.

    Uses the server's configured LLM to produce natural-language summaries
    and the configured embedder to produce embedding vectors. All failures
    are non-fatal: if summary generation or embedding generation fails,
    a no-summary result is returned and a warning is logged.
    """

    def __init__(self, memory_instance) -> None:
        """Initialize with an existing mem0 memory instance.

        Args:
            memory_instance: Configured mem0 Memory instance that provides
                access to the LLM and embedder clients.
        """
        self._memory = memory_instance

    def generate_summary(self, chunk_content: str, chunk_name: str = "") -> SummaryResult:
        """Generate a natural-language summary and embedding for a code chunk.

        Args:
            chunk_content: Raw code content of the chunk.
            chunk_name: Optional name/identifier for the chunk (e.g. function name).
                Used to improve summary quality.

        Returns:
            SummaryResult with summary_text and summary_embedding populated on success.
            Returns SummaryResult() (all None) on any failure.
        """
        summary_text = self._generate_text(chunk_content, chunk_name)
        if summary_text is None:
            return SummaryResult()

        summary_embedding = self._generate_embedding(summary_text)
        if summary_embedding is None:
            return SummaryResult()
        return SummaryResult(summary_text=summary_text, summary_embedding=summary_embedding)

    def _generate_text(self, chunk_content: str, chunk_name: str) -> Optional[str]:
        """Generate summary text via the LLM. Returns None on failure."""
        name_hint = f" named `{chunk_name}`" if chunk_name else ""
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a code documentation assistant. "
                    "Summarize the following code chunk in 1-3 concise sentences. "
                    "Describe what it does, its inputs/outputs, and any notable behavior."
                ),
            },
            {
                "role": "user",
                "content": f"Code chunk{name_hint}:\n\n{chunk_content}",
            },
        ]
        try:
            response = generate_response(
                self._memory, messages=messages
            )
            text = response.strip() if isinstance(response, str) else ""
            if not text:
                logger.warning("Summary generation returned empty text for chunk%s", name_hint)
                return None
            return text
        except Exception as e:
            logger.warning("Summary generation failed for chunk%s: %s", name_hint, e)
            return None

    def _generate_embedding(self, text: str) -> Optional[list[float]]:
        """Generate embedding for summary text. Returns None on failure."""
        try:
            result = embed(
                self._memory, text, memory_action="add"
            )
            if result is None:
                logger.warning("Embedding model returned None for summary text")
                return None
            return list(result)
        except Exception as e:
            logger.warning("Embedding generation failed for summary text: %s", e)
            return None
