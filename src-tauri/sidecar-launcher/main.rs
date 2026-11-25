//! `muse-engine` native launcher — Plan B of `scripts/build_sidecar.py`.
//!
//! Pure Rust standard library (no crates, no PyInstaller PKG overlay, never links `python311.dll`;
//! imports only Windows system DLLs). It picks a working Python interpreter through a three-tier
//! fallback chain and runs `muse_sidecar_main.py` with every argument forwarded verbatim
//! (`--self-test`, `--serve`, `--port`, `--data-dir`, `--parent-pid`, `--token-stdin`, ...).
//! stdin/stdout/stderr are inherited, so the Tauri readiness handshake (JSON line on stdout) is unchanged.
//!
//!   Tier 1  `<runtime>\python.exe`, cwd = runtime, PATH = runtime;runtime\DLLs;<exe dir>;<full PATH>
//!           (runtime = `%MUSE_SIDECAR_RUNTIME%` | `<exe dir>\sidecar-runtime` | `<exe dir>\resources\...`
//!           | up to three parent directories — dev layout `src-tauri\binaries`, `target\release`).
//!   Tier 2  runtime mirrored to `%LOCALAPPDATA%\Muse\sidecar-runtime` (re-synced only when
//!           `runtime-stamp.txt` changes) and started from the C: drive.
//!   Tier 3  installed system Python (`%LOCALAPPDATA%\Programs\Python\Python3*`, `py -3.11`, PATH) with
//!           `-I`, running the packaged `<runtime>\app\muse_sidecar_main.py` (or the nearest
//!           `src\muse_sidecar_main.py` in a developer checkout).
//!
//! A tier is skipped when it cannot be spawned, when it exits with a loader NTSTATUS
//! (0xC0000135 DLL not found, 0xC0000142, 0xC000007B, 0xC0000022, 0xC0000139) or when it exits
//! non-zero within 1.5 s (nothing useful ran yet). A real failure after that is returned as-is.
//! Each child runs with `CREATE_NO_WINDOW` in a kill-on-close Job Object (no orphaned python.exe), and
//! `SetErrorMode(0x8003)` guarantees a missing DLL never shows a modal dialog.
//! `MUSE_LAUNCHER_TIER=1|2|3` forces one tier; `MUSE_LAUNCHER_VERBOSE=1` logs choices to stderr.

use std::env;
use std::ffi::OsString;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, Instant};

const RUNTIME_DIR: &str = "sidecar-runtime";
const STAMP_FILE: &str = "runtime-stamp.txt";
const ENTRY: &str = "muse_sidecar_main.py";
const LOADER_FAILURES: [u32; 5] = [0xC000_0135, 0xC000_0142, 0xC000_007B, 0xC000_0022, 0xC000_0139];
const QUICK_FAIL: Duration = Duration::from_millis(1500);

fn verbose() -> bool {
    env::var_os("MUSE_LAUNCHER_VERBOSE").map_or(false, |v| !v.is_empty() && v != "0")
}

fn log(message: &str) {
    if verbose() {
        eprintln!("[muse-engine] {message}");
    }
}

fn exe_dir() -> Option<PathBuf> {
    env::current_exe().ok().and_then(|p| p.parent().map(Path::to_path_buf))
}

fn is_runtime(dir: &Path) -> bool {
    dir.join("python.exe").is_file() && dir.join("app").join(ENTRY).is_file()
}

fn find_runtime(dir: &Path) -> Option<PathBuf> {
    let mut candidates = Vec::new();
    if let Some(custom) = env::var_os("MUSE_SIDECAR_RUNTIME").filter(|v| !v.is_empty()) {
        candidates.push(PathBuf::from(custom));
    }
    candidates.push(dir.join(RUNTIME_DIR));
    candidates.push(dir.join("resources").join(RUNTIME_DIR));
    let mut current = dir.parent();
    for _ in 0..3 {
        match current {
            Some(parent) => {
                candidates.push(parent.join(RUNTIME_DIR));
                current = parent.parent();
            }
            None => break,
        }
    }
    candidates.into_iter().find(|c| is_runtime(c))
}

fn find_source_script(dir: &Path) -> Option<PathBuf> {
    let mut current = Some(dir);
    for _ in 0..6 {
        let here = current?;
        let script = here.join("src").join(ENTRY);
        if script.is_file() {
            return Some(script);
        }
        current = here.parent();
    }
    None
}

fn local_app_data() -> Option<PathBuf> {
    env::var_os("LOCALAPPDATA")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .or_else(|| env::var_os("USERPROFILE").map(|home| PathBuf::from(home).join("AppData").join("Local")))
}

fn read_stamp(dir: &Path) -> Option<String> {
    fs::read_to_string(dir.join(STAMP_FILE)).ok().map(|s| s.trim().to_string())
}

fn copy_dir(src: &Path, dst: &Path) -> io::Result<()> {
    fs::create_dir_all(dst)?;
    for entry in fs::read_dir(src)? {
        let entry = entry?;
        let from = entry.path();
        let to = dst.join(entry.file_name());
        if entry.file_type()?.is_dir() {
            copy_dir(&from, &to)?;
        } else {
            let same = match (fs::metadata(&from), fs::metadata(&to)) {
                (Ok(a), Ok(b)) => a.len() == b.len() && a.modified().ok() <= b.modified().ok(),
                _ => false,
            };
            if !same {
                fs::copy(&from, &to)?;
            }
        }
    }
    Ok(())
}

/// Tier 2: mirror the runtime to `%LOCALAPPDATA%\Muse\sidecar-runtime` (C: drive).
fn mirror_runtime(runtime: &Path) -> Option<PathBuf> {
    let target = local_app_data()?.join("Muse").join(RUNTIME_DIR);
    if same_path(runtime, &target) {
        return None;
    }
    let wanted = read_stamp(runtime);
    if is_runtime(&target) && wanted.is_some() && read_stamp(&target) == wanted {
        return Some(target);
    }
    log(&format!("mirroring runtime to {}", target.display()));
    let _ = fs::remove_file(target.join(STAMP_FILE));
    if let Err(err) = copy_dir(runtime, &target) {
        eprintln!("[muse-engine] tier 2 mirror to {} failed: {err}", target.display());
        return None;
    }
    if let Some(stamp) = wanted {
        let _ = fs::write(target.join(STAMP_FILE), stamp);
    }
    if is_runtime(&target) {
        Some(target)
    } else {
        None
    }
}

fn same_path(a: &Path, b: &Path) -> bool {
    match (fs::canonicalize(a), fs::canonicalize(b)) {
        (Ok(x), Ok(y)) => x == y,
        _ => false,
    }
}

/// Tier 3 interpreter candidates: installed CPython (never the Store alias stub, never a venv).
fn system_pythons() -> Vec<(PathBuf, Vec<OsString>)> {
    let mut out: Vec<(PathBuf, Vec<OsString>)> = Vec::new();
    if let Some(custom) = env::var_os("MUSE_SYSTEM_PYTHON").filter(|v| !v.is_empty()) {
        out.push((PathBuf::from(custom), Vec::new()));
    }
    if let Some(local) = local_app_data() {
        let programs = local.join("Programs").join("Python");
        let mut dirs: Vec<PathBuf> = fs::read_dir(&programs)
            .map(|rd| rd.filter_map(|e| e.ok().map(|e| e.path())).collect())
            .unwrap_or_default();
        dirs.sort();
        dirs.reverse();
        dirs.sort_by_key(|d| !d.to_string_lossy().ends_with("Python311")); // prefer 3.11, then newest
        for dir in dirs {
            let exe = dir.join("python.exe");
            if exe.is_file() {
                out.push((exe, Vec::new()));
            }
        }
    }
    for root in ["ProgramFiles", "ProgramW6432"] {
        if let Some(pf) = env::var_os(root) {
            let exe = PathBuf::from(pf).join("Python311").join("python.exe");
            if exe.is_file() {
                out.push((exe, Vec::new()));
            }
        }
    }
    if let Some(path) = env::var_os("PATH") {
        for entry in env::split_paths(&path) {
            let lowered = entry.to_string_lossy().to_lowercase();
            if lowered.contains("windowsapps") || lowered.contains(".venv") || lowered.contains("sidecar-runtime") {
                continue;
            }
            let py = entry.join("py.exe");
            if py.is_file() {
                out.push((py, vec![OsString::from("-3.11")]));
            }
            let exe = entry.join("python.exe");
            if exe.is_file() {
                out.push((exe, Vec::new()));
            }
        }
    }
    out
}

#[cfg(windows)]
#[allow(non_camel_case_types, dead_code)]
mod job {
    use std::os::windows::io::AsRawHandle;
    use std::process::Child;

    type HANDLE = *mut core::ffi::c_void;

    #[repr(C)]
    #[derive(Default)]
    struct BasicLimit {
        per_process_user_time_limit: i64,
        per_job_user_time_limit: i64,
        limit_flags: u32,
        minimum_working_set_size: usize,
        maximum_working_set_size: usize,
        active_process_limit: u32,
        affinity: usize,
        priority_class: u32,
        scheduling_class: u32,
    }

    #[repr(C)]
    #[derive(Default)]
    struct IoCounters {
        counters: [u64; 6],
    }

    #[repr(C)]
    #[derive(Default)]
    struct ExtendedLimit {
        basic: BasicLimit,
        io: IoCounters,
        process_memory_limit: usize,
        job_memory_limit: usize,
        peak_process_memory_used: usize,
        peak_job_memory_used: usize,
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn CreateJobObjectW(attributes: *mut core::ffi::c_void, name: *const u16) -> HANDLE;
        fn SetInformationJobObject(job: HANDLE, class: i32, info: *const core::ffi::c_void, len: u32) -> i32;
        fn AssignProcessToJobObject(job: HANDLE, process: HANDLE) -> i32;
        fn SetErrorMode(mode: u32) -> u32;
    }

    const JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: u32 = 0x2000;
    const JOB_OBJECT_EXTENDED_LIMIT_INFORMATION: i32 = 9;

    /// SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX (inherited by children).
    pub fn no_error_dialogs() {
        unsafe {
            SetErrorMode(0x8003);
        }
    }

    /// The job handle is intentionally never closed: the OS closes it when the launcher exits
    /// (normally or by force-kill), which kills the whole Python child tree.
    pub fn kill_on_close(child: &Child) {
        unsafe {
            let job = CreateJobObjectW(core::ptr::null_mut(), core::ptr::null());
            if job.is_null() {
                return;
            }
            let mut info = ExtendedLimit::default();
            info.basic.limit_flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let ok = SetInformationJobObject(
                job,
                JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                &info as *const ExtendedLimit as *const core::ffi::c_void,
                core::mem::size_of::<ExtendedLimit>() as u32,
            );
            if ok != 0 {
                let _ = AssignProcessToJobObject(job, child.as_raw_handle() as HANDLE);
            }
        }
    }
}

struct Attempt {
    tier: u8,
    program: PathBuf,
    prefix: Vec<OsString>,
    script: PathBuf,
    runtime: Option<PathBuf>,
    cwd: PathBuf,
}

enum Outcome {
    Done(i32),
    Retry(String),
}

/// `--data-dir rel` stays correct even though the child's cwd becomes the runtime directory.
fn absolutize_args(args: &[OsString]) -> Vec<OsString> {
    let base = env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let mut out = Vec::with_capacity(args.len());
    let mut next_is_path = false;
    for arg in args {
        let text = arg.to_string_lossy().to_string();
        if next_is_path {
            out.push(base.join(arg).into_os_string());
            next_is_path = false;
        } else if text == "--data-dir" {
            out.push(arg.clone());
            next_is_path = true;
        } else if let Some(value) = text.strip_prefix("--data-dir=") {
            out.push(OsString::from(format!("--data-dir={}", base.join(value).display())));
        } else {
            out.push(arg.clone());
        }
    }
    out
}

fn child_path(front: &[PathBuf]) -> OsString {
    let inherited: Vec<PathBuf> = env::var_os("PATH")
        .map(|p| env::split_paths(&p).collect())
        .unwrap_or_default();
    let kept = inherited
        .into_iter()
        .filter(|p| !p.to_string_lossy().to_lowercase().contains(".venv"))
        .filter(|p| !front.iter().any(|f| f == p));
    env::join_paths(front.iter().cloned().chain(kept)).unwrap_or_default()
}

fn run_attempt(attempt: &Attempt, forwarded: &[OsString], launcher_dir: &Path) -> Outcome {
    let mut command = Command::new(&attempt.program);
    command
        .args(&attempt.prefix)
        .arg(&attempt.script)
        .args(forwarded)
        .current_dir(&attempt.cwd)
        .env_remove("PYTHONHOME")
        .env_remove("PYTHONPATH")
        .env("PYTHONNOUSERSITE", "1")
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .env("MUSE_LAUNCHER_TIER_USED", attempt.tier.to_string());
    let mut front: Vec<PathBuf> = Vec::new();
    if let Some(runtime) = &attempt.runtime {
        front.push(runtime.clone());
        front.push(runtime.join("DLLs"));
        let ui = runtime.join("ui-prototype").join("dist");
        if ui.join("index.html").is_file() && env::var_os("MUSE_UI_DIST").map_or(true, |v| v.is_empty()) {
            command.env("MUSE_UI_DIST", ui);
        }
    }
    front.push(launcher_dir.to_path_buf());
    command.env("PATH", child_path(&front));
    if let Ok(me) = env::current_exe() {
        command.env("MUSE_ENGINE_LAUNCHER", me);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    log(&format!("tier {} -> {}", attempt.tier, attempt.program.display()));
    let started = Instant::now();
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(err) => return Outcome::Retry(format!("spawn {} failed: {err}", attempt.program.display())),
    };
    #[cfg(windows)]
    job::kill_on_close(&child);
    let code = match child.wait() {
        Ok(status) => status.code().unwrap_or(1),
        Err(err) => return Outcome::Retry(format!("wait failed: {err}")),
    };
    let raw = code as u32;
    if LOADER_FAILURES.contains(&raw) {
        return Outcome::Retry(format!("tier {} loader failure 0x{raw:08X}", attempt.tier));
    }
    if code != 0 && started.elapsed() < QUICK_FAIL {
        return Outcome::Retry(format!("tier {} exited {code} after {:?}", attempt.tier, started.elapsed()));
    }
    Outcome::Done(code)
}

fn main() {
    #[cfg(windows)]
    job::no_error_dialogs();
    let forwarded = absolutize_args(&env::args_os().skip(1).collect::<Vec<_>>());
    let dir = match exe_dir() {
        Some(dir) => dir,
        None => {
            eprintln!("[muse-engine] cannot resolve own executable directory");
            std::process::exit(90);
        }
    };
    let forced: Option<u8> = env::var("MUSE_LAUNCHER_TIER").ok().and_then(|v| v.trim().parse().ok());
    let wants = |tier: u8| forced.map_or(true, |f| f == tier);
    let runtime = find_runtime(&dir);
    let mut errors: Vec<String> = Vec::new();
    let tier_prefix: Vec<OsString> = ["-B", "-s", "-u", "-X", "utf8"].iter().map(OsString::from).collect();

    // Tier 1: packaged runtime in place.
    if let (Some(rt), true) = (&runtime, wants(1)) {
        let attempt = Attempt { tier: 1, program: rt.join("python.exe"), prefix: tier_prefix.clone(),
                                script: rt.join("app").join(ENTRY), runtime: Some(rt.clone()), cwd: rt.clone() };
        match run_attempt(&attempt, &forwarded, &dir) {
            Outcome::Done(code) => std::process::exit(code),
            Outcome::Retry(why) => errors.push(why),
        }
    }
    // Tier 2: runtime mirrored to %LOCALAPPDATA%\Muse\sidecar-runtime (C: drive).
    if let (Some(rt), true) = (&runtime, wants(2)) {
        match mirror_runtime(rt) {
            Some(mirror) => {
                let attempt = Attempt { tier: 2, program: mirror.join("python.exe"), prefix: tier_prefix.clone(),
                                        script: mirror.join("app").join(ENTRY), runtime: Some(mirror.clone()),
                                        cwd: mirror.clone() };
                match run_attempt(&attempt, &forwarded, &dir) {
                    Outcome::Done(code) => std::process::exit(code),
                    Outcome::Retry(why) => errors.push(why),
                }
            }
            None => errors.push("tier 2 mirror unavailable".to_string()),
        }
    }
    // Tier 3: installed system Python, isolated mode, packaged app (or developer checkout source).
    if wants(3) {
        let script = runtime
            .as_ref()
            .map(|rt| rt.join("app").join(ENTRY))
            .filter(|p| p.is_file())
            .or_else(|| find_source_script(&dir));
        match script {
            Some(script) => {
                let cwd = script.parent().and_then(Path::parent).map(Path::to_path_buf).unwrap_or_else(|| dir.clone());
                for (program, mut prefix) in system_pythons() {
                    prefix.extend(["-I", "-B", "-u", "-X", "utf8"].iter().map(OsString::from));
                    let attempt = Attempt { tier: 3, program, prefix, script: script.clone(), runtime: None,
                                            cwd: cwd.clone() };
                    match run_attempt(&attempt, &forwarded, &dir) {
                        Outcome::Done(code) => std::process::exit(code),
                        Outcome::Retry(why) => errors.push(why),
                    }
                }
            }
            None => errors.push(format!("tier 3: no {ENTRY} found near {}", dir.display())),
        }
    }
    eprintln!("[muse-engine] no working Python interpreter (tier 1 -> 2 -> 3): {}", errors.join(" | "));
    std::process::exit(92);
}
