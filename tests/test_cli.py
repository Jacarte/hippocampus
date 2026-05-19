from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mgrep_cli import app  # noqa: E402

runner = CliRunner()


def _make_response(json_data: dict, status_code: int = 200) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data
    mock.text = json.dumps(json_data)
    if status_code >= 400:
        from requests.exceptions import HTTPError
        http_exc = HTTPError(response=mock)
        mock.raise_for_status.side_effect = http_exc
    else:
        mock.raise_for_status.return_value = None
    return mock


class TestHelp:
    def test_root_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "query" in result.output.lower() or "mgrep" in result.output.lower()

    def test_query_help(self):
        result = runner.invoke(app, ["query", "--help"])
        assert result.exit_code == 0

    def test_sync_help(self):
        result = runner.invoke(app, ["sync", "--help"])
        assert result.exit_code == 0

    def test_watch_help(self):
        result = runner.invoke(app, ["watch", "--help"])
        assert result.exit_code == 0

    def test_status_help(self):
        result = runner.invoke(app, ["status", "--help"])
        assert result.exit_code == 0

    def test_reset_help(self):
        result = runner.invoke(app, ["reset", "--help"])
        assert result.exit_code == 0


QUERY_HITS_RESPONSE = {
    "hits": [
        {
            "corpus": "file_corpus",
            "score": 0.92,
            "path": "src/foo.py",
            "line_start": 10,
            "snippet": "def hello_world():",
        },
        {
            "corpus": "memory_store",
            "score": 0.85,
            "content": "Remember to update the README",
        },
    ]
}


class TestQueryCommand:
    def test_happy_path_formatted_output(self):
        with patch("mgrep_cli.requests.post", return_value=_make_response(QUERY_HITS_RESPONSE)):
            result = runner.invoke(app, ["query", "hello world"])
        assert result.exit_code == 0
        assert "FILE" in result.output
        assert "src/foo.py" in result.output
        assert "MEM" in result.output

    def test_happy_path_raw_flag(self):
        with patch("mgrep_cli.requests.post", return_value=_make_response(QUERY_HITS_RESPONSE)):
            result = runner.invoke(app, ["query", "--raw", "hello"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "hits" in parsed

    def test_no_results(self):
        with patch("mgrep_cli.requests.post", return_value=_make_response({"hits": []})):
            result = runner.invoke(app, ["query", "nonexistent"])
        assert result.exit_code == 0
        assert "No results found." in result.output

    def test_backend_unavailable(self):
        from requests.exceptions import ConnectionError as ReqConnError
        with patch("mgrep_cli.requests.post", side_effect=ReqConnError("refused")):
            result = runner.invoke(app, ["query", "hello"])
        assert result.exit_code != 0

    def test_backend_http_error(self):
        with patch("mgrep_cli.requests.post", return_value=_make_response({"detail": "bad"}, 500)):
            result = runner.invoke(app, ["query", "hello"])
        assert result.exit_code != 0

    def test_corpus_option_passed_in_payload(self):
        captured: list[dict] = []

        def fake_post(url: str, json: dict, timeout: int):  # noqa: A002
            captured.append(json)
            return _make_response({"hits": []})

        with patch("mgrep_cli.requests.post", side_effect=fake_post):
            result = runner.invoke(app, ["query", "--corpus", "files", "hello"])
        assert result.exit_code == 0
        assert captured[0]["corpora"] == ["files"]

    def test_limit_option(self):
        captured: list[dict] = []

        def fake_post(url: str, json: dict, timeout: int):  # noqa: A002
            captured.append(json)
            return _make_response({"hits": []})

        with patch("mgrep_cli.requests.post", side_effect=fake_post):
            result = runner.invoke(app, ["query", "--limit", "5", "hello"])
        assert result.exit_code == 0
        assert captured[0]["limit"] == 5

    def test_path_filter_forwarded(self):
        captured: list[dict] = []

        def fake_post(url: str, json: dict, timeout: int):  # noqa: A002
            captured.append(json)
            return _make_response({"hits": []})

        with patch("mgrep_cli.requests.post", side_effect=fake_post):
            result = runner.invoke(app, ["query", "--path-filter", "src/", "hello"])
        assert result.exit_code == 0
        assert captured[0]["path_filter"] == "src/"

    def test_custom_url_used(self):
        captured_urls: list[str] = []

        def fake_post(url: str, json: dict, timeout: int):  # noqa: A002
            captured_urls.append(url)
            return _make_response({"hits": []})

        with patch("mgrep_cli.requests.post", side_effect=fake_post):
            result = runner.invoke(app, ["query", "--url", "http://myserver:9000", "hello"])
        assert result.exit_code == 0
        assert captured_urls[0].startswith("http://myserver:9000")

    def test_degraded_backend_missing_hits_key(self):
        # 200 OK but no 'hits' key — CLI must not crash, falls back to "No results found."
        with patch("mgrep_cli.requests.post", return_value=_make_response({"status": "degraded"})):
            result = runner.invoke(app, ["query", "hello"])
        assert result.exit_code == 0
        assert "No results found." in result.output

    def test_unknown_hit_type_rendered(self):
        payload = {"hits": [{"type": "exotic", "score": 0.5, "data": "x"}]}
        with patch("mgrep_cli.requests.post", return_value=_make_response(payload)):
            result = runner.invoke(app, ["query", "hello"])
        assert result.exit_code == 0
        assert "exotic" in result.output


class TestSyncCommand:
    def test_happy_path(self):
        resp = {"indexed": 42, "root": "/tmp/repo"}
        with patch("mgrep_cli.requests.post", return_value=_make_response(resp)):
            result = runner.invoke(app, ["sync", "/tmp/repo"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["indexed"] == 42

    def test_backend_unavailable(self):
        from requests.exceptions import ConnectionError as ReqConnError
        with patch("mgrep_cli.requests.post", side_effect=ReqConnError("refused")):
            result = runner.invoke(app, ["sync", "/tmp/repo"])
        assert result.exit_code != 0

    def test_payload_contains_root(self):
        captured: list[dict] = []

        def fake_post(url: str, json: dict, timeout: int):  # noqa: A002
            captured.append(json)
            return _make_response({"ok": True})

        with patch("mgrep_cli.requests.post", side_effect=fake_post):
            runner.invoke(app, ["sync", "/some/path"])
        assert captured[0]["root"] == "/some/path"


class TestWatchCommand:
    def test_start_watch(self):
        captured_urls: list[str] = []

        def fake_post(url: str, json: dict, timeout: int):  # noqa: A002
            captured_urls.append(url)
            return _make_response({"watching": True})

        with patch("mgrep_cli.requests.post", side_effect=fake_post):
            result = runner.invoke(app, ["watch", "/tmp/repo"])
        assert result.exit_code == 0
        assert "/index/watch/start" in captured_urls[0]

    def test_stop_watch(self):
        captured_urls: list[str] = []

        def fake_post(url: str, json: dict, timeout: int):  # noqa: A002
            captured_urls.append(url)
            return _make_response({"watching": False})

        with patch("mgrep_cli.requests.post", side_effect=fake_post):
            result = runner.invoke(app, ["watch", "--stop", "/tmp/repo"])
        assert result.exit_code == 0
        assert "/index/watch/stop" in captured_urls[0]

    def test_backend_unavailable(self):
        from requests.exceptions import ConnectionError as ReqConnError
        with patch("mgrep_cli.requests.post", side_effect=ReqConnError("refused")):
            result = runner.invoke(app, ["watch", "/tmp/repo"])
        assert result.exit_code != 0


class TestStatusCommand:
    def test_happy_path(self):
        resp = {"files_indexed": 10, "watching": False}
        with patch("mgrep_cli.requests.get", return_value=_make_response(resp)):
            result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["files_indexed"] == 10

    def test_uses_get_index_status_endpoint(self):
        captured_urls: list[str] = []

        def fake_get(url: str, timeout: int):
            captured_urls.append(url)
            return _make_response({"files_indexed": 0})

        with patch("mgrep_cli.requests.get", side_effect=fake_get):
            runner.invoke(app, ["status"])
        assert captured_urls[0].endswith("/index/status")

    def test_backend_unavailable(self):
        from requests.exceptions import ConnectionError as ReqConnError
        with patch("mgrep_cli.requests.get", side_effect=ReqConnError("refused")):
            result = runner.invoke(app, ["status"])
        assert result.exit_code != 0

    def test_backend_http_error(self):
        with patch("mgrep_cli.requests.get", return_value=_make_response({"detail": "oops"}, 503)):
            result = runner.invoke(app, ["status"])
        assert result.exit_code != 0


class TestResetCommand:
    def test_with_yes_flag(self):
        resp = {"reset": True}
        with patch("mgrep_cli.requests.post", return_value=_make_response(resp)):
            result = runner.invoke(app, ["reset", "--yes"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["reset"] is True

    def test_without_confirm_flag_aborts_when_declined(self):
        # typer.confirm reads from stdin; 'n' declines and must exit 0 without calling backend
        with patch("mgrep_cli.requests.post") as mock_post:
            result = runner.invoke(app, ["reset"], input="n\n")
        assert result.exit_code == 0
        assert "Aborted" in result.output
        mock_post.assert_not_called()

    def test_without_confirm_flag_proceeds_when_confirmed(self):
        resp = {"reset": True}
        with patch("mgrep_cli.requests.post", return_value=_make_response(resp)):
            result = runner.invoke(app, ["reset"], input="y\n")
        assert result.exit_code == 0

    def test_payload_contains_confirm_true(self):
        captured: list[dict] = []

        def fake_post(url: str, json: dict, timeout: int):  # noqa: A002
            captured.append(json)
            return _make_response({"reset": True})

        with patch("mgrep_cli.requests.post", side_effect=fake_post):
            runner.invoke(app, ["reset", "--yes"])
        assert captured[0]["confirm"] is True

    def test_backend_unavailable(self):
        from requests.exceptions import ConnectionError as ReqConnError
        with patch("mgrep_cli.requests.post", side_effect=ReqConnError("refused")):
            result = runner.invoke(app, ["reset", "--yes"])
        assert result.exit_code != 0

    def test_backend_http_error(self):
        with patch("mgrep_cli.requests.post", return_value=_make_response({"detail": "err"}, 500)):
            result = runner.invoke(app, ["reset", "--yes"])
        assert result.exit_code != 0
