from __future__ import annotations

import re
import uuid
from typing import Any

_HEADING_RE = re.compile(r"^#{1,6} ", re.MULTILINE)


class MarkdownChunker:
    def chunk(self, file_path: str, content: str) -> list[dict[str, Any]]:
        lines = content.splitlines(keepends=True)
        heading_positions = [idx for idx, line in enumerate(lines) if _HEADING_RE.match(line)]

        if not heading_positions:
            return [{
                "id": str(uuid.uuid4()),
                "file_path": file_path,
                "language": "markdown",
                "symbol_name": None,
                "symbol_kind": "section",
                "line_start": 1,
                "line_end": max(len(lines), 1),
                "content": content,
                "score": 0.0,
            }]

        chunks: list[dict[str, Any]] = []

        for i, start_idx in enumerate(heading_positions):
            end_idx = heading_positions[i + 1] if i + 1 < len(heading_positions) else len(lines)
            heading_text = lines[start_idx].rstrip("\r\n")
            symbol_name = _HEADING_RE.sub("", heading_text, count=1).strip()

            chunks.append({
                "id": str(uuid.uuid4()),
                "file_path": file_path,
                "language": "markdown",
                "symbol_name": symbol_name,
                "symbol_kind": "section",
                "line_start": start_idx + 1,
                "line_end": end_idx,
                "content": "".join(lines[start_idx:end_idx]),
                "score": 0.0,
            })

        return chunks
