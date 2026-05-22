# Tree-sitter Chunker — Design Spec

**Date:** 2026-05-18  
**Status:** Approved  
**Scope:** Replace regex-based Go/JS/TS symbol extraction and add AST-accurate symbol extraction for Rust, Java, C, C++, Ruby using `tree-sitter-languages`.

---

## Background

The v1 `CodeChunker` uses Python's `ast` module for Python (accurate) but falls back to regex for Go/JS/TS and sliding-window text chunks for Rust, Java, C, C++, and Ruby. The regex approach misses structs, interfaces, type aliases, enums, and class methods. The sliding-window approach extracts no symbol information at all.

This upgrade replaces those paths with production-quality AST parsing via tree-sitter, while keeping the Python `ast` path unchanged.

---

## Decisions

| Question | Answer |
|---|---|
| Languages upgraded | All 8: Go, JavaScript, TypeScript, Rust, Java, C, C++, Ruby |
| Python chunker | Unchanged — keeps `ast` module |
| Markdown chunker | Unchanged — heading-based, works correctly |
| On parse/grammar error | Hard fail — re-raise, do not index the file |
| Grammar distribution | `tree-sitter-languages` PyPI package (pre-compiled, ~50 MB) |
| Symbol kinds | Standard set: functions/methods, classes, interfaces/traits, structs/enums/type aliases |
| Chunk content | Full source text of matched node (start to end line inclusive) |
| Sub-chunking large symbols | Not in v2 — deferred |
| `score` field | Stays `0.0` (lexical MVP) |

---

## Architecture

### New file: `services/chunkers/tree_sitter_chunker.py`

Single `TreeSitterChunker` class:

```
TreeSitterChunker
  LANGUAGE_MAP: dict[str, str]       # language name → tree-sitter-languages key
  QUERY_MAP: dict[str, str]          # language name → S-expression query string
  chunk(file_path, content, language) → list[dict]
```

- Imports `tree_sitter_languages` at module load time — hard `ImportError` if missing, no lazy loading.
- `chunk()` does **not** catch exceptions. Parse errors, query errors, and grammar errors propagate to the caller (`IndexingService`).
- Returns chunks in the existing schema: `id`, `file_path`, `language`, `symbol_name`, `symbol_kind`, `line_start`, `line_end`, `content`, `score`.
- `line_start` and `line_end` are 1-based (consistent with existing chunkers).

### Modified file: `services/chunkers/code_chunker.py`

- **Removed:** `_GO_FUNC_RE`, `_JS_SYMBOL_RE`, `_chunk_regex()`, `_go_symbol_kind()`, `_js_symbol_kind()`
- **Added:** `from .tree_sitter_chunker import TreeSitterChunker` + instantiation
- `CodeChunker.chunk()` updated:
  - Python → `_chunk_python()` (unchanged)
  - Go, JavaScript, TypeScript, Rust, Java, C, C++, Ruby → `TreeSitterChunker.chunk()`
  - All other extensions → `_chunk_text()` (unchanged)

### Modified file: `requirements.txt`

```
tree-sitter-languages==1.10.2
```

---

## Symbol Kinds Per Language

| Language | `symbol_kind` values extracted |
|---|---|
| Go | `function`, `method`, `struct`, `interface`, `type_alias` |
| JavaScript | `function`, `class` |
| TypeScript | `function`, `method`, `class`, `interface`, `type_alias`, `enum` |
| Rust | `function`, `method`, `struct`, `enum`, `trait`, `type_alias` |
| Java | `function`, `class`, `interface`, `enum` |
| C | `function`, `struct`, `enum`, `type_alias` |
| C++ | `function`, `method`, `class`, `struct`, `enum`, `type_alias` |
| Ruby | `function`, `class`, `module` |

**Note:** JavaScript has no native interfaces or type declarations — only `function` and `class` are extracted.

---

## Error Handling

- `TreeSitterChunker` is intentionally exception-transparent.
- Any failure propagates to `IndexingService`, which already handles per-file errors at the orchestration level.
- If `tree-sitter-languages` is not installed, `import` fails at module load with a clear `ImportError` — no silent degradation.

---

## Testing

### New file: `tests/test_tree_sitter_chunker.py`

- One fixture file per language under `tests/fixtures/ts_samples/`:
  - `sample.go`, `sample.ts`, `sample.js`, `sample.rs`, `sample.java`, `sample.c`, `sample.cpp`, `sample.rb`
  - Each fixture contains one instance of every symbol kind for that language
- Tests assert per chunk: `symbol_name`, `symbol_kind`, `line_start`, `line_end`, `language`, non-empty `content`
- Hard-fail test: malformed source → exception propagates (not swallowed)

### Modified file: `tests/test_code_chunker.py`

- Remove regex-path tests for Go, JS, TS
- Add dispatch tests: mock `TreeSitterChunker.chunk()`, assert `CodeChunker.chunk()` calls it for each of the 8 languages
- Python `ast` tests: unchanged

### Unaffected tests

`test_indexing.py`, `test_integration.py`, `test_e2e_parity.py`, `test_server.py`, `test_cli.py`, `test_mcp_bridge.py` — chunk schema is identical, no upstream changes required.

---

## Out of Scope (Deferred to v3)

- Sub-chunking of large symbols (e.g. a 500-line class body)
- Adding more languages (Swift, Kotlin, Scala, Dart, etc.)
- Semantic/vector search on chunks (requires embedding pipeline)
- Incremental re-parse on file change (watch service already triggers full re-index per file)
