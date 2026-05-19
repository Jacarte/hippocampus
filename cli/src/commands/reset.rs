use anyhow::Result;
use serde_json::json;

use crate::client::{build_client, post_json};
use crate::output::print_json;

/// Run the `reset` command: optionally prompt for confirmation, then POST /index/reset.
///
/// # Arguments
/// * `base_url` - Base URL of the mem0 server.
/// * `yes` - If true, skip the confirmation prompt.
pub fn run_reset(base_url: &str, yes: bool) -> Result<()> {
    run_reset_with_input(base_url, yes, std::io::stdin().lock())
}

/// Testable variant that accepts an arbitrary `BufRead` for stdin simulation.
pub fn run_reset_with_input(base_url: &str, yes: bool, mut reader: impl std::io::BufRead) -> Result<()> {
    if !yes {
        eprintln!("This will permanently delete all indexed data. Continue? [y/N]");
        let mut line = String::new();
        reader.read_line(&mut line)?;
        let trimmed = line.trim();
        if trimmed != "y" && trimmed != "Y" {
            println!("Aborted.");
            return Ok(());
        }
    }

    let client = build_client()?;
    let payload = json!({ "confirm": true });
    let resp = post_json(&client, base_url, "index/reset", &payload).map_err(|e| {
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
    use std::io::Cursor;

    #[test]
    fn test_yes_flag_skips_prompt_and_calls_backend() {
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/index/reset")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"reset":true}"#)
            .create();

        run_reset(&server.url(), true).unwrap();
        mock.assert();
    }

    #[test]
    fn test_confirmed_via_stdin_y_calls_backend_with_confirm_true() {
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/index/reset")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"reset":true}"#)
            .match_body(mockito::Matcher::Json(serde_json::json!({ "confirm": true })))
            .create();

        let input = Cursor::new("y\n");
        run_reset_with_input(&server.url(), false, input).unwrap();
        mock.assert();
    }

    #[test]
    fn test_declined_via_stdin_n_prints_aborted_and_does_not_call_backend() {
        // No mock created — if backend is called, test will connect to nowhere and error
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/index/reset")
            .expect(0)
            .create();

        let input = Cursor::new("n\n");
        let result = run_reset_with_input(&server.url(), false, input);
        assert!(result.is_ok());
        mock.assert();
    }

    #[test]
    fn test_capital_y_also_proceeds() {
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/index/reset")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"reset":true}"#)
            .create();

        let input = Cursor::new("Y\n");
        run_reset_with_input(&server.url(), false, input).unwrap();
        mock.assert();
    }

    #[test]
    fn test_connection_error_returns_err() {
        let result = run_reset("http://127.0.0.1:19997", true);
        assert!(result.is_err());
    }

    #[test]
    fn test_http_error_returns_err() {
        let mut server = Server::new();
        server
            .mock("POST", "/index/reset")
            .with_status(500)
            .with_body(r#"{"detail":"internal error"}"#)
            .create();

        let result = run_reset(&server.url(), true);
        assert!(result.is_err());
    }
}
