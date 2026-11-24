#!/usr/bin/env python3
"""Apply Muse's per-platform bundle policy to build output.

Policy (creator decision, 2026-11-24):
- Windows: keep the NSIS .exe installer, and build a REAL portable zip that
  contains the raw muse-desktop.exe plus the Python project root it needs at
  runtime (pyproject.toml + src/muse + config examples + README). The old
  portable zip only held the installer and was not actually portable.
- Linux: keep .deb / AppImage / .rpm (x86_64 and aarch64 via native ARM runner).
- macOS: keep .dmg only; drop .app.tar.gz and .sig sidecars.
- Muse Server native packages are staged by a separate step.

This script only copies files into a staging directory; it never mutates the
Tauri source tree.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "desktop" / "src-tauri" / "target" / "release" / "bundle"
RELEASE_EXE = ROOT / "desktop" / "src-tauri" / "target" / "release" / "muse-desktop.exe"
RELEASE_BIN = ROOT / "desktop" / "src-tauri" / "target" / "release" / "muse-desktop"


def iter_files(directory: Path, suffix: str | None = None):
    if not directory.exists():
        return
    for path in sorted(directory.rglob("*")):
        if path.is_file() and (suffix is None or path.name.lower().endswith(suffix)):
            yield path


def copy_to(path: Path, stage: Path, arcname: str | None = None) -> Path:
    target = stage / (arcname or path.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.resolve() != path.resolve():
        shutil.copy2(path, target)
    return target


def copy_python_project(dest: Path) -> int:
    """Copy the minimal Python project root the desktop shell looks for."""
    count = 0
    pyproject = ROOT / "pyproject.toml"
    if pyproject.is_file():
        copy_to(pyproject, dest); count += 1
    for source in sorted((ROOT / "src" / "muse").glob("*.py")):
        copy_to(source, dest / "src" / "muse"); count += 1
    for data_file in sorted((ROOT / "src" / "muse").glob("*.yaml")):
        copy_to(data_file, dest / "src" / "muse"); count += 1
    for example in sorted((ROOT / "config").glob("*.example.*")):
        copy_to(example, dest / "config"); count += 1
    return count


def make_windows_portable(stage: Path) -> Path | None:
    exe = RELEASE_EXE if RELEASE_EXE.is_file() else next(iter_files(BUNDLE, ".exe"), None)
    if exe is None:
        exe = next(iter_files(ROOT / "desktop" / "src-tauri" / "target" / "release", ".exe"), None)
    if exe is None:
        return None

    payload = stage / "_portable_payload"
    if payload.exists():
        shutil.rmtree(payload)
    payload.mkdir(parents=True)
    copy_to(exe, payload, "muse-desktop.exe")
    n = copy_python_project(payload)
    (payload / "README.txt").write_text(
        "Muse Windows 便携版\n"
        "===================\n\n"
        "1) 双击 muse-desktop.exe 启动（需要本机已安装 Python 3.11+，并在 PATH 中）。\n"
        "2) 若桌面壳提示找不到 Muse 源码，请设置环境变量 MUSE_PROJECT_ROOT 指向本目录。\n"
        "3) 本包不含大型模型/运行数据/第三方引擎；这些信息与密钥不会随包分发。\n"
        "4) 首次运行会用 pyproject.toml + src/muse 启动本机 Status API。\n\n"
        f"随包 Python 文件数：{n}\n",
        encoding="utf-8",
    )

    portable = stage / "Muse-Windows-portable.zip"
    with zipfile.ZipFile(portable, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(payload.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=str(path.relative_to(payload)))
    shutil.rmtree(payload)
    return portable


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True)
    parser.add_argument(
        "--platform",
        required=True,
        choices=["windows", "linux", "linux-aarch64", "macos-intel", "macos-arm"],
    )
    args = parser.parse_args(argv)

    stage = Path(args.stage)
    stage.mkdir(parents=True, exist_ok=True)

    if args.platform == "windows":
        for path in iter_files(BUNDLE, ".exe"):
            copy_to(path, stage)
        portable = make_windows_portable(stage)
        print(f"windows: kept installer exe, real portable={portable.name if portable else 'none'}")
        return 0

    if args.platform in ("linux", "linux-aarch64"):
        for suffix in (".deb", ".AppImage", ".rpm"):
            for path in iter_files(BUNDLE, suffix):
                copy_to(path, stage)
        print("linux: kept deb/AppImage/rpm")
        return 0

    for path in iter_files(BUNDLE, ".dmg"):
        copy_to(path, stage)
        sig = path.with_name(path.name + ".sig")
        if sig.exists():
            copy_to(sig, stage)
    print("macos: kept dmg only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
