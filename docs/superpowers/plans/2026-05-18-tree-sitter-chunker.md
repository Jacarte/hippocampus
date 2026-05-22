# Tree-sitter Chunker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the regex-based Go/JS/TS chunkers and add AST-accurate symbol extraction for Rust, Java, C, C++, and Ruby using `tree-sitter-languages`, while keeping the Python `ast` and Markdown chunkers untouched.

**Architecture:** A new `TreeSitterChunker` class in `services/chunkers/tree_sitter_chunker.py` holds all tree-sitter logic (language map, S-expression queries, chunking). `CodeChunker` delegates the 8 new languages to it; the Python `ast` path and the `_chunk_text` fallback remain unchanged. Hard-fail on any parse/grammar error — no silent degradation.

**Tech Stack:** Python 3.x, `tree-sitter-languages==1.10.2` (pre-compiled grammars), `pytest`

**Spec:** `docs/superpowers/specs/2026-05-18-tree-sitter-chunker-design.md`

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `services/chunkers/tree_sitter_chunker.py` | All tree-sitter logic: language map, queries, chunking |
| Modify | `services/chunkers/code_chunker.py` | Remove regex paths, delegate 8 languages to `TreeSitterChunker` |
| Modify | `requirements.txt` | Add `tree-sitter-languages==1.10.2` |
| Create | `tests/fixtures/ts_samples/sample.go` | Go fixture with function, method, struct, interface, type_alias |
| Create | `tests/fixtures/ts_samples/sample.js` | JS fixture with function, class |
| Create | `tests/fixtures/ts_samples/sample.ts` | TS fixture with function, method, class, interface, type_alias, enum |
| Create | `tests/fixtures/ts_samples/sample.rs` | Rust fixture with function, method, struct, enum, trait, type_alias |
| Create | `tests/fixtures/ts_samples/sample.java` | Java fixture with method, class, interface, enum |
| Create | `tests/fixtures/ts_samples/sample.c` | C fixture with function, struct, enum, type_alias |
| Create | `tests/fixtures/ts_samples/sample.cpp` | C++ fixture with function, method, class, struct, enum, type_alias |
| Create | `tests/fixtures/ts_samples/sample.rb` | Ruby fixture with function, class, module |
| Create | `tests/test_tree_sitter_chunker.py` | Unit tests for `TreeSitterChunker` directly |
| Modify | `tests/test_indexing.py` | Update `TestCodeChunker` — remove regex assertions, update language dispatch tests |

---

## Task 1: Install `tree-sitter-languages` and verify import

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add dependency to requirements.txt**

Open `requirements.txt`. After the existing `regex==2026.1.15` line, add:

```
tree-sitter-languages==1.10.2
```

- [ ] **Step 2: Install the package**

```bash
pip install tree-sitter-languages==1.10.2
```

Expected output: `Successfully installed tree-sitter-languages-1.10.2` (or already satisfied).

- [ ] **Step 3: Verify the import works**

```bash
python3 -c "from tree_sitter_languages import get_language, get_parser; p = get_parser('go'); print('ok')"
```

Expected output: `ok`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "chore: add tree-sitter-languages dependency"
```

---

## Task 2: Create fixture files for all 8 languages

**Files:**
- Create: `tests/fixtures/ts_samples/sample.go`
- Create: `tests/fixtures/ts_samples/sample.js`
- Create: `tests/fixtures/ts_samples/sample.ts`
- Create: `tests/fixtures/ts_samples/sample.rs`
- Create: `tests/fixtures/ts_samples/sample.java`
- Create: `tests/fixtures/ts_samples/sample.c`
- Create: `tests/fixtures/ts_samples/sample.cpp`
- Create: `tests/fixtures/ts_samples/sample.rb`

Each fixture must contain one clear instance of every symbol kind for that language.

- [ ] **Step 1: Create `tests/fixtures/ts_samples/sample.go`**

```go
package main

import "fmt"

type Animal interface {
	Speak() string
}

type Dog struct {
	Name string
}

type DogAlias = Dog

func (d Dog) Speak() string {
	return "Woof"
}

func Greet(name string) {
	fmt.Println("Hello", name)
}
```

Expected symbol kinds: `interface` (Animal), `struct` (Dog), `type_alias` (DogAlias), `method` (Speak), `function` (Greet).

- [ ] **Step 2: Create `tests/fixtures/ts_samples/sample.js`**

```javascript
function greet(name) {
  return "Hello, " + name;
}

class Animal {
  constructor(name) {
    this.name = name;
  }
}
```

Expected symbol kinds: `function` (greet), `class` (Animal).

- [ ] **Step 3: Create `tests/fixtures/ts_samples/sample.ts`**

```typescript
interface Greeter {
  greet(name: string): string;
}

type Alias = string;

enum Color {
  Red,
  Green,
  Blue,
}

class Dog implements Greeter {
  greet(name: string): string {
    return "Hello, " + name;
  }
}

function hello(name: string): string {
  return "Hello, " + name;
}
```

Expected symbol kinds: `interface` (Greeter), `type_alias` (Alias), `enum` (Color), `class` (Dog), `method` (greet), `function` (hello).

- [ ] **Step 4: Create `tests/fixtures/ts_samples/sample.rs`**

```rust
trait Greeter {
    fn greet(&self) -> String;
}

type Alias = String;

enum Color {
    Red,
    Green,
    Blue,
}

struct Dog {
    name: String,
}

impl Dog {
    fn bark(&self) -> String {
        String::from("Woof")
    }
}

fn hello(name: &str) -> String {
    format!("Hello, {}", name)
}
```

Expected symbol kinds: `trait` (Greeter), `type_alias` (Alias), `enum` (Color), `struct` (Dog), `method` (bark), `function` (hello).

- [ ] **Step 5: Create `tests/fixtures/ts_samples/sample.java`**

```java
public class Main {
    interface Greeter {
        String greet(String name);
    }

    enum Color {
        RED, GREEN, BLUE
    }

    public String hello(String name) {
        return "Hello, " + name;
    }
}
```

Expected symbol kinds: `class` (Main), `interface` (Greeter), `enum` (Color), `function`/method (hello).

- [ ] **Step 6: Create `tests/fixtures/ts_samples/sample.c`**

```c
#include <stdio.h>

typedef struct {
    int x;
    int y;
} Point;

typedef int MyInt;

typedef enum {
    RED,
    GREEN,
    BLUE
} Color;

void greet(const char* name) {
    printf("Hello, %s\n", name);
}
```

Expected symbol kinds: `struct` (Point), `type_alias` (MyInt or typedef declarations), `enum` (Color), `function` (greet).

- [ ] **Step 7: Create `tests/fixtures/ts_samples/sample.cpp`**

```cpp
#include <string>

typedef int MyInt;

enum class Color {
    Red,
    Green,
    Blue
};

struct Point {
    int x;
    int y;
};

class Animal {
public:
    virtual std::string speak() {
        return "...";
    }
};

void greet(const std::string& name) {
    // greet
}
```

Expected symbol kinds: `type_alias` (MyInt), `enum` (Color), `struct` (Point), `class` (Animal), `method` (speak), `function` (greet).

- [ ] **Step 8: Create `tests/fixtures/ts_samples/sample.rb`**

```ruby
module Greetable
  def greet(name)
    "Hello, #{name}"
  end
end

class Animal
  include Greetable

  def speak
    "..."
  end
end

def hello(name)
  "Hello, #{name}"
end
```

Expected symbol kinds: `module` (Greetable), `function`/method (greet inside module), `class` (Animal), `function`/method (speak, hello).

- [ ] **Step 9: Commit**

```bash
git add tests/fixtures/ts_samples/
git commit -m "test: add tree-sitter fixture files for 8 languages"
```

---

## Task 3: Create `TreeSitterChunker` with Go support first (TDD)

**Files:**
- Create: `services/chunkers/tree_sitter_chunker.py`
- Create: `tests/test_tree_sitter_chunker.py`

Start with Go only to get the TDD loop working end-to-end, then extend in Task 4.

- [ ] **Step 1: Write the failing test for Go**

Create `tests/test_tree_sitter_chunker.py`:

```python
from __future__ import annotations

import pathlib
import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "ts_samples"


class TestTreeSitterChunkerGo:
    def _chunker(self):
        from services.chunkers.tree_sitter_chunker import TreeSitterChunker
        return TreeSitterChunker()

    def test_go_extracts_all_symbol_kinds(self):
        content = (FIXTURES / "sample.go").read_text()
        chunks = self._chunker().chunk("sample.go", content, "go")
        kinds = {c["symbol_kind"] for c in chunks}
        names = {c["symbol_name"] for c in chunks}
        assert "function" in kinds
        assert "method" in kinds
        assert "struct" in kinds
        assert "interface" in kinds
        assert "type_alias" in kinds
        assert "Greet" in names
        assert "Dog" in names
        assert "Animal" in names

    def test_go_chunk_schema_fields(self):
        content = (FIXTURES / "sample.go").read_text()
        chunks = self._chunker().chunk("sample.go", content, "go")
        assert len(chunks) > 0
        for chunk in chunks:
            assert "id" in chunk
            assert chunk["file_path"] == "sample.go"
            assert chunk["language"] == "go"
            assert isinstance(chunk["line_start"], int)
            assert isinstance(chunk["line_end"], int)
            assert chunk["line_start"] >= 1
            assert chunk["line_end"] >= chunk["line_start"]
            assert isinstance(chunk["content"], str)
            assert len(chunk["content"]) > 0
            assert chunk["score"] == 0.0

    def test_go_hard_fail_on_bad_language(self):
        chunker = self._chunker()
        with pytest.raises(Exception):
            chunker.chunk("bad.xyz", "not code", "nonexistent_language_xyz")
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
python3 -m pytest tests/test_tree_sitter_chunker.py -v
```

Expected: `ImportError` or `ModuleNotFoundError` — `tree_sitter_chunker` doesn't exist yet.

- [ ] **Step 3: Create `services/chunkers/tree_sitter_chunker.py` with Go support**

```python
from __future__ import annotations

import uuid
from typing import Any

from tree_sitter_languages import get_language, get_parser  # hard fail if not installed

# Maps our language name → tree-sitter-languages key
_LANGUAGE_MAP: dict[str, str] = {
    "go": "go",
    "javascript": "javascript",
    "typescript": "typescript",
    "rust": "rust",
    "java": "java",
    "c": "c",
    "cpp": "cpp",
    "ruby": "ruby",
}

# S-expression queries per language.
# Each capture group name becomes the symbol_kind in the output chunk.
_QUERY_MAP: dict[str, str] = {
    "go": """
        (function_declaration name: (identifier) @function)
        (method_declaration name: (field_identifier) @method)
        (type_declaration (type_spec name: (type_identifier) @struct
            type: (struct_type)))
        (type_declaration (type_spec name: (type_identifier) @interface
            type: (interface_type)))
        (type_declaration (type_spec name: (type_identifier) @type_alias
            type: (type_alias)))
    """,
    # Remaining languages filled in Task 4
}


class TreeSitterChunker:
    """Extracts symbol-level chunks from source files using tree-sitter AST parsing.

    Raises on any parse or grammar error — does not silently fall back.
    """

    def chunk(self, file_path: str, content: str, language: str) -> list[dict[str, Any]]:
        """Parse *content* and return one chunk per matched symbol.

        Args:
            file_path: Original file path (stored in each chunk, not read from disk).
            content: Source code as a string.
            language: Language name matching keys in ``_LANGUAGE_MAP``.

        Returns:
            List of chunk dicts with keys: id, file_path, language, symbol_name,
            symbol_kind, line_start, line_end, content, score.

        Raises:
            KeyError: If *language* is not in ``_LANGUAGE_MAP``.
            Exception: Any tree-sitter parse or query error propagates unchanged.
        """
        ts_lang_key = _LANGUAGE_MAP[language]  # KeyError on unknown language
        ts_language = get_language(ts_lang_key)
        parser = get_parser(ts_lang_key)

        tree = parser.parse(content.encode("utf-8"))
        query = ts_language.query(_QUERY_MAP[language])
        captures = query.captures(tree.root_node)

        lines = content.splitlines(keepends=True)
        chunks: list[dict[str, Any]] = []

        for node, capture_name in captures:
            symbol_name = content[node.start_byte:node.end_byte]
            # For named captures like "function", "method", etc., capture_name IS the kind
            symbol_kind = capture_name

            # Find the meaningful parent node to get the full symbol body
            body_node = _find_body_node(node)
            line_start = body_node.start_point[0] + 1  # tree-sitter is 0-based
            line_end = body_node.end_point[0] + 1

            chunks.append({
                "id": str(uuid.uuid4()),
                "file_path": file_path,
                "language": language,
                "symbol_name": symbol_name,
                "symbol_kind": symbol_kind,
                "line_start": line_start,
                "line_end": line_end,
                "content": "".join(lines[line_start - 1:line_end]),
                "score": 0.0,
            })

        return chunks


def _find_body_node(name_node: Any) -> Any:
    """Walk up from a name-capture node to find the enclosing declaration node."""
    node = name_node.parent
    if node is None:
        return name_node
    # For type_spec inside type_declaration, go up one more level
    if node.type in ("type_spec",):
        parent = node.parent
        if parent is not None:
            return parent
    return node
```

- [ ] **Step 4: Run the Go tests**

```bash
python3 -m pytest tests/test_tree_sitter_chunker.py::TestTreeSitterChunkerGo -v
```

Expected: all 3 tests PASS. If `test_go_extracts_all_symbol_kinds` fails on specific kinds, adjust the S-expression query in `_QUERY_MAP["go"]` — tree-sitter node type names are exact; use `tree-sitter-cli` or print `tree.root_node.sexp()` to inspect the AST.

- [ ] **Step 5: Commit**

```bash
git add services/chunkers/tree_sitter_chunker.py tests/test_tree_sitter_chunker.py
git commit -m "feat: add TreeSitterChunker with Go support (TDD)"
```

---

## Task 4: Extend `TreeSitterChunker` with the remaining 7 languages

**Files:**
- Modify: `services/chunkers/tree_sitter_chunker.py` — fill in `_QUERY_MAP` for JS, TS, Rust, Java, C, C++, Ruby
- Modify: `tests/test_tree_sitter_chunker.py` — add test classes for each language

- [ ] **Step 1: Write failing tests for all 7 remaining languages**

Append to `tests/test_tree_sitter_chunker.py`:

```python
class TestTreeSitterChunkerJS:
    def _chunker(self):
        from services.chunkers.tree_sitter_chunker import TreeSitterChunker
        return TreeSitterChunker()

    def test_js_extracts_function_and_class(self):
        content = (FIXTURES / "sample.js").read_text()
        chunks = self._chunker().chunk("sample.js", content, "javascript")
        kinds = {c["symbol_kind"] for c in chunks}
        names = {c["symbol_name"] for c in chunks}
        assert "function" in kinds
        assert "class" in kinds
        assert "greet" in names
        assert "Animal" in names

    def test_js_chunk_schema(self):
        content = (FIXTURES / "sample.js").read_text()
        chunks = self._chunker().chunk("sample.js", content, "javascript")
        for chunk in chunks:
            assert chunk["language"] == "javascript"
            assert chunk["line_start"] >= 1
            assert chunk["line_end"] >= chunk["line_start"]
            assert chunk["score"] == 0.0


class TestTreeSitterChunkerTS:
    def _chunker(self):
        from services.chunkers.tree_sitter_chunker import TreeSitterChunker
        return TreeSitterChunker()

    def test_ts_extracts_all_symbol_kinds(self):
        content = (FIXTURES / "sample.ts").read_text()
        chunks = self._chunker().chunk("sample.ts", content, "typescript")
        kinds = {c["symbol_kind"] for c in chunks}
        names = {c["symbol_name"] for c in chunks}
        assert "function" in kinds
        assert "class" in kinds
        assert "interface" in kinds
        assert "type_alias" in kinds
        assert "enum" in kinds
        assert "Dog" in names
        assert "Greeter" in names
        assert "Color" in names
        assert "hello" in names

    def test_ts_chunk_schema(self):
        content = (FIXTURES / "sample.ts").read_text()
        chunks = self._chunker().chunk("sample.ts", content, "typescript")
        for chunk in chunks:
            assert chunk["language"] == "typescript"
            assert chunk["line_start"] >= 1
            assert chunk["score"] == 0.0


class TestTreeSitterChunkerRust:
    def _chunker(self):
        from services.chunkers.tree_sitter_chunker import TreeSitterChunker
        return TreeSitterChunker()

    def test_rust_extracts_all_symbol_kinds(self):
        content = (FIXTURES / "sample.rs").read_text()
        chunks = self._chunker().chunk("sample.rs", content, "rust")
        kinds = {c["symbol_kind"] for c in chunks}
        names = {c["symbol_name"] for c in chunks}
        assert "function" in kinds
        assert "struct" in kinds
        assert "enum" in kinds
        assert "trait" in kinds
        assert "type_alias" in kinds
        assert "Dog" in names
        assert "Greeter" in names
        assert "Color" in names
        assert "hello" in names

    def test_rust_chunk_schema(self):
        content = (FIXTURES / "sample.rs").read_text()
        chunks = self._chunker().chunk("sample.rs", content, "rust")
        for chunk in chunks:
            assert chunk["language"] == "rust"
            assert chunk["score"] == 0.0


class TestTreeSitterChunkerJava:
    def _chunker(self):
        from services.chunkers.tree_sitter_chunker import TreeSitterChunker
        return TreeSitterChunker()

    def test_java_extracts_class_interface_enum_method(self):
        content = (FIXTURES / "sample.java").read_text()
        chunks = self._chunker().chunk("sample.java", content, "java")
        kinds = {c["symbol_kind"] for c in chunks}
        names = {c["symbol_name"] for c in chunks}
        assert "class" in kinds
        assert "interface" in kinds
        assert "enum" in kinds
        assert "function" in kinds
        assert "Main" in names
        assert "Greeter" in names
        assert "Color" in names

    def test_java_chunk_schema(self):
        content = (FIXTURES / "sample.java").read_text()
        chunks = self._chunker().chunk("sample.java", content, "java")
        for chunk in chunks:
            assert chunk["language"] == "java"
            assert chunk["score"] == 0.0


class TestTreeSitterChunkerC:
    def _chunker(self):
        from services.chunkers.tree_sitter_chunker import TreeSitterChunker
        return TreeSitterChunker()

    def test_c_extracts_function_struct_enum(self):
        content = (FIXTURES / "sample.c").read_text()
        chunks = self._chunker().chunk("sample.c", content, "c")
        kinds = {c["symbol_kind"] for c in chunks}
        names = {c["symbol_name"] for c in chunks}
        assert "function" in kinds
        assert "struct" in kinds or "type_alias" in kinds
        assert "greet" in names

    def test_c_chunk_schema(self):
        content = (FIXTURES / "sample.c").read_text()
        chunks = self._chunker().chunk("sample.c", content, "c")
        for chunk in chunks:
            assert chunk["language"] == "c"
            assert chunk["score"] == 0.0


class TestTreeSitterChunkerCpp:
    def _chunker(self):
        from services.chunkers.tree_sitter_chunker import TreeSitterChunker
        return TreeSitterChunker()

    def test_cpp_extracts_all_symbol_kinds(self):
        content = (FIXTURES / "sample.cpp").read_text()
        chunks = self._chunker().chunk("sample.cpp", content, "cpp")
        kinds = {c["symbol_kind"] for c in chunks}
        names = {c["symbol_name"] for c in chunks}
        assert "function" in kinds
        assert "class" in kinds
        assert "struct" in kinds or "enum" in kinds
        assert "greet" in names
        assert "Animal" in names

    def test_cpp_chunk_schema(self):
        content = (FIXTURES / "sample.cpp").read_text()
        chunks = self._chunker().chunk("sample.cpp", content, "cpp")
        for chunk in chunks:
            assert chunk["language"] == "cpp"
            assert chunk["score"] == 0.0


class TestTreeSitterChunkerRuby:
    def _chunker(self):
        from services.chunkers.tree_sitter_chunker import TreeSitterChunker
        return TreeSitterChunker()

    def test_ruby_extracts_function_class_module(self):
        content = (FIXTURES / "sample.rb").read_text()
        chunks = self._chunker().chunk("sample.rb", content, "ruby")
        kinds = {c["symbol_kind"] for c in chunks}
        names = {c["symbol_name"] for c in chunks}
        assert "function" in kinds
        assert "class" in kinds
        assert "module" in kinds
        assert "Animal" in names
        assert "Greetable" in names

    def test_ruby_chunk_schema(self):
        content = (FIXTURES / "sample.rb").read_text()
        chunks = self._chunker().chunk("sample.rb", content, "ruby")
        for chunk in chunks:
            assert chunk["language"] == "ruby"
            assert chunk["score"] == 0.0
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python3 -m pytest tests/test_tree_sitter_chunker.py -v -k "not Go"
```

Expected: failures on KeyError (missing `_QUERY_MAP` entries).

- [ ] **Step 3: Fill in `_QUERY_MAP` for all 7 remaining languages**

In `services/chunkers/tree_sitter_chunker.py`, extend `_QUERY_MAP` with these entries (add after the `"go"` entry):

```python
    "javascript": """
        (function_declaration name: (identifier) @function)
        (lexical_declaration
            (variable_declarator
                name: (identifier) @function
                value: (arrow_function)))
        (class_declaration name: (identifier) @class)
    """,
    "typescript": """
        (function_declaration name: (identifier) @function)
        (method_definition name: (property_identifier) @method)
        (class_declaration name: (type_identifier) @class)
        (interface_declaration name: (type_identifier) @interface)
        (type_alias_declaration name: (type_identifier) @type_alias)
        (enum_declaration name: (identifier) @enum)
    """,
    "rust": """
        (function_item name: (identifier) @function)
        (impl_item
            (declaration_list
                (function_item name: (identifier) @method)))
        (struct_item name: (type_identifier) @struct)
        (enum_item name: (type_identifier) @enum)
        (trait_item name: (type_identifier) @trait)
        (type_item name: (type_identifier) @type_alias)
    """,
    "java": """
        (class_declaration name: (identifier) @class)
        (interface_declaration name: (identifier) @interface)
        (enum_declaration name: (identifier) @enum)
        (method_declaration name: (identifier) @function)
    """,
    "c": """
        (function_definition declarator: (function_declarator
            declarator: (identifier) @function))
        (struct_specifier name: (type_identifier) @struct)
        (enum_specifier name: (type_identifier) @enum)
        (type_definition declarator: (type_identifier) @type_alias)
    """,
    "cpp": """
        (function_definition declarator: (function_declarator
            declarator: (identifier) @function))
        (function_definition declarator: (function_declarator
            declarator: (qualified_identifier
                name: (identifier) @method)))
        (class_specifier name: (type_identifier) @class)
        (struct_specifier name: (type_identifier) @struct)
        (enum_specifier name: (type_identifier) @enum)
        (type_definition declarator: (type_identifier) @type_alias)
    """,
    "ruby": """
        (method name: (identifier) @function)
        (class name: (constant) @class)
        (module name: (constant) @module)
    """,
```

**Note:** Tree-sitter node type names must match the grammar exactly. If a test fails with a query parse error, print `parser.parse(content.encode()).root_node.sexp()` to inspect the real AST and adjust the S-expression accordingly.

- [ ] **Step 4: Run all tree-sitter tests**

```bash
python3 -m pytest tests/test_tree_sitter_chunker.py -v
```

Expected: all tests PASS. For any failures, inspect the AST:
```bash
python3 -c "
from tree_sitter_languages import get_parser
p = get_parser('rust')
tree = p.parse(open('tests/fixtures/ts_samples/sample.rs', 'rb').read())
print(tree.root_node.sexp()[:3000])
"
```
Adjust the query until it passes.

- [ ] **Step 5: Commit**

```bash
git add services/chunkers/tree_sitter_chunker.py tests/test_tree_sitter_chunker.py
git commit -m "feat: extend TreeSitterChunker to all 8 languages"
```

---

## Task 5: Update `CodeChunker` to delegate to `TreeSitterChunker`

**Files:**
- Modify: `services/chunkers/code_chunker.py`
- Modify: `tests/test_indexing.py`

- [ ] **Step 1: Write the updated `TestCodeChunker` tests first**

In `tests/test_indexing.py`, replace the existing `TestCodeChunker` class with:

```python
class TestCodeChunker:
    def test_python_chunker_emits_symbol_boundaries(self):
        from services.chunkers import CodeChunker
        content = (
            "def foo():\n"
            "    return 1\n"
            "\n"
            "async def bar():\n"
            "    return 2\n"
            "\n"
            "class Baz:\n"
            "    pass\n"
        )
        chunks = CodeChunker().chunk("src/a.py", content, "python")
        assert len(chunks) == 3
        names = {c["symbol_name"] for c in chunks}
        assert names == {"foo", "bar", "Baz"}
        kinds = {c["symbol_kind"] for c in chunks}
        assert kinds == {"function", "class"}
        for chunk in chunks:
            assert chunk["line_start"] >= 1
            assert chunk["line_end"] >= chunk["line_start"]
            assert chunk["file_path"] == "src/a.py"
            assert chunk["language"] == "python"
            assert chunk["score"] == 0.0

    def test_fallback_chunker_activates_on_unsupported_language(self):
        """Languages not in tree-sitter or Python ast fall back to text windowing."""
        from services.chunkers import CodeChunker
        content = "some text content\nline two\n"
        # "text" is not handled by any symbol chunker
        chunks = CodeChunker().chunk("readme.txt", content, "text")
        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk["symbol_name"] is None
            assert chunk["symbol_kind"] is None
            assert chunk["content"]
            assert chunk["file_path"] == "readme.txt"

    def test_fallback_chunker_activates_on_python_parse_failure(self):
        from services.chunkers import CodeChunker
        invalid_python = "def broken(\n    not valid python @@@@\n"
        chunks = CodeChunker().chunk("broken.py", invalid_python, "python")
        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk["symbol_name"] is None

    def test_go_delegates_to_tree_sitter(self):
        """Go chunking uses TreeSitterChunker and returns real symbol kinds."""
        from unittest.mock import patch, MagicMock
        from services.chunkers import CodeChunker
        mock_chunks = [{"id": "x", "symbol_name": "Foo", "symbol_kind": "function",
                        "file_path": "main.go", "language": "go",
                        "line_start": 1, "line_end": 5, "content": "func Foo(){}", "score": 0.0}]
        with patch("services.chunkers.code_chunker.TreeSitterChunker.chunk",
                   return_value=mock_chunks) as mock_chunk:
            result = CodeChunker().chunk("main.go", "func Foo(){}", "go")
            mock_chunk.assert_called_once_with("main.go", "func Foo(){}", "go")
            assert result == mock_chunks

    def test_typescript_delegates_to_tree_sitter(self):
        from unittest.mock import patch
        from services.chunkers import CodeChunker
        mock_chunks = [{"id": "y", "symbol_name": "Bar", "symbol_kind": "class",
                        "file_path": "a.ts", "language": "typescript",
                        "line_start": 1, "line_end": 3, "content": "class Bar{}", "score": 0.0}]
        with patch("services.chunkers.code_chunker.TreeSitterChunker.chunk",
                   return_value=mock_chunks) as mock_chunk:
            result = CodeChunker().chunk("a.ts", "class Bar{}", "typescript")
            mock_chunk.assert_called_once_with("a.ts", "class Bar{}", "typescript")
            assert result == mock_chunks

    def test_rust_delegates_to_tree_sitter(self):
        from unittest.mock import patch
        from services.chunkers import CodeChunker
        mock_chunks = [{"id": "z", "symbol_name": "MyStruct", "symbol_kind": "struct",
                        "file_path": "lib.rs", "language": "rust",
                        "line_start": 1, "line_end": 4, "content": "struct MyStruct{}", "score": 0.0}]
        with patch("services.chunkers.code_chunker.TreeSitterChunker.chunk",
                   return_value=mock_chunks) as mock_chunk:
            result = CodeChunker().chunk("lib.rs", "struct MyStruct{}", "rust")
            mock_chunk.assert_called_once_with("lib.rs", "struct MyStruct{}", "rust")
            assert result == mock_chunks
```

- [ ] **Step 2: Run the new tests to confirm they fail**

```bash
python3 -m pytest tests/test_indexing.py::TestCodeChunker -v
```

Expected: `test_go_delegates_to_tree_sitter`, `test_typescript_delegates_to_tree_sitter`, `test_rust_delegates_to_tree_sitter` all FAIL (old code still uses regex). `test_fallback_chunker_activates_on_unsupported_language` may fail too (Rust now is expected to go through tree-sitter, not fallback).

- [ ] **Step 3: Rewrite `CodeChunker` in `services/chunkers/code_chunker.py`**

Replace the entire file content with:

```python
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
```

- [ ] **Step 4: Run all `TestCodeChunker` tests**

```bash
python3 -m pytest tests/test_indexing.py::TestCodeChunker -v
```

Expected: all PASS.

- [ ] **Step 5: Run full test suite**

```bash
python3 -m pytest tests/ -v
```

Expected: all tests PASS (152+ tests). If any pre-existing test breaks because it expected Rust/Go/TS to use the old text-window or regex output, update that test to match the new tree-sitter behavior (richer `symbol_kind` values).

- [ ] **Step 6: Commit**

```bash
git add services/chunkers/code_chunker.py tests/test_indexing.py
git commit -m "feat: wire CodeChunker to TreeSitterChunker for 8 languages"
```

---

## Task 6: Final docstring pass + full suite green check

**Files:**
- Review: `services/chunkers/tree_sitter_chunker.py`
- Review: `services/chunkers/code_chunker.py`

- [ ] **Step 1: Verify docstrings on all public methods**

Check that `TreeSitterChunker.chunk()`, `CodeChunker.chunk()`, `CodeChunker._chunk_python()`, and `CodeChunker._chunk_text()` all have accurate, complete docstrings covering args, returns, and raises. The docstrings written in Tasks 3 and 5 should already cover this — confirm nothing was accidentally dropped.

- [ ] **Step 2: Run full test suite one final time**

```bash
python3 -m pytest tests/ -v --tb=short
```

Expected: all tests PASS, 0 failures, 0 errors.

- [ ] **Step 3: Final commit**

```bash
git add services/chunkers/
git commit -m "docs: final docstring pass on tree-sitter chunker modules"
```

---

## Definition of Done

- [ ] `tree-sitter-languages==1.10.2` in `requirements.txt`
- [ ] `services/chunkers/tree_sitter_chunker.py` exists with `TreeSitterChunker` class
- [ ] All 8 languages in `_LANGUAGE_MAP` and `_QUERY_MAP`
- [ ] `services/chunkers/code_chunker.py` has no regex imports, delegates 8 languages to `TreeSitterChunker`
- [ ] `tests/fixtures/ts_samples/` contains 8 fixture files
- [ ] `tests/test_tree_sitter_chunker.py` covers all 8 languages + schema + hard-fail
- [ ] `tests/test_indexing.py::TestCodeChunker` updated — no regex-path assertions
- [ ] `python3 -m pytest tests/ -v` → all green
