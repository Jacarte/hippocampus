use anyhow::{anyhow, Context, Result};
use reqwest::blocking::Client;
use serde::Serialize;
use std::time::Duration;
use crate::config::DEFAULT_TIMEOUT_SECS;

/// Build a blocking reqwest [`Client`] with the configured timeout.
pub fn build_client() -> Result<Client> {
    Client::builder()
        .timeout(Duration::from_secs(DEFAULT_TIMEOUT_SECS))
        .build()
        .context("Failed to build HTTP client")
}

/// POST JSON to `{base_url}/{path}` and return the response body as a [`String`].
///
/// Maps connection errors and non-success HTTP status codes to [`anyhow::Error`].
pub fn post_json<T: Serialize>(client: &Client, base_url: &str, path: &str, body: &T) -> Result<String> {
    let url = format!("{}/{}", base_url.trim_end_matches('/'), path);
    let resp = client
        .post(&url)
        .json(body)
        .send()
        .with_context(|| format!("Connection failed: {}", url))?;

    let status = resp.status();
    let text = resp.text().context("Failed to read response body")?;

    if !status.is_success() {
        return Err(anyhow!("Server error {}: {}", status, text));
    }
    Ok(text)
}

/// GET `{base_url}/{path}` and return the response body as a [`String`].
///
/// Maps connection errors and non-success HTTP status codes to [`anyhow::Error`].
pub fn get_json(client: &Client, base_url: &str, path: &str) -> Result<String> {
    let url = format!("{}/{}", base_url.trim_end_matches('/'), path);
    let resp = client
        .get(&url)
        .send()
        .with_context(|| format!("Connection failed: {}", url))?;

    let status = resp.status();
    let text = resp.text().context("Failed to read response body")?;

    if !status.is_success() {
        return Err(anyhow!("Server error {}: {}", status, text));
    }
    Ok(text)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_post_to_unavailable_server_returns_error() {
        let client = build_client().unwrap();
        let result = post_json(&client, "http://127.0.0.1:19999", "test", &serde_json::json!({}));
        assert!(result.is_err());
        let err = result.unwrap_err().to_string();
        assert!(err.contains("Connection failed") || err.contains("error"), "Got: {}", err);
    }

    #[test]
    fn test_get_to_unavailable_server_returns_error() {
        let client = build_client().unwrap();
        let result = get_json(&client, "http://127.0.0.1:19999", "test");
        assert!(result.is_err());
    }
}
