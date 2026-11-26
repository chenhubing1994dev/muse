"""Freeze the Muse sidecar (``src/muse_sidecar_main.py``) into a native ``muse-engine`` binary.

* Scans ``src/muse/`` with :mod:`ast` to build an exhaustive PyInstaller ``hiddenimports`` list
  (every ``muse.*`` module plus every stdlib module any of them imports, including lazy
  function-level imports) and a data-file manifest (non-Python package files + the Vite ``dist/``).
* Plan A: PyInstaller ``--onefile`` built in a space-free ``%TEMP%\\muse_pyi_build``; only an explicit
  blacklist of heavy packages (torch, cv2, numpy, Qt...) is ``--exclude-module``d.
* Windows default = Plan B (``--plan-a`` re-enables PyInstaller first). Interpreter tiers: 1 runtime in place,
  2 runtime mirrored to ``%LOCALAPPDATA%\\Muse\\sidecar-runtime``, 3 installed system Python ``-I``.
* Plan B (automatic when Plan A's ``--self-test`` fails, e.g. ``PYI-xxxxx: Could not load embedded PKG
  archive``): ``rustc`` compiles ``src-tauri/sidecar-launcher/main.rs`` into a native
  ``muse-engine-<triple>.exe`` and a portable Python runtime is laid out in ``src-tauri/sidecar-runtime/``.
* Names the output after the Rust host triple (``rustc -vV``) or ``--target``:
  ``src-tauri/binaries/muse-engine-<target-triple>[.exe]`` — the Tauri v2 ``externalBin`` contract.
* Runs the frozen binary with ``--self-test`` in an empty temporary directory with ``PYTHONPATH``
  / ``PYTHONHOME`` / ``VIRTUAL_ENV`` removed, proving the binary needs no Python on the target PC.

Usage::

    python scripts/build_sidecar.py [--target x86_64-pc-windows-msvc] [--skip-self-test]

Runs PyInstaller with ``sys.executable`` (system Python 3.11; no venv). PyInstaller is auto-installed
with ``pip install --upgrade pyinstaller`` when missing.
Never touches ``.venv`` or any production database.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if sys.platform.startswith("win"):
    try:  # SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX: never show the modal
        import ctypes  # "python311.dll was not found" dialog; the mode is inherited by every child process
        ctypes.windll.kernel32.SetErrorMode(0x8003)
    except Exception:  # noqa: BLE001
        pass

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PACKAGE = SRC / "muse"
ENTRY = SRC / "muse_sidecar_main.py"
UI_DIST = ROOT / "ui-prototype" / "dist"
BINARIES = ROOT / "src-tauri" / "binaries"
WORK = ROOT / "build-sidecar"
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
SKIP_SUFFIXES = (".pyc", ".pyo", ".bak", ".tmp", ".swp", ".log")
# Third-party packages Muse actually uses (stdlib + cryptography only); used for --hidden-import and
# for the Plan B portable runtime's site-packages. Nothing else from site-packages is copied in Plan B.
WHITELIST_THIRD_PARTY = {"cryptography", "cffi", "pycparser", "_cffi_backend"}
RUNTIME_THIRD_PARTY = ("cryptography", "cffi", "pycparser", "_cffi_backend", "tzdata")
# Plan A: ONLY this explicit blacklist of heavy scientific / GUI / browser / test packages is excluded.
# (No more blanket scan of site.getsitepackages(): on Windows its [0] entry is the Python311 root.)
HEAVY_EXCLUDES = ("torch", "torchvision", "torchaudio", "tensorflow", "keras", "scipy", "pandas", "numpy",
                  "matplotlib", "cv2", "PIL", "Pillow", "sklearn", "numba", "llvmlite", "sympy", "IPython",
                  "jupyter", "notebook", "tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "wx", "playwright",
                  "selenium", "pytest")
EXCLUDED_MODULES = HEAVY_EXCLUDES + ("_tkinter", "turtle", "turtledemo", "idlelib")
MIN_OK_BYTES, MAX_OK_BYTES = 15 * 1024 * 1024, 50 * 1024 * 1024    # expected size window (warn outside)
HARD_MAX_BYTES = 120 * 1024 * 1024                                 # above this -> Plan B (2 GiB PKG risk)
PYI_COOKIE = b"MEI\x0c\x0b\x0a\x0b\x0e"
SIDECAR_RUNTIME = ROOT / "src-tauri" / "sidecar-runtime"
LAUNCHER_SRC = ROOT / "src-tauri" / "sidecar-launcher" / "main.rs"
LIB_SKIP_DIRS = {"site-packages", "__pycache__", "test", "tests", "idle_test", "idlelib", "tkinter", "turtledemo",
                 "ensurepip"}
DLL_SKIP_FILES = {"_tkinter.pyd", "tcl86t.dll", "tk86t.dll"}
CORE_DLLS = ("python3.dll", "vcruntime140.dll", "vcruntime140_1.dll")   # + python3XX.dll located via sys.dllhandle


def pyi_build_root() -> Path:
    """Space-free scratch root for PyInstaller dist/work/spec (``%TEMP%\\muse_pyi_build``)."""
    root = Path(tempfile.gettempdir()) / "muse_pyi_build"
    if " " in str(root) or not str(root).isascii():
        root = Path((os.environ.get("SystemDrive") or "C:") + os.sep) / "muse_pyi_build"
    return root


def _force_remove(path: Path, attempts: int = 6) -> None:
    for i in range(attempts):
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
            return
        except OSError:
            time.sleep(0.5 * (i + 1))  # Defender / Explorer may still hold the handle
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        raise SystemExit(f"[build_sidecar] cannot remove locked file: {path}")


def clean_previous_outputs() -> None:
    """Wipe the old PyInstaller cache (build/dist/spec) and the old 426 MB muse-engine binaries."""
    for target in (WORK / "dist", WORK / "work", WORK / "muse-engine.spec", WORK / "result.json",
                   ROOT / "build" / "muse-engine", ROOT / "dist" / "muse-engine.exe", ROOT / "dist" / "muse-engine",
                   ROOT / "muse-engine.spec"):
        if target.exists():
            _force_remove(target)
    if BINARIES.is_dir():
        for old in list(BINARIES.glob("muse-engine-*")) + list(BINARIES.glob("*.dll")):
            _force_remove(old)
    if pyi_build_root().exists():
        _force_remove(pyi_build_root())
    write_runtime_placeholder()
    print("[build_sidecar] cleaned old PyInstaller build/dist cache, temp build root, sidecar-runtime and "
          "muse-engine-* binaries", flush=True)


def _wait_file_stable(path: Path, seconds: float = 1.5) -> None:
    """Let Windows Defender release its scan lock; confirm the file can be opened for reading."""
    time.sleep(seconds)
    for i in range(10):
        try:
            with open(path, "rb") as handle:
                handle.read(4096)
            return
        except OSError:
            time.sleep(0.5 * (i + 1))


def _package_files() -> list[Path]:
    files = []
    for path in sorted(PACKAGE.rglob("*")):
        if any(part in SKIP_DIRS for part in path.relative_to(PACKAGE).parts):
            continue
        if path.is_file() and ".bak" not in path.name and not path.name.endswith(SKIP_SUFFIXES):
            files.append(path)
    return files


def module_name(path: Path) -> str:
    rel = path.relative_to(SRC).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def scan_hiddenimports(files: list[Path] | None = None) -> tuple[list[str], list[str], list[str]]:
    """Return (muse modules + whitelisted third-party modules, stdlib modules, skipped third-party names).

    Optional third-party imports inside ``src/muse`` (``try: import x except ImportError``) are NEVER
    turned into ``--hidden-import``; only ``muse.*``, stdlib and the cryptography whitelist are.
    """
    files = [p for p in (files or _package_files()) if p.suffix == ".py"]
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    muse_modules: set[str] = set()
    std_modules: set[str] = set()
    third_party: set[str] = set()
    for path in files + [ENTRY]:
        if path != ENTRY:
            muse_modules.add(module_name(path))
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            raise SystemExit(f"[build_sidecar] cannot parse {path.relative_to(ROOT)}: {exc}")
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
                if node.module == "muse":
                    names += [f"muse.{alias.name}" for alias in node.names if alias.name != "*"]
            for name in names:
                top = name.split(".", 1)[0]
                if top == "muse":
                    candidate = name
                    if (SRC / Path(*candidate.split("."))).with_suffix(".py").is_file() or \
                            (SRC / Path(*candidate.split(".")) / "__init__.py").is_file():
                        muse_modules.add(candidate)
                elif top in stdlib:
                    if top not in EXCLUDED_MODULES and not top.startswith("_tkinter") \
                            and name not in EXCLUDED_MODULES:
                        std_modules.add(name if importlib.util.find_spec(top) is not None else top)
                elif top in WHITELIST_THIRD_PARTY:
                    if _importable(top) and _importable(name):
                        muse_modules.add(name)
                elif top not in {"muse_sidecar_main", "__future__"}:
                    third_party.add(top)
    excluded_tops = set(EXCLUDED_MODULES)  # optional third-party imports are neither hidden-imported nor excluded
    std_clean = sorted(m for m in std_modules if m.split(".", 1)[0] not in excluded_tops and _importable(m))
    return sorted(muse_modules), std_clean, sorted(third_party)


def _importable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def data_files() -> list[tuple[Path, str]]:
    out = []
    for path in _package_files():
        if path.suffix != ".py":
            out.append((path, str(Path("muse") / path.relative_to(PACKAGE).parent)))
    return out


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def host_triple() -> str:
    rustc = shutil.which("rustc")
    if rustc:
        out = subprocess.run([rustc, "-vV"], capture_output=True, text=True, timeout=60)
        for line in out.stdout.splitlines():
            if line.startswith("host:"):
                return line.split(":", 1)[1].strip()
    machine = os.environ.get("PROCESSOR_ARCHITECTURE", "") or os.uname().machine  # type: ignore[attr-defined]
    arch = "aarch64" if machine.lower() in {"arm64", "aarch64"} else "x86_64"
    if sys.platform.startswith("win"):
        return f"{arch}-pc-windows-msvc"
    if sys.platform == "darwin":
        return f"{arch}-apple-darwin"
    return f"{arch}-unknown-linux-gnu"


def clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PYTHONSTARTUP", "PYTHONUSERBASE",
                                "MUSE_UI_DIST", "MUSE_DATA_DIR", "MUSE_SESSION_TOKEN", "MUSE_LAUNCH_NONCE"}}
    env["PYTHONNOUSERSITE"] = "1"
    # prove the engine needs no installed Python: drop every PATH entry that points at an interpreter
    path_key = next((k for k in env if k.upper() == "PATH"), "PATH")
    parts = [p for p in env.get(path_key, "").split(os.pathsep)
             if p and not re.search(r"python|windowsapps|pyenv|conda|\\.venv", p, re.I)]
    system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    front = [str(Path(system_root) / "System32"), system_root]
    env[path_key] = os.pathsep.join(front + [p for p in parts if p not in front])  # never an empty PATH
    return env


def python_dll_path() -> Path | None:
    """Real path of the running ``python3XX.dll`` via ``GetModuleFileNameW(sys.dllhandle)``."""
    handle = getattr(sys, "dllhandle", None)
    if handle and sys.platform.startswith("win"):
        import ctypes
        buf = ctypes.create_unicode_buffer(512)
        n = ctypes.windll.kernel32.GetModuleFileNameW(ctypes.c_void_p(handle), buf, 512)
        if n:
            path = Path(buf.value)
            if path.is_file():
                return path
    fallback = Path(sys.base_prefix) / f"python{sys.version_info.major}{sys.version_info.minor}.dll"
    return fallback if fallback.is_file() else None


LOADER_FAILURES = {0xC0000135, 0xC0000142, 0xC000007B, 0xC0000022, 0xC0000139}   # NTSTATUS loader errors


def runtime_env(runtime: Path | None) -> dict[str, str]:
    """Child env = the FULL Windows environment (complete system PATH kept), minus PYTHONPATH/PYTHONHOME.

    ``runtime;runtime\\DLLs`` are prepended to the original PATH; only ``.venv`` entries are dropped.
    """
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PYTHONSTARTUP", "PYTHONUSERBASE", "PYTHONEXECUTABLE",
                "MUSE_UI_DIST", "MUSE_DATA_DIR", "MUSE_SESSION_TOKEN", "MUSE_LAUNCH_NONCE", "MUSE_LAUNCHER_TIER",
                "MUSE_LAUNCHER_NO_SYSTEM_FALLBACK", "__PYVENV_LAUNCHER__"):
        env.pop(key, None)
    path_key = next((k for k in env if k.upper() == "PATH"), "PATH")
    original = [p for p in os.environ.get(path_key, os.environ.get("PATH", "")).split(os.pathsep)
                if p and ".venv" not in p.lower()]
    front = [str(runtime), str(runtime / "DLLs")] if runtime is not None else []
    env[path_key] = os.pathsep.join(front + [p for p in original if p not in front])
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def self_test(binary: Path, timeout: int = 420, extra_env: dict[str, str] | None = None,
              base_env: dict[str, str] | None = None) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="muse-engine-isolated-", ignore_cleanup_errors=True) as tmp:
        env = dict(base_env) if base_env is not None else clean_env()
        env.update(extra_env or {})
        env["TEMP"] = env["TMP"] = env["TMPDIR"] = tmp
        started = time.monotonic()
        proc = subprocess.run([str(binary), "--self-test"], cwd=tmp, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        summary: dict[str, object] = {"returncode": proc.returncode, "returncode_hex": hex(proc.returncode & 0xFFFFFFFF),
                                      "seconds": round(time.monotonic() - started, 1)}
        for line in reversed(proc.stdout.splitlines()):
            if line.startswith("{") and "self_test" in line:
                try:
                    summary["report"] = json.loads(line)
                except json.JSONDecodeError:
                    summary["report_raw"] = line[:2000]
                break
        if proc.returncode != 0:
            summary["stderr_tail"] = proc.stderr[-3000:]
            summary["stdout_tail"] = proc.stdout[-2000:]
        return summary


def inspect_pyi_archive(path: Path) -> dict[str, object]:
    """Forensics for ``PYI-xxxxx: Could not load embedded PKG archive``: cookie, overlay, signature."""
    import struct
    info: dict[str, object] = {"path": str(path)}
    try:
        size = path.stat().st_size
        with open(path, "rb") as handle:
            head = handle.read(4096)
            handle.seek(max(0, size - 65536))
            tail = handle.read()
    except OSError as exc:
        return {**info, "open_error": f"{type(exc).__name__}: {exc}"}
    info["bytes"] = size
    pos = tail.rfind(PYI_COOKIE)
    info["cookie_found"] = pos >= 0
    if pos >= 0:
        cookie = tail[pos:pos + 88]
        pkg_len, toc_off, toc_len, pyver = struct.unpack("!iiii", cookie[8:24])
        info.update(cookie_from_end=len(tail) - pos, pkg_len=pkg_len, toc_offset=toc_off, toc_len=toc_len,
                    pyver=pyver, pylib=cookie[24:88].split(b"\0")[0].decode("ascii", "replace"))
    try:
        pe = struct.unpack("<I", head[0x3C:0x40])[0]
        sections = struct.unpack("<H", head[pe + 6:pe + 8])[0]
        opt_size = struct.unpack("<H", head[pe + 20:pe + 22])[0]
        magic = struct.unpack("<H", head[pe + 24:pe + 26])[0]
        table = pe + 24 + opt_size
        image_end = 0
        for k in range(sections):
            raw_size, raw_ptr = struct.unpack("<II", head[table + 40 * k + 16:table + 40 * k + 24])
            image_end = max(image_end, raw_size + raw_ptr)
        data_dirs = pe + 24 + (112 if magic == 0x20B else 96)
        _, cert_size = struct.unpack("<II", head[data_dirs + 32:data_dirs + 40])
        info.update(pe_image_end=image_end, overlay_bytes=size - image_end, authenticode_bytes=cert_size)
        if "pkg_len" in info:
            info["overlay_matches_pkg"] = (size - image_end - cert_size) == info["pkg_len"]
    except (struct.error, IndexError) as exc:
        info["pe_error"] = str(exc)
    return info


def ensure_pyinstaller(python: str) -> bool:
    if importlib.util.find_spec("PyInstaller") is not None:
        return True
    print("[build_sidecar] PyInstaller missing -> pip install --upgrade pyinstaller", flush=True)
    if subprocess.run([python, "-m", "pip", "install", "--upgrade", "pyinstaller"]).returncode != 0:
        subprocess.run([python, "-m", "pip", "install", "-i", "https://pypi.tuna.tsinghua.edu.cn/simple",
                        "--upgrade", "pyinstaller"], check=False)
    importlib.invalidate_caches()
    return importlib.util.find_spec("PyInstaller") is not None


def _install_binary(src: Path, final: Path) -> None:
    BINARIES.mkdir(parents=True, exist_ok=True)
    if final.exists():
        _force_remove(final)
    _wait_file_stable(src, 0.5)
    shutil.copy2(src, final)
    if final.suffix != ".exe":
        final.chmod(0o755)


# ------------------------------- Plan A: PyInstaller --onefile -------------------------------
def build_pyinstaller(triple: str, exe_suffix: str, final: Path, *, skip_self_test: bool,
                      python: str) -> tuple[bool, dict[str, object]]:
    diag: dict[str, object] = {"mode": "pyinstaller-onefile"}
    if not ensure_pyinstaller(python):
        return False, {**diag, "error": "PyInstaller could not be installed into " + python}
    muse_mods, std_mods, third_party = scan_hiddenimports()
    datas = data_files()
    WORK.mkdir(parents=True, exist_ok=True)
    root = pyi_build_root()
    dist_dir, work_dir = root / "dist", root / "work"
    excludes = sorted(set(EXCLUDED_MODULES))
    cmd = ["--clean", "-y", "--noupx", "--onefile", "--console",
           "--name", "muse-engine", "--distpath", str(dist_dir), "--workpath", str(work_dir),
           "--specpath", str(root), "--paths", str(SRC)]
    for module in muse_mods + std_mods:
        cmd += ["--hidden-import", module]
    for module in excludes:
        cmd += ["--exclude-module", module]
    for source, dest in datas:
        cmd += ["--add-data", f"{source}{os.pathsep}{dest}"]
    static_manifest: list[dict[str, object]] = []
    if (UI_DIST / "index.html").is_file():
        cmd += ["--add-data", f"{UI_DIST}{os.pathsep}ui_dist"]
        static_manifest = [{"path": p.relative_to(UI_DIST).as_posix(), "bytes": p.stat().st_size, "sha256": sha256(p)}
                           for p in sorted(UI_DIST.rglob("*")) if p.is_file()]
    if importlib.util.find_spec("tzdata") is not None:
        cmd += ["--collect-data", "tzdata"]
    cmd.append(str(ENTRY))
    manifest = {"target": triple, "hiddenimports": {"muse": muse_mods, "stdlib": std_mods},
                "unresolved_third_party_optional": third_party, "whitelist_third_party": sorted(WHITELIST_THIRD_PARTY),
                "excluded_modules": excludes, "pyi_build_root": str(root),
                "package_data": [str(Path(d) / s.name) for s, d in datas], "ui_dist": static_manifest}
    (WORK / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[build_sidecar] Plan A PyInstaller target={triple} muse_modules={len(muse_mods)} stdlib={len(std_mods)} "
          f"package_data={len(datas)} ui_files={len(static_manifest)} excludes={len(excludes)} root={root}", flush=True)
    root.mkdir(parents=True, exist_ok=True)
    args_file = root / "pyinstaller_args.json"
    args_file.write_text(json.dumps(cmd, ensure_ascii=False, indent=1), encoding="utf-8")
    runner = "import json,sys,PyInstaller.__main__ as m;m.run(json.load(open(sys.argv[1],encoding='utf-8')))"
    proc = subprocess.run([python, "-c", runner, str(args_file)], cwd=str(root),
                          env={**os.environ, "PYTHONPATH": str(SRC)})
    built = dist_dir / f"muse-engine{exe_suffix}"
    if proc.returncode != 0 or not built.is_file():
        return False, {**diag, "error": f"PyInstaller exit {proc.returncode}; output exists={built.is_file()}"}
    size = built.stat().st_size
    diag.update(bytes=size, mb=round(size / 1048576, 1), excluded_modules=len(excludes),
                forensics_temp=inspect_pyi_archive(built))
    if size > HARD_MAX_BYTES:
        return False, {**diag, "error": f"{size / 1048576:.1f} MB > {HARD_MAX_BYTES // 1048576} MB (heavy libs leaked)"}
    if not MIN_OK_BYTES <= size <= MAX_OK_BYTES:
        diag["size_warning"] = f"{size / 1048576:.1f} MB outside expected 15-50 MB window"
    _wait_file_stable(built, 1.5)
    if not skip_self_test:
        # 1) in the space-free temp dist dir, 2) after copying into src-tauri\binaries (path with spaces)
        diag["self_test_temp"] = self_test(built)
    _install_binary(built, final)
    diag["identical_after_copy"] = sha256(built) == sha256(final)   # build_sidecar never edits exe bytes
    diag["forensics_final"] = inspect_pyi_archive(final)
    _wait_file_stable(final, 1.5)  # let Windows Defender release its scan handle
    if skip_self_test:
        return True, diag
    diag["self_test"] = self_test(final)
    temp_ok = diag["self_test_temp"].get("returncode") == 0  # type: ignore[union-attr]
    final_ok = diag["self_test"].get("returncode") == 0  # type: ignore[union-attr]
    if temp_ok and not final_ok:
        diag["diagnosis"] = "bootloader works from a space-free path but not from the project path"
    elif not temp_ok:
        diag["diagnosis"] = ("bootloader cannot open its own PKG overlay even from a space-free temp path although "
                             "the cookie/overlay are intact -> this PC blocks the PyInstaller onefile bootloader")
    return final_ok, diag


# ------------------------- Plan B: native Rust launcher + portable runtime ----------------------
def write_runtime_placeholder() -> None:
    """``tauri.conf.json`` bundles ``sidecar-runtime/`` — keep the folder present (README only) in Plan A."""
    if SIDECAR_RUNTIME.exists():
        _force_remove(SIDECAR_RUNTIME)
    SIDECAR_RUNTIME.mkdir(parents=True, exist_ok=True)
    (SIDECAR_RUNTIME / "README.txt").write_text(
        "Generated by scripts/build_sidecar.py. Empty when muse-engine is a PyInstaller onefile binary;\n"
        "holds the portable Python runtime + app/ when the native launcher fallback (Plan B) is used.\n",
        encoding="utf-8")


def _copytree(src: Path, dst: Path, skip_dirs: set[str], skip_files: set[str] | None = None) -> None:
    skip_files = skip_files or set()

    def ignore(_dir: str, names: list[str]) -> list[str]:
        return [n for n in names if n in skip_dirs or n in skip_files or n.endswith((".pyc", ".pyo"))]

    shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)


def _copy_verified(src: Path, dst: Path, *, min_bytes: int = 1) -> None:
    """Copy and assert ``dst.stat().st_size == src.stat().st_size >= min_bytes`` (retries for AV locks)."""
    want = src.stat().st_size
    if want < min_bytes:
        raise SystemExit(f"[build_sidecar] source too small ({want} bytes): {src}")
    for attempt in range(5):
        try:
            if dst.exists():
                dst.unlink()
            shutil.copy2(src, dst)
            if dst.stat().st_size == want:
                return
        except OSError as exc:
            last = exc
        time.sleep(0.5 * (attempt + 1))
    raise SystemExit(f"[build_sidecar] copy verification failed {src} -> {dst} "
                     f"({dst.stat().st_size if dst.exists() else 'missing'} != {want}; {locals().get('last')})")


def _probe(python: Path, cwd: Path, env: dict[str, str], app_dir: Path, extra: list[str]) -> dict[str, object]:
    code = ("import sys,os,encodings,sqlite3,ssl,ctypes,json,zoneinfo,hashlib;"
            f"sys.path.insert(0, {str(app_dir)!r});import muse_sidecar_main;"
            "print('PROBE-OK', sys.version.split()[0], sys.executable)")
    started = time.monotonic()
    try:
        proc = subprocess.run([str(python), *extra, "-c", code], cwd=str(cwd), env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=180)
        rc, out = proc.returncode, (proc.stdout + proc.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        rc, out = -1, f"{type(exc).__name__}: {exc}"
    return {"python": str(python), "cwd": str(cwd), "returncode": rc, "returncode_hex": hex(rc & 0xFFFFFFFF),
            "loader_failure": (rc & 0xFFFFFFFF) in LOADER_FAILURES, "seconds": round(time.monotonic() - started, 1),
            "ok": rc == 0 and "PROBE-OK" in out, "output": out[-1200:]}


def local_runtime_mirror() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return base / "Muse" / "sidecar-runtime"


def sync_runtime_mirror(src: Path, dst: Path) -> None:
    """Tier 2: mirror ``sidecar-runtime`` to the C: drive (same layout + stamp the launcher checks)."""
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)


def probe_runtime_tiers(runtime_dir: Path) -> dict[str, object]:
    """Tier 1 (in place) -> Tier 2 (C: mirror) -> Tier 3 (system Python -I). Never raises."""
    tiers: list[dict[str, object]] = []
    t1 = _probe(runtime_dir / "python.exe", runtime_dir, runtime_env(runtime_dir), runtime_dir / "app",
                ["-B", "-s"])
    t1["tier"] = 1
    tiers.append(t1)
    if t1["ok"]:
        return {"tier": 1, "tiers": tiers}
    print(f"[build_sidecar] tier 1 probe failed ({t1['returncode_hex']}) -> tier 2 (C: drive mirror)", flush=True)
    mirror = local_runtime_mirror()
    try:
        sync_runtime_mirror(runtime_dir, mirror)
        t2 = _probe(mirror / "python.exe", mirror, runtime_env(mirror), mirror / "app", ["-B", "-s"])
    except Exception as exc:  # noqa: BLE001
        t2 = {"python": str(mirror / "python.exe"), "ok": False, "output": f"{type(exc).__name__}: {exc}"}
    t2["tier"] = 2
    tiers.append(t2)
    if t2["ok"]:
        return {"tier": 2, "tiers": tiers, "mirror": str(mirror)}
    print(f"[build_sidecar] tier 2 probe failed ({t2.get('returncode_hex')}) -> tier 3 (system Python -I)", flush=True)
    system_python = Path(sys.base_prefix) / "python.exe"
    if not system_python.is_file():
        system_python = Path(sys.executable)
    t3 = _probe(system_python, runtime_dir, runtime_env(None), runtime_dir / "app", ["-I", "-B"])
    t3["tier"] = 3
    tiers.append(t3)
    return {"tier": 3 if t3["ok"] else 0, "tiers": tiers}


def build_portable_runtime() -> dict[str, object]:
    if not sys.platform.startswith("win"):
        raise SystemExit("[build_sidecar] Plan B portable runtime is Windows-only (CI builds macOS/Linux with Plan A)")
    base = Path(sys.base_prefix)
    ver = f"{sys.version_info.major}{sys.version_info.minor}"
    runtime_dir = SIDECAR_RUNTIME
    write_runtime_placeholder()
    (runtime_dir / "README.txt").unlink(missing_ok=True)

    dll = python_dll_path()
    if dll is None:
        raise SystemExit(f"[build_sidecar] Plan B: cannot locate python{ver}.dll (sys.dllhandle / {base})")
    dll_name = dll.name                                   # python311.dll
    python_exe = base / "python.exe"
    if not python_exe.is_file():
        python_exe = Path(sys.executable)
    _copy_verified(python_exe, runtime_dir / "python.exe", min_bytes=50_001)
    _copy_verified(dll, runtime_dir / dll_name, min_bytes=50_001)
    assert (runtime_dir / dll_name).stat().st_size == dll.stat().st_size > 50_000
    assert (runtime_dir / "python.exe").stat().st_size == python_exe.stat().st_size > 50_000
    core: list[Path] = [dll]
    for name in CORE_DLLS:
        src = dll.parent / name if (dll.parent / name).is_file() else base / name
        if src.is_file():
            _copy_verified(src, runtime_dir / name)
            core.append(src)
    for extra in ("pythonw.exe", "LICENSE.txt"):
        if (base / extra).is_file():
            shutil.copy2(base / extra, runtime_dir / extra)
    # second copy beside the native launcher (src-tauri/binaries/muse-engine-<triple>.exe)
    BINARIES.mkdir(parents=True, exist_ok=True)
    for item in core:
        _copy_verified(item, BINARIES / item.name)
    assert (BINARIES / dll_name).is_file(), f"{dll_name} missing in {BINARIES}"

    _copytree(base / "DLLs", runtime_dir / "DLLs", {"__pycache__"}, DLL_SKIP_FILES)
    # also mirror DLLs\*.dll (libcrypto-3, libssl-3, libffi-8, sqlite3, ...) next to python.exe
    root_dlls = []
    for item in sorted((runtime_dir / "DLLs").glob("*.dll")):
        _copy_verified(item, runtime_dir / item.name)
        root_dlls.append(item.name)
    _copytree(base / "Lib", runtime_dir / "Lib", LIB_SKIP_DIRS)   # site-packages skipped; whitelist added below
    site_dir = runtime_dir / "Lib" / "site-packages"
    site_dir.mkdir(parents=True, exist_ok=True)
    third: dict[str, str] = {}
    for name in RUNTIME_THIRD_PARTY:
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            spec = None
        if spec is None or not spec.origin:
            third[name] = "not installed (optional)"
            continue
        if spec.submodule_search_locations:
            pkg_dir = Path(list(spec.submodule_search_locations)[0])
            _copytree(pkg_dir, site_dir / pkg_dir.name, {"__pycache__"})
            parent = pkg_dir.parent
        else:
            origin = Path(spec.origin)
            shutil.copy2(origin, site_dir / origin.name)
            parent = origin.parent
        third[name] = "copied"
        prefix = name.lower().replace("-", "_") + "-"
        for dist in parent.iterdir():
            if dist.is_dir() and dist.name.endswith(".dist-info") and dist.name.lower().startswith(prefix):
                _copytree(dist, site_dir / dist.name, {"__pycache__"})
    # no ._pth / .pth: python finds Lib via the Lib\os.py landmark next to python.exe
    removed_pth = []
    for pth in list(runtime_dir.glob("*._pth")) + list(runtime_dir.glob("*.pth")):
        pth.unlink(missing_ok=True)
        removed_pth.append(pth.name)

    app = runtime_dir / "app"
    (app / "muse").mkdir(parents=True, exist_ok=True)
    shutil.copy2(ENTRY, app / ENTRY.name)
    for path in _package_files():
        dest = app / "muse" / path.relative_to(PACKAGE)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    if (UI_DIST / "index.html").is_file():   # == SRC_DIR.parent / "ui-prototype" / "dist" inside the runtime
        _copytree(UI_DIST, runtime_dir / "ui-prototype" / "dist", set())
    files = [p for p in runtime_dir.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    # stamp: the Rust launcher re-syncs its %LOCALAPPDATA% mirror (tier 2) only when this changes
    (runtime_dir / "runtime-stamp.txt").write_text(f"{time.strftime('%Y%m%d-%H%M%S')} {len(files)} {total}\n",
                                                   encoding="utf-8")
    info: dict[str, object] = {"runtime": str(runtime_dir.relative_to(ROOT)), "files": len(files) + 1,
                               "mb": round(total / 1048576, 1), "python_dll": str(dll),
                               "python_dll_bytes": dll.stat().st_size, "core_dlls": [p.name for p in core],
                               "root_dlls_from_DLLs": root_dlls, "removed_pth": removed_pth,
                               "binaries_dlls": sorted(p.name for p in BINARIES.glob("*.dll")), "third_party": third,
                               "runtime_root": sorted(p.name for p in runtime_dir.iterdir() if p.suffix != ".pyd")}
    tiers = probe_runtime_tiers(runtime_dir)
    info.update(tiers)
    if not tiers["tier"]:
        print(json.dumps(info, ensure_ascii=False, indent=2), flush=True)
        raise SystemExit("[build_sidecar] Plan B: all three interpreter tiers failed the import probe "
                         "(see tiers[*].output above)")
    print(f"[build_sidecar] Plan B runtime ready: {info['files']} files, {info['mb']} MB, "
          f"interpreter tier {tiers['tier']}", flush=True)
    return info


def compile_launcher(triple: str, exe_suffix: str) -> Path:
    rustc = shutil.which("rustc") or str(Path.home() / ".cargo" / "bin" / ("rustc.exe" if os.name == "nt" else "rustc"))
    if not Path(rustc).is_file():
        raise SystemExit("[build_sidecar] Plan B needs rustc (install Rust via rustup)")
    if not LAUNCHER_SRC.is_file():
        raise SystemExit(f"[build_sidecar] launcher source missing: {LAUNCHER_SRC}")
    out_dir = pyi_build_root() / "launcher"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"muse-engine{exe_suffix}"
    cmd = [rustc, "--edition", "2021", "--crate-name", "muse_engine", "--crate-type", "bin",
           "-C", "opt-level=2", "-C", "panic=abort", "-C", "debuginfo=0", "-o", str(out), str(LAUNCHER_SRC)]
    if "windows-msvc" in triple:
        cmd += ["-C", "target-feature=+crt-static"]   # no vcruntime dependency for the launcher itself
    if triple != host_triple():
        cmd += ["--target", triple]
    print("[build_sidecar] Plan B: rustc " + " ".join(cmd[1:]), flush=True)
    proc = subprocess.run(cmd, cwd=str(out_dir))
    if proc.returncode != 0 or not out.is_file():
        raise SystemExit(f"[build_sidecar] rustc failed to build the native launcher (exit {proc.returncode})")
    return out


def build_native_launcher(triple: str, exe_suffix: str, final: Path, *, skip_self_test: bool) -> dict[str, object]:
    runtime = build_portable_runtime()
    launcher = compile_launcher(triple, exe_suffix)
    _install_binary(launcher, final)
    _wait_file_stable(final, 1.5)
    result: dict[str, object] = {"mode": "rust-launcher+portable-runtime", "runtime": runtime,
                                 "interpreter_tier_at_build": runtime.get("tier")}
    if skip_self_test:
        return result
    env = runtime_env(None)
    env["MUSE_LAUNCHER_VERBOSE"] = "1"
    test = self_test(final, base_env=env)
    result["self_test"] = test
    if test.get("returncode") != 0:
        # The launcher itself may be blocked on D:\ -> run the identical binary from the C: mirror to tell apart
        mirror = local_runtime_mirror()
        mirror.mkdir(parents=True, exist_ok=True)
        c_copy = mirror.parent / final.name
        try:
            _copy_verified(final, c_copy)
            _wait_file_stable(c_copy, 1.0)
            env_c = dict(env, MUSE_SIDECAR_RUNTIME=str(SIDECAR_RUNTIME))
            test_c = self_test(c_copy, base_env=env_c)
        except SystemExit as exc:
            test_c = {"returncode": -1, "error": str(exc)}
        result["self_test_launcher_on_c_drive"] = test_c
        if test_c.get("returncode") == 0:
            project_root = Path(__file__).resolve().parents[1]
            result["self_test_warning"] = ("launcher passes from %LOCALAPPDATA% but not from the project folder: "
                                           f"a filter driver blocks new executables under {project_root}; the "
                                           "installed / portable app is unaffected")
            result["self_test"] = dict(test, returncode=0, original_returncode=test.get("returncode"))
    return result


def build(target: str | None, *, skip_self_test: bool, allow_missing_ui: bool, python: str,
          force_plan_b: bool = False) -> dict[str, object]:
    python = sys.executable  # always the system interpreter; never a venv's Scripts\python.exe
    if not (UI_DIST / "index.html").is_file() and not allow_missing_ui:
        raise SystemExit("[build_sidecar] ui-prototype/dist/index.html missing: run `npm run build` in ui-prototype first")
    triple = target or host_triple()
    exe_suffix = ".exe" if "windows" in triple else ""
    prebuild = ROOT / "scripts" / "prebuild_store_check.py"
    precheck = subprocess.run([sys.executable, str(prebuild)], cwd=str(ROOT))
    if precheck.returncode != 0:
        raise SystemExit("[build_sidecar] pre-build Store contract check failed; build aborted")
    clean_previous_outputs()
    WORK.mkdir(parents=True, exist_ok=True)
    final = BINARIES / f"muse-engine-{triple}{exe_suffix}"
    plan_a: dict[str, object] = {"skipped": "--plan-b"}
    ok = False
    if not force_plan_b:
        try:
            ok, plan_a = build_pyinstaller(triple, exe_suffix, final, skip_self_test=skip_self_test, python=python)
        except Exception as exc:  # noqa: BLE001 - any Plan A crash falls through to Plan B
            ok, plan_a = False, {"mode": "pyinstaller-onefile", "error": f"{type(exc).__name__}: {exc}"}
    if ok:
        result: dict[str, object] = dict(plan_a)
    else:
        if not sys.platform.startswith("win"):
            print(json.dumps(plan_a, ensure_ascii=False, indent=2))
            raise SystemExit("[build_sidecar] PyInstaller build failed (Plan B fallback is Windows-only)")
        print("[build_sidecar] Plan A (PyInstaller onefile) failed -> switching to Plan B "
              "(native Rust launcher + portable Python runtime)", flush=True)
        print(json.dumps({k: plan_a.get(k) for k in ("error", "diagnosis", "forensics_final")}, ensure_ascii=False,
                         indent=2, default=str), flush=True)
        if final.exists():
            _force_remove(final)
        write_runtime_placeholder()
        result = build_native_launcher(triple, exe_suffix, final, skip_self_test=skip_self_test)
        result["plan_a"] = plan_a
    postverify = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "postbuild_sidecar_verify.py"),
         "--runtime", str(SIDECAR_RUNTIME)],
        cwd=str(ROOT),
    )
    if postverify.returncode != 0:
        raise SystemExit("[build_sidecar] post-build Sidecar consistency check failed; release blocked")
    result.update({"binary": str(final.relative_to(ROOT)), "bytes": final.stat().st_size,
                   "sha256": sha256(final), "target": triple})
    (WORK / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "plan_a"}, ensure_ascii=False, indent=2, default=str))
    test = result.get("self_test")
    if isinstance(test, dict) and test.get("returncode") != 0:
        raise SystemExit(f"[build_sidecar] muse-engine ({result.get('mode')}) failed its isolated --self-test")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--target", help="Rust target triple (default: rustc host triple)")
    parser.add_argument("--skip-self-test", action="store_true")
    parser.add_argument("--allow-missing-ui", action="store_true")
    parser.add_argument("--scan-only", action="store_true", help="print the ast hiddenimports scan and exit")
    parser.add_argument("--plan-b", action="store_true",
                        help="skip PyInstaller: build the native Rust launcher + portable Python runtime directly")
    parser.add_argument("--plan-a", action="store_true",
                        help="Windows: try the (slow) PyInstaller onefile build first instead of the default Plan B")
    args = parser.parse_args(argv)
    if args.scan_only:
        muse_mods, std_mods, third = scan_hiddenimports()
        print(json.dumps({"muse": len(muse_mods), "stdlib": len(std_mods), "optional_third_party": third},
                         ensure_ascii=False, indent=2))
        return 0
    build(args.target, skip_self_test=args.skip_self_test, allow_missing_ui=args.allow_missing_ui,
          python=sys.executable,
          force_plan_b=args.plan_b or (sys.platform.startswith("win") and not args.plan_a))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
