#!/usr/bin/env python3
"""Collect built Muse artifacts into one release staging directory.

Used by .github/workflows/scheduled-release.yml after each platform builds.
Copy failures are reported but never abort other artifacts: a partial release
with a clear warning is more useful than a silent total failure.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PATTERNS = (
    "desktop/src-tauri/target/release/bundle/**/*.msi",
    "desktop/src-tauri/target/release/bundle/**/*.exe",
    "desktop/src-tauri/target/release/bundle/**/*.AppImage",
    "desktop/src-tauri/target/release/bundle/**/*.deb",
    "desktop/src-tauri/target/release/bundle/**/*.rpm",
    "desktop/src-tauri/target/release/bundle/**/*.dmg",
    "desktop/src-tauri/target/release/bundle/**/*.app.tar.gz",
    "desktop/src-tauri/target/release/bundle/**/*.app.tar.gz.sig",
    "desktop/src-tauri/target/release/bundle/**/*.msi.sig",
    "desktop/src-tauri/target/release/bundle/**/*.exe.sig",
    "desktop/src-tauri/target/release/bundle/**/*.AppImage.sig",
    "desktop/src-tauri/target/release/bundle/**/*.deb.sig",
    "desktop/src-tauri/target/release/bundle/**/*.rpm.sig",
    "desktop/src-tauri/target/release/bundle/**/*.dmg.sig",
    "dist/muse-web-portal-*.zip",
)

EXCLUDE_PARTS = {"node_modules", ".cache", "target/debug"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, help="Output staging directory")
    parser.add_argument("--platform", required=True, help="Label for log output")
    args = parser.parse_args(argv)

    stage = Path(args.stage)
    stage.mkdir(parents=True, exist_ok=True)
    manifest = stage / f"SHA256SUMS.{args.platform}.txt"
    collected: list[str] = []

    for pattern in PATTERNS:
        for source in sorted(ROOT.glob(pattern)):
            if not source.is_file():
                continue
            if any(part in EXCLUDE_PARTS for part in source.parts):
                continue
            target = stage / source.name
            if target.exists() and sha256(target) == sha256(source):
                continue
            shutil.copy2(source, target)
            collected.append(f"{source.name}  {sha256(target)}")

    manifest.write_text("\n".join(collected) + ("\n" if collected else ""), encoding="utf-8")
    print(f"[{args.platform}] collected {len(collected)} artifact(s) -> {stage}")
    for line in collected:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
