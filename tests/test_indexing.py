from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import MagicMock

from services.file_corpus_service import FileCorpusService
from services.index_manifest_service import IndexManifestService


class TestFileCorpusIsolation:
    def test_upsert_and_query(self):
        svc = FileCorpusService()
        svc.upsert_chunks(
            root="/repo",
            file_path="src/main.py",
            chunks=[
                {"content": "def hello(): pass", "language": "python", "line_start": 1, "line_end": 1},
                {"content": "def world(): pass", "language": "python", "line_start": 2, "line_end": 2},
            ],
        )
        results = svc.query("hello")
        assert len(results) == 1
        assert results[0]["content"] == "def hello(): pass"
        assert results[0]["root"] == "/repo"
        assert results[0]["file_path"] == "src/main.py"
        assert results[0]["score"] > 0.0

    def test_file_corpus_isolation(self):
        svc = FileCorpusService()
        svc.upsert_chunks(
            root="/repo",
            file_path="src/main.py",
            chunks=[{"content": "chunk one"}, {"content": "chunk two"}],
        )
        assert svc.get_status()["total_chunks"] == 2

        memory_mock = MagicMock()
        memory_mock.get_all.return_value = {"results": [{"id": "m1", "memory": "some memory"}]}

        result = svc.reset()
        assert result["cleared_chunks"] == 2
        assert svc.get_status()["total_chunks"] == 0

        memory_mock.get_all.assert_not_called()
        memory_mock.reset.assert_not_called()

    def test_reset_only_clears_corpus(self):
        svc = FileCorpusService()
        svc.upsert_chunks("/r", "a.py", [{"content": "x"}])
        svc.upsert_chunks("/r", "b.py", [{"content": "y"}])
        assert svc.get_status()["total_chunks"] == 2

        cleared = svc.reset()
        assert cleared["cleared_chunks"] == 2
        assert svc.get_status()["total_chunks"] == 0

    def test_delete_file_removes_only_target(self):
        svc = FileCorpusService()
        svc.upsert_chunks("/r", "a.py", [{"content": "keep"}])
        svc.upsert_chunks("/r", "b.py", [{"content": "remove"}])

        svc.delete_file("/r", "b.py")

        status = svc.get_status()
        assert status["total_chunks"] == 1
        results = svc.query("keep")
        assert len(results) == 1

    def test_upsert_replaces_existing_file_chunks(self):
        svc = FileCorpusService()
        svc.upsert_chunks("/r", "a.py", [{"content": "old"}])
        svc.upsert_chunks("/r", "a.py", [{"content": "new1"}, {"content": "new2"}])
        assert svc.get_status()["total_chunks"] == 2
        assert len(svc.query("old")) == 0
        assert len(svc.query("new1")) == 1

    def test_query_with_filters(self):
        svc = FileCorpusService()
        svc.upsert_chunks("/r", "a.py", [
            {"content": "python func", "language": "python"},
            {"content": "js func", "language": "javascript"},
        ])
        results = svc.query("func", filters={"language": "python"})
        assert len(results) == 1
        assert results[0]["language"] == "python"

    def test_query_limit(self):
        svc = FileCorpusService()
        svc.upsert_chunks("/r", "a.py", [{"content": f"item {i}"} for i in range(10)])
        results = svc.query("item", limit=3)
        assert len(results) == 3


class TestManifestTracksFingerprints:
    def test_manifest_tracks_fingerprints(self):
        svc = IndexManifestService()
        svc.register_root("/repo")
        svc.update_file(
            root="/repo",
            file_path="src/main.py",
            fingerprint="sha256:abc123",
            chunk_ids=["c1", "c2", "c3"],
        )

        status = svc.get_status()
        root_info = status["roots"]["/repo"]
        assert root_info["file_count"] == 1
        assert root_info["chunk_count"] == 3

        file_key = "/repo\x00src/main.py"
        record = svc._files[file_key]
        assert record.fingerprint == "sha256:abc123"
        assert record.chunk_ids == ["c1", "c2", "c3"]

    def test_update_file_replaces_fingerprint(self):
        svc = IndexManifestService()
        svc.update_file("/r", "a.py", "fp1", ["c1"])
        svc.update_file("/r", "a.py", "fp2", ["c2", "c3"])

        status = svc.get_status()
        assert status["roots"]["/r"]["file_count"] == 1
        assert status["roots"]["/r"]["chunk_count"] == 2

        record = svc._files["/r\x00a.py"]
        assert record.fingerprint == "fp2"

    def test_remove_file_updates_counts(self):
        svc = IndexManifestService()
        svc.update_file("/r", "a.py", "fp1", ["c1", "c2"])
        svc.update_file("/r", "b.py", "fp2", ["c3"])

        svc.remove_file("/r", "a.py")

        status = svc.get_status()
        assert status["roots"]["/r"]["file_count"] == 1
        assert status["roots"]["/r"]["chunk_count"] == 1

    def test_manifest_reset(self):
        svc = IndexManifestService()
        svc.update_file("/r", "a.py", "fp1", ["c1"])
        svc.reset()
        status = svc.get_status()
        assert status["roots"] == {}
        assert status["total_files"] == 0

    def test_register_root_idempotent(self):
        svc = IndexManifestService()
        svc.register_root("/r")
        svc.register_root("/r")
        assert len(svc._roots) == 1

    def test_remove_nonexistent_file_is_noop(self):
        svc = IndexManifestService()
        svc.register_root("/r")
        svc.remove_file("/r", "ghost.py")
        assert svc.get_status()["roots"]["/r"]["file_count"] == 0


class TestFileScanner:
    def test_scanner_returns_only_supported_files(self, tmp_path):
        (tmp_path / "valid.py").write_text("print('hello')")
        (tmp_path / "notes.md").write_text("# notes")
        (tmp_path / "app.exe").write_bytes(b"\x00\x01\x02\x03binary")
        (tmp_path / "empty.py").write_text("")
        big = tmp_path / "big.py"
        big.write_bytes(b"x" * (1 * 1024 * 1024 + 1))
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "hidden.py").write_text("# should be ignored")

        from services.file_scanner import FileScanner
        scanner = FileScanner()
        results = scanner.scan(str(tmp_path))
        paths = {r["file_path"] for r in results}

        assert "valid.py" in paths
        assert "notes.md" in paths
        assert "app.exe" not in paths
        assert "empty.py" not in paths
        assert "big.py" not in paths
        assert str(nm / "hidden.py").endswith("hidden.py") and not any("node_modules" in p for p in paths)
        assert len(results) == 2

    def test_fingerprint_change_detection(self, tmp_path):
        (tmp_path / "a.py").write_text("version 1")
        (tmp_path / "b.py").write_text("stable content")

        from services.file_scanner import FileScanner
        scanner = FileScanner()

        first_scan = scanner.scan(str(tmp_path))
        prev_manifest = {item["file_path"]: item["fingerprint"] for item in first_scan}

        (tmp_path / "a.py").write_text("version 2 — changed")
        (tmp_path / "b.py").unlink()

        changes = scanner.diff(str(tmp_path), prev_manifest)

        assert "a.py" in changes["updated"]
        assert "b.py" in changes["deleted"]
        assert changes["created"] == []


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
        from unittest.mock import patch
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


class TestMarkdownChunker:
    def test_markdown_heading_boundaries(self):
        from services.chunkers import MarkdownChunker
        content = (
            "# Introduction\n"
            "Some intro text.\n"
            "\n"
            "## Details\n"
            "Detail content.\n"
            "\n"
            "### Sub-section\n"
            "Sub content.\n"
        )
        chunks = MarkdownChunker().chunk("doc.md", content)
        assert len(chunks) == 3
        assert chunks[0]["symbol_name"] == "Introduction"
        assert chunks[1]["symbol_name"] == "Details"
        assert chunks[2]["symbol_name"] == "Sub-section"
        for chunk in chunks:
            assert chunk["language"] == "markdown"
            assert chunk["symbol_kind"] == "section"
            assert chunk["file_path"] == "doc.md"
            assert chunk["score"] == 0.0

    def test_markdown_no_headings_produces_single_chunk(self):
        from services.chunkers import MarkdownChunker
        content = "Just plain text.\nNo headings here.\n"
        chunks = MarkdownChunker().chunk("notes.md", content)
        assert len(chunks) == 1
        assert chunks[0]["symbol_name"] is None
        assert chunks[0]["content"] == content


class TestIndexingService:
    def _make_service(self):
        from services.indexing_service import IndexingService
        corpus = FileCorpusService()
        manifest = IndexManifestService()
        from services.file_scanner import FileScanner
        scanner = FileScanner()
        return IndexingService(corpus, manifest, scanner), corpus, manifest

    def test_sync_indexes_fixture_root(self, tmp_path):
        (tmp_path / "main.py").write_text("def hello():\n    pass\n")
        (tmp_path / "README.md").write_text("# Title\nSome content.\n")

        svc, corpus, manifest = self._make_service()
        result = svc.sync(str(tmp_path))

        assert result["files_indexed"] == 2
        assert result["chunks_indexed"] > 0
        assert result["errors"] == []

        status = svc.status()
        assert status["total_files"] == 2
        assert status["total_chunks"] > 0
        assert len(status["roots"]) == 1

    def test_sync_is_incremental(self, tmp_path):
        (tmp_path / "a.py").write_text("def foo():\n    pass\n")
        (tmp_path / "b.py").write_text("def bar():\n    pass\n")

        svc, corpus, manifest = self._make_service()
        first = svc.sync(str(tmp_path))
        assert first["files_indexed"] == 2

        (tmp_path / "a.py").write_text("def foo():\n    return 42\n")

        second = svc.sync(str(tmp_path))
        assert second["files_indexed"] == 1

    def test_reset_clears_corpus_and_manifest(self, tmp_path):
        (tmp_path / "x.py").write_text("x = 1\n")

        svc, corpus, manifest = self._make_service()
        svc.sync(str(tmp_path))
        assert svc.status()["total_files"] > 0

        separate_corpus = FileCorpusService()
        separate_corpus.upsert_chunks("/mem", "note.py", [{"content": "memory item"}])
        assert separate_corpus.get_status()["total_chunks"] == 1

        result = svc.reset()
        assert result["files_cleared"] >= 1
        assert result["chunks_cleared"] >= 1

        status = svc.status()
        assert status["total_files"] == 0
        assert status["total_chunks"] == 0

        assert separate_corpus.get_status()["total_chunks"] == 1


class TestWatchService:
    def _make_services(self):
        from services.indexing_service import IndexingService
        from services.file_corpus_service import FileCorpusService
        from services.index_manifest_service import IndexManifestService
        from services.file_scanner import FileScanner
        from services.watch_service import WatchService

        corpus = FileCorpusService()
        manifest = IndexManifestService()
        scanner = FileScanner()
        indexing = IndexingService(corpus, manifest, scanner)
        watch = WatchService(indexing, poll_interval=0.2)
        return watch, indexing, corpus

    def test_watch_start_stop_lifecycle(self, tmp_path):
        import threading
        watch, _, _ = self._make_services()
        root = str(tmp_path)
        initial_threads = threading.active_count()

        watch.start(root)
        assert watch.is_watching(root)
        assert threading.active_count() > initial_threads

        watch.stop(root)
        assert not watch.is_watching(root)
        assert threading.active_count() <= initial_threads

    def test_watch_start_noop_if_already_watching(self, tmp_path):
        import threading
        watch, _, _ = self._make_services()
        root = str(tmp_path)

        watch.start(root)
        count_after_first = threading.active_count()
        watch.start(root)
        assert threading.active_count() == count_after_first
        assert len(watch.list_roots()) == 1
        watch.stop(root)

    def test_watch_propagates_file_changes(self, tmp_path):
        import time
        watch, _, corpus = self._make_services()
        root = str(tmp_path)

        (tmp_path / "hello.py").write_text("def original():\n    pass\n")
        watch.start(root)

        time.sleep(0.5)

        (tmp_path / "hello.py").write_text("def updated_function():\n    return 99\n")

        found = False
        for _ in range(50):
            results = corpus.query("updated_function", filters={"root": root}, limit=5)
            if any("updated_function" in r.get("content", "") for r in results):
                found = True
                break
            time.sleep(0.1)

        watch.stop(root)
        assert found, "updated content was not indexed within 5s"

    def test_watch_status_reports_active_roots(self, tmp_path):
        watch, _, _ = self._make_services()
        root1 = str(tmp_path / "r1")
        root2 = str(tmp_path / "r2")
        (tmp_path / "r1").mkdir()
        (tmp_path / "r2").mkdir()

        watch.start(root1)
        watch.start(root2)

        roots = watch.list_roots()
        assert root1 in roots
        assert root2 in roots

        watch.stop(root1)

        roots = watch.list_roots()
        assert root1 not in roots
        assert root2 in roots

        watch.stop(root2)


class TestFileCorpusSummaryFields:
    def test_file_corpus_stores_summary_text_when_provided(self):
        svc = FileCorpusService()
        svc.upsert_chunks(
            root="/repo",
            file_path="src/a.py",
            chunks=[{"content": "def hello(): pass", "summary_text": "some summary"}],
        )
        results = svc.query("hello")
        assert len(results) == 1
        assert results[0]["summary_text"] == "some summary"

    def test_file_corpus_stores_nullable_summary_embedding(self):
        svc = FileCorpusService()
        svc.upsert_chunks(
            root="/repo",
            file_path="src/b.py",
            chunks=[{"content": "def world(): pass"}],
        )
        results = svc.query("world")
        assert len(results) == 1
        assert results[0]["summary_text"] is None
        assert results[0]["summary_embedding"] is None

    def test_file_corpus_mixed_summary_and_plain_chunks_query_safely(self):
        svc = FileCorpusService()
        svc.upsert_chunks(
            root="/repo",
            file_path="src/c.py",
            chunks=[
                {"content": "chunk with summary", "summary_text": "a summary", "summary_embedding": [0.1, 0.2]},
                {"content": "chunk without summary"},
            ],
        )
        results = svc.query("chunk")
        assert len(results) == 2
        with_summary = next(r for r in results if r["summary_text"] is not None)
        without_summary = next(r for r in results if r["summary_text"] is None)
        assert with_summary["summary_text"] == "a summary"
        assert with_summary["summary_embedding"] == [0.1, 0.2]
        assert without_summary["summary_embedding"] is None

    def test_file_corpus_reset_clears_summary_fields(self):
        svc = FileCorpusService()
        svc.upsert_chunks(
            root="/repo",
            file_path="src/d.py",
            chunks=[{"content": "something", "summary_text": "will be cleared"}],
        )
        assert svc.get_status()["total_chunks"] == 1
        svc.reset()
        assert svc.get_status()["total_chunks"] == 0
        results = svc.query("something")
        assert results == []


class TestIndexingServiceSummaries:
    def _make_service(self, memory_instance=None):
        from services.indexing_service import IndexingService
        from services.file_scanner import FileScanner
        corpus = FileCorpusService()
        manifest = IndexManifestService()
        scanner = FileScanner()
        return IndexingService(corpus, manifest, scanner, memory_instance=memory_instance), corpus, manifest

    def test_sync_no_summaries_baseline_unchanged(self, tmp_path):
        """generate_summaries=False (default) produces no summary fields on chunks."""
        (tmp_path / "hello.py").write_text("def hello(): pass\n")
        svc, corpus, _ = self._make_service()
        result = svc.sync(str(tmp_path))
        assert result["files_indexed"] == 1
        chunks = corpus.query("hello")
        assert len(chunks) >= 1
        assert chunks[0]["summary_text"] is None
        assert chunks[0]["summary_embedding"] is None

    def test_sync_with_summaries_stores_text_and_embedding(self, tmp_path):
        """generate_summaries=True calls SummaryService and stores results into the chunk record."""
        from unittest.mock import MagicMock, patch
        from services.summary_service import SummaryResult

        (tmp_path / "hello.py").write_text("def hello(): pass\n")
        mock_memory = MagicMock()
        svc, corpus, _ = self._make_service(mock_memory)
        mock_result = SummaryResult(summary_text="Greets the world", summary_embedding=[0.1, 0.2])

        with patch("services.indexing_service.SummaryService") as MockSummary:
            MockSummary.return_value.generate_summary.return_value = mock_result
            result = svc.sync(str(tmp_path), generate_summaries=True)

        assert result["files_indexed"] == 1
        chunks = corpus.query("hello")
        assert len(chunks) >= 1
        assert chunks[0]["summary_text"] == "Greets the world"
        assert chunks[0]["summary_embedding"] == [0.1, 0.2]

    def test_sync_summary_exception_still_indexes_chunk_without_summary(self, tmp_path):
        """If SummaryService.generate_summary raises, chunk is still indexed (no summary fields)."""
        from unittest.mock import MagicMock, patch

        (tmp_path / "hello.py").write_text("def hello(): pass\n")
        mock_memory = MagicMock()
        svc, corpus, _ = self._make_service(mock_memory)

        with patch("services.indexing_service.SummaryService") as MockSummary:
            MockSummary.return_value.generate_summary.side_effect = Exception("LLM unavailable")
            result = svc.sync(str(tmp_path), generate_summaries=True)

        assert result["files_indexed"] == 1
        chunks = corpus.query("hello")
        assert len(chunks) >= 1
        assert chunks[0]["summary_text"] is None

    def test_sync_empty_summary_text_not_stored(self, tmp_path):
        """Whitespace-only summary_text is treated as failure: not stored."""
        from unittest.mock import MagicMock, patch
        from services.summary_service import SummaryResult

        (tmp_path / "hello.py").write_text("def hello(): pass\n")
        mock_memory = MagicMock()
        svc, corpus, _ = self._make_service(mock_memory)

        with patch("services.indexing_service.SummaryService") as MockSummary:
            MockSummary.return_value.generate_summary.return_value = SummaryResult(
                summary_text="   ", summary_embedding=[0.5]
            )
            svc.sync(str(tmp_path), generate_summaries=True)

        chunks = corpus.query("hello")
        assert chunks[0]["summary_text"] is None
        assert chunks[0]["summary_embedding"] is None

    def test_sync_summary_instantiated_with_memory_instance(self, tmp_path):
        """SummaryService is created with the memory_instance passed to IndexingService."""
        from unittest.mock import MagicMock, patch
        from services.summary_service import SummaryResult

        (tmp_path / "hello.py").write_text("def hello(): pass\n")
        mock_memory = MagicMock()
        svc, corpus, _ = self._make_service(mock_memory)

        with patch("services.indexing_service.SummaryService") as MockSummary:
            MockSummary.return_value.generate_summary.return_value = SummaryResult()
            svc.sync(str(tmp_path), generate_summaries=True)

        MockSummary.assert_called_once_with(mock_memory)


class TestWatchServiceSummaryFlag:
    def _make_watch(self, mock_indexing=None, poll_interval=0.05):
        from unittest.mock import MagicMock
        from services.watch_service import WatchService
        if mock_indexing is None:
            mock_indexing = MagicMock()
        return WatchService(mock_indexing, poll_interval=poll_interval), mock_indexing

    def test_watch_start_forwards_generate_summaries_false_by_default(self, tmp_path):
        """WatchService calls sync with generate_summaries=False by default."""
        import time
        watch, mock_indexing = self._make_watch()
        watch.start(str(tmp_path))
        time.sleep(0.15)
        watch.stop(str(tmp_path))
        assert mock_indexing.sync.call_count >= 1
        for call in mock_indexing.sync.call_args_list:
            args, kwargs = call
            flag = kwargs.get("generate_summaries", args[1] if len(args) > 1 else False)
            assert flag is False

    def test_watch_start_forwards_generate_summaries_true(self, tmp_path):
        """WatchService.start(root, generate_summaries=True) passes flag to sync."""
        import time
        watch, mock_indexing = self._make_watch()
        watch.start(str(tmp_path), generate_summaries=True)
        time.sleep(0.15)
        watch.stop(str(tmp_path))
        assert mock_indexing.sync.call_count >= 1
        for call in mock_indexing.sync.call_args_list:
            args, kwargs = call
            flag = kwargs.get("generate_summaries", args[1] if len(args) > 1 else False)
            assert flag is True


class TestFileCorpusGetFileChunks:
    def test_returns_chunks_for_known_file(self):
        svc = FileCorpusService()
        svc.upsert_chunks(
            root="/repo",
            file_path="src/a.py",
            chunks=[
                {"content": "def foo(): pass", "line_start": 1, "line_end": 1},
                {"content": "def bar(): pass", "line_start": 3, "line_end": 3},
            ],
        )
        chunks = svc.get_file_chunks("/repo", "src/a.py")
        assert len(chunks) == 2
        assert chunks[0]["line_start"] == 1
        assert chunks[1]["line_start"] == 3
        # score must be stripped
        for c in chunks:
            assert "score" not in c

    def test_returns_empty_list_for_unknown_file(self):
        svc = FileCorpusService()
        assert svc.get_file_chunks("/repo", "ghost.py") == []

    def test_does_not_return_chunks_from_other_root(self):
        svc = FileCorpusService()
        svc.upsert_chunks("/repo-a", "src/a.py", [{"content": "a"}])
        svc.upsert_chunks("/repo-b", "src/a.py", [{"content": "b"}])
        chunks = svc.get_file_chunks("/repo-a", "src/a.py")
        assert len(chunks) == 1
        assert chunks[0]["content"] == "a"

    def test_include_embeddings_false_replaces_with_bool(self):
        svc = FileCorpusService()
        svc.upsert_chunks(
            root="/repo",
            file_path="src/a.py",
            chunks=[
                {"content": "with emb", "summary_embedding": [0.1, 0.2]},
                {"content": "without emb"},
            ],
        )
        chunks = svc.get_file_chunks("/repo", "src/a.py", include_embeddings=False)
        by_content = {c["content"]: c for c in chunks}
        assert "summary_embedding" not in by_content["with emb"]
        assert by_content["with emb"]["has_summary_embedding"] is True
        assert by_content["without emb"]["has_summary_embedding"] is False

    def test_include_embeddings_true_preserves_raw_vector(self):
        svc = FileCorpusService()
        svc.upsert_chunks(
            root="/repo",
            file_path="src/a.py",
            chunks=[{"content": "vec chunk", "summary_embedding": [0.5, 0.6]}],
        )
        chunks = svc.get_file_chunks("/repo", "src/a.py", include_embeddings=True)
        assert chunks[0]["summary_embedding"] == [0.5, 0.6]
        assert "has_summary_embedding" not in chunks[0]

    def test_sorted_by_line_start_with_none_last(self):
        svc = FileCorpusService()
        svc.upsert_chunks(
            root="/r",
            file_path="a.py",
            chunks=[
                {"content": "no line"},
                {"content": "line 5", "line_start": 5},
                {"content": "line 1", "line_start": 1},
            ],
        )
        chunks = svc.get_file_chunks("/r", "a.py")
        assert chunks[0]["line_start"] == 1
        assert chunks[1]["line_start"] == 5
        assert chunks[2]["line_start"] is None


class TestIndexingServiceFileChunks:
    def _make_service(self):
        from services.indexing_service import IndexingService
        from services.file_scanner import FileScanner
        corpus = FileCorpusService()
        manifest = IndexManifestService()
        scanner = FileScanner()
        return IndexingService(corpus, manifest, scanner), corpus, manifest

    def test_file_chunks_returns_chunks_and_manifest_for_indexed_file(self, tmp_path):
        (tmp_path / "hello.py").write_text("def hello(): pass\n")
        svc, _, _ = self._make_service()
        svc.sync(str(tmp_path))

        result = svc.file_chunks("hello.py", root=str(tmp_path))
        assert result["file_path"] == "hello.py"
        assert result["chunk_count"] >= 1
        assert len(result["chunks"]) >= 1
        assert result["manifest"] is not None
        assert result["manifest"]["root"] == str(tmp_path)
        assert "fingerprint" in result["manifest"]

    def test_file_chunks_returns_empty_for_unknown_file(self, tmp_path):
        svc, _, _ = self._make_service()
        svc.sync(str(tmp_path))

        result = svc.file_chunks("nonexistent.py", root=str(tmp_path))
        assert result["chunk_count"] == 0
        assert result["chunks"] == []
        assert result["manifest"] is None

    def test_file_chunks_searches_all_roots_when_root_is_none(self, tmp_path):
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_a.mkdir()
        root_b.mkdir()
        (root_a / "shared.py").write_text("def foo(): pass\n")
        (root_b / "shared.py").write_text("def bar(): pass\n")

        svc, _, _ = self._make_service()
        svc.sync(str(root_a))
        svc.sync(str(root_b))

        result = svc.file_chunks("shared.py", root=None)
        # chunks from both roots
        assert result["chunk_count"] >= 2

    def test_file_chunks_include_embeddings_propagated(self, tmp_path):
        (tmp_path / "a.py").write_text("def foo(): pass\n")
        svc, corpus, _ = self._make_service()
        svc.sync(str(tmp_path))
        # manually add embedding to first chunk
        for key, chunk in corpus._chunks.items():
            if chunk["file_path"] == "a.py":
                chunk["summary_embedding"] = [0.1, 0.2]
                break

        result_no_emb = svc.file_chunks("a.py", root=str(tmp_path), include_embeddings=False)
        for c in result_no_emb["chunks"]:
            assert "summary_embedding" not in c
            assert "has_summary_embedding" in c

        result_with_emb = svc.file_chunks("a.py", root=str(tmp_path), include_embeddings=True)
        # at least one chunk has the raw vector
        assert any(c.get("summary_embedding") is not None for c in result_with_emb["chunks"])
