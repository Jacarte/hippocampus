mod config;
mod client;
mod output;
mod commands;

use clap::{Parser, Subcommand};
use config::Config;

#[derive(Parser)]
#[command(name = "m0grep", version, about, long_about = None)]
pub struct Cli {
    #[command(subcommand)]
    pub command: Commands,
}

#[derive(Subcommand)]
pub enum Commands {
    /// Query the code index
    Query {
        query: String,
        #[arg(long, short = 'c', default_values = &["all"])]
        corpus: Vec<String>,
        #[arg(long, short = 'l', default_value = "10")]
        limit: u32,
        #[arg(long, short = 'u', default_value = "")]
        url: String,
        #[arg(long)]
        path_filter: Option<String>,
        #[arg(long)]
        language_filter: Option<String>,
        #[arg(long)]
        scope_filter: Option<String>,
        #[arg(long)]
        raw: bool,
    },
    /// Sync/index a directory
    Sync {
        /// Directory to index (default: current directory)
        #[arg(default_value = ".")]
        path: String,
        /// Stable project identifier used as corpus namespace (e.g. 'my-app').
        /// When omitted, the resolved absolute path is used as the namespace.
        #[arg(long)]
        project_id: Option<String>,
        /// Generate LLM summaries for indexed chunks
        #[arg(long)]
        generate_summaries: bool,
        /// Server base URL (overrides MEM0_BASE_URL env var and config file)
        #[arg(long, short = 'u')]
        url: Option<String>,
    },
    Watch {
        /// Directory to watch (default: current directory)
        #[arg(default_value = ".")]
        path: String,
        #[arg(long, short = 'u')]
        url: Option<String>,
    },
    /// Show indexing status
    Status {
        /// Show chunks for a specific file instead of aggregate stats
        #[arg(long)]
        file: Option<String>,
        /// Root namespace to scope --file query
        #[arg(long)]
        root: Option<String>,
        /// Include raw summary_embedding vectors in --file output
        #[arg(long)]
        embeddings: bool,
        #[arg(long, short = 'u', default_value = "")]
        url: String,
    },
    /// Reset the index
    Reset {
        /// Skip confirmation prompt
        #[arg(long)]
        yes: bool,
        #[arg(long, short = 'u')]
        url: Option<String>,
    },
}

fn resolve_base_url(url_arg: &str) -> String {
    if url_arg.is_empty() {
        Config::from_env().base_url
    } else {
        url_arg.trim_end_matches('/').to_string()
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();
    match cli.command {
        Commands::Query {
            query,
            corpus,
            limit,
            url,
            path_filter,
            language_filter,
            scope_filter,
            raw,
        } => {
            let base = resolve_base_url(&url);
            commands::query::run_query(
                &base,
                &query,
                &corpus,
                limit,
                path_filter.as_deref(),
                language_filter.as_deref(),
                scope_filter.as_deref(),
                raw,
            )?;
        }
        Commands::Sync { path, url, generate_summaries, project_id } => {
            let base = url.as_deref().map(|u| resolve_base_url(u)).unwrap_or_else(|| Config::from_env().base_url);
            commands::sync::run_sync(&base, &path, generate_summaries, project_id.as_deref())?;
        }
        Commands::Watch { path, url } => {
            let base = url.as_deref().map(|u| resolve_base_url(u)).unwrap_or_else(|| Config::from_env().base_url);
            commands::watch::run_watch_start(&base, &path)?;
        }
        Commands::Status { file, root, embeddings, url } => {
            let base = resolve_base_url(&url);
            if let Some(file_path) = file {
                commands::status::run_file_status(&base, &file_path, root.as_deref(), embeddings)?;
            } else {
                commands::status::run_status(&base)?;
            }
        }
        Commands::Reset { yes, url } => {
            let base = url.as_deref().map(|u| resolve_base_url(u)).unwrap_or_else(|| Config::from_env().base_url);
            if let Err(e) = commands::reset::run_reset(&base, yes) {
                eprintln!("Error: {}", e);
                std::process::exit(1);
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use clap::CommandFactory;

    #[test]
    fn test_command_surface_has_all_subcommands() {
        let cmd = Cli::command();
        let subcommand_names: Vec<&str> = cmd.get_subcommands()
            .map(|s| s.get_name())
            .collect();
        assert!(subcommand_names.contains(&"query"));
        assert!(subcommand_names.contains(&"sync"));
        assert!(subcommand_names.contains(&"watch"));
        assert!(subcommand_names.contains(&"status"));
        assert!(subcommand_names.contains(&"reset"));
    }

    #[test]
    fn test_resolve_base_url_empty_uses_env_default() {
        std::env::remove_var("MEM0_SERVER_URL");
        let url = resolve_base_url("");
        assert_eq!(url, config::DEFAULT_BASE_URL);
    }

    #[test]
    fn test_resolve_base_url_explicit_overrides_env() {
        let url = resolve_base_url("http://myserver:9000");
        assert_eq!(url, "http://myserver:9000");
    }

    #[test]
    fn test_resolve_base_url_strips_trailing_slash() {
        let url = resolve_base_url("http://myserver:9000/");
        assert_eq!(url, "http://myserver:9000");
    }
}
