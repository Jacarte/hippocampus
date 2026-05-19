from __future__ import annotations

import json
import os
from typing import Optional

import typer
import requests

app = typer.Typer(
    name="mgrep",
    help="HTTP CLI for the mem0server backend (query, sync, watch, status, reset).",
    no_args_is_help=True,
)

DEFAULT_URL = "http://localhost:8000"


def _base_url(url: str) -> str:
    return url or os.environ.get("MEM0_SERVER_URL", DEFAULT_URL).rstrip("/")


def _post(base: str, path: str, payload: dict) -> dict:
    try:
        resp = requests.post(f"{base}{path}", json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError as exc:
        typer.echo(f"Error: could not connect to {base} — {exc}", err=True)
        raise typer.Exit(code=1)
    except requests.exceptions.HTTPError as exc:
        typer.echo(
            f"Error: HTTP {exc.response.status_code} from {base}{path} — {exc.response.text}",
            err=True,
        )
        raise typer.Exit(code=1)


def _get(base: str, path: str) -> dict:
    try:
        resp = requests.get(f"{base}{path}", timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError as exc:
        typer.echo(f"Error: could not connect to {base} — {exc}", err=True)
        raise typer.Exit(code=1)
    except requests.exceptions.HTTPError as exc:
        typer.echo(
            f"Error: HTTP {exc.response.status_code} from {base}{path} — {exc.response.text}",
            err=True,
        )
        raise typer.Exit(code=1)


@app.command()
def query(
    query_text: str = typer.Argument(..., metavar="QUERY", help="Search query string."),
    corpus: list[str] = typer.Option(
        ["all"],
        "--corpus",
        "-c",
        help="Corpus to search: all | files | memory. Repeat for multiple.",
    ),
    limit: int = typer.Option(10, "--limit", "-l", min=1, max=50, help="Max results (1-50)."),
    url: str = typer.Option("", "--url", "-u", help="Backend base URL (default: http://localhost:8000)."),
    path_filter: Optional[str] = typer.Option(None, "--path-filter", help="Filter results by file path substring."),
    language_filter: Optional[str] = typer.Option(None, "--language-filter", help="Filter by programming language."),
    scope_filter: Optional[str] = typer.Option(None, "--scope-filter", help="Filter by code scope."),
    raw: bool = typer.Option(False, "--raw", help="Print raw JSON response."),
) -> None:
    """Search code files and memory for a query string.

    Sends POST /query to the backend and prints matching results.
    """
    base = _base_url(url)
    payload: dict = {"query": query_text, "corpora": corpus, "limit": limit}
    if path_filter:
        payload["path_filter"] = path_filter
    if language_filter:
        payload["language_filter"] = language_filter
    if scope_filter:
        payload["scope_filter"] = scope_filter

    result = _post(base, "/query", payload)

    if raw:
        typer.echo(json.dumps(result, indent=2))
        return

    hits = result.get("hits", [])
    if not hits:
        typer.echo("No results found.")
        return

    for i, hit in enumerate(hits, 1):
        hit_type = hit.get("corpus", "unknown")
        score = hit.get("score", 0)
        if hit_type == "file_corpus":
            path = hit.get("path", "")
            line = hit.get("line_start", "?")
            snippet = (hit.get("snippet") or "").strip().splitlines()[0][:120] if hit.get("snippet") else ""
            typer.echo(f"[{i}] FILE  score={score:.3f}  {path}:{line}")
            if snippet:
                typer.echo(f"     {snippet}")
        elif hit_type == "memory_store":
            content = (hit.get("content") or "").strip().splitlines()[0][:120]
            typer.echo(f"[{i}] MEM   score={score:.3f}  {content}")
        else:
            typer.echo(f"[{i}] {json.dumps(hit)}")


@app.command()
def sync(
    root: str = typer.Argument(..., metavar="ROOT", help="Root directory path to index."),
    url: str = typer.Option("", "--url", "-u", help="Backend base URL (default: http://localhost:8000)."),
) -> None:
    """Index (sync) a directory into the file corpus.

    Sends POST /index/sync with the given root path.
    """
    base = _base_url(url)
    result = _post(base, "/index/sync", {"root": root})
    typer.echo(json.dumps(result, indent=2))


@app.command()
def watch(
    root: str = typer.Argument(..., metavar="ROOT", help="Root directory path to watch."),
    stop: bool = typer.Option(False, "--stop", help="Stop watching instead of starting."),
    url: str = typer.Option("", "--url", "-u", help="Backend base URL (default: http://localhost:8000)."),
) -> None:
    """Start or stop watching a directory for file changes.

    Sends POST /index/watch/start (or /stop) with the given root path.
    """
    base = _base_url(url)
    path = "/index/watch/stop" if stop else "/index/watch/start"
    result = _post(base, path, {"root": root})
    typer.echo(json.dumps(result, indent=2))


@app.command()
def status(
    url: str = typer.Option("", "--url", "-u", help="Backend base URL (default: http://localhost:8000)."),
) -> None:
    """Show the current index status.

    Sends GET /index/status and prints the response.
    """
    base = _base_url(url)
    result = _get(base, "/index/status")
    typer.echo(json.dumps(result, indent=2))


@app.command()
def reset(
    url: str = typer.Option("", "--url", "-u", help="Backend base URL (default: http://localhost:8000)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """Reset the file index (destructive — removes all indexed data).

    Sends POST /index/reset with confirm=True.
    """
    base = _base_url(url)
    if not yes:
        confirmed = typer.confirm("This will permanently delete all indexed data. Continue?")
        if not confirmed:
            typer.echo("Aborted.")
            raise typer.Exit(code=0)

    result = _post(base, "/index/reset", {"confirm": True})
    typer.echo(json.dumps(result, indent=2))


if __name__ == "__main__":
    app()
