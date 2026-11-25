//! `muse-engine` sidecar lifecycle: silent launch, readiness handshake, supervision,
//! graceful WAL-checkpointing shutdown and zero-orphan guarantees.
//!
//! * Windows: the engine is started with `CREATE_NO_WINDOW` (no console flash) and the whole
//!   process tree lives inside a Win32 Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
//!   The shell assigns *itself* to the job at startup, so every descendant (including the
//!   PyInstaller bootloader's Python child) inherits membership with no race; when the shell
//!   exits or crashes the kernel closes the job handle and terminates everything left.
//! * Unix: the engine leads its own process group (signalled as a group on shutdown); on Linux
//!   it also receives `SIGTERM` through `PR_SET_PDEATHSIG` if the shell dies.
//! * The engine itself watches `--parent-pid` and exits when the shell disappears.
//! * Graceful path: `POST /__muse/shutdown` → engine runs `PRAGMA wal_checkpoint(TRUNCATE)` on
//!   every SQLite database in the user data root and exits; force-kill only after a timeout.

use std::fs::OpenOptions;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Manager, Url};

const READY_TIMEOUT: Duration = Duration::from_secs(150);
const MAX_FAILURES: u32 = 5;
const PORTABLE_MARKER: &str = "MUSE_PORTABLE";

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[cfg(windows)]
const SPLASH_URL: &str = "http://tauri.localhost/index.html";
#[cfg(not(windows))]
const SPLASH_URL: &str = "tauri://localhost/index.html";

pub struct Engine {
    child: Child,
    pub port: u16,
    pub token: String,
    nonce: String,
}

impl Engine {
    pub fn base_url(&self) -> String {
        format!("http://127.0.0.1:{}/", self.port)
    }
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BootStatus {
    pub phase: String,
    pub message: String,
    pub elapsed_ms: u64,
}

pub struct EngineState {
    pub inner: Mutex<Option<Engine>>,
    boot: Mutex<(String, String)>,
    started: Instant,
    data_dir: Mutex<Option<PathBuf>>,
    shutting_down: AtomicBool,
    restart_requested: AtomicBool,
    supervising: AtomicBool,
}

impl Default for EngineState {
    fn default() -> Self {
        Self {
            inner: Mutex::new(None),
            boot: Mutex::new(("starting".to_string(), "正在点亮星枢…".to_string())),
            started: Instant::now(),
            data_dir: Mutex::new(None),
            shutting_down: AtomicBool::new(false),
            restart_requested: AtomicBool::new(false),
            supervising: AtomicBool::new(false),
        }
    }
}

impl EngineState {
    pub fn boot_status(&self) -> BootStatus {
        let (phase, message) = match self.boot.lock() {
            Ok(guard) => (*guard).clone(),
            Err(_) => ("error".to_string(), "引擎状态不可用".to_string()),
        };
        BootStatus {
            phase,
            message,
            elapsed_ms: self.started.elapsed().as_millis() as u64,
        }
    }

    fn set_phase(&self, phase: &str, message: impl Into<String>) {
        if let Ok(mut guard) = self.boot.lock() {
            *guard = (phase.to_string(), message.into());
        }
    }

    pub fn data_dir_display(&self) -> String {
        match self.data_dir.lock() {
            Ok(guard) => guard
                .as_ref()
                .map(|path| path.display().to_string())
                .unwrap_or_default(),
            Err(_) => String::new(),
        }
    }

    pub fn connection(&self) -> Option<(u16, String)> {
        match self.inner.lock() {
            Ok(guard) => guard.as_ref().map(|engine| (engine.port, engine.token.clone())),
            Err(_) => None,
        }
    }

    fn take_engine(&self) -> Option<Engine> {
        match self.inner.lock() {
            Ok(mut guard) => guard.take(),
            Err(_) => None,
        }
    }

    fn engine_exited(&self) -> bool {
        match self.inner.lock() {
            Ok(mut guard) => match guard.as_mut() {
                Some(engine) => matches!(engine.child.try_wait(), Ok(Some(_))),
                None => false,
            },
            Err(_) => false,
        }
    }
}

// ------------------------------------------------------------------------------------------
// Paths
// ------------------------------------------------------------------------------------------

fn exe_dir() -> Option<PathBuf> {
    std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(Path::to_path_buf))
}

/// Per-user runtime data root; never the developer checkout.
pub fn resolve_data_dir() -> PathBuf {
    if let Some(dir) = std::env::var_os("MUSE_DATA_DIR").filter(|value| !value.is_empty()) {
        return PathBuf::from(dir);
    }
    if let Some(dir) = exe_dir() {
        if dir.join(PORTABLE_MARKER).is_file() {
            return dir.join("muse-data");
        }
    }
    platform_data_dir()
}

#[cfg(windows)]
fn platform_data_dir() -> PathBuf {
    std::env::var_os("LOCALAPPDATA")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var_os("USERPROFILE").map(|home| PathBuf::from(home).join("AppData").join("Local"))
        })
        .unwrap_or_else(|| PathBuf::from("."))
        .join("Muse")
}

#[cfg(target_os = "macos")]
fn platform_data_dir() -> PathBuf {
    home_dir()
        .join("Library")
        .join("Application Support")
        .join("com.muse.consciousness")
}

#[cfg(all(unix, not(target_os = "macos")))]
fn platform_data_dir() -> PathBuf {
    std::env::var_os("XDG_DATA_HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| home_dir().join(".local").join("share"))
        .join("muse")
}

#[cfg(unix)]
fn home_dir() -> PathBuf {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

fn engine_binary() -> Result<PathBuf, String> {
    if let Some(path) = std::env::var_os("MUSE_ENGINE_PATH") {
        let path = PathBuf::from(path);
        if path.is_file() {
            return Ok(path);
        }
    }
    let dir = exe_dir().ok_or_else(|| "无法定位客户端安装目录".to_string())?;
    let ext = std::env::consts::EXE_SUFFIX;
    let triple = env!("MUSE_TARGET_TRIPLE");
    let candidates = [
        dir.join(format!("muse-engine{ext}")),
        dir.join(format!("muse-engine-{triple}{ext}")),
        dir.join("binaries").join(format!("muse-engine-{triple}{ext}")),
    ];
    candidates
        .into_iter()
        .find(|path| path.is_file())
        .ok_or_else(|| format!("找不到内置引擎 muse-engine{ext}（客户端目录：{}）", dir.display()))
}

// ------------------------------------------------------------------------------------------
// Randomness (OS CSPRNG, no extra crates)
// ------------------------------------------------------------------------------------------

fn random_hex(bytes: usize) -> Result<String, String> {
    let mut buffer = vec![0u8; bytes];
    fill_random(&mut buffer)?;
    Ok(buffer.iter().map(|byte| format!("{byte:02x}")).collect())
}

#[cfg(windows)]
fn fill_random(buffer: &mut [u8]) -> Result<(), String> {
    use windows_sys::Win32::Security::Cryptography::{BCryptGenRandom, BCRYPT_USE_SYSTEM_PREFERRED_RNG};
    let status = unsafe {
        BCryptGenRandom(
            std::mem::zeroed(),
            buffer.as_mut_ptr(),
            buffer.len() as u32,
            BCRYPT_USE_SYSTEM_PREFERRED_RNG,
        )
    };
    if status == 0 {
        Ok(())
    } else {
        Err(format!("系统安全随机数不可用（NTSTATUS {status}）"))
    }
}

#[cfg(unix)]
fn fill_random(buffer: &mut [u8]) -> Result<(), String> {
    let mut source = std::fs::File::open("/dev/urandom").map_err(|e| e.to_string())?;
    source.read_exact(buffer).map_err(|e| e.to_string())
}

// ------------------------------------------------------------------------------------------
// Windows Job Object
// ------------------------------------------------------------------------------------------

#[cfg(windows)]
mod job {
    use std::sync::OnceLock;
    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    use windows_sys::Win32::System::Threading::GetCurrentProcess;

    static JOB: OnceLock<usize> = OnceLock::new();

    fn handle() -> Option<HANDLE> {
        let raw = *JOB.get_or_init(|| unsafe {
            let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if job as usize == 0 {
                return 0;
            }
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let ok = SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                &info as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION as *const core::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            );
            if ok == 0 {
                CloseHandle(job);
                return 0;
            }
            job as usize
        });
        if raw == 0 {
            None
        } else {
            Some(raw as HANDLE)
        }
    }

    /// Put the shell itself into the kill-on-close job: all descendants inherit it.
    pub fn guard_current_process() {
        if let Some(job) = handle() {
            unsafe {
                AssignProcessToJobObject(job, GetCurrentProcess());
            }
        }
    }

    /// Belt and braces: also assign the engine explicitly (no-op if already inherited).
    pub fn assign(child: &std::process::Child) {
        use std::os::windows::io::AsRawHandle;
        if let Some(job) = handle() {
            unsafe {
                AssignProcessToJobObject(job, child.as_raw_handle() as HANDLE);
            }
        }
    }
}

pub fn install_process_guard() {
    #[cfg(windows)]
    job::guard_current_process();
}

// ------------------------------------------------------------------------------------------
// Engine start / stop
// ------------------------------------------------------------------------------------------

fn append_log(path: &Path, line: &str) {
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "{line}");
    }
}

fn parse_ready(line: &str) -> Option<u16> {
    let value: serde_json::Value = serde_json::from_str(line.trim()).ok()?;
    if value.get("event")?.as_str()? != "ready" {
        return None;
    }
    value
        .get("port")?
        .as_u64()
        .and_then(|port| u16::try_from(port).ok())
        .filter(|port| *port != 0)
}

fn start_engine(data_dir: &Path) -> Result<Engine, String> {
    std::fs::create_dir_all(data_dir.join("logs"))
        .map_err(|e| format!("无法创建用户数据目录 {}：{e}", data_dir.display()))?;
    let binary = engine_binary()?;
    let token = random_hex(32)?;
    let nonce = random_hex(24)?;
    let log_path = data_dir.join("logs").join("muse-engine.log");

    let mut command = Command::new(&binary);
    command
        .arg("--serve")
        .arg("--port")
        .arg("0")
        .arg("--data-dir")
        .arg(data_dir)
        .arg("--parent-pid")
        .arg(std::process::id().to_string())
        .env("MUSE_SESSION_TOKEN", &token)
        .env("MUSE_LAUNCH_NONCE", &nonce)
        .env_remove("PYTHONPATH")
        .env_remove("PYTHONHOME")
        .env_remove("MUSE_UI_DIST")
        .current_dir(data_dir)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::process::CommandExt;
        unsafe {
            command.pre_exec(|| {
                libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM);
                Ok(())
            });
        }
    }

    let mut child = command
        .spawn()
        .map_err(|e| format!("无法启动内置引擎 {}：{e}", binary.display()))?;
    #[cfg(windows)]
    job::assign(&child);

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "无法读取引擎输出".to_string())?;
    let stderr = child.stderr.take();
    let (sender, receiver) = mpsc::channel::<u16>();
    let out_log = log_path.clone();
    thread::spawn(move || {
        let mut sender = Some(sender);
        for line in BufReader::new(stdout).lines() {
            let Ok(line) = line else { break };
            if sender.is_some() {
                if let Some(port) = parse_ready(&line) {
                    if let Some(tx) = sender.take() {
                        let _ = tx.send(port);
                    }
                    continue;
                }
            }
            append_log(&out_log, &line);
        }
    });
    if let Some(stderr) = stderr {
        let err_log = log_path.clone();
        thread::spawn(move || {
            for line in BufReader::new(stderr).lines() {
                let Ok(line) = line else { break };
                append_log(&err_log, &line);
            }
        });
    }

    match receiver.recv_timeout(READY_TIMEOUT) {
        Ok(port) => Ok(Engine {
            child,
            port,
            token,
            nonce,
        }),
        Err(_) => {
            terminate(&mut child);
            Err(format!(
                "内置引擎未在 {} 秒内就绪，请查看日志：{}",
                READY_TIMEOUT.as_secs(),
                log_path.display()
            ))
        }
    }
}

fn terminate(child: &mut Child) {
    #[cfg(unix)]
    {
        let group = -(child.id() as i32);
        unsafe {
            libc::kill(group, libc::SIGTERM);
        }
        let deadline = Instant::now() + Duration::from_secs(2);
        while Instant::now() < deadline {
            if matches!(child.try_wait(), Ok(Some(_))) {
                break;
            }
            thread::sleep(Duration::from_millis(50));
        }
        unsafe {
            libc::kill(group, libc::SIGKILL);
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let pid = child.id().to_string();
        let _ = Command::new("taskkill")
            .args(["/PID", pid.as_str(), "/T", "/F"])
            .creation_flags(CREATE_NO_WINDOW)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
    let _ = child.kill();
    let _ = child.wait();
}

fn graceful_stop(engine: &mut Engine) {
    let _ = http_request(
        engine.port,
        "POST",
        "/__muse/shutdown",
        &engine.token,
        Some("{}"),
        Duration::from_secs(5),
    );
    let deadline = Instant::now() + Duration::from_secs(6);
    while Instant::now() < deadline {
        match engine.child.try_wait() {
            Ok(Some(_)) => return,
            Ok(None) => thread::sleep(Duration::from_millis(100)),
            Err(_) => break,
        }
    }
    terminate(&mut engine.child);
}

/// Minimal loopback HTTP/1.1 client (no extra crates, never leaves 127.0.0.1).
pub fn http_request(
    port: u16,
    method: &str,
    path: &str,
    token: &str,
    body: Option<&str>,
    timeout: Duration,
) -> Result<(u16, String), String> {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream = TcpStream::connect_timeout(&address, timeout).map_err(|e| e.to_string())?;
    let _ = stream.set_read_timeout(Some(timeout));
    let _ = stream.set_write_timeout(Some(timeout));
    let payload = body.unwrap_or("");
    let request = format!(
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {token}\r\nAccept: application/json\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{payload}",
        payload.len()
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|e| e.to_string())?;
    let mut raw = Vec::new();
    stream.read_to_end(&mut raw).map_err(|e| e.to_string())?;
    let text = String::from_utf8_lossy(&raw).into_owned();
    let status = text
        .split_whitespace()
        .nth(1)
        .and_then(|code| code.parse::<u16>().ok())
        .ok_or_else(|| "引擎响应无效".to_string())?;
    let response_body = text
        .split_once("\r\n\r\n")
        .map(|(_, rest)| rest.to_string())
        .unwrap_or_default();
    Ok((status, response_body))
}

/// POST a JSON body to the engine on behalf of the tray (pause / kill switch / …).
pub fn post_api(app: &AppHandle, path: &str, body: &str) -> Result<(u16, String), String> {
    let (port, token) = app
        .state::<EngineState>()
        .connection()
        .ok_or_else(|| "Muse 引擎尚未就绪".to_string())?;
    http_request(port, "POST", path, &token, Some(body), Duration::from_secs(15))
}

// ------------------------------------------------------------------------------------------
// Navigation
// ------------------------------------------------------------------------------------------

fn navigate_url(app: &AppHandle, target: &str) {
    if let (Some(window), Ok(url)) = (app.get_webview_window("main"), Url::parse(target)) {
        let _ = window.navigate(url);
    }
}

fn navigate_bootstrap(app: &AppHandle, port: u16, nonce: &str) {
    navigate_url(
        app,
        &format!("http://127.0.0.1:{port}/__muse/bootstrap?nonce={nonce}"),
    );
}

/// Show the main window and open a same-origin console page (e.g. `review-desk.html`).
pub fn navigate_main(app: &AppHandle, page: &str) -> bool {
    let Some((port, _)) = app.state::<EngineState>().connection() else {
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.show();
            let _ = window.set_focus();
        }
        return false;
    };
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
    navigate_url(
        app,
        &format!("http://127.0.0.1:{port}/{}", page.trim_start_matches('/')),
    );
    true
}

/// Absolute engine URL for secondary windows (quick note).
pub fn engine_url(app: &AppHandle, page: &str) -> Option<Url> {
    let (port, _) = app.state::<EngineState>().connection()?;
    Url::parse(&format!("http://127.0.0.1:{port}/{}", page.trim_start_matches('/'))).ok()
}

// ------------------------------------------------------------------------------------------
// Supervisor
// ------------------------------------------------------------------------------------------

fn smoke_mode() -> bool {
    std::env::var("MUSE_SMOKE_TEST")
        .map(|value| value == "1")
        .unwrap_or(false)
}

fn write_smoke_report(data_dir: &Path, report: &serde_json::Value) {
    let path = std::env::var_os("MUSE_SMOKE_RESULT")
        .map(PathBuf::from)
        .unwrap_or_else(|| data_dir.join("smoke-result.json"));
    let _ = std::fs::write(&path, serde_json::to_vec_pretty(report).unwrap_or_default());
}

fn run_smoke(app: &AppHandle, port: u16, token: &str, data_dir: &Path) {
    let state = app.state::<EngineState>();
    let boot_ms = state.started.elapsed().as_millis() as u64;
    let health = http_request(port, "GET", "/api/v1/health", token, None, Duration::from_secs(20));
    let consciousness = http_request(
        port,
        "GET",
        "/api/v1/omega/consciousness",
        token,
        None,
        Duration::from_secs(30),
    );
    let health_status = health.as_ref().map(|r| r.0).unwrap_or(0);
    let consciousness_status = consciousness.as_ref().map(|r| r.0).unwrap_or(0);
    let ok = health_status == 200 && consciousness_status == 200;
    let report = serde_json::json!({
        "ok": ok,
        "engine_port": port,
        "health_status": health_status,
        "consciousness_status": consciousness_status,
        "health_error": health.err(),
        "consciousness_error": consciousness.err(),
        "boot_ms": boot_ms,
        "data_dir": data_dir.display().to_string(),
        "hardware": crate::hardware::probe(),
    });
    write_smoke_report(data_dir, &report);
    shutdown(app);
    app.exit(if ok { 0 } else { 3 });
}

pub fn boot(app: AppHandle, data_dir: PathBuf) {
    {
        let state = app.state::<EngineState>();
        if let Ok(mut guard) = state.data_dir.lock() {
            *guard = Some(data_dir.clone());
        }
        if state.supervising.swap(true, Ordering::SeqCst) {
            return;
        }
    }
    let spawned = thread::Builder::new()
        .name("muse-engine-supervisor".to_string())
        .spawn(move || {
            supervise(&app, &data_dir);
            app.state::<EngineState>()
                .supervising
                .store(false, Ordering::SeqCst);
        });
    if spawned.is_err() {
        eprintln!("Muse: 无法创建引擎守护线程");
    }
}

fn supervise(app: &AppHandle, data_dir: &Path) {
    let state = app.state::<EngineState>();
    let mut need_start = true;
    let mut failures: u32 = 0;
    loop {
        if state.shutting_down.load(Ordering::SeqCst) {
            break;
        }
        if state.restart_requested.swap(false, Ordering::SeqCst) {
            if let Some(mut old) = state.take_engine() {
                graceful_stop(&mut old);
            }
            need_start = true;
            failures = 0;
        }
        if need_start {
            state.set_phase(
                "starting",
                if failures == 0 {
                    "正在唤醒 Muse 引擎（首次启动需解压，约 3–15 秒）…".to_string()
                } else {
                    format!("引擎正在自动重启（第 {failures} 次）…")
                },
            );
            match start_engine(data_dir) {
                Ok(engine) => {
                    let (port, token, nonce) = (engine.port, engine.token.clone(), engine.nonce.clone());
                    if let Ok(mut guard) = state.inner.lock() {
                        *guard = Some(engine);
                    }
                    if state.shutting_down.load(Ordering::SeqCst) {
                        if let Some(mut late) = state.take_engine() {
                            graceful_stop(&mut late);
                        }
                        break;
                    }
                    need_start = false;
                    state.set_phase("ready", "引擎就绪，正在进入星枢主控台…");
                    if smoke_mode() {
                        run_smoke(app, port, &token, data_dir);
                        break;
                    }
                    navigate_bootstrap(app, port, &nonce);
                }
                Err(error) => {
                    failures += 1;
                    if failures >= MAX_FAILURES {
                        need_start = false;
                        state.set_phase("error", error.clone());
                        if smoke_mode() {
                            write_smoke_report(data_dir, &serde_json::json!({"ok": false, "error": error}));
                            shutdown(app);
                            app.exit(4);
                            break;
                        }
                    } else {
                        state.set_phase("starting", format!("{error}（{failures}/{MAX_FAILURES}，即将重试）"));
                        thread::sleep(Duration::from_millis(800 * u64::from(failures)));
                    }
                }
            }
        } else {
            if state.engine_exited() && !state.shutting_down.load(Ordering::SeqCst) {
                let _ = state.take_engine();
                failures += 1;
                if failures < MAX_FAILURES {
                    need_start = true;
                } else {
                    state.set_phase("error", "内置引擎多次异常退出，请点击“重新启动引擎”或查看日志");
                    navigate_url(app, SPLASH_URL);
                }
            }
            thread::sleep(Duration::from_millis(500));
        }
    }
}

/// Ask the supervisor to restart the engine (or start a new supervisor after a fatal error).
pub fn restart(app: &AppHandle) -> Result<(), String> {
    let state = app.state::<EngineState>();
    if state.shutting_down.load(Ordering::SeqCst) {
        return Err("客户端正在退出".to_string());
    }
    state.restart_requested.store(true, Ordering::SeqCst);
    state.set_phase("starting", "正在重新启动引擎…");
    if !state.supervising.load(Ordering::SeqCst) {
        let data_dir = match state.data_dir.lock() {
            Ok(guard) => guard.clone(),
            Err(_) => None,
        }
        .unwrap_or_else(resolve_data_dir);
        boot(app.clone(), data_dir);
    }
    Ok(())
}

/// Graceful, idempotent shutdown: WAL checkpoint via the engine, then force-kill on timeout.
pub fn shutdown(app: &AppHandle) {
    let state = app.state::<EngineState>();
    state.shutting_down.store(true, Ordering::SeqCst);
    if let Some(mut engine) = state.take_engine() {
        graceful_stop(&mut engine);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ready_line_is_parsed_strictly() {
        assert_eq!(parse_ready(r#"{"event":"ready","port":51234,"pid":1}"#), Some(51234));
        assert_eq!(parse_ready(r#"{"event":"ready","port":0}"#), None);
        assert_eq!(parse_ready(r#"{"event":"log","port":5}"#), None);
        assert_eq!(parse_ready("not json"), None);
    }

    #[test]
    fn random_tokens_are_long_and_distinct() {
        let a = random_hex(32).expect("csprng");
        let b = random_hex(32).expect("csprng");
        assert_eq!(a.len(), 64);
        assert_ne!(a, b);
    }
}
