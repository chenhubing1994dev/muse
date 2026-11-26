"""Stage-2 autopilot (double-click target): build the zero-config Muse desktop client, package the
Chinese installer, run the zero-leak firewall and sync the verified branch to GitHub ``main``.

Run by ``2_双击全自动生成exe安装包并同步GitHub周更.bat`` with ``py -3.11``. Every step is logged to
``reports/autopilot/stage2_client_latest.md`` (+ ``.json``). It never touches the locked ``.venv``
or any production database, and it never force-pushes.

Steps
  1. Toolchain: system Python 3.11 (``sys.executable``; no venv — stale ``.venv-packager`` is
     deleted) + ``pip install --upgrade pyinstaller``, Node/npm, Rust (silent
     ``rustup-init -y`` if missing), MSVC linker via ``vswhere`` → ``winget`` Build Tools → GNU
     toolchain fallback.
  2. Local ``.exe`` first: ``npm run build`` → freeze ``muse-engine.exe`` (isolated ``--self-test``
     with no Python on PATH) → ``cargo build --release`` → cold-start smoke test of
     ``src-tauri/target/release/muse.exe`` against a brand-new empty data directory, then verify
     **zero** leftover ``muse-engine`` processes.
  3. Chinese installer: ``tauri build --bundles nsis`` → ``开箱即用安装包/`` gets
     ``Muse_<ver>_x64-setup.exe`` + ``Muse_Windows_x64_绿色免安装版.zip`` + ``SHA256SUMS.txt``;
     Explorer opens the folder.
  4. GitHub: remove forbidden / >25 MB / generated files from the index (kept on disk), sanitize
     machine paths, run ``pre_github_sync_audit.py`` (blocks on any finding), commit this stage on
     ``agent/omega-build-20261125``, tag old ``main`` as ``backup-main-pre-omega``, fast-forward or
     merge the branch into ``main``, ``git push origin main`` (Git Credential Manager / ``gh auth
     login --web`` browser sign-in when needed) and trigger the first weekly cross-platform build.

Flags: ``--skip-build`` (only steps 1+4), ``--skip-push`` (steps 1-3 only), ``--no-explorer``.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRANCH = "agent/omega-build-20261125"
MAIN = "main"
BACKUP_TAG = "backup-main-pre-omega"
REMOTE_URL = "https://github.com/chenhubing1994dev/muse.git"
REPO_SLUG = "chenhubing1994dev/muse"
WEEKLY_WORKFLOW = "weekly-cross-platform-release.yml"
# Local Windows build release tag. Deliberately NOT "v*.*.*": that pattern would re-trigger the weekly
# cross-platform workflow, which then writes its own assets into the same release.
LOCAL_RELEASE_TAG = "muse-windows-local-{version}"
OUT_DIR = ROOT / "开箱即用安装包"
REPORT_DIR = ROOT / "reports" / "autopilot"
REPORT_MD = REPORT_DIR / "stage2_client_latest.md"
REPORT_JSON = REPORT_DIR / "stage2_client_latest.json"
PACKAGER_VENV = ROOT / ".venv-packager"
SRC_TAURI = ROOT / "src-tauri"
UI_DIR = ROOT / "ui-prototype"
STAMP = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
PYPI_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
# Cargo output lives OUTSIDE the repo (space-free, avoids the "os error 5 / 拒绝访问" lock under the project
# folder). Default D:/muse-cargo-target (keeps C: free); override with MUSE_CARGO_TARGET_DIR.
LEGACY_CARGO_TARGET_DIR = Path.home() / "AppData" / "Local" / "muse-cargo-target"   # old C: cache, auto-removed


def _default_cargo_target() -> Path:
    override = os.environ.get("MUSE_CARGO_TARGET_DIR", "").strip()
    if override:
        return Path(override)
    if Path("D:/").is_dir():
        return Path("D:/muse-cargo-target")
    return LEGACY_CARGO_TARGET_DIR   # machine without a D: drive


CARGO_TARGET_DIR = _default_cargo_target()
COLDSTART_DIR = Path(tempfile.gettempdir()) / "muse_coldstart_test"

STAGE2_FILES = [
    ".gitignore", ".gitattributes",
    "2_双击全自动生成exe安装包并同步GitHub周更.bat",
    "QUICK_STATE.md",
    "src/muse_sidecar_main.py",
    "src/muse/xuanshu_symbolic_fallback.py", "src/muse/xuanshu_fusion.py", "src/muse/omega_api.py",
    "scripts/build_sidecar.py", "scripts/pre_github_sync_audit.py", "scripts/sanitize_local_paths.py",
    "scripts/generate_client_icons.py", "scripts/autopilot_stage2_client_and_github.py",
    "tests/test_muse_sidecar.py", "tests/test_xuanshu_symbolic_fallback.py",
    "client-splash/index.html", "client-splash/splash.css", "client-splash/splash.js",
    "src-tauri/Cargo.toml", "src-tauri/build.rs", "src-tauri/tauri.conf.json",
    "src-tauri/capabilities/default.json", "src-tauri/binaries/.gitkeep",
    "src-tauri/src/main.rs", "src-tauri/src/lib.rs", "src-tauri/src/sidecar.rs", "src-tauri/src/hardware.rs",
    "src-tauri/src/tray.rs", "src-tauri/src/backup.rs", "src-tauri/src/crash.rs", "src-tauri/sidecar-launcher/main.rs",
    "src-tauri/icons/32x32.png", "src-tauri/icons/128x128.png", "src-tauri/icons/128x128@2x.png",
    "src-tauri/icons/icon.png", "src-tauri/icons/icon.ico", "src-tauri/icons/icon.icns",
    ".github/workflows/weekly-cross-platform-release.yml", ".github/workflows/ci-pr-verify.yml",
    ".github/workflows/scheduled-release.yml",
]


# --------------------------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------------------------
class Report:
    def __init__(self) -> None:
        self.steps: list[dict[str, object]] = []
        self.facts: dict[str, object] = {"started": _dt.datetime.now().isoformat(timespec="seconds")}
        self.lines: list[str] = []

    def log(self, message: str) -> None:
        line = f"[{_dt.datetime.now().strftime('%H:%M:%S')}] {message}"
        self.lines.append(line)
        print(line, flush=True)

    def step(self, name: str, status: str, detail: str = "") -> None:
        self.steps.append({"name": name, "status": status, "detail": detail})
        icon = {"ok": "✅", "warn": "⚠️", "fail": "❌", "skip": "⏭️"}.get(status, "•")
        self.log(f"{icon} {name}: {detail}")

    def write(self) -> None:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        self.facts["finished"] = _dt.datetime.now().isoformat(timespec="seconds")
        overall = "PASS" if all(s["status"] in {"ok", "skip", "warn"} for s in self.steps) else "FAIL"
        self.facts["overall"] = overall
        md = [f"# Muse Stage-2 客户端打包与 GitHub 同步报告 — {overall}", "",
              f"- 开始：{self.facts['started']}  结束：{self.facts['finished']}",
              f"- 分支：`{BRANCH}` → `{MAIN}`；远端：{REMOTE_URL}", "",
              "| 步骤 | 状态 | 说明 |", "|---|---|---|"]
        for s in self.steps:
            detail = str(s["detail"]).replace("|", "\\|").replace("\n", " ")[:400]
            md.append(f"| {s['name']} | {s['status']} | {detail} |")
        md += ["", "## 关键事实", "```json", json.dumps(self.facts, ensure_ascii=False, indent=2, default=str), "```",
               "", "## 运行日志（末尾 200 行）", "```", *self.lines[-200:], "```"]
        REPORT_MD.write_text("\n".join(md) + "\n", encoding="utf-8")
        REPORT_JSON.write_text(json.dumps({"steps": self.steps, "facts": self.facts}, ensure_ascii=False, indent=2,
                                          default=str), encoding="utf-8")


R = Report()


class StepFailed(RuntimeError):
    pass


# --------------------------------------------------------------------------------------------
# Process helpers
# --------------------------------------------------------------------------------------------
def build_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.pop("VIRTUAL_ENV", None)
    env["PYTHONUTF8"] = "1"
    extra = []
    home = Path(env.get("USERPROFILE") or Path.home())
    for candidate in (home / ".cargo" / "bin", Path(env.get("ProgramFiles", "")) / "nodejs",
                      Path(env.get("APPDATA", "")) / "npm", Path(env.get("ProgramFiles", "")) / "Git" / "cmd",
                      Path(env.get("ProgramFiles", "")) / "GitHub CLI", Path(env.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links"):
        if str(candidate) and candidate.is_dir() and str(candidate) not in env.get("PATH", ""):
            extra.append(str(candidate))
    if extra:
        env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    return env


ENV = build_env()


def which(name: str) -> str | None:
    return shutil.which(name, path=ENV.get("PATH"))


def run(cmd: list[str], *, cwd: Path = ROOT, timeout: int = 3600, check: bool = True, stream: bool = True,
        env: dict[str, str] | None = None, label: str = "") -> tuple[int, str]:
    """Run a command, streaming output to the console; returns (code, last ~16 KB of output)."""
    import threading
    shown = " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd)
    R.log(f"$ {shown}" + (f"   [{label}]" if label else ""))
    tail: list[str] = []
    try:
        proc = subprocess.Popen([str(c) for c in cmd], cwd=str(cwd), env=env or ENV, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    except OSError as exc:
        if check:
            raise StepFailed(f"无法启动 {cmd[0]}：{exc}") from exc
        return 127, str(exc)
    assert proc.stdout is not None

    def pump() -> None:
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip()
            tail.append(line)
            if len(tail) > 400:
                del tail[:100]
            if stream:
                print("    " + line, flush=True)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.call(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
        proc.kill()
        code = proc.wait()
        tail.append(f"[超时 {timeout}s，已终止]")
    reader.join(timeout=10)
    output = "\n".join(tail)[-16000:]
    if check and code != 0:
        raise StepFailed(f"{Path(str(cmd[0])).name} 退出码 {code}：{output[-1500:]}")
    return code, output


def git(*args: str, check: bool = True, timeout: int = 600, stream: bool = False) -> tuple[int, str]:
    exe = which("git")
    if not exe:
        raise StepFailed("未找到 git")
    return run([exe, *args], check=check, timeout=timeout, stream=stream)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tauri_version() -> str:
    return json.loads((SRC_TAURI / "tauri.conf.json").read_text(encoding="utf-8"))["version"]


def load_script_module(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    namespace: dict[str, object] = {"__name__": name, "__file__": str(path)}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)  # noqa: S102
    return namespace


def cargo_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Env for cargo build / cargo test / tauri build: C: target dir + low parallelism (AV scan locks)."""
    CARGO_TARGET_DIR.mkdir(parents=True, exist_ok=True)
    env = dict(base or ENV)
    env["CARGO_TARGET_DIR"] = str(CARGO_TARGET_DIR)
    env["CARGO_BUILD_JOBS"] = "2"
    return env


def run_cargo(cmd: list[str], *, cwd: Path = ROOT, timeout: int = 5400, check: bool = True, label: str = "",
              retries: int = 2) -> tuple[int, str]:
    """Run a cargo/tauri command; on a transient ``os error 5`` / 拒绝访问 wait 2 s and retry (max 2x)."""
    code, out = 1, ""
    for attempt in range(retries + 1):
        code, out = run(cmd, cwd=cwd, env=cargo_env(), timeout=timeout, check=False,
                        label=label + (f" (重试 {attempt})" if attempt else ""))
        if code == 0:
            return code, out
        if attempt < retries and re.search(r"os error 5|拒绝访问|Access is denied|PermissionDenied", out, re.I):
            R.log(f"检测到 os error 5 文件锁，2 秒后重试（cargo 复用已编译缓存）[{attempt + 1}/{retries}]")
            time.sleep(2)
            continue
        break
    global CARGO_TARGET_DIR
    if code != 0 and re.search(r"os error 5|拒绝访问|Access is denied|PermissionDenied", out, re.I) \
            and CARGO_TARGET_DIR != LEGACY_CARGO_TARGET_DIR:
        R.log(f"{CARGO_TARGET_DIR} 持续 os error 5 → 改用已验证可用的 {LEGACY_CARGO_TARGET_DIR} 重新编译")
        CARGO_TARGET_DIR = LEGACY_CARGO_TARGET_DIR
        R.facts["cargo_target_dir_fallback"] = str(CARGO_TARGET_DIR)
        code, out = run(cmd, cwd=cwd, env=cargo_env(), timeout=timeout, check=False, label=label + "（C 盘回退）")
    if check and code != 0:
        raise StepFailed(f"{Path(str(cmd[0])).name} 退出码 {code}：{out[-1500:]}")
    return code, out


def release_dirs() -> list[Path]:
    """Cargo release output dirs: C: target dir first, legacy ``src-tauri/target/release`` as fallback."""
    dirs = [CARGO_TARGET_DIR / "release", LEGACY_CARGO_TARGET_DIR / "release", SRC_TAURI / "target" / "release"]
    return list(dict.fromkeys(dirs))


def find_release_file(*parts: str) -> Path | None:
    hits = [d.joinpath(*parts) for d in release_dirs() if d.joinpath(*parts).is_file()]
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None


def find_bundles(kind: str, pattern: str) -> list[Path]:
    found: list[Path] = []
    for d in release_dirs():
        folder = d / "bundle" / kind
        if folder.is_dir():
            found += list(folder.glob(pattern))
    return sorted(found, key=lambda p: p.stat().st_mtime)


def show_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)



def cleanup_legacy_cargo_cache() -> None:
    """Free the multi-GB C: cargo cache from earlier runs (new builds use CARGO_TARGET_DIR on D:)."""
    legacy = LEGACY_CARGO_TARGET_DIR
    if legacy.exists() and legacy.resolve() != CARGO_TARGET_DIR.resolve():
        R.log(f"清理旧的 C 盘 Cargo 编译缓存 {legacy}（以后编译改用 {CARGO_TARGET_DIR}）")
        shutil.rmtree(legacy, ignore_errors=True)
        R.facts["legacy_cargo_cache_removed"] = not legacy.exists()

BUILD_INPUTS = ("src-tauri/src", "src-tauri/sidecar-launcher", "src-tauri/capabilities", "src-tauri/icons",
                "src-tauri/Cargo.toml", "src-tauri/build.rs", "src-tauri/tauri.conf.json", "client-splash",
                "src/muse_sidecar_main.py", "src/muse", "ui-prototype/src", "ui-prototype/index.html",
                "ui-prototype/package.json", "scripts/build_sidecar.py")
SKIP_PARTS = {"__pycache__", "node_modules", "target", "dist", ".pytest_cache"}


def newest_source_change(since: float) -> str | None:
    """Relative path of a build input modified after ``since`` (installer mtime), else None."""
    for rel in BUILD_INPUTS:
        base = ROOT / rel
        items = [base] if base.is_file() else (p for p in base.rglob("*") if p.is_file()) if base.is_dir() else []
        for item in items:
            if SKIP_PARTS.intersection(item.relative_to(ROOT).parts) or item.suffix in {".pyc", ".bak", ".log"}:
                continue
            try:
                if item.stat().st_mtime > since + 2:
                    return item.relative_to(ROOT).as_posix()
            except OSError:
                continue
    return None


def deliverables_ready() -> tuple[bool, str]:
    """True when setup.exe + portable zip + SHA256SUMS.txt exist and every listed hash matches."""
    version = tauri_version()
    setup = OUT_DIR / f"Muse_{version}_x64-setup.exe"
    portable = OUT_DIR / "Muse_Windows_x64_绿色免安装版.zip"
    sums = OUT_DIR / "SHA256SUMS.txt"
    missing = [p.name for p in (setup, portable, sums) if not p.is_file()]
    if missing:
        return False, "缺少 " + "、".join(missing)
    newer = newest_source_change(min(setup.stat().st_mtime, portable.stat().st_mtime))
    if newer:
        return False, f"源码有更新（{newer}），需要重新编译"
    listed: dict[str, str] = {}
    for line in sums.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            listed[parts[1].lstrip("*").strip()] = parts[0].lower()
    for item in (setup, portable):
        want = listed.get(item.name)
        if not want:
            return False, f"SHA256SUMS.txt 未列出 {item.name}"
        if sha256(item) != want:
            return False, f"{item.name} 校验和不匹配"
    R.facts["deliverables"] = {p.name: {"bytes": p.stat().st_size, "sha256": listed[p.name]} for p in (setup, portable)}
    return True, f"{setup.name} · {portable.name} · SHA256 校验通过"



# --------------------------------------------------------------------------------------------
# Step 1: toolchains
# --------------------------------------------------------------------------------------------
def packager_python() -> Path:
    """Always the verified system interpreter (no venv: ``python -m venv`` exits 106 on this PC)."""
    return Path(sys.executable)


def cleanup_stale_packager_venv() -> None:
    """Remove the half-created ``.venv-packager`` left behind by the old ``venv``/ensurepip (exit 106)."""
    if PACKAGER_VENV.exists():
        R.log(f"清理残留的半成品虚拟环境 {PACKAGER_VENV.name}（不再创建任何 venv）")
        shutil.rmtree(PACKAGER_VENV, ignore_errors=True)


def ensure_packager_venv() -> Path:
    """Install PyInstaller (+ runtime deps) straight into the system Python 3.11 — no venv at all."""
    py = packager_python()
    req = ROOT / "requirements.txt"
    base = [str(py), "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pyinstaller"]
    code, _ = run(base, timeout=1800, check=False, label="pip pyinstaller")
    if code != 0:
        R.log("默认 PyPI 安装失败，切换清华镜像重试")
        run(base[:5] + ["-i", PYPI_MIRROR] + base[5:], timeout=1800, label="pip pyinstaller (mirror)")
    if req.is_file():
        code, _ = run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(req)],
                      timeout=1800, check=False, label="pip requirements")
        if code != 0:
            run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-i", PYPI_MIRROR, "-r", str(req)],
                timeout=1800, check=False, label="pip requirements (mirror)")
    code, out = run([str(py), "-c", "import PyInstaller,sys;print(PyInstaller.__version__, sys.version.split()[0])"],
                    stream=False)
    R.facts["pyinstaller"] = out.strip().splitlines()[-1] if out.strip() else "?"
    return py


def vswhere_vc_tools() -> str | None:
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio" / \
        "Installer" / "vswhere.exe"
    if not vswhere.is_file():
        return None
    code, out = run([str(vswhere), "-latest", "-products", "*", "-requires",
                     "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                    check=False, stream=False)
    path = out.strip().splitlines()[-1].strip() if code == 0 and out.strip() else ""
    return path or None


def ensure_rust() -> str:
    """Return the Rust target triple that will be built (msvc preferred, gnu fallback)."""
    if not which("cargo") or not which("rustc"):
        R.log("未检测到 Rust，静默安装 rustup（stable, minimal）")
        installer = Path(tempfile.gettempdir()) / "rustup-init.exe"
        urllib.request.urlretrieve("https://win.rustup.rs/x86_64", installer)  # noqa: S310 - official HTTPS
        run([str(installer), "-y", "--profile", "minimal", "--default-toolchain", "stable"], timeout=1800)
        global ENV
        ENV = build_env()
    if not which("cargo"):
        raise StepFailed("Rust 安装后仍找不到 cargo，请重新打开本窗口后再双击一次")
    msvc = vswhere_vc_tools()
    if not msvc and which("winget"):
        R.log("缺少 MSVC 链接器，尝试用 winget 静默安装 VS Build Tools（可能弹出 UAC，请点“是”）")
        run([which("winget") or "winget", "install", "--id", "Microsoft.VisualStudio.2022.BuildTools", "-e",
             "--accept-package-agreements", "--accept-source-agreements", "--silent", "--override",
             "--wait --quiet --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"],
            timeout=5400, check=False, label="winget-buildtools")
        msvc = vswhere_vc_tools()
    if msvc:
        R.facts["msvc"] = msvc
        triple = "x86_64-pc-windows-msvc"
        run([which("rustup") or "rustup", "target", "add", triple], check=False, stream=False)
    else:
        R.log("MSVC 不可用 → 切换 GNU 工具链（自带 mingw 链接器）")
        run([which("rustup") or "rustup", "toolchain", "install", "stable-x86_64-pc-windows-gnu", "--profile",
             "minimal"], timeout=1800)
        ENV["RUSTUP_TOOLCHAIN"] = "stable-x86_64-pc-windows-gnu"
        triple = "x86_64-pc-windows-gnu"
    _, out = run([which("rustc") or "rustc", "-vV"], stream=False)
    host = next((line.split(":", 1)[1].strip() for line in out.splitlines() if line.startswith("host:")), triple)
    R.facts["rust_host"] = host
    return host


def step_toolchain() -> dict[str, object]:
    if os.name != "nt":
        raise StepFailed("本双击脚本面向 Windows；macOS/Linux 安装包由 GitHub Actions 每周自动编译")
    info: dict[str, object] = {"python": sys.version.split()[0]}
    py = ensure_packager_venv()
    info["packager_python"] = str(py)
    npm = which("npm.cmd") or which("npm")
    if not npm:
        raise StepFailed("未找到 npm（请安装 Node.js LTS）")
    info["npm"] = npm
    info["triple"] = ensure_rust()
    R.step("1 系统 Python 打包环境与工具链", "ok",
           f"py {info['python']} · {R.facts.get('pyinstaller')} · rust {info['triple']} · npm OK")
    return info


# --------------------------------------------------------------------------------------------
# Step 2: local exe + cold start
# --------------------------------------------------------------------------------------------
def count_processes(image: str) -> int:
    code, out = run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"], check=False, stream=False)
    return sum(1 for line in out.splitlines() if line.lower().startswith(f'"{image.lower()}"'))


def step_local_exe(info: dict[str, object]) -> Path:
    npm = str(info["npm"])
    if not (UI_DIR / "node_modules").is_dir():
        run([npm, "ci"], cwd=UI_DIR, timeout=1800, label="npm ci")
    run([npm, "run", "build"], cwd=UI_DIR, timeout=1800, label="vite build")
    R.step("2a 前端 dist/", "ok", "npm run build 完成")

    run([sys.executable, str(ROOT / "src" / "muse_sidecar_main.py"), "--self-test"], timeout=600,
        label="源码引擎自检")
    run([sys.executable, str(ROOT / "scripts" / "build_sidecar.py"), "--target", str(info["triple"])],
        timeout=3600, label="冻结 muse-engine")
    result = json.loads((ROOT / "build-sidecar" / "result.json").read_text(encoding="utf-8"))
    R.facts["sidecar"] = {k: result.get(k) for k in ("mode", "binary", "bytes", "sha256", "target")}
    if result.get("plan_a"):
        R.facts["sidecar_plan_a"] = {k: result["plan_a"].get(k) for k in ("error", "diagnosis", "forensics_final")}
    R.facts["sidecar_self_test"] = result.get("self_test", {}).get("report", {}).get("self_test")
    mode = "PyInstaller 单文件" if result.get("mode", "pyinstaller-onefile") == "pyinstaller-onefile" \
        else "Rust 原生启动器 + 便携 Python 运行时（Plan B 自动兜底）"
    R.step("2b 冻结 muse-engine.exe + 无 Python 隔离自检", "ok", f"{mode} · {result['binary']} ({result['bytes']} bytes)")

    run_cargo([which("cargo") or "cargo", "build", "--release", "--manifest-path", str(SRC_TAURI / "Cargo.toml")],
              timeout=5400, label="cargo build --release")
    exe = find_release_file("muse.exe")
    if exe is None:
        raise StepFailed(f"未产出 muse.exe（已查找 {', '.join(str(d) for d in release_dirs())}）")
    R.facts["cargo_target_dir"] = str(CARGO_TARGET_DIR)
    R.facts["local_exe"] = {"path": show_path(exe), "bytes": exe.stat().st_size}
    R.step("2c 本地客户端 muse.exe", "ok", f"{show_path(exe)}（CARGO_TARGET_DIR 位于 C 盘）")

    before = count_processes("muse-engine.exe")
    shutil.rmtree(COLDSTART_DIR, ignore_errors=True)
    cold = COLDSTART_DIR
    cold.mkdir(parents=True, exist_ok=True)
    smoke_file = cold / "smoke-result.json"
    data_root = cold / "data-root"
    env = dict(ENV, MUSE_DATA_DIR=str(data_root), MUSE_APP_DATA_DIR=str(data_root), MUSE_SMOKE_TEST="1",
               MUSE_SMOKE_RESULT=str(smoke_file), TEMP=str(cold), TMP=str(cold))
    runtime = SRC_TAURI / "sidecar-runtime"
    if (runtime / "python.exe").is_file() and not (exe.parent / "sidecar-runtime" / "python.exe").is_file():
        env["MUSE_SIDECAR_RUNTIME"] = str(runtime)   # Plan B launcher: runtime not copied next to the C: exe
    started = time.monotonic()
    try:
        code, _ = run([str(exe)], cwd=cold, env=env, timeout=300, check=False, label="冷启动冒烟")
        seconds = round(time.monotonic() - started, 1)
        time.sleep(3)
        after = count_processes("muse-engine.exe")
        smoke = json.loads(smoke_file.read_text(encoding="utf-8")) if smoke_file.is_file() else {"ok": False}
    finally:
        time.sleep(1)
        shutil.rmtree(cold, ignore_errors=True)
    leftover = max(0, after - before)
    R.facts["cold_start"] = {"exit_code": code, "seconds": seconds, "smoke": smoke, "leftover_engine_processes": leftover}
    if not smoke.get("ok") or code != 0:
        raise StepFailed(f"冷启动冒烟失败（退出码 {code}）：{json.dumps(smoke, ensure_ascii=False)[:600]}")
    if leftover:
        raise StepFailed(f"退出后仍残留 {leftover} 个 muse-engine.exe 进程")
    R.step("2d 全新空目录冷启动验收", "ok",
           f"健康 {smoke.get('health_status')} · 十维意识 {smoke.get('consciousness_status')} · "
           f"{seconds}s · 残留进程 0")
    return exe


# --------------------------------------------------------------------------------------------
# Step 3: installer + portable zip
# --------------------------------------------------------------------------------------------
def step_installer(info: dict[str, object], exe: Path, *, open_explorer: bool) -> list[Path]:
    version = tauri_version()
    npx = which("npx.cmd") or which("npx")
    produced: list[Path] = []
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    setup_name = f"Muse_{version}_x64-setup.exe"
    if npx:
        code, out = run_cargo([npx, "--yes", "@tauri-apps/cli@^2", "build", "--bundles", "nsis", "--ci"],
                              cwd=SRC_TAURI, timeout=5400, check=False, label="tauri build (NSIS 中文安装包)")
        nsis = find_bundles("nsis", "*-setup.exe") if code == 0 else []
        if nsis:
            target = OUT_DIR / setup_name
            shutil.copy2(nsis[-1], target)
            produced.append(target)
            R.step("3a 中文一键安装包", "ok", target.name)
        else:
            R.step("3a 中文一键安装包", "fail", f"tauri build 失败：{out[-600:]}")
    else:
        R.step("3a 中文一键安装包", "fail", "未找到 npx")

    engine = find_release_file("muse-engine.exe") or SRC_TAURI / "binaries" / f"muse-engine-{info['triple']}.exe"
    if not engine.is_file():
        engine = SRC_TAURI / "binaries" / f"muse-engine-{info['triple']}.exe"
    portable = OUT_DIR / "Muse_Windows_x64_绿色免安装版.zip"
    readme = ("Muse 星枢 绿色免安装版\r\n\r\n1. 解压到任意文件夹，双击 Muse.exe 即可使用（无需安装 Python）。\r\n"
              "2. 同目录的 MUSE_PORTABLE 标记让数据保存在 .\\muse-data\\，整个文件夹可直接拷走。\r\n"
              "3. 需要 Microsoft Edge WebView2 运行时（Windows 10/11 默认自带）。\r\n"
              "4. 常驻自主守护默认关闭（安全 stopped），可在主控台或托盘一键开关。\r\n"
              "5. 若双击后没有窗口：会弹出中文错误提示；日志在本文件夹的 muse-crash.log / muse-launch.log，\r\n"
              "   或 %LOCALAPPDATA%\\Muse\\ 下的同名文件。被杀毒软件拦截时，请把整个文件夹放到 C:\\ 或桌面再运行。\r\n")
    with zipfile.ZipFile(portable, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.write(exe, "Muse/Muse.exe")
        archive.write(engine, "Muse/muse-engine.exe")
        archive.writestr("Muse/MUSE_PORTABLE", "portable: user data is stored in ./muse-data\r\n")
        runtime = SRC_TAURI / "sidecar-runtime"
        if (runtime / "python.exe").is_file():   # Plan B: native launcher needs its portable runtime beside it
            for item in sorted(runtime.rglob("*")):
                if item.is_file():
                    archive.write(item, "Muse/sidecar-runtime/" + item.relative_to(runtime).as_posix())
        archive.writestr("Muse/使用说明.txt", readme)
    produced.append(portable)
    R.step("3b 绿色免安装版", "ok", portable.name)

    sums = OUT_DIR / "SHA256SUMS.txt"
    entries = sorted(p for p in OUT_DIR.iterdir() if p.is_file() and p.name != sums.name)
    sums.write_text("".join(f"{sha256(p)}  {p.name}\n" for p in entries), encoding="utf-8")
    R.facts["deliverables"] = {p.name: {"bytes": p.stat().st_size, "sha256": sha256(p)} for p in entries}
    R.step("3c SHA256SUMS.txt", "ok", f"{len(entries)} 个文件")
    if open_explorer:
        subprocess.Popen(["explorer.exe", str(OUT_DIR)])  # noqa: S603,S607
    return produced


# --------------------------------------------------------------------------------------------
# Step 4: audit + git sync
# --------------------------------------------------------------------------------------------
def current_branch() -> str:
    return git("rev-parse", "--abbrev-ref", "HEAD")[1].strip().splitlines()[-1]


def ref_exists(ref: str) -> bool:
    return git("rev-parse", "--verify", "--quiet", ref, check=False)[0] == 0


def is_ancestor(a: str, b: str) -> bool:
    return git("merge-base", "--is-ancestor", a, b, check=False)[0] == 0


def ensure_remote() -> str:
    code, out = git("remote", "get-url", "origin", check=False)
    url = out.strip().splitlines()[-1] if code == 0 and out.strip() else ""
    normalized = url.rstrip("/").removesuffix(".git").lower()
    if not url:
        git("remote", "add", "origin", REMOTE_URL)
        return "origin"
    if normalized.endswith("github.com/chenhubing1994dev/muse"):
        return "origin"
    alt = "muse-github"
    if git("remote", "get-url", alt, check=False)[0] == 0:
        git("remote", "set-url", alt, REMOTE_URL)
    else:
        git("remote", "add", alt, REMOTE_URL)
    R.log(f"origin 指向 {url}，改用独立远端 {alt} → {REMOTE_URL}")
    return alt


def push_with_auth(remote: str) -> tuple[bool, str]:
    env = dict(ENV, GIT_TERMINAL_PROMPT="1", GCM_INTERACTIVE="always")
    exe = which("git") or "git"
    code, out = run([exe, "push", remote, f"{MAIN}:{MAIN}"], env=env, check=False, timeout=1800, label="git push")
    if code == 0:
        return True, out
    gh = which("gh")
    needs_auth = re.search(r"auth|403|denied|could not read|credential|workflow", out, re.I)
    if gh and needs_auth:
        R.log("推送需要授权：即将打开浏览器完成 GitHub 网页一键授权（gh auth login --web）")
        subprocess.call([gh, "auth", "login", "--web", "-h", "github.com", "-p", "https", "-s", "workflow"], env=env)
        subprocess.call([gh, "auth", "setup-git"], env=env)
        code, out = run([exe, "push", remote, f"{MAIN}:{MAIN}"], env=env, check=False, timeout=1800,
                        label="git push (重试)")
        return code == 0, out
    return False, out


def commit_index(message: str) -> None:
    code, out = git("commit", "-m", message, check=False)
    if code != 0 and re.search(r"user\.(name|email)|identity", out, re.I):
        git("-c", "user.name=Muse Autopilot", "-c", "user.email=muse-autopilot@users.noreply.github.com",
            "commit", "-m", message)
    elif code != 0 and not re.search(r"nothing to commit", out, re.I):
        raise StepFailed(f"提交失败：{out[-600:]}")


def squash_clean_release(remote_main: str) -> None:
    """Old unpushed commits still carry the untracked violators (.sqlite3-wal, >25 MB fonts...).

    Tag the dirty HEAD as a local backup, then rebuild the branch as ONE clean commit containing exactly
    the current sanitized index. Ignored/untracked files on disk are never touched; no force-push needed.
    """
    tag = f"backup-pre-clean-push-{STAMP}"
    git("tag", "-f", tag, "HEAD")
    R.facts["squash_backup_tag"] = tag
    R.log(f"历史未推送提交含已移出索引的违规文件 → 已打本地备份标签 {tag}，改为单一干净 Squash Release 提交")
    message = f"release: Muse clean squash release {STAMP} (sanitized index, stage-1 + stage-2)"
    if ref_exists(remote_main):
        # soft reset keeps the clean index & working tree; works with or without a common ancestor,
        # and the new commit is a fast-forward of the remote, so a plain push succeeds.
        git("reset", "--soft", remote_main)
        commit_index(message)
    else:
        tree = git("write-tree")[1].strip().splitlines()[-1]
        env_id = ["-c", "user.name=Muse Autopilot", "-c", "user.email=muse-autopilot@users.noreply.github.com"]
        code, out = git("commit-tree", tree, "-m", message, check=False)
        if code != 0:
            code, out = git(*env_id, "commit-tree", tree, "-m", message)
        sha = out.strip().splitlines()[-1]
        git("reset", "--soft", sha)
    head = git("rev-parse", "--short", "HEAD")[1].strip().splitlines()[-1]
    R.facts["commit"] = head
    R.step("4d+ Squash 干净发布提交", "ok", f"{BRANCH} @ {head}（备份 {tag}）")


def ensure_gh_auth() -> str | None:
    """Return the gh path when logged in; otherwise run the one-time browser login (gh auth login --web)."""
    gh = which("gh")
    if not gh:
        return None
    if run([gh, "auth", "status", "-h", "github.com"], check=False, stream=False)[0] == 0:
        return gh
    print("\n" + "-" * 62)
    print("  需要一次性登录 GitHub CLI（用于上传安装包到 Releases、触发云端编译）。")
    print("  接下来会显示一次性验证码，并打开浏览器：粘贴验证码 → 点 Authorize 即可。")
    print("-" * 62, flush=True)
    env = dict(ENV, GH_PROMPT_DISABLED="")
    subprocess.call([gh, "auth", "login", "--web", "-h", "github.com", "-p", "https", "-s", "workflow"], env=env)
    subprocess.call([gh, "auth", "setup-git"], env=env)
    if run([gh, "auth", "status", "-h", "github.com"], check=False, stream=False)[0] == 0:
        R.log("GitHub CLI 登录成功（凭据由 gh 保存在本机，脚本不读取、不记录令牌）")
        return gh
    return None


def stage_release_assets(version: str) -> list[Path]:
    """Copy deliverables to ASCII asset names (GitHub mangles CJK attachment names) + matching SHA256SUMS."""
    staging = Path(tempfile.gettempdir()) / "muse_release_assets"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    mapping = {
        OUT_DIR / f"Muse_{version}_x64-setup.exe": f"Muse_{version}_x64-setup.exe",
        OUT_DIR / "Muse_Windows_x64_绿色免安装版.zip": f"Muse_{version}_Windows_x64_Portable.zip",
    }
    assets = []
    for src, name in mapping.items():
        dst = staging / name
        shutil.copy2(src, dst)
        if dst.stat().st_size != src.stat().st_size:
            raise StepFailed(f"暂存 Release 附件失败：{name}")
        assets.append(dst)
    sums = staging / "SHA256SUMS.txt"
    sums.write_text("".join(f"{sha256(p)}  {p.name}\n" for p in assets), encoding="utf-8")
    return assets + [sums]


def step_release_upload(gh: str | None) -> None:
    """4i: upload the locally built Windows installer + portable zip to a GitHub Release (idempotent)."""
    name = "4i 上传安装包到 GitHub Releases"
    ready, why = deliverables_ready()
    if not ready:
        R.step(name, "warn", f"本地安装包未就绪（{why}），未上传；可加 --rebuild 重新编译")
        return
    if not gh:
        R.step(name, "warn", f"gh 未登录/未安装：可手动上传到 https://github.com/{REPO_SLUG}/releases/new")
        return
    version = tauri_version()
    tag = LOCAL_RELEASE_TAG.format(version=version)
    assets = stage_release_assets(version)
    files = [str(p) for p in assets]
    exists = run([gh, "release", "view", tag, "-R", REPO_SLUG], check=False, stream=False)[0] == 0
    if exists:
        code, out = run([gh, "release", "upload", tag, *files, "--clobber", "-R", REPO_SLUG], check=False,
                        timeout=1800, label="gh release upload（覆盖同名附件）")
    else:
        notes = Path(tempfile.gettempdir()) / "muse_release_assets" / "RELEASE_NOTES.md"
        notes.write_text(
            f"## Muse 星枢 {version} · Windows x64（本机编译）\n\n"
            f"| 文件 | 用法 |\n|---|---|\n"
            f"| `Muse_{version}_x64-setup.exe` | 中文一键安装包，双击安装（当前用户，无需管理员）。 |\n"
            f"| `Muse_{version}_Windows_x64_Portable.zip` | 绿色免安装版：解压后双击 `Muse.exe`，无需安装 Python。 |\n"
            f"| `SHA256SUMS.txt` | 校验：`certutil -hashfile <文件> SHA256`。 |\n\n"
            "- 需要 Microsoft Edge WebView2 运行时（Windows 10/11 默认自带）。\n"
            "- 用户数据保存在本机，卸载默认保留。\n"
            "- macOS / Linux 安装包由每周云端全平台编译自动发布（见其他 Release）。\n", encoding="utf-8")
        code, out = run([gh, "release", "create", tag, *files, "-R", REPO_SLUG, "--target", MAIN,
                         "--title", f"Muse {version} · Windows x64", "--notes-file", str(notes), "--latest"],
                        check=False, timeout=1800, label="gh release create")
    url = f"https://github.com/{REPO_SLUG}/releases/tag/{tag}"
    if code == 0:
        R.facts["release"] = {"tag": tag, "url": url, "assets": [p.name for p in assets], "updated": exists}
        R.step(name, "ok", f"{'已更新' if exists else '已发布'} {url}（{len(assets)} 个附件）")
    else:
        R.facts["release_tail"] = out[-1500:]
        R.step(name, "warn", f"上传失败（源码已推送，不影响 main）：{out[-300:]}")


def _push_and_trigger(remote: str, *, skip_push: bool) -> None:
    if skip_push:
        R.step("4g 推送 GitHub", "skip", "--skip-push")
        return
    ok, out = push_with_auth(remote)
    if not ok:
        R.facts["push_tail"] = out[-2000:]
        raise StepFailed("git push 失败（已保留本地 main 与备份标签，未强推）：" + out[-500:])
    R.step("4g 推送 GitHub main", "ok", f"{remote} → {REMOTE_URL}")

    gh = ensure_gh_auth()
    if gh:
        code, out = run([gh, "workflow", "run", WEEKLY_WORKFLOW, "--ref", MAIN, "-R", REPO_SLUG], check=False,
                        stream=False)
        R.step("4h 触发首次全平台云端编译", "ok" if code == 0 else "warn",
               f"https://github.com/{REPO_SLUG}/actions/workflows/{WEEKLY_WORKFLOW}" if code == 0 else out[-300:])
    else:
        R.step("4h 触发首次全平台云端编译", "warn",
               f"gh 未安装或未登录：已按 cron 每周一 02:00 自动编译，也可在 https://github.com/{REPO_SLUG}/actions 点 Run workflow")
    step_release_upload(gh)


def step_audit_and_sync(*, skip_push: bool) -> None:
    if not (ROOT / ".git").exists():
        raise StepFailed("当前目录不是 git 仓库")
    branch = current_branch()
    if branch != BRANCH:
        if ref_exists(BRANCH):
            git("checkout", BRANCH)
        else:
            raise StepFailed(f"当前分支 {branch}，且本地不存在 {BRANCH}")
    sanitize = load_script_module("sanitize_local_paths")
    rewritten = sanitize["run"](ROOT)  # type: ignore[operator]
    R.step("4a 本机绝对路径脱敏", "ok", f"改写 {len(rewritten)} 个文件（原件备份于 .qw-backups/sanitize-*）")

    audit = load_script_module("pre_github_sync_audit")
    untrack = audit["untrack_candidates"](ROOT)  # type: ignore[operator]
    for start in range(0, len(untrack), 100):
        git("rm", "-r", "--cached", "--quiet", "--ignore-unmatch", "--", *untrack[start:start + 100])
    R.step("4b 移出索引（文件保留在本机）", "ok", f"{len(untrack)} 个：数据库/备份/报告/超 25MB/生成物")

    stage_files = [rel for rel in STAGE2_FILES if (ROOT / rel).exists()]
    git("add", "--", *stage_files)
    sanitized: set[str] = set(rewritten)
    for backup_root in (ROOT / ".qw-backups").glob("sanitize-*"):
        for item in backup_root.rglob("*"):
            if item.is_file():
                sanitized.add(item.relative_to(backup_root).as_posix())
    sanitized_existing = sorted(rel for rel in sanitized if (ROOT / rel).is_file() and rel not in untrack)
    for start in range(0, len(sanitized_existing), 100):
        git("add", "--", *sanitized_existing[start:start + 100])
    R.facts["sanitized_files_staged"] = len(sanitized_existing)
    remote = ensure_remote()
    git("fetch", remote, MAIN, check=False, timeout=600)
    remote_main = f"{remote}/{MAIN}"
    history_range = f"{remote_main}..HEAD" if ref_exists(remote_main) else "HEAD"
    json_out = REPORT_DIR / "stage2_pre_github_sync_audit.json"
    code, out = run([sys.executable, str(ROOT / "scripts" / "pre_github_sync_audit.py"), "--include", *stage_files,
                     "--json-out", str(json_out)], check=False, label="零泄漏防火墙")
    if code != 0:
        R.facts["audit_tail"] = out[-3000:]
        raise StepFailed("零泄漏防火墙未通过：已阻断提交与上传（详见 stage2_pre_github_sync_audit.json）")
    R.step("4c 零泄漏防火墙（工作区）", "ok", out.strip().splitlines()[-1] if out.strip() else "PASS")

    if git("diff", "--cached", "--quiet", check=False)[0] != 0:
        message = "feat(stage2): Tauri v2 zero-config client, frozen muse-engine sidecar, zero-leak audit, weekly cross-platform release"
        code, out = git("commit", "-m", message, check=False)
        if code != 0 and re.search(r"user\.(name|email)|identity", out, re.I):
            git("-c", "user.name=Muse Autopilot", "-c", "user.email=muse-autopilot@users.noreply.github.com",
                "commit", "-m", message)
        elif code != 0:
            raise StepFailed(f"提交失败：{out[-600:]}")
    head = git("rev-parse", "--short", "HEAD")[1].strip().splitlines()[-1]
    R.facts["commit"] = head
    R.step("4d 提交本阶段源码", "ok", f"{BRANCH} @ {head}")

    code, out = run([sys.executable, str(ROOT / "scripts" / "pre_github_sync_audit.py"), "--history-range",
                     history_range, "--quiet"], check=False, label="待推送历史审计")
    squashed = False
    if code != 0:
        R.facts["history_audit_tail"] = out[-3000:]
        squash_clean_release(remote_main)
        squashed = True
        history_range = f"{remote_main}..HEAD" if ref_exists(remote_main) else "HEAD"
        code, out = run([sys.executable, str(ROOT / "scripts" / "pre_github_sync_audit.py"), "--history-range",
                         history_range, "--quiet"], check=False, label="待推送历史审计（Squash 后复检）")
        if code != 0:
            R.facts["history_audit_tail_after_squash"] = out[-3000:]
            raise StepFailed(f"Squash 后待推送历史仍未通过审计（{history_range}），已阻断上传（未强推）。")
    R.step("4e 待推送历史审计", "ok", history_range + ("（已自动 Squash 为单一干净提交）" if squashed else ""))

    if squashed:
        if ref_exists(MAIN):
            git("tag", "-f", BACKUP_TAG, MAIN)
            R.facts["backup_tag"] = BACKUP_TAG
        git("branch", "-f", MAIN, BRANCH)
        R.step("4f 更新默认主分支 main", "ok",
               f"main = 干净 Squash 提交 {git('rev-parse', '--short', MAIN)[1].strip()}（旧历史见 {R.facts.get('squash_backup_tag')}）")
        _push_and_trigger(remote, skip_push=skip_push)
        return

    if ref_exists(MAIN):
        git("tag", "-f", BACKUP_TAG, MAIN)
        R.facts["backup_tag"] = BACKUP_TAG
    if ref_exists(remote_main) and ref_exists(MAIN) and not is_ancestor(remote_main, MAIN) \
            and not is_ancestor(remote_main, BRANCH):
        git("checkout", MAIN)
        code, out = git("merge", "--no-edit", remote_main, check=False)
        if code != 0:
            git("merge", "--abort", check=False)
            git("checkout", BRANCH)
            raise StepFailed("远端 main 与本地历史冲突，已中止合并（未推送、未强推）；请人工处理冲突")
        git("checkout", BRANCH)
    if not ref_exists(MAIN):
        base = remote_main if ref_exists(remote_main) else BRANCH
        git("branch", MAIN, base)
    if is_ancestor(MAIN, BRANCH):
        git("branch", "-f", MAIN, BRANCH)
        R.log(f"{MAIN} 快进到 {BRANCH}")
    else:
        git("checkout", MAIN)
        code, out = git("merge", "--no-ff", "--no-edit", BRANCH, "-m",
                        f"merge {BRANCH}: verified stage-1 + stage-2 build into {MAIN}", check=False)
        if code != 0:
            git("merge", "--abort", check=False)
            git("checkout", BRANCH)
            raise StepFailed(f"合并 {BRANCH} → {MAIN} 冲突，已中止（未推送）")
        git("checkout", BRANCH)
    R.step("4f 更新默认主分支 main", "ok", f"备份标签 {BACKUP_TAG}；main = {git('rev-parse', '--short', MAIN)[1].strip()}")

    _push_and_trigger(remote, skip_push=skip_push)


# --------------------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Muse stage-2 client packaging + GitHub sync")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-push", action="store_true")
    parser.add_argument("--no-explorer", action="store_true")
    parser.add_argument("--rebuild", action="store_true", help="rebuild steps 1-3 even if the installers already exist")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    print("\n" + "=" * 62)
    print("  Muse 第二阶段一键收官：本地 exe → 中文安装包 → 零泄漏安检 → GitHub main")
    print("  使用系统 Python（不建任何 venv）、不碰生产数据库、不强推；日志写入 reports\\autopilot")
    print("=" * 62 + "\n", flush=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    R.facts["version"] = tauri_version()
    R.facts["python"] = sys.executable
    exit_code = 0
    try:
        cleanup_stale_packager_venv()
        cleanup_legacy_cargo_cache()
        ready, why = (False, "--rebuild") if args.rebuild else deliverables_ready()
        if ready and not args.skip_build:
            R.step("1-3 工具链/构建/安装包", "skip", f"已完工，跳过重复编译：{why}（强制重编请加 --rebuild）")
        else:
            info = step_toolchain()
            if args.skip_build:
                R.step("2-3 构建与安装包", "skip", "--skip-build")
            else:
                if not args.rebuild:
                    R.log(f"安装包未就绪（{why}）→ 执行完整构建")
                exe = step_local_exe(info)
                step_installer(info, exe, open_explorer=not args.no_explorer)
        step_audit_and_sync(skip_push=args.skip_push)
    except StepFailed as exc:
        R.step("中止", "fail", str(exc))
        exit_code = 1
    except Exception as exc:  # pragma: no cover - always leave a report behind
        R.step("异常", "fail", f"{type(exc).__name__}: {exc}")
        R.facts["traceback"] = traceback.format_exc()[-4000:]
        exit_code = 2
    finally:
        R.write()
    if any(s["status"] == "fail" for s in R.steps) and exit_code == 0:
        exit_code = 1
    print()
    if exit_code == 0:
        print("[完成] 安装包位于 开箱即用安装包\\ ，源码已同步 GitHub main，每周一 02:00 自动全平台编译。")
        release = R.facts.get("release")
        if isinstance(release, dict):
            print(f"[下载页] {release.get('url')}")
    else:
        print(f"[注意] 有步骤未通过，退出码 {exit_code}。请把下面的报告发给 AI 继续修复：")
    print(f"报告：{REPORT_MD.relative_to(ROOT)}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
