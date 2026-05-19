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
