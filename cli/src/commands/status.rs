use anyhow::Result;

use crate::client::{build_client, get_json, post_json};
use crate::output::print_json;

/// Run the `status` command: GET /index/status and print the JSON response.
pub fn run_status(base_url: &str) -> Result<()> {
    let client = build_client()?;
    let resp = get_json(&client, base_url, "index/status").map_err(|e| {
        eprintln!("Error: {e}");
        e
    })?;
    print_json(&resp, true);
    Ok(())
}

/// Query all indexed chunks for a single file via POST /index/file.
///
/// When *root* is Some, the search is scoped to that root namespace only.
/// When *include_embeddings* is true the raw summary_embedding vectors are
/// returned per chunk; otherwise each chunk carries a boolean
/// has_summary_embedding instead, keeping the response compact.
pub fn run_file_status(
    base_url: &str,
    file_path: &str,
    root: Option<&str>,
    include_embeddings: bool,
) -> Result<()> {
    let client = build_client()?;
    let mut payload = serde_json::json!({
        "file_path": file_path,
        "include_embeddings": include_embeddings,
    });
    if let Some(r) = root {
        payload["root"] = serde_json::json!(r);
    }
    let resp = post_json(&client, base_url, "index/file", &payload).map_err(|e| {
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
    fn test_status_gets_index_status_endpoint() {
        let mut server = Server::new();
        let mock = server
            .mock("GET", "/index/status")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"files_indexed":10,"watching":false}"#)
            .create();

        run_status(&server.url()).unwrap();
        mock.assert();
    }

    #[test]
    fn test_status_connection_error_returns_err() {
        let result = run_status("http://127.0.0.1:19997");
        assert!(result.is_err());
    }

    #[test]
    fn test_status_http_error_returns_err() {
        let mut server = Server::new();
        server
            .mock("GET", "/index/status")
            .with_status(503)
            .with_body(r#"{"detail":"oops"}"#)
            .create();

        let result = run_status(&server.url());
        assert!(result.is_err());
    }

    #[test]
    fn test_file_status_posts_to_index_file() {
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/index/file")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"file_path":"src/main.rs","chunk_count":2,"chunks":[]}"#)
            .create();

        run_file_status(&server.url(), "src/main.rs", None, false).unwrap();
        mock.assert();
    }

    #[test]
    fn test_file_status_payload_contains_file_path_and_embeddings_flag() {
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/index/file")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"file_path":"src/main.rs","chunk_count":0,"chunks":[]}"#)
            .match_body(mockito::Matcher::PartialJson(serde_json::json!({
                "file_path": "src/main.rs",
                "include_embeddings": false,
            })))
            .create();

        run_file_status(&server.url(), "src/main.rs", None, false).unwrap();
        mock.assert();
    }

    #[test]
    fn test_file_status_with_root_includes_root_in_payload() {
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/index/file")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"file_path":"src/main.rs","chunk_count":0,"chunks":[]}"#)
            .match_body(mockito::Matcher::PartialJson(serde_json::json!({
                "file_path": "src/main.rs",
                "root": "/home/user/myproject",
                "include_embeddings": false,
            })))
            .create();

        run_file_status(&server.url(), "src/main.rs", Some("/home/user/myproject"), false).unwrap();
        mock.assert();
    }

    #[test]
    fn test_file_status_embeddings_flag_forwarded() {
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/index/file")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"file_path":"src/lib.rs","chunk_count":0,"chunks":[]}"#)
            .match_body(mockito::Matcher::PartialJson(serde_json::json!({
                "include_embeddings": true,
            })))
            .create();

        run_file_status(&server.url(), "src/lib.rs", None, true).unwrap();
        mock.assert();
    }

    #[test]
    fn test_file_status_connection_error_returns_err() {
        let result = run_file_status("http://127.0.0.1:19994", "src/main.rs", None, false);
        assert!(result.is_err());
    }

    #[test]
    fn test_file_status_http_error_returns_err() {
        let mut server = Server::new();
        server
            .mock("POST", "/index/file")
            .with_status(404)
            .with_body(r#"{"detail":"not found"}"#)
            .create();

        let result = run_file_status(&server.url(), "src/main.rs", None, false);
        assert!(result.is_err());
    }
}
