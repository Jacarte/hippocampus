/// Print JSON pretty-printed or raw to stdout.
///
/// If `pretty` is true, attempts to parse and re-serialize as indented JSON.
/// Falls back to printing the raw string if parsing fails.
pub fn print_json(raw: &str, pretty: bool) {
    if pretty {
        if let Ok(val) = serde_json::from_str::<serde_json::Value>(raw) {
            println!("{}", serde_json::to_string_pretty(&val).unwrap_or_else(|_| raw.to_string()));
        } else {
            println!("{}", raw);
        }
    } else {
        println!("{}", raw);
    }
}

/// Print a standard "No results found." message to stdout.
pub fn print_no_results() {
    println!("No results found.");
}
