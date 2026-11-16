#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

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
use tauri::{Manager, RunEvent};

const HEALTH_INTERVAL: Duration = Duration::from_secs(2);
const HEALTH_FAILURE_LIMIT: u8 = 3;
const BOOTSTRAP_TIMEOUT: Duration = Duration::from_secs(20);

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

fn find_project_root() -> Result<PathBuf, String> {
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

fn start_status_api(root: &Path) -> Result<RunningApi, String> {
    let python_override = env::var_os("MUSE_PYTHON");
    let mut command = if let Some(python) = python_override {
        let mut cmd = Command::new(python);
        cmd.arg("-u");
        cmd
    } else {
        let mut cmd = Command::new("py");
        cmd.args(["-3.11", "-u"]);
        cmd
    };

    command
        .args([
            "-m",
            "muse.status_api",
            "--task-db",
            "data/tasks/muse.sqlite3",
            "--progress-db",
            "data/progress/muse.sqlite3",
            "--capability-db",
            "data/capabilities/muse.sqlite3",
            "--agent-db",
            "data/agents/muse.sqlite3",
            "--improvement-db",
            "data/improvements/muse.sqlite3",
            "--port",
            "0",
        ])
        .current_dir(root)
        .env("PYTHONPATH", root.join("src"))
        .env("PYTHONUNBUFFERED", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());

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
    shutdown: Arc<AtomicBool>,
) {
    let mut unhealthy_checks = 0_u8;
    let mut restart_attempt = 0_u32;

    while !shutdown.load(Ordering::SeqCst) {
        let root = root_state.lock().ok().and_then(|guard| guard.clone());
        let root = match root {
            Some(root) => root,
            None => match find_project_root() {
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
            match start_status_api(&root) {
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

fn main() {
    let app = tauri::Builder::default()
        .manage(ApiState(Arc::new(Mutex::new(None))))
        .manage(ProjectRootState(Arc::new(Mutex::new(None))))
        .manage(ShutdownState(Arc::new(AtomicBool::new(false))))
        .manage(SupervisorThread(Mutex::new(None)))
        .setup(|app| {
            let root_state = app.state::<ProjectRootState>().0.clone();
            let api_state = app.state::<ApiState>().0.clone();
            let shutdown = app.state::<ShutdownState>().0.clone();

            match find_project_root() {
                Ok(root) => {
                    if let Ok(mut guard) = root_state.lock() {
                        *guard = Some(root.clone());
                    }
                    match start_status_api(&root) {
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
            let supervisor_shutdown = shutdown.clone();
            let handle = thread::spawn(move || {
                supervise_backend(supervisor_api, supervisor_root, supervisor_shutdown);
            });
            if let Ok(mut guard) = app.state::<SupervisorThread>().0.lock() {
                *guard = Some(handle);
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![get_status_connection])
        .build(tauri::generate_context!())
        .expect("failed to build Muse desktop application");

    app.run(|handle, event| {
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
}
