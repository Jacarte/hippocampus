// Config module for m0grep CLI
// Reads base URL from MEM0_SERVER_URL env var with fallback to http://localhost:8000
// 30-second request timeout

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
}
