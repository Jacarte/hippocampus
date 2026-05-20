use hex::encode as hex_encode;
use sha2::{Digest, Sha256};

/// Default base URL for the mem0 server.
pub const DEFAULT_BASE_URL: &str = "http://localhost:8000";

/// Default HTTP request timeout in seconds.
pub const DEFAULT_TIMEOUT_SECS: u64 = 30;

/// CLI configuration resolved from environment variables.
pub struct Config {
    /// Base URL of the mem0 server.
    pub base_url: String,
}

impl Config {
    /// Resolve config from environment variables.
    ///
    /// Reads `MEM0_SERVER_URL`; falls back to [`DEFAULT_BASE_URL`].
    pub fn from_env() -> Self {
        let base_url = std::env::var("MEM0_SERVER_URL")
            .unwrap_or_else(|_| DEFAULT_BASE_URL.to_string());
        Config { base_url }
    }
}

/// Resolve the user identity sent in every `/query` request.
///
/// Precedence (highest → lowest):
/// 1. `explicit` — the value of `--user-id` on the command line.
/// 2. `MEM0_USER_ID` environment variable.
/// 3. `OPENCODE_USER_ID` environment variable.
/// 4. Derived hash: `oc-user-<16 hex chars of SHA-256("<USER|USERNAME|anonymous>@local")>`.
///
/// This mirrors `resolveUserID()` in the TypeScript `mem0-functional` plugin
/// so that the CLI and the plugin share the same stable identity.
pub fn resolve_user_id(explicit: Option<&str>) -> String {
    if let Some(uid) = explicit {
        return uid.to_string();
    }
    if let Ok(uid) = std::env::var("MEM0_USER_ID") {
        if !uid.is_empty() {
            return uid;
        }
    }
    if let Ok(uid) = std::env::var("OPENCODE_USER_ID") {
        if !uid.is_empty() {
            return uid;
        }
    }
    let username = std::env::var("USER")
        .or_else(|_| std::env::var("USERNAME"))
        .unwrap_or_else(|_| "anonymous".to_string());
    let fallback_identity = format!("{username}@local");
    let mut hasher = Sha256::new();
    hasher.update(fallback_identity.as_bytes());
    let hex_full = hex_encode(hasher.finalize());
    format!("oc-user-{}", &hex_full[..16])
}

#[cfg(test)]
mod tests {
    use super::*;
    use serial_test::serial;

    #[test]
    #[serial]
    fn test_default_url_when_env_not_set() {
        std::env::remove_var("MEM0_SERVER_URL");
        let config = Config::from_env();
        assert_eq!(config.base_url, DEFAULT_BASE_URL);
    }

    #[test]
    #[serial]
    fn test_custom_url_from_env() {
        std::env::set_var("MEM0_SERVER_URL", "http://custom:9000");
        let config = Config::from_env();
        assert_eq!(config.base_url, "http://custom:9000");
        std::env::remove_var("MEM0_SERVER_URL");
    }

    #[test]
    #[serial]
    fn test_resolve_user_id_explicit_flag() {
        std::env::remove_var("MEM0_USER_ID");
        std::env::remove_var("OPENCODE_USER_ID");
        let result = resolve_user_id(Some("bob"));
        assert_eq!(result, "bob");
    }

    #[test]
    #[serial]
    fn test_resolve_user_id_mem0_env() {
        std::env::set_var("MEM0_USER_ID", "env-user-42");
        std::env::remove_var("OPENCODE_USER_ID");
        let result = resolve_user_id(None);
        assert_eq!(result, "env-user-42");
        std::env::remove_var("MEM0_USER_ID");
    }

    #[test]
    #[serial]
    fn test_resolve_user_id_opencode_env_fallback() {
        std::env::remove_var("MEM0_USER_ID");
        std::env::set_var("OPENCODE_USER_ID", "oc-env-user");
        let result = resolve_user_id(None);
        assert_eq!(result, "oc-env-user");
        std::env::remove_var("OPENCODE_USER_ID");
    }

    #[test]
    #[serial]
    fn test_resolve_user_id_hash_fallback_format() {
        std::env::remove_var("MEM0_USER_ID");
        std::env::remove_var("OPENCODE_USER_ID");
        std::env::set_var("USER", "testuser");
        std::env::remove_var("USERNAME");
        let result = resolve_user_id(None);
        assert!(result.starts_with("oc-user-"), "expected oc-user- prefix, got: {result}");
        assert_eq!(result.len(), "oc-user-".len() + 16, "expected 16 hex chars after prefix");
        let result2 = resolve_user_id(None);
        assert_eq!(result, result2);
        std::env::remove_var("USER");
    }

    #[test]
    #[serial]
    fn test_resolve_user_id_hash_is_deterministic() {
        std::env::remove_var("MEM0_USER_ID");
        std::env::remove_var("OPENCODE_USER_ID");
        std::env::set_var("USER", "alice");
        std::env::remove_var("USERNAME");
        let mut hasher = Sha256::new();
        hasher.update("alice@local");
        let hex_full = hex_encode(hasher.finalize());
        let expected = format!("oc-user-{}", &hex_full[..16]);
        let result = resolve_user_id(None);
        assert_eq!(result, expected);
        std::env::remove_var("USER");
    }

    #[test]
    #[serial]
    fn test_resolve_user_id_explicit_wins_over_env() {
        std::env::set_var("MEM0_USER_ID", "env-id");
        let result = resolve_user_id(Some("explicit-wins"));
        assert_eq!(result, "explicit-wins");
        std::env::remove_var("MEM0_USER_ID");
    }
}
