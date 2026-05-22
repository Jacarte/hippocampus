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
/// * `min_score_memory` - Minimum relevance score (0.0–1.0) for memory-store hits forwarded to
///   the server.  Hits with a score strictly below this value are excluded.  Defaults to `0.5`.
///   Set to `0.0` to disable memory-hit filtering.
/// * `min_score_files` - Minimum relevance score (0.0–1.0) for file-corpus hits forwarded to
///   the server.  Hits with a score strictly below this value are excluded.  Defaults to `0.05`
///   (BM25 noise floor).  Set to `0.0` to disable file-hit filtering.
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
                println!("{}", render_hit_line(i, hit));
            }
        }
    }

    if let Some(notice) = build_hidden_memory_notice(&parsed) {
        eprintln!("{notice}");
    }

    Ok(())
}

fn build_hidden_memory_notice(parsed: &Value) -> Option<String> {
    let hits = parsed.get("hits")?.as_array()?;
    let available = parsed.get("available_hits_by_corpus")?.as_object()?;
    let available_memory = available.get("memory_store")?.as_u64()? as usize;

    let shown_memory = hits
        .iter()
        .filter(|hit| {
            hit.get("corpus")
                .and_then(|value| value.as_str())
                == Some("memory_store")
        })
        .count();

    if available_memory > shown_memory {
        return Some(format!(
            "Note: {available_memory} memory hit(s) matched, but only {shown_memory} are shown because the shared result limit was filled by higher-ranked hits. Try --limit <n> or --corpus memory_store."
        ));
    }

    None
}

fn render_hit_line(index: usize, hit: &Value) -> String {
    let hit_type = hit
        .get("corpus")
        .and_then(|value| value.as_str())
        .unwrap_or("unknown");
    let score = hit.get("score").and_then(|value| value.as_f64()).unwrap_or(0.0);
    let datetime = hit.get("datetime").and_then(|value| value.as_str());

    if hit_type == "file_corpus" {
        let path = hit.get("path").and_then(|value| value.as_str()).unwrap_or("");
        let line = hit
            .get("line_start")
            .map(|value| value.to_string())
            .unwrap_or_else(|| "?".to_string());
        let snippet = hit
            .get("snippet")
            .and_then(|value| value.as_str())
            .unwrap_or("");
        let label = render_label("FILE", datetime);
        return format!(
            "[{}] {} {}:{} score={:.3} {}",
            index + 1,
            label,
            path,
            line,
            score,
            first_line(snippet)
        );
    }

    if hit_type == "memory_store" {
        let content = hit
            .get("content")
            .and_then(|value| value.as_str())
            .unwrap_or("");
        let label = render_label("MEMORY", datetime);
        return format!(
            "[{}] {} score={:.3} {}",
            index + 1,
            label,
            score,
            first_line(content)
        );
    }



    format!(
        "[{}] {}",
        index + 1,
        serde_json::to_string(hit).unwrap_or_default()
    )
}

fn render_label(kind: &str, datetime: Option<&str>) -> String {
    match datetime {
        Some(value) if !value.is_empty() => format!("{kind} from {value}"),
        _ => kind.to_string(),
    }
}

fn first_line(text: &str) -> String {
    text.trim()
        .lines()
        .next()
        .unwrap_or("")
        .chars()
        .take(120)
        .collect()
}

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

    #[test]
    fn test_hidden_memory_notice_when_shared_limit_hides_memory_hits() {
        let parsed = serde_json::json!({
            "hits": [
                {"corpus": "file_corpus"},
                {"corpus": "file_corpus"}
            ],
            "available_hits_by_corpus": {
                "file_corpus": 2,
                "memory_store": 1
            }
        });

        let notice = build_hidden_memory_notice(&parsed);

        assert!(notice.is_some());
        assert!(notice.unwrap().contains("memory hit"));
    }

    #[test]
    fn test_hidden_memory_notice_absent_when_all_memory_hits_are_shown() {
        let parsed = serde_json::json!({
            "hits": [
                {"corpus": "memory_store"},
                {"corpus": "file_corpus"}
            ],
            "available_hits_by_corpus": {
                "file_corpus": 1,
                "memory_store": 1
            }
        });

        assert!(build_hidden_memory_notice(&parsed).is_none());
    }

    #[test]
    fn test_render_hit_line_includes_memory_datetime_and_summary() {
        let hit = serde_json::json!({
            "corpus": "memory_store",
            "score": 0.91,
            "datetime": "2026-05-20T10:00:00Z",
            "content": "remember this important detail\nwith more text",
        });

        let line = render_hit_line(0, &hit);

        assert_eq!(
            line,
            "[1] MEMORY from 2026-05-20T10:00:00Z score=0.910 remember this important detail"
        );
    }

    #[test]
    fn test_render_hit_line_includes_file_datetime_path_and_snippet() {
        let hit = serde_json::json!({
            "corpus": "file_corpus",
            "score": 0.75,
            "datetime": "2026-05-19T09:00:00Z",
            "path": "src/foo.py",
            "line_start": 12,
            "snippet": "def hello_world():\n    pass",
        });

        let line = render_hit_line(1, &hit);

        assert_eq!(
            line,
            "[2] FILE from 2026-05-19T09:00:00Z src/foo.py:12 score=0.750 def hello_world():"
        );
    }
}
