#!/usr/bin/env python3
"""Apply Muse's per-platform bundle policy to Tauri build output.

Policy (2026-11-24, creator decision):
- Windows: keep .exe, replace .msi with a portable .zip containing the installers/exe.
- Linux: keep .deb and .AppImage; add an aarch64-named bundle via CI matrix.
- macOS: keep .dmg only; drop .app.tar.gz and its .sig sidecars.
- Muse Server native binaries are staged by a separate step.

This script only copies/labels files into a staging directory; it never mutates
the Tauri bundle source tree.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "desktop" / "src-tauri" / "target" / "release" / "bundle"


def iter_files(directory: Path, suffix: str | None = None):
    if not directory.exists():
        return
    for path in sorted(directory.rglob("*")):
        if path.is_file() and (suffix is None or path.name.lower().endswith(suffix)):
            yield path


def copy_to(path: Path, stage: Path) -> Path:
    target = stage / path.name
    if target.resolve() != path.resolve():
        shutil.copy2(path, target)
    return target


def make_windows_portable(stage: Path) -> Path | None:
    exe = next(iter_files(BUNDLE, ".exe"), None)
    if exe is None:
        return None
    portable = stage / "Muse-Windows-portable.zip"
    with zipfile.ZipFile(portable, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(exe, arcname=exe.name)
        for sidecar in iter_files(BUNDLE, ".exe.sig"):
            archive.write(sidecar, arcname=sidecar.name)
    return portable


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--platform", required=True, choices=["windows", "linux", "linux-aarch64", "macos-intel", "macos-arm"])
    args = parser.parse_args(argv)

    stage = Path(args.stage)
    stage.mkdir(parents=True, exist_ok=True)

    if args.platform == "windows":
        for path in iter_files(BUNDLE, ".exe"):
            copy_to(path, stage)
        portable = make_windows_portable(stage)
        print(f"windows: kept exe, portable={portable.name if portable else 'none'}")
        return 0

    if args.platform in ("linux", "linux-aarch64"):
        suffixes = (".deb", ".AppImage", ".rpm")
        for suffix in suffixes:
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
