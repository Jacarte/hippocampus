use anyhow::Result;
use serde_json::{json, Value};

use crate::client::{build_client, post_json};
use crate::output::{print_json, print_no_results};

/// Run the `query` command: POST /query and print formatted or raw results.
///
/// # Arguments
/// * `base_url` - Base URL of the mem0 server.
/// * `query_text` - Search query string.
/// * `corpora` - Corpora to search (e.g. `["all"]`, `["files"]`, `["memory"]`).
/// * `limit` - Maximum number of results (1–50).
/// * `path_filter` - Optional file-path substring filter.
/// * `language_filter` - Optional programming-language filter.
/// * `scope_filter` - Optional code-scope filter.
/// * `user_id` - Optional user identifier forwarded as `user_id` in the request
///   payload.  When `Some`, the server scopes memory-corpus results to that
///   user.  When `None`, the field is omitted and the server applies its
///   default (no per-user scoping).
/// * `min_score_memory` - Minimum relevance score for memory-store hits forwarded to the server.
///   Defaults to `0.5`.
/// * `min_score_files` - Minimum relevance score for file-corpus hits forwarded to the server.
///   Defaults to `0.05`.
/// * `raw` - When `true`, print raw JSON instead of formatted hits.
pub fn run_query(
    base_url: &str,
    query_text: &str,
    corpora: &[String],
    limit: u32,
    path_filter: Option<&str>,
    language_filter: Option<&str>,
    scope_filter: Option<&str>,
    user_id: Option<&str>,
    min_score_memory: f64,
    min_score_files: f64,
    raw: bool,
) -> Result<()> {
    let client = build_client()?;

    let mut payload = json!({
        "query": query_text,
        "corpora": corpora,
        "limit": limit,
        "min_score_memory": min_score_memory,
        "min_score_files": min_score_files,
    });

    if let Some(pf) = path_filter {
        payload["path_filter"] = json!(pf);
    }
    if let Some(lf) = language_filter {
        payload["language_filter"] = json!(lf);
    }
    if let Some(sf) = scope_filter {
        payload["scope_filter"] = json!(sf);
    }
    if let Some(uid) = user_id {
        payload["user_id"] = json!(uid);
        eprintln!("[query] user: {uid}");
    }

    let resp = post_json(&client, base_url, "query", &payload)
        .map_err(|e| {
            eprintln!("Error: {e}");
            e
        })?;

    if raw {
        print_json(&resp, true);
        return Ok(());
    }

    let parsed: Value = serde_json::from_str(&resp)?;
    let hits = parsed.get("hits").and_then(|h| h.as_array());

    match hits {
        None => {
            print_no_results();
        }
        Some(hits) if hits.is_empty() => {
            print_no_results();
        }
        Some(hits) => {
            for (i, hit) in hits.iter().enumerate() {
                let hit_type = hit
                    .get("corpus")
                    .and_then(|v| v.as_str())
                    .unwrap_or("unknown");
                let score = hit.get("score").and_then(|v| v.as_f64()).unwrap_or(0.0);

                if hit_type == "file_corpus" {
                    let path = hit.get("path").and_then(|v| v.as_str()).unwrap_or("");
                    let line = hit
                        .get("line_start")
                        .map(|v| v.to_string())
                        .unwrap_or_else(|| "?".to_string());
                    println!("[{}] {}:{} score={:.3}", i + 1, path, line, score);
                } else if hit_type == "memory_store" {
                    let content = hit
                        .get("content")
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                    let first_line: String = content
                        .trim()
                        .lines()
                        .next()
                        .unwrap_or("")
                        .chars()
                        .take(120)
                        .collect();
                    println!("[{}] MEM   score={:.3}  {}", i + 1, score, first_line);
                } else {
                    println!(
                        "[{}] {}",
                        i + 1,
                        serde_json::to_string(hit).unwrap_or_default()
                    );
                }
            }
        }
    }

    Ok(())
}

#[cfg(test)]
#[cfg(test)]
mod tests {
    use super::*;
    use mockito::Server;

    fn hit_response(hits: serde_json::Value) -> String {
        serde_json::json!({ "hits": hits }).to_string()
    }

    #[test]
    fn test_query_posts_to_correct_endpoint() {
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/query")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(hit_response(serde_json::json!([])))
            .create();

        run_query(&server.url(), "hello", &["all".to_string()], 10, None, None, None, None, 0.5_f64, 0.05_f64, false)
            .unwrap();
        mock.assert();
    }

    #[test]
    fn test_query_payload_contains_corpora_and_limit() {
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/query")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(hit_response(serde_json::json!([])))
            .match_body(mockito::Matcher::Json(serde_json::json!({
                "query": "hello",
                "corpora": ["files"],
                "limit": 5,
                "min_score_memory": 0.5,
                "min_score_files": 0.05,
            })))
            .create();

        run_query(&server.url(), "hello", &["files".to_string()], 5, None, None, None, None, 0.5_f64, 0.05_f64, false)
            .unwrap();
        mock.assert();
    }

    #[test]
    fn test_query_path_filter_forwarded() {
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/query")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(hit_response(serde_json::json!([])))
            .match_body(mockito::Matcher::Json(serde_json::json!({
                "query": "hello",
                "corpora": ["all"],
                "limit": 10,
                "path_filter": "src/",
                "min_score_memory": 0.5,
                "min_score_files": 0.05,
            })))
            .create();

        run_query(
            &server.url(),
            "hello",
            &["all".to_string()],
            10,
            Some("src/"),
            None,
            None,
            None,
            0.5_f64,
            0.05_f64,
            false,
        )
        .unwrap();
        mock.assert();
    }

    #[test]
    fn test_query_no_results_prints_no_results() {
        let mut server = Server::new();
        server
            .mock("POST", "/query")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(hit_response(serde_json::json!([])))
            .create();

        let result = run_query(&server.url(), "nonexistent", &["all".to_string()], 10, None, None, None, None, 0.5_f64, 0.05_f64, false);
        assert!(result.is_ok());
    }

    #[test]
    fn test_query_connection_error_returns_err() {
        let result = run_query("http://127.0.0.1:19998", "hello", &["all".to_string()], 10, None, None, None, None, 0.5_f64, 0.05_f64, false);
        assert!(result.is_err());
    }

    #[test]
    fn test_query_http_error_returns_err() {
        let mut server = Server::new();
        server
            .mock("POST", "/query")
            .with_status(500)
            .with_body(r#"{"detail":"oops"}"#)
            .create();

        let result = run_query(&server.url(), "hello", &["all".to_string()], 10, None, None, None, None, 0.5_f64, 0.05_f64, false);
        assert!(result.is_err());
    }

    #[test]
    fn test_query_raw_flag_returns_ok() {
        let hits = serde_json::json!([{
            "corpus": "file_corpus",
            "score": 0.92,
            "path": "src/foo.py",
            "line_start": 10,
            "snippet": "def hello_world():",
        }]);
        let mut server = Server::new();
        server
            .mock("POST", "/query")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(hit_response(hits))
            .create();

        let result = run_query(&server.url(), "hello", &["all".to_string()], 10, None, None, None, None, 0.5_f64, 0.05_f64, true);
        assert!(result.is_ok());
    }

    #[test]
    fn test_query_missing_hits_key_returns_ok() {
        let mut server = Server::new();
        server
            .mock("POST", "/query")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"status":"degraded"}"#)
            .create();

        let result = run_query(&server.url(), "hello", &["all".to_string()], 10, None, None, None, None, 0.5_f64, 0.05_f64, false);
        assert!(result.is_ok());
    }

    #[test]
    fn test_query_user_id_forwarded() {
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/query")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(hit_response(serde_json::json!([])))
            .match_body(mockito::Matcher::Json(serde_json::json!({
                "query": "hello",
                "corpora": ["all"],
                "limit": 10,
                "user_id": "alice",
                "min_score_memory": 0.5,
                "min_score_files": 0.05,
            })))
            .create();

        run_query(
            &server.url(),
            "hello",
            &["all".to_string()],
            10,
            None,
            None,
            None,
            Some("alice"),
            0.5_f64,
            0.05_f64,
            false,
        )
        .unwrap();
        mock.assert();
    }

    #[test]
    fn test_query_without_user_id_omits_field() {
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/query")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(hit_response(serde_json::json!([])))
            .match_body(mockito::Matcher::Json(serde_json::json!({
                "query": "hello",
                "corpora": ["all"],
                "limit": 10,
                "min_score_memory": 0.5,
                "min_score_files": 0.05,
            })))
            .create();

        run_query(
            &server.url(),
            "hello",
            &["all".to_string()],
            10,
            None,
            None,
            None,
            None,
            0.5_f64,
            0.05_f64,
            false,
        )
        .unwrap();
        mock.assert();
    }

    #[test]
    fn test_query_min_score_forwarded() {
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/query")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(hit_response(serde_json::json!([])))
            .match_body(mockito::Matcher::Json(serde_json::json!({
                "query": "hello",
                "corpora": ["all"],
                "limit": 10,
                "min_score_memory": 0.7,
                "min_score_files": 0.05,
            })))
            .create();

        run_query(
            &server.url(),
            "hello",
            &["all".to_string()],
            10,
            None,
            None,
            None,
            None,
            0.7_f64,
            0.05_f64,
            false,
        )
        .unwrap();
        mock.assert();
    }

    #[test]
    fn test_query_default_min_score_included_in_payload() {
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/query")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(hit_response(serde_json::json!([])))
            .match_body(mockito::Matcher::Json(serde_json::json!({
                "query": "hello",
                "corpora": ["all"],
                "limit": 10,
                "min_score_memory": 0.5,
                "min_score_files": 0.05,
            })))
            .create();

        run_query(
            &server.url(),
            "hello",
            &["all".to_string()],
            10,
            None,
            None,
            None,
            None,
            0.5_f64,
            0.05_f64,
            false,
        )
        .unwrap();
        mock.assert();
    }
}
