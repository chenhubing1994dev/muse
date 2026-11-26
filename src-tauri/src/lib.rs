//! Muse 星枢 desktop shell (Tauri v2).
//!
//! The window opens instantly on the bundled deep-space splash page (`client-splash/`), while a
//! background thread starts the frozen `muse-engine` sidecar. As soon as the engine reports
//! ready, the main window is navigated to the engine's same-origin console
//! (`http://127.0.0.1:<port>/__muse/bootstrap?nonce=…`). Closing the main window, choosing
//! "安全退出" in the tray, or a crash of this process always tears the engine down.

mod backup;
mod crash;
mod hardware;
mod sidecar;
mod tray;

use serde::Serialize;
use tauri::{RunEvent, WindowEvent};

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct ApiConnection {
    base_url: String,
    token: String,
}

#[tauri::command]
fn get_status_connection(state: tauri::State<'_, sidecar::EngineState>) -> Result<ApiConnection, String> {
    let guard = state
        .inner
        .lock()
        .map_err(|_| "Muse 引擎状态不可用".to_string())?;
    match guard.as_ref() {
        Some(engine) => Ok(ApiConnection {
            base_url: engine.base_url(),
            token: engine.token.clone(),
        }),
        None => Err("Muse 引擎正在启动或重启，客户端会自动重连".to_string()),
    }
}

#[tauri::command]
fn engine_boot_status(state: tauri::State<'_, sidecar::EngineState>) -> sidecar::BootStatus {
    state.boot_status()
}

#[tauri::command]
fn restart_engine(app: tauri::AppHandle) -> Result<(), String> {
    sidecar::restart(&app)
}

#[tauri::command]
fn get_hardware_profile() -> hardware::HardwareProfile {
    hardware::probe()
}

#[tauri::command]
fn get_data_directory(state: tauri::State<'_, sidecar::EngineState>) -> String {
    state.data_dir_display()
}

#[tauri::command]
fn is_mobile_companion() -> bool {
    false
}

#[tauri::command]
fn win_minimize(window: tauri::WebviewWindow) -> Result<(), String> {
    window.minimize().map_err(|e| e.to_string())
}

#[tauri::command]
fn win_close(window: tauri::WebviewWindow) -> Result<(), String> {
    window.close().map_err(|e| e.to_string())
}

#[tauri::command]
fn win_toggle_maximize(window: tauri::WebviewWindow) -> Result<bool, String> {
    let maximized = window.is_maximized().map_err(|e| e.to_string())?;
    if maximized {
        window.unmaximize().map_err(|e| e.to_string())?;
    } else {
        window.maximize().map_err(|e| e.to_string())?;
    }
    Ok(!maximized)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    crash::install_panic_hook();
    crash::launch_breadcrumb(env!("CARGO_PKG_VERSION"));
    sidecar::install_process_guard();

    let built = tauri::Builder::default()
        .manage(sidecar::EngineState::default())
        .setup(|app| {
            let handle = app.handle().clone();
            let data_dir = sidecar::resolve_data_dir();
            let version = handle.package_info().version.to_string();
            match backup::backup_before_upgrade(&data_dir, &version) {
                Ok(Some(dir)) => eprintln!("Muse: 升级前已备份用户数据库到 {}", dir.display()),
                Ok(None) => {}
                Err(error) => eprintln!("Muse: 升级前备份失败（不影响启动）：{error}"),
            }
            if let Err(error) = tray::install(&handle) {
                eprintln!("Muse: 系统托盘初始化失败：{error}");
            }
            sidecar::boot(handle, data_dir);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_status_connection,
            engine_boot_status,
            restart_engine,
            get_hardware_profile,
            get_data_directory,
            is_mobile_companion,
            win_minimize,
            win_close,
            win_toggle_maximize
        ])
        .build(tauri::generate_context!());
    let app = match built {
        Ok(app) => app,
        // Typical: WebView2 runtime missing/broken, or the window could not be created.
        Err(error) => crash::fatal("创建主窗口（WebView2）", &error.to_string()),
    };

    app.run(|handle, event| match event {
        RunEvent::WindowEvent {
            label,
            event: WindowEvent::Destroyed,
            ..
        } => {
            if label == "main" {
                sidecar::shutdown(handle);
                handle.exit(0);
            }
        }
        RunEvent::Exit => {
            sidecar::shutdown(handle);
        }
        _ => {}
    });
}
