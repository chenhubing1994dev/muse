//! Crash visibility for the windowless (GUI-subsystem) release build.
//!
//! Without this, any failure before the window appears (WebView2 missing, a blocked folder, a panic)
//! makes `Muse.exe` "flash and vanish" with no trace. Every launch now leaves a breadcrumb in
//! `muse-launch.log`, and every fatal error / panic is appended to `muse-crash.log` and shown in a
//! Chinese error dialog.
//!
//! Log locations (each is tried; at least one is normally writable):
//!   1. next to `Muse.exe`
//!   2. `%LOCALAPPDATA%\Muse\`
//!   3. `%TEMP%\`
//! Dialogs are suppressed when `MUSE_SMOKE_TEST=1` or `MUSE_NO_DIALOG=1` (unattended builds/tests).

use std::io::Write;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

pub const CRASH_LOG: &str = "muse-crash.log";
pub const LAUNCH_LOG: &str = "muse-launch.log";

fn exe_dir() -> Option<PathBuf> {
    std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(PathBuf::from))
}

fn log_dirs() -> Vec<PathBuf> {
    let mut dirs = Vec::new();
    if let Some(dir) = exe_dir() {
        dirs.push(dir);
    }
    if let Some(local) = std::env::var_os("LOCALAPPDATA").filter(|value| !value.is_empty()) {
        dirs.push(PathBuf::from(local).join("Muse"));
    }
    dirs.push(std::env::temp_dir());
    dirs
}

/// UTC timestamp `YYYY-MM-DD HH:MM:SS` without extra crates.
fn timestamp() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let (days, rem) = (secs.div_euclid(86_400), secs.rem_euclid(86_400));
    // civil-from-days (Howard Hinnant)
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!(
        "{year:04}-{month:02}-{day:02} {:02}:{:02}:{:02} UTC",
        rem / 3_600,
        (rem % 3_600) / 60,
        rem % 60
    )
}

/// Append one line to `file_name` in every log directory; returns the first path that worked.
fn append(file_name: &str, line: &str) -> Option<PathBuf> {
    let stamped = format!("[{}] [pid {}] {line}", timestamp(), std::process::id());
    let mut first = None;
    for dir in log_dirs() {
        let _ = std::fs::create_dir_all(&dir);
        let path = dir.join(file_name);
        if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
            if writeln!(file, "{stamped}").is_ok() && first.is_none() {
                first = Some(path);
            }
        }
    }
    first
}

/// Breadcrumb written at every launch (proves the process started and from where).
pub fn launch_breadcrumb(version: &str) {
    let exe = std::env::current_exe()
        .map(|p| p.display().to_string())
        .unwrap_or_default();
    let cwd = std::env::current_dir()
        .map(|p| p.display().to_string())
        .unwrap_or_default();
    append(LAUNCH_LOG, &format!("启动 Muse {version} · exe={exe} · cwd={cwd}"));
}

/// Record a non-fatal but important error (e.g. the engine failed to start after all retries).
pub fn log_error(context: &str, detail: &str) -> Option<PathBuf> {
    append(CRASH_LOG, &format!("{context}：{detail}"))
}

fn dialogs_enabled() -> bool {
    let off = |key: &str| std::env::var(key).map(|v| v == "1").unwrap_or(false);
    !(off("MUSE_SMOKE_TEST") || off("MUSE_NO_DIALOG"))
}

#[cfg(windows)]
pub fn show_error(title: &str, body: &str) {
    if !dialogs_enabled() {
        return;
    }
    #[link(name = "user32")]
    extern "system" {
        fn MessageBoxW(hwnd: *mut core::ffi::c_void, text: *const u16, caption: *const u16, kind: u32) -> i32;
    }
    const MB_OK: u32 = 0x0000_0000;
    const MB_ICONERROR: u32 = 0x0000_0010;
    const MB_SETFOREGROUND: u32 = 0x0001_0000;
    const MB_TOPMOST: u32 = 0x0004_0000;
    let title: Vec<u16> = title.encode_utf16().chain(std::iter::once(0)).collect();
    let body: Vec<u16> = body.encode_utf16().chain(std::iter::once(0)).collect();
    unsafe {
        MessageBoxW(
            core::ptr::null_mut(),
            body.as_ptr(),
            title.as_ptr(),
            MB_OK | MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST,
        );
    }
}

#[cfg(not(windows))]
pub fn show_error(title: &str, body: &str) {
    if dialogs_enabled() {
        eprintln!("{title}\n{body}");
    }
}

fn hints() -> &'static str {
    "常见原因：\n\
     1. 未安装 Microsoft Edge WebView2 运行时：请到 https://go.microsoft.com/fwlink/p/?LinkId=2124703 下载安装后重试。\n\
     2. 程序所在文件夹被杀毒/监控软件拦截：把 Muse 文件夹复制到 C:\\ 或桌面再运行，或把它加入杀毒软件白名单。\n\
     3. 文件不完整：请重新解压整个 zip（不要只拷贝 Muse.exe，旁边的 muse-engine.exe 和 sidecar-runtime 文件夹也需要）。"
}

/// Log + dialog + exit(1). Used for errors that make the client unusable.
pub fn fatal(stage: &str, detail: &str) -> ! {
    let log = log_error(&format!("致命错误（{stage}）"), detail);
    let where_ = log
        .map(|p| p.display().to_string())
        .unwrap_or_else(|| "（日志无法写入）".to_string());
    show_error(
        "Muse 启动失败",
        &format!("Muse 在「{stage}」阶段出错，无法继续：\n\n{detail}\n\n{}\n\n详细日志：{where_}", hints()),
    );
    std::process::exit(1);
}

/// Panics anywhere are logged; a panic on the main thread also shows the dialog.
pub fn install_panic_hook() {
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        let thread = std::thread::current();
        let name = thread.name().unwrap_or("unnamed").to_string();
        let message = info.to_string();
        let log = log_error(&format!("程序崩溃（线程 {name}）"), &message);
        if name == "main" {
            let where_ = log
                .map(|p| p.display().to_string())
                .unwrap_or_else(|| "（日志无法写入）".to_string());
            show_error(
                "Muse 意外崩溃",
                &format!("Muse 遇到未处理的错误：\n\n{message}\n\n{}\n\n详细日志：{where_}", hints()),
            );
        }
        previous(info);
    }));
}
