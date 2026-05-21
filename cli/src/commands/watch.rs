use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};
use std::time::Duration;

use anyhow::Result;
use notify::{EventKind, RecursiveMode, Watcher};
use serde_json::json;
use walkdir::WalkDir;

use crate::client::{build_client, post_json};

// ── helpers ──────────────────────────────────────────────────────────────────

fn is_hidden_rel(rel: &str) -> bool {
    rel.contains("/.") || rel.starts_with('.')
}

/// Read a single file and POST it to `/index/ingest`.
///
/// Silently skips binary/unreadable files and hidden paths.
fn ingest_file(
    client: &reqwest::blocking::Client,
    base_url: &str,
    root: &Path,
    abs_path: &Path,
    generate_summaries: bool,
) -> Result<()> {
    let rel_path = abs_path
        .strip_prefix(root)
        .unwrap_or(abs_path)
        .to_string_lossy()
        .to_string();

    if is_hidden_rel(&rel_path) {
        return Ok(());
    }

    let content = match std::fs::read_to_string(abs_path) {
        Ok(c) => c,
        Err(_) => return Ok(()),
    };

    let root_str = root.to_string_lossy().to_string();

    let payload = json!({
        "root": root_str,
        "files": [{"file_path": rel_path, "content": content}],
        "generate_summaries": generate_summaries,
    });

    post_json(client, base_url, "index/ingest", &payload)?;
    eprintln!("[watch] Updated: {rel_path}");
    Ok(())
}

// ── initial full sync ─────────────────────────────────────────────────────────

/// Perform a one-shot full sync of all readable non-hidden files under *root*
/// by POSTing them in a single batch to `/index/ingest`.
///
/// Silently skips binary files, hidden paths, and unreadable entries.
/// Logs each file name via `eprintln!` so progress is visible in the terminal.
fn initial_sync(client: &reqwest::blocking::Client, base_url: &str, root: &Path, generate_summaries: bool) -> Result<()> {
    let mut files = Vec::new();

    for entry in WalkDir::new(root)
        .follow_links(false)
        .into_iter()
        .filter_map(|e| e.ok())
        .filter(|e| e.file_type().is_file())
    {
        let abs_path = entry.path();
        let rel_path = abs_path
            .strip_prefix(root)
            .unwrap_or(abs_path)
            .to_string_lossy()
            .to_string();
        if is_hidden_rel(&rel_path) {
            continue;
        }
        let content = match std::fs::read_to_string(abs_path) {
            Ok(c) => c,
            Err(_) => continue,
        };
        eprintln!("[watch] Indexing: {rel_path}");
        files.push(json!({"file_path": rel_path, "content": content}));
    }

    if files.is_empty() {
        eprintln!("[watch] No readable files found in {}", root.display());
        return Ok(());
    }

    let payload = json!({
        "root": root.to_string_lossy(),
        "files": files,
        "generate_summaries": generate_summaries,
    });
    post_json(client, base_url, "index/ingest", &payload)?;
    eprintln!("[watch] Initial sync complete ({} file(s))", files.len());
    Ok(())
}

// ── public API ────────────────────────────────────────────────────────────────

/// Watch *path* for file changes and send updated content to the server.
///
/// This function performs an initial full sync of the directory, then enters a
/// blocking loop that forwards every `Create` / `Modify` event to the server's
/// `/index/ingest` endpoint.  It returns when Ctrl-C is received.
///
/// # Arguments
/// * `base_url` - Base URL of the mem0 server.
/// * `path` - Root directory to watch.
pub fn run_watch_start(base_url: &str, path: &str, generate_summaries: bool) -> Result<()> {
    let root = Path::new(path)
        .canonicalize()
        .unwrap_or_else(|_| Path::new(path).to_path_buf());

    let client = build_client()?;

    // ── initial sync ──────────────────────────────────────────────────────────
    if let Err(e) = initial_sync(&client, base_url, &root, generate_summaries) {
        eprintln!("[watch] Warning: initial sync failed: {e}");
    }

    eprintln!("[watch] Watching: {} — press Ctrl-C to stop", root.display());

    // ── Ctrl-C flag ───────────────────────────────────────────────────────────
    let running = Arc::new(AtomicBool::new(true));
    {
        let r = running.clone();
        ctrlc::set_handler(move || {
            eprintln!("\n[watch] Stopping.");
            r.store(false, Ordering::SeqCst);
        })?;
    }

    // ── filesystem watcher ────────────────────────────────────────────────────
    let (tx, rx) = mpsc::channel();
    let mut watcher = notify::recommended_watcher(move |res| {
        if let Ok(event) = res {
            let _ = tx.send(event);
        }
    })?;
    watcher.watch(&root, RecursiveMode::Recursive)?;

    while running.load(Ordering::SeqCst) {
        match rx.recv_timeout(Duration::from_millis(200)) {
            Ok(event) => {
                let event: notify::Event = event;
                match event.kind {
                    EventKind::Create(_) | EventKind::Modify(_) => {
                        for abs_path in &event.paths {
                            if abs_path.is_file() {
                                if let Err(e) = ingest_file(&client, base_url, &root, abs_path, generate_summaries) {
                                    eprintln!("[watch] Error ingesting {}: {e}", abs_path.display());
                                }
                            }
                        }
                    }
                    EventKind::Remove(_) => {
                        for abs_path in &event.paths {
                            let rel = abs_path
                                .strip_prefix(&root)
                                .unwrap_or(abs_path)
                                .to_string_lossy();
                            eprintln!("[watch] Removed: {rel}");
                        }
                    }
                    _ => {}
                }
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
        }
    }

    Ok(())
}

// ── tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use mockito::Server;
    use std::io::Write;

    fn make_temp_dir_with_file(name: &str, content: &str) -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join(name);
        let mut f = std::fs::File::create(&file_path).unwrap();
        write!(f, "{content}").unwrap();
        dir
    }

    // ── is_hidden_rel ─────────────────────────────────────────────────────────

    #[test]
    fn test_is_hidden_dotfile() {
        assert!(is_hidden_rel(".hidden"));
    }

    #[test]
    fn test_is_hidden_dot_dir() {
        assert!(is_hidden_rel(".git/config"));
    }

    #[test]
    fn test_is_hidden_nested_dot_dir() {
        assert!(is_hidden_rel("src/.cache/foo"));
    }

    #[test]
    fn test_is_hidden_normal_file() {
        assert!(!is_hidden_rel("src/main.rs"));
    }

    // ── ingest_file ───────────────────────────────────────────────────────────

    #[test]
    fn test_ingest_file_posts_to_ingest_endpoint() {
        let dir = make_temp_dir_with_file("hello.txt", "hello world");
        let abs = dir.path().join("hello.txt");
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/index/ingest")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"files_indexed":1}"#)
            .create();

        let client = build_client().unwrap();
        ingest_file(&client, &server.url(), dir.path(), &abs, false).unwrap();
        mock.assert();
    }

    #[test]
    fn test_ingest_file_skips_binary() {
        let dir = tempfile::tempdir().unwrap();
        let bin_path = dir.path().join("blob.bin");
        std::fs::write(&bin_path, b"\xff\xfe\x00\x01").unwrap();
        let client = build_client().unwrap();
        // No server needed — binary files are skipped before sending
        let result = ingest_file(&client, "http://127.0.0.1:19997", dir.path(), &bin_path, false);
        assert!(result.is_ok());
    }

    #[test]
    fn test_ingest_file_skips_hidden() {
        let dir = tempfile::tempdir().unwrap();
        let hidden = dir.path().join(".hidden");
        std::fs::write(&hidden, "secret").unwrap();
        let client = build_client().unwrap();
        // No server call expected for hidden file
        let result = ingest_file(&client, "http://127.0.0.1:19997", dir.path(), &hidden, false);
        assert!(result.is_ok());
    }

    // ── initial_sync ──────────────────────────────────────────────────────────

    #[test]
    fn test_initial_sync_posts_to_ingest() {
        let dir = make_temp_dir_with_file("main.rs", "fn main() {}");
        let mut server = Server::new();
        let mock = server
            .mock("POST", "/index/ingest")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"files_indexed":1}"#)
            .create();

        let client = build_client().unwrap();
        initial_sync(&client, &server.url(), dir.path(), false).unwrap();
        mock.assert();
    }

    #[test]
    fn test_initial_sync_empty_dir_no_request() {
        let dir = tempfile::tempdir().unwrap();
        let client = build_client().unwrap();
        // No server — nothing to send
        let result = initial_sync(&client, "http://127.0.0.1:19997", dir.path(), false);
        assert!(result.is_ok());
    }

}
