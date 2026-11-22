/// Android/iOS companion shell. It deliberately does not spawn the desktop
/// Python backend or expose the desktop loopback bearer token.
#[tauri::command]
fn get_status_connection() -> Result<serde_json::Value, String> {
    Err(
        "Muse Companion is not paired with a Muse Host. Pair with a trusted Host to continue."
            .into(),
    )
}

#[tauri::command]
fn is_mobile_companion() -> bool {
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mobile_companion_starts_unpaired_and_fails_closed() {
        let error = get_status_connection()
            .expect_err("unpaired companion must not expose a host connection");
        assert!(error.contains("not paired"));
        assert!(is_mobile_companion());
    }
}

#[cfg(mobile)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            get_status_connection,
            is_mobile_companion
        ])
        .run(tauri::generate_context!())
        .expect("failed to run Muse mobile companion");
}
