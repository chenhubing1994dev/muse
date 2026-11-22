use serde::{Deserialize, Serialize};
use std::{
    env,
    io::{BufRead, BufReader, Read, Write},
    net::{SocketAddr, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc, Mutex,
    },
    thread::{self, JoinHandle},
    time::Duration,
};
use tauri::{Manager, RunEvent, WindowEvent};

#[path = "platform.rs"]
mod platform;

const HEALTH_INTERVAL: Duration = Duration::from_secs(2);
const HEALTH_FAILURE_LIMIT: u8 = 3;
const BOOTSTRAP_TIMEOUT: Duration = Duration::from_secs(20);
const TASK_DB_PATH: &str = "data/tasks/muse.sqlite3";
const PROGRESS_DB_PATH: &str = "data/progress/muse.sqlite3";
const CAPABILITY_DB_PATH: &str = "data/capabilities/muse.sqlite3";

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct ApiConnection {
    base_url: String,
    token: String,
}

#[derive(Deserialize)]
struct ApiBootstrap {
    address: String,
    port: u16,
    token: String,
}

struct RunningApi {
    child: Child,
    connection: ApiConnection,
}

struct ApiState(Arc<Mutex<Option<RunningApi>>>);
struct ProjectRootState(Arc<Mutex<Option<PathBuf>>>);
struct ShutdownState(Arc<AtomicBool>);
struct SupervisorThread(Mutex<Option<JoinHandle<()>>>);

fn is_muse_root(path: &Path) -> bool {
    path.join("pyproject.toml").is_file() && path.join("src/muse/status_api.py").is_file()
}

fn bundled_project_root(resource_dir: Option<&Path>) -> Option<PathBuf> {
    let resource_dir = resource_dir?;
    [resource_dir.to_path_buf(), resource_dir.join("resources")]
        .into_iter()
        .find(|candidate| is_muse_root(candidate))
}

fn find_project_root(resource_dir: Option<&Path>) -> Result<PathBuf, String> {
    if let Ok(value) = env::var("MUSE_PROJECT_ROOT") {
        let root = PathBuf::from(value);
        if is_muse_root(&root) {
            return Ok(root);
        }
        return Err("MUSE_PROJECT_ROOT does not contain the Muse Python project".into());
    }

    let mut candidates = Vec::new();
    if let Ok(current) = env::current_dir() {
        candidates.extend(current.ancestors().map(Path::to_path_buf));
    }
    if let Ok(executable) = env::current_exe() {
        if let Some(parent) = executable.parent() {
            candidates.extend(parent.ancestors().map(Path::to_path_buf));
        }
    }
    if let Some(root) = bundled_project_root(resource_dir) {
        candidates.push(root);
    }
    candidates
        .into_iter()
        .find(|candidate| is_muse_root(candidate))
        .ok_or_else(|| {
            "Muse source root not found; set MUSE_PROJECT_ROOT to the project directory".into()
        })
}
fn parse_bootstrap(line: &str) -> Result<ApiConnection, String> {
    let bootstrap: ApiBootstrap = serde_json::from_str(line.trim())
        .map_err(|_| "Muse Python API returned an invalid bootstrap response".to_string())?;
    if bootstrap.address != "127.0.0.1" || bootstrap.port == 0 || bootstrap.token.len() < 32 {
        return Err("Muse Python API returned an unsafe local connection".into());
    }
    Ok(ApiConnection {
        base_url: format!("http://{}:{}", bootstrap.address, bootstrap.port),
        token: bootstrap.token,
    })
}

fn start_status_api(root: &Path, data_root: &Path) -> Result<RunningApi, String> {
    let target = if cfg!(target_os = "windows") {
        "windows"
    } else if cfg!(target_os = "macos") {
        "macos"
    } else {
        "linux"
    };
    let python = platform::resolve_python_command(root, target, env::var_os("MUSE_PYTHON"));
    let mut command = match python {
        platform::PythonCommand::Executable(path) => Command::new(path),
        platform::PythonCommand::Launcher { program, args } => {
            let mut command = Command::new(program);
            command.args(args);
            command
        }
    };
    command.arg("-u");

    command
        .args([
            "-m",
            "muse.status_api",
            "--task-db",
            TASK_DB_PATH,
            "--progress-db",
            PROGRESS_DB_PATH,
            "--capability-db",
            CAPABILITY_DB_PATH,
            "--agent-db",
            "data/agents/muse.sqlite3",
            "--improvement-db",
            "data/improvements/muse.sqlite3",
            "--knowledge-db",
            "data/research/knowledge.sqlite3",
            "--report-dir",
            "data/research/reports",
            "--products-dir",
            "data/products",
            "--gpu-resource-db",
            "data/gpu-resources.sqlite3",
            "--port",
            "0",
        ])
        .current_dir(data_root)
        .env("PYTHONPATH", root.join("src"))
        .env("PYTHONUNBUFFERED", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());
    // A console child launched from a windowed process would otherwise pop up
    // its own console window on top of the frameless UI.
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(CREATE_NO_WINDOW);
    }

    let mut child = command.spawn().map_err(|error| {
        format!(
            "could not start the Muse Python API; install Python 3.11 or set MUSE_PYTHON: {error}"
        )
    })?;
    let stdout = match child.stdout.take() {
        Some(stdout) => stdout,
        None => {
            let _ = child.kill();
            let _ = child.wait();
            return Err("Muse API bootstrap pipe unavailable".into());
        }
    };

    // Read the one-line bootstrap with a deadline, then keep draining stdout so
    // the child never blocks on a full pipe during normal operation.
    let (sender, receiver) = mpsc::sync_channel::<Result<String, String>>(1);
    thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let mut line = String::new();
        let result = reader
            .read_line(&mut line)
            .map(|_| line)
            .map_err(|error| format!("could not read Muse API bootstrap: {error}"));
        let _ = sender.send(result);
        let mut discard = String::new();
        while reader.read_line(&mut discard).unwrap_or(0) > 0 {
            discard.clear();
        }
    });

    let line = match receiver.recv_timeout(BOOTSTRAP_TIMEOUT) {
        Ok(Ok(line)) if !line.trim().is_empty() => line,
        Ok(Ok(_)) => {
            let status = child.wait().map_err(|error| error.to_string())?;
            return Err(format!("Muse Python API exited before startup ({status})"));
        }
        Ok(Err(error)) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(error);
        }
        Err(_) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err("Muse Python API startup timed out after 20 seconds".into());
        }
    };

    let connection = match parse_bootstrap(&line) {
        Ok(connection) => connection,
        Err(error) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(error);
        }
    };

    let running = RunningApi { child, connection };
    if !probe_api(&running.connection) {
        let mut running = running;
        let _ = running.child.kill();
        let _ = running.child.wait();
        return Err("Muse Python API started but failed its authenticated health check".into());
    }
    Ok(running)
}

fn probe_api(connection: &ApiConnection) -> bool {
    let port = match connection
        .base_url
        .rsplit(':')
        .next()
        .and_then(|value| value.parse::<u16>().ok())
    {
        Some(port) => port,
        None => return false,
    };
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream = match TcpStream::connect_timeout(&address, Duration::from_secs(2)) {
        Ok(stream) => stream,
        Err(_) => return false,
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
    let request = format!(
        "GET /api/v1/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {}\r\nConnection: close\r\n\r\n",
        connection.token
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut response = Vec::with_capacity(1024);
    if stream.take(8192).read_to_end(&mut response).is_err() {
        return false;
    }
    let response = String::from_utf8_lossy(&response);
    let Some((headers, body)) = response.split_once("\r\n\r\n") else {
        return false;
    };
    if !headers
        .lines()
        .next()
        .is_some_and(|status| status.contains(" 200 "))
    {
        return false;
    }
    serde_json::from_str::<serde_json::Value>(body)
        .ok()
        .is_some_and(|payload| {
            payload.get("status").and_then(|value| value.as_str()) == Some("ok")
                && payload.get("api_version").and_then(|value| value.as_u64()) == Some(1)
        })
}

fn next_restart_delay(attempt: u32) -> Duration {
    let seconds = 1_u64.checked_shl(attempt.min(5)).unwrap_or(32).min(30);
    Duration::from_secs(seconds)
}

fn sleep_with_shutdown(shutdown: &AtomicBool, duration: Duration) -> bool {
    let steps = (duration.as_millis() / 100).max(1) as u32;
    for _ in 0..steps {
        if shutdown.load(Ordering::SeqCst) {
            return true;
        }
        thread::sleep(Duration::from_millis(100));
    }
    shutdown.load(Ordering::SeqCst)
}

fn supervise_backend(
    api_state: Arc<Mutex<Option<RunningApi>>>,
    root_state: Arc<Mutex<Option<PathBuf>>>,
    data_root: PathBuf,
    resource_dir: Option<PathBuf>,
    shutdown: Arc<AtomicBool>,
) {
    let mut unhealthy_checks = 0_u8;
    let mut restart_attempt = 0_u32;

    while !shutdown.load(Ordering::SeqCst) {
        let root = root_state.lock().ok().and_then(|guard| guard.clone());
        let root = match root {
            Some(root) => root,
            None => match find_project_root(resource_dir.as_deref()) {
                Ok(root) => {
                    if let Ok(mut guard) = root_state.lock() {
                        *guard = Some(root.clone());
                    }
                    root
                }
                Err(error) => {
                    eprintln!("Muse backend supervisor: {error}");
                    if sleep_with_shutdown(&shutdown, next_restart_delay(restart_attempt)) {
                        break;
                    }
                    restart_attempt = restart_attempt.saturating_add(1);
                    continue;
                }
            },
        };

        let connection = api_state
            .lock()
            .ok()
            .and_then(|guard| guard.as_ref().map(|running| running.connection.clone()));

        if let Some(connection) = connection {
            let exited = api_state.lock().ok().and_then(|mut guard| {
                guard
                    .as_mut()
                    .and_then(|running| match running.child.try_wait() {
                        Ok(Some(status)) => Some(format!("Python API exited ({status})")),
                        Err(error) => Some(format!("Python API process check failed: {error}")),
                        Ok(None) => None,
                    })
            });
            if let Some(reason) = exited {
                eprintln!("Muse backend supervisor: {reason}; scheduling restart");
                if let Ok(mut guard) = api_state.lock() {
                    if let Some(mut running) = guard.take() {
                        let _ = running.child.wait();
                    }
                }
                unhealthy_checks = 0;
            } else if probe_api(&connection) {
                unhealthy_checks = 0;
                restart_attempt = 0;
            } else {
                unhealthy_checks = unhealthy_checks.saturating_add(1);
                if unhealthy_checks >= HEALTH_FAILURE_LIMIT {
                    eprintln!("Muse backend supervisor: health check failed three times; restarting backend");
                    if let Ok(mut guard) = api_state.lock() {
                        if let Some(mut running) = guard.take() {
                            let _ = running.child.kill();
                            let _ = running.child.wait();
                        }
                    }
                    unhealthy_checks = 0;
                }
            }
        }

        let needs_start = api_state
            .lock()
            .map(|guard| guard.is_none())
            .unwrap_or(false);
        if needs_start {
            let delay = next_restart_delay(restart_attempt);
            if sleep_with_shutdown(&shutdown, delay) {
                break;
            }
            match start_status_api(&root, &data_root) {
                Ok(running) => {
                    eprintln!("Muse backend supervisor: Python API is healthy and connected");
                    if let Ok(mut guard) = api_state.lock() {
                        *guard = Some(running);
                    }
                    unhealthy_checks = 0;
                    restart_attempt = 0;
                }
                Err(error) => {
                    eprintln!("Muse backend supervisor: restart failed: {error}");
                    restart_attempt = restart_attempt.saturating_add(1);
                }
            }
        } else if sleep_with_shutdown(&shutdown, HEALTH_INTERVAL) {
            break;
        }
    }

    if let Ok(mut guard) = api_state.lock() {
        if let Some(mut running) = guard.take() {
            let _ = running.child.kill();
            let _ = running.child.wait();
        }
    }
}

#[tauri::command]
fn get_status_connection(state: tauri::State<'_, ApiState>) -> Result<ApiConnection, String> {
    let guard = state
        .0
        .lock()
        .map_err(|_| "Muse API state is unavailable".to_string())?;
    guard
        .as_ref()
        .map(|running| running.connection.clone())
        .ok_or_else(|| {
            "Muse Python API is restarting; the desktop client will reconnect automatically".into()
        })
}

#[tauri::command]
fn is_mobile_companion() -> bool {
    false
}

type WindowGeometry = (i32, i32, u32, u32);

#[derive(Default)]
struct MaxState(Arc<Mutex<Option<WindowGeometry>>>);

#[tauri::command]
fn win_minimize(window: tauri::Window) -> Result<(), String> {
    window.minimize().map_err(|e| e.to_string())
}

#[tauri::command]
fn win_close(window: tauri::Window) -> Result<(), String> {
    window.close().map_err(|e| e.to_string())
}

/// Maximize/restore against the real work area.
///
/// The OS maximize path leaves a hidden resize frame on undecorated windows,
/// which pushes the window ~7px past every screen edge and clips the UI. Doing
/// it manually keeps the client area exactly inside the work area.
#[tauri::command]
fn win_toggle_maximize(
    window: tauri::Window,
    state: tauri::State<'_, MaxState>,
) -> Result<bool, String> {
    let mut saved = state
        .0
        .lock()
        .map_err(|_| "max state unavailable".to_string())?;
    if let Some((x, y, w, h)) = saved.take() {
        // `set_size` sets the client size, so the snapshot stores inner size to
        // round-trip exactly; saving outer geometry grew the window on restore.
        window
            .set_size(tauri::PhysicalSize::new(w, h))
            .map_err(|e| e.to_string())?;
        window
            .set_position(tauri::PhysicalPosition::new(x, y))
            .map_err(|e| e.to_string())?;
        return Ok(false);
    }

    let monitor = window
        .current_monitor()
        .map_err(|e| e.to_string())?
        .ok_or_else(|| "no monitor".to_string())?;
    let work = monitor.work_area();
    let position = window.outer_position().map_err(|e| e.to_string())?;
    let outer = window.outer_size().map_err(|e| e.to_string())?;
    let inner = window.inner_size().map_err(|e| e.to_string())?;
    *saved = Some((position.x, position.y, inner.width, inner.height));

    // An undecorated resizable window carries a hidden frame. Maximizing to the
    // raw work area therefore puts the *client* edge ~18px past the screen.
    // Measure the real overhang and subtract it.
    let over_w =
        ((position.x + outer.width as i32) - (work.position.x + work.size.width as i32)).max(0);
    let over_h =
        ((position.y + outer.height as i32) - (work.position.y + work.size.height as i32)).max(0);
    let frame_w = over_w.max(18);
    let frame_h = over_h.max(5);

    window
        .set_position(tauri::PhysicalPosition::new(
            work.position.x + frame_w / 2,
            work.position.y + frame_h / 2,
        ))
        .map_err(|e| e.to_string())?;
    window
        .set_size(tauri::PhysicalSize::new(
            (work.size.width as i32 - frame_w).max(320) as u32,
            (work.size.height as i32 - frame_h).max(240) as u32,
        ))
        .map_err(|e| e.to_string())?;
    Ok(true)
}

pub fn run() {
    let app = tauri::Builder::default()
        .manage(ApiState(Arc::new(Mutex::new(None))))
        .manage(ProjectRootState(Arc::new(Mutex::new(None))))
        .manage(ShutdownState(Arc::new(AtomicBool::new(false))))
        .manage(SupervisorThread(Mutex::new(None)))
        .manage(MaxState::default())
        .setup(|app| {
            // Fit the window to the real work area. Work-area values are
            // physical pixels, so they must not be reused as logical sizes:
            // on a 125% display that made the window 1.25x wider than intended.
            if let Some(window) = app.get_webview_window("main") {
                if let Ok(Some(monitor)) = window.current_monitor() {
                    let scale = monitor.scale_factor().max(0.5);
                    let work = monitor.work_area();
                    let max_w = work.size.width as f64 / scale;
                    let max_h = work.size.height as f64 / scale;
                    let target_w = max_w.clamp(960.0, 1500.0);
                    let target_h = max_h.clamp(640.0, 960.0);
                    let _ = window.set_size(tauri::LogicalSize::new(target_w, target_h));
                    let _ = window.center();
                }
            }

            let root_state = app.state::<ProjectRootState>().0.clone();
            let api_state = app.state::<ApiState>().0.clone();
            let shutdown = app.state::<ShutdownState>().0.clone();
            let resource_dir = app.path().resource_dir().ok();
            let bundled_root = bundled_project_root(resource_dir.as_deref());
            let app_data_root = app
                .path()
                .app_data_dir()
                .map_err(|error| std::io::Error::other(error.to_string()))?;
            let mut data_root = app_data_root;

            match find_project_root(resource_dir.as_deref()) {
                Ok(root) => {
                    if bundled_root.as_ref() != Some(&root)
                        || env::var_os("MUSE_PROJECT_ROOT").is_some()
                    {
                        data_root = root.clone();
                    }
                    std::fs::create_dir_all(&data_root)?;
                    if let Ok(mut guard) = root_state.lock() {
                        *guard = Some(root.clone());
                    }
                    match start_status_api(&root, &data_root) {
                        Ok(running) => {
                            if let Ok(mut guard) = api_state.lock() {
                                *guard = Some(running);
                            }
                        }
                        Err(error) => {
                            eprintln!("Muse backend startup failed; supervisor will retry: {error}")
                        }
                    }
                }
                Err(error) => {
                    eprintln!("Muse backend startup failed; supervisor will retry: {error}")
                }
            }

            let supervisor_api = api_state.clone();
            let supervisor_root = root_state.clone();
            let supervisor_data_root = data_root.clone();
            let supervisor_resource_dir = resource_dir.clone();
            let supervisor_shutdown = shutdown.clone();
            let handle = thread::spawn(move || {
                supervise_backend(
                    supervisor_api,
                    supervisor_root,
                    supervisor_data_root,
                    supervisor_resource_dir,
                    supervisor_shutdown,
                );
            });
            if let Ok(mut guard) = app.state::<SupervisorThread>().0.lock() {
                *guard = Some(handle);
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_status_connection,
            is_mobile_companion,
            win_minimize,
            win_close,
            win_toggle_maximize
        ])
        .build(tauri::generate_context!())
        .expect("failed to build Muse desktop application");

    app.run(|handle, event| {
        if let RunEvent::WindowEvent {
            label,
            event: WindowEvent::CloseRequested { .. },
            ..
        } = &event
        {
            if label == "main" {
                handle
                    .state::<ShutdownState>()
                    .0
                    .store(true, Ordering::SeqCst);
            }
        }
        if matches!(event, RunEvent::Exit) {
            handle
                .state::<ShutdownState>()
                .0
                .store(true, Ordering::SeqCst);
            if let Ok(mut guard) = handle.state::<SupervisorThread>().0.lock() {
                if let Some(thread) = guard.take() {
                    let _ = thread.join();
                }
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn restart_backoff_is_bounded() {
        assert_eq!(next_restart_delay(0), Duration::from_secs(1));
        assert_eq!(next_restart_delay(1), Duration::from_secs(2));
        assert_eq!(next_restart_delay(2), Duration::from_secs(4));
        assert_eq!(next_restart_delay(3), Duration::from_secs(8));
        assert_eq!(next_restart_delay(4), Duration::from_secs(16));
        assert_eq!(next_restart_delay(5), Duration::from_secs(30));
        assert_eq!(next_restart_delay(100), Duration::from_secs(30));
    }

    #[test]
    fn desktop_uses_distinct_task_progress_and_capability_databases() {
        assert_eq!(TASK_DB_PATH, "data/tasks/muse.sqlite3");
        assert_eq!(PROGRESS_DB_PATH, "data/progress/muse.sqlite3");
        assert_eq!(CAPABILITY_DB_PATH, "data/capabilities/muse.sqlite3");
        assert_ne!(TASK_DB_PATH, PROGRESS_DB_PATH);
        assert_ne!(TASK_DB_PATH, CAPABILITY_DB_PATH);
        assert_ne!(PROGRESS_DB_PATH, CAPABILITY_DB_PATH);
    }

    #[test]
    fn bootstrap_accepts_only_loopback_and_long_token() {
        let token = "a".repeat(40);
        let valid = format!(r#"{{"address":"127.0.0.1","port":43210,"token":"{token}"}}"#);
        assert!(parse_bootstrap(&valid).is_ok());
        let unsafe_address = valid.replace("127.0.0.1", "0.0.0.0");
        assert!(parse_bootstrap(&unsafe_address).is_err());
        let weak_token = valid.replace(&token, "short");
        assert!(parse_bootstrap(&weak_token).is_err());
    }

    #[test]
    fn health_probe_requires_authenticated_api_v1_success() {
        let valid = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"status\":\"ok\",\"api_version\":1}";
        let parsed =
            serde_json::from_str::<serde_json::Value>(valid.split_once("\r\n\r\n").unwrap().1)
                .unwrap();
        assert_eq!(parsed["status"], "ok");
        assert_eq!(parsed["api_version"], 1);
        assert!(!valid
            .replace("200 OK", "401 Unauthorized")
            .starts_with("HTTP/1.1 200 "));
    }
    #[test]
    fn bundled_project_root_resolves_resource_directory() {
        let root =
            std::env::temp_dir().join(format!("muse-bundled-root-{}-direct", std::process::id()));
        std::fs::create_dir_all(root.join("src/muse")).expect("create bundled source tree");
        std::fs::write(root.join("pyproject.toml"), b"[project]\nname = \"muse\"\n")
            .expect("write bundled project marker");
        std::fs::write(root.join("src/muse/status_api.py"), b"").expect("write API marker");

        assert_eq!(bundled_project_root(Some(&root)), Some(root.clone()));
        std::fs::remove_dir_all(root).expect("remove temporary bundled source tree");
    }

    #[test]
    fn bundled_project_root_accepts_nested_resources_directory() {
        let root =
            std::env::temp_dir().join(format!("muse-bundled-root-{}-nested", std::process::id()));
        let resources = root.join("resources");
        std::fs::create_dir_all(resources.join("src/muse"))
            .expect("create nested bundled source tree");
        std::fs::write(
            resources.join("pyproject.toml"),
            b"[project]\nname = \"muse\"\n",
        )
        .expect("write bundled project marker");
        std::fs::write(resources.join("src/muse/status_api.py"), b"").expect("write API marker");

        assert_eq!(bundled_project_root(Some(&root)), Some(resources));
        std::fs::remove_dir_all(root).expect("remove temporary bundled source tree");
    }
}
