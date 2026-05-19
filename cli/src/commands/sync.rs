use anyhow::Result;
use serde_json::json;

use crate::client::{build_client, post_json};
use crate::output::print_json;

/// Run the `sync` command: POST /index/sync with the given root path and print the JSON response.
///
/// # Arguments
/// * `base_url` - Base URL of the mem0 server.
/// * `path` - Root directory path to index.
/// * `generate_summaries` - Whether to generate LLM summaries for indexed chunks.
pub fn run_sync(base_url: &str, path: &str, generate_summaries: bool) -> Result<()> {
    let client = build_client()?;
    let payload = json!({ "root": path, "generate_summaries": generate_summaries });
    let resp = post_json(&client, base_url, "index/sync", &payload).map_err(|e| {
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

    #[test]
    fn test_sync_posts_to_index_sync_endpoint() {
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/index/sync")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"status":"ok","indexed":5}"#)
            .create();

        run_sync(&server.url(), "/tmp/myproject", false).unwrap();
        mock.assert();
    }

    #[test]
    fn test_sync_payload_contains_root_field() {
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/index/sync")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"status":"ok"}"#)
            .match_body(mockito::Matcher::Json(serde_json::json!({
                "root": "/tmp/myproject",
                "generate_summaries": false,
            })))
            .create();

        run_sync(&server.url(), "/tmp/myproject", false).unwrap();
        mock.assert();
    }

    #[test]
    fn test_sync_connection_error_returns_err() {
        let result = run_sync("http://127.0.0.1:19996", "/tmp/project", false);
        assert!(result.is_err());
    }

    #[test]
    fn test_sync_http_error_returns_err() {
        let mut server = Server::new();
        server
            .mock("POST", "/index/sync")
            .with_status(500)
            .with_body(r#"{"detail":"internal error"}"#)
            .create();

        let result = run_sync(&server.url(), "/tmp/project", false);
        assert!(result.is_err());
    }

    #[test]
    fn test_sync_generate_summaries_forwarded() {
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/index/sync")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"status":"ok"}"#)
            .match_body(mockito::Matcher::Json(serde_json::json!({
                "root": "/tmp/myproject",
                "generate_summaries": true,
            })))
            .create();

        run_sync(&server.url(), "/tmp/myproject", true).unwrap();
        mock.assert();
    }
}
