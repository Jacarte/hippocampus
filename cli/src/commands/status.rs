use anyhow::Result;

use crate::client::{build_client, get_json};
use crate::output::print_json;

/// Run the `status` command: GET /index/status and print the JSON response.
///
/// Always prints raw pretty-printed JSON (matches Python CLI behavior).
pub fn run_status(base_url: &str) -> Result<()> {
    let client = build_client()?;
    let resp = get_json(&client, base_url, "index/status").map_err(|e| {
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
}
