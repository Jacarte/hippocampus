from __future__ import annotations

import ast
import uuid
from typing import Any

from .tree_sitter_chunker import TreeSitterChunker

_TREE_SITTER_LANGUAGES = frozenset({
    "go", "javascript", "typescript", "rust", "java", "c", "cpp", "ruby",
})

_ts_chunker = TreeSitterChunker()


class CodeChunker:
    """Dispatches source files to the appropriate chunker based on language.

    - Python: uses the standard-library ``ast`` module for accurate symbol extraction.
    - Go, JavaScript, TypeScript, Rust, Java, C, C++, Ruby: delegates to
      :class:`TreeSitterChunker` for AST-accurate symbol extraction.
    - All other languages: falls back to a sliding-window text chunker.

    Raises:
        Exception: Any exception from ``TreeSitterChunker`` propagates unchanged
            (hard-fail policy — no silent fallback for tree-sitter languages).
    """

    WINDOW_SIZE = 200
    OVERLAP = 20

    def chunk(self, file_path: str, content: str, language: str) -> list[dict[str, Any]]:
        """Chunk *content* into symbol-level or text-window segments.

        Args:
            file_path: Source file path (stored in each chunk).
            content: Source code as a string.
            language: Language name (e.g. ``"python"``, ``"go"``).

        Returns:
            List of chunk dicts with keys: id, file_path, language, symbol_name,
            symbol_kind, line_start, line_end, content, score.
        """
        if language == "python":
            try:
                return self._chunk_python(file_path, content, language)
            except Exception:
                pass
        elif language in _TREE_SITTER_LANGUAGES:
            return _ts_chunker.chunk(file_path, content, language)
        return self._chunk_text(file_path, content, language)

    def _chunk_python(self, file_path: str, content: str, language: str) -> list[dict[str, Any]]:
        """Extract top-level functions, async functions, and classes using ``ast``."""
        tree = ast.parse(content)
        lines = content.splitlines(keepends=True)
        chunks: list[dict[str, Any]] = []

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbol_kind = "function"
            elif isinstance(node, ast.ClassDef):
                symbol_kind = "class"
            else:
                continue

            line_start: int = node.lineno
            line_end: int = node.end_lineno or node.lineno  # type: ignore[attr-defined]
            chunks.append({
                "id": str(uuid.uuid4()),
                "file_path": file_path,
                "language": language,
                "symbol_name": node.name,
                "symbol_kind": symbol_kind,
                "line_start": line_start,
                "line_end": line_end,
                "content": "".join(lines[line_start - 1:line_end]),
                "score": 0.0,
            })

        if not chunks:
            return self._chunk_text(file_path, content, language)
        return chunks

    def _chunk_text(self, file_path: str, content: str, language: str) -> list[dict[str, Any]]:
        """Sliding-window fallback for languages without a symbol-level chunker."""
        lines = content.splitlines(keepends=True)
        total = len(lines)
        if total == 0:
            return [{
                "id": str(uuid.uuid4()),
                "file_path": file_path,
                "language": language,
                "symbol_name": None,
                "symbol_kind": None,
                "line_start": 1,
                "line_end": 1,
                "content": "",
                "score": 0.0,
            }]

        chunks: list[dict[str, Any]] = []
        start = 0
        while start < total:
            end = min(start + self.WINDOW_SIZE, total)
            chunks.append({
                "id": str(uuid.uuid4()),
                "file_path": file_path,
                "language": language,
                "symbol_name": None,
                "symbol_kind": None,
                "line_start": start + 1,
                "line_end": end,
                "content": "".join(lines[start:end]),
                "score": 0.0,
            })
            if end == total:
                break
            start = end - self.OVERLAP
        return chunks
