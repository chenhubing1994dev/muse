#[cfg(any(mobile, test))]
mod mobile;

#[cfg(not(mobile))]
#[path = "desktop.rs"]
mod desktop;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    #[cfg(mobile)]
    mobile::run();

    #[cfg(not(mobile))]
    desktop::run();
}
