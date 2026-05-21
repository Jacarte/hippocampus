from __future__ import annotations

import hashlib
import os
from typing import Any

_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".go": "go",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".rb": "ruby",
    ".md": "markdown",
    ".txt": "text",
    ".rst": "restructuredtext",
}

_IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        "dist",
        "build",
        ".venv",
        "venv",
        ".tox",
        ".eggs",
        ".mypy_cache",
        ".ruff_cache",
    }
)

_MAX_FILE_SIZE = 1 * 1024 * 1024
_BINARY_CHECK_BYTES = 8


def _is_binary(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(_BINARY_CHECK_BYTES)
        return b"\x00" in chunk
    except OSError:
        return True


def _fingerprint(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


class FileScanner:
    @staticmethod
    def is_supported(file_path: str) -> bool:
        """Return True for supported extensions: .py .go .ts .tsx .js .jsx .rs .java .c .cpp .h .rb .md .txt .rst"""
        _, ext = os.path.splitext(file_path.lower())
        return ext in _EXT_TO_LANG

    def scan(self, root: str) -> list[dict[str, Any]]:
        """
        Walk *root* and return {file_path, language, size_bytes, fingerprint} for each supported file.

        Skips: ignored dirs, hidden files (unless .md/.rst/.txt), empty files, files > 1 MB, binary files.
        """
        results: list[dict[str, Any]] = []
        root = os.path.abspath(root)

        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [
                d
                for d in dirnames
                if d not in _IGNORED_DIRS
                and not d.startswith(".")
                and not d.endswith(".egg-info")
            ]

            for filename in filenames:
                abs_path = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(abs_path, root)

                if filename.startswith("."):
                    _, ext = os.path.splitext(filename.lower())
                    if ext not in (".md", ".rst", ".txt"):
                        continue

                if not self.is_supported(filename):
                    continue

                try:
                    size = os.path.getsize(abs_path)
                except OSError:
                    continue

                if size == 0 or size > _MAX_FILE_SIZE:
                    continue

                if _is_binary(abs_path):
                    continue

                _, ext = os.path.splitext(filename.lower())
                language = _EXT_TO_LANG[ext]

                try:
                    fp = _fingerprint(abs_path)
                except OSError:
                    continue

                results.append(
                    {
                        "file_path": rel_path,
                        "language": language,
                        "size_bytes": size,
                        "fingerprint": fp,
                    }
                )

        return results

    def diff(self, root: str, prev_manifest: dict[str, str]) -> dict[str, list[str]]:
        """
        Compare current scan of *root* against *prev_manifest* (file_path -> fingerprint).

        Returns {"created": [...], "updated": [...], "deleted": [...]}.
        """
        current: dict[str, str] = {
            item["file_path"]: item["fingerprint"] for item in self.scan(root)
        }

        created = [p for p in current if p not in prev_manifest]
        updated = [p for p in current if p in prev_manifest and prev_manifest[p] != current[p]]
        deleted = [p for p in prev_manifest if p not in current]

        return {"created": created, "updated": updated, "deleted": deleted}
