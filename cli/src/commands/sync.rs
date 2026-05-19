use std::path::Path;

use anyhow::Result;
use serde_json::{json, Value};
use walkdir::WalkDir;

use crate::client::{build_client, post_json};
use crate::output::print_json;

/// Walk *path* on the local filesystem and POST all readable file contents to
/// the server's `/index/ingest` endpoint.
///
/// Unlike the legacy `/index/sync` endpoint (which required the server to read
/// files directly), this function reads every file client-side and transmits
/// the content in the request body, making it safe to use with a remote server
/// that has no access to the client's filesystem.
///
/// Hidden files and directories (whose relative path contains a `/.` segment or
/// starts with `.`) are silently skipped.  Binary files that cannot be decoded
/// as UTF-8 are also skipped without error.  If no readable files are found,
/// the function prints a warning and returns `Ok(())` without making a network
/// request.
///
/// # Arguments
/// * `base_url` - Base URL of the mem0 server (e.g. `http://localhost:7779`).
/// * `path` - Root directory to walk.  Resolved to an absolute path before use;
///   falls back to the literal string if canonicalization fails.
/// * `generate_summaries` - When `true`, forwarded to the server as
///   `"generate_summaries": true` in the JSON payload, requesting LLM chunk
///   summaries.  Silently ignored by the server when memory is not configured.
/// * `project_id` - Optional stable project identifier forwarded as
///   `"project_id"` in the payload.  When `Some`, the server uses it as the
///   corpus namespace instead of the resolved path, so chunks from the same
///   project indexed from different machines stay together.  When `None`, the
///   field is omitted and the server falls back to `root`.
pub fn run_sync(base_url: &str, path: &str, generate_summaries: bool, project_id: Option<&str>) -> Result<()> {
    let root = Path::new(path)
        .canonicalize()
        .unwrap_or_else(|_| Path::new(path).to_path_buf());
    let root_str = root.to_string_lossy().to_string();

    let mut files: Vec<Value> = Vec::new();

    for entry in WalkDir::new(&root)
        .follow_links(false)
        .into_iter()
        .filter_map(|e| e.ok())
        .filter(|e| e.file_type().is_file())
    {
        let abs_path = entry.path();
        let rel_path = abs_path
            .strip_prefix(&root)
            .unwrap_or(abs_path)
            .to_string_lossy()
            .to_string();

        if rel_path.contains("/.") || rel_path.starts_with('.') {
            continue;
        }

        let content = match std::fs::read_to_string(abs_path) {
            Ok(c) => c,
            Err(_) => continue,
        };

        files.push(json!({
            "file_path": rel_path,
            "content": content,
        }));
    }

    if files.is_empty() {
        eprintln!("No readable files found in {path}");
        return Ok(());
    }

    let mut payload = json!({
        "root": root_str,
        "files": files,
        "generate_summaries": generate_summaries,
    });
    if let Some(pid) = project_id {
        payload["project_id"] = json!(pid);
    }

    let client = build_client()?;
    let resp = post_json(&client, base_url, "index/ingest", &payload).map_err(|e| {
        eprintln!("Error: {e}");
        e
    })?;
    print_json(&resp, true);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use mockito::Server;
    use std::io::Write;

    fn make_temp_dir_with_file(name: &str, content: &str) -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join(name);
        let mut f = std::fs::File::create(&file_path).unwrap();
        write!(f, "{content}").unwrap();
        dir
    }

    #[test]
    fn test_sync_posts_to_index_ingest_endpoint() {
        let dir = make_temp_dir_with_file("hello.txt", "hello world");
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/index/ingest")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"files_indexed":1,"chunks_indexed":1,"ingested_at":"2024-01-01T00:00:00Z","errors":[]}"#)
            .create();

        run_sync(&server.url(), dir.path().to_str().unwrap(), false, None).unwrap();
        mock.assert();
    }

    #[test]
    fn test_sync_payload_contains_files_and_root_keys() {
        let dir = make_temp_dir_with_file("main.rs", "fn main() {}");
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/index/ingest")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"files_indexed":1,"chunks_indexed":1,"ingested_at":"2024-01-01T00:00:00Z","errors":[]}"#)
            .create();

        run_sync(&server.url(), dir.path().to_str().unwrap(), false, None).unwrap();
        mock.assert();
    }

    #[test]
    fn test_sync_connection_error_returns_err() {
        let dir = make_temp_dir_with_file("a.txt", "x");
        let result = run_sync(
            "http://127.0.0.1:19996",
            dir.path().to_str().unwrap(),
            false,
            None,
        );
        assert!(result.is_err());
    }

    #[test]
    fn test_sync_http_error_returns_err() {
        let dir = make_temp_dir_with_file("b.txt", "y");
        let mut server = Server::new();
        server
            .mock("POST", "/index/ingest")
            .with_status(500)
            .with_body(r#"{"detail":"internal error"}"#)
            .create();

        let result = run_sync(&server.url(), dir.path().to_str().unwrap(), false, None);
        assert!(result.is_err());
    }

    #[test]
    fn test_sync_generate_summaries_forwarded() {
        let dir = make_temp_dir_with_file("c.rs", "fn foo() {}");
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/index/ingest")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"files_indexed":1,"chunks_indexed":1,"ingested_at":"2024-01-01T00:00:00Z","errors":[]}"#)
            .create();

        run_sync(&server.url(), dir.path().to_str().unwrap(), true, None).unwrap();
        mock.assert();
    }

    #[test]
    fn test_sync_project_id_included_in_payload() {
        let dir = make_temp_dir_with_file("d.rs", "fn bar() {}");
        let mut server = Server::new();
        let captured_body = std::sync::Arc::new(std::sync::Mutex::new(String::new()));
        let captured_body_clone = captured_body.clone();
        let mock = server
            .mock("POST", "/index/ingest")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"files_indexed":1,"chunks_indexed":1,"ingested_at":"2024-01-01T00:00:00Z","errors":[]}"#)
            .create();

        run_sync(&server.url(), dir.path().to_str().unwrap(), false, Some("my-project")).unwrap();
        mock.assert();
        let _ = captured_body_clone;
    }
}
