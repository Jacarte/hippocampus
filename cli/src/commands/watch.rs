use anyhow::Result;
use serde_json::json;

use crate::client::{build_client, post_json};
use crate::output::print_json;

/// Run the `watch start` action: POST /index/watch/start with the given root path.
///
/// # Arguments
/// * `base_url` - Base URL of the mem0 server.
/// * `path` - Root directory path to watch.
pub fn run_watch_start(base_url: &str, path: &str) -> Result<()> {
    let client = build_client()?;
    let payload = json!({ "root": path });
    let resp = post_json(&client, base_url, "index/watch/start", &payload).map_err(|e| {
        eprintln!("Error: {e}");
        e
    })?;
    print_json(&resp, true);
    Ok(())
}

/// Run the `watch stop` action: POST /index/watch/stop with the given root path.
///
/// # Arguments
/// * `base_url` - Base URL of the mem0 server.
/// * `path` - Root directory path to stop watching.
pub fn run_watch_stop(base_url: &str, path: &str) -> Result<()> {
    let client = build_client()?;
    let payload = json!({ "root": path });
    let resp = post_json(&client, base_url, "index/watch/stop", &payload).map_err(|e| {
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
    fn test_watch_start_posts_to_correct_endpoint() {
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/index/watch/start")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"status":"watching"}"#)
            .create();

        run_watch_start(&server.url(), "/tmp/myproject").unwrap();
        mock.assert();
    }

    #[test]
    fn test_watch_start_payload_contains_root_field() {
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/index/watch/start")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"status":"watching"}"#)
            .match_body(mockito::Matcher::Json(serde_json::json!({
                "root": "/tmp/myproject",
            })))
            .create();

        run_watch_start(&server.url(), "/tmp/myproject").unwrap();
        mock.assert();
    }

    #[test]
    fn test_watch_stop_posts_to_correct_endpoint() {
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/index/watch/stop")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"status":"stopped"}"#)
            .create();

        run_watch_stop(&server.url(), "/tmp/myproject").unwrap();
        mock.assert();
    }

    #[test]
    fn test_watch_stop_payload_contains_root_field() {
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/index/watch/stop")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"status":"stopped"}"#)
            .match_body(mockito::Matcher::Json(serde_json::json!({
                "root": "/tmp/myproject",
            })))
            .create();

        run_watch_stop(&server.url(), "/tmp/myproject").unwrap();
        mock.assert();
    }

    #[test]
    fn test_watch_start_connection_error_returns_err() {
        let result = run_watch_start("http://127.0.0.1:19995", "/tmp/project");
        assert!(result.is_err());
    }

    #[test]
    fn test_watch_stop_connection_error_returns_err() {
        let result = run_watch_stop("http://127.0.0.1:19995", "/tmp/project");
        assert!(result.is_err());
    }

    #[test]
    fn test_watch_start_http_error_returns_err() {
        let mut server = Server::new();
        server
            .mock("POST", "/index/watch/start")
            .with_status(500)
            .with_body(r#"{"detail":"internal error"}"#)
            .create();

        let result = run_watch_start(&server.url(), "/tmp/project");
        assert!(result.is_err());
    }

    #[test]
    fn test_watch_stop_http_error_returns_err() {
        let mut server = Server::new();
        server
            .mock("POST", "/index/watch/stop")
            .with_status(500)
            .with_body(r#"{"detail":"internal error"}"#)
            .create();

        let result = run_watch_stop(&server.url(), "/tmp/project");
        assert!(result.is_err());
    }
}
