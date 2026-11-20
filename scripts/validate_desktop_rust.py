from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUST = ROOT / "desktop" / "src-tauri"
ENV = os.environ.copy()
ENV["RUSTUP_HOME"] = r"D:\Tools\rustup"
ENV["CARGO_HOME"] = r"D:\Tools\Cargo"
CARGO = Path(ENV["CARGO_HOME"]) / "bin" / "cargo.exe"
ENV["PATH"] = str(Path(ENV["CARGO_HOME"]) / "bin") + os.pathsep + ENV.get("PATH", "")
COMMANDS = (
    ("Rust formatting", [str(CARGO), "fmt", "--", "--check"]),
    ("Tauri Rust tests (offline)", [str(CARGO), "test", "--offline"]),
    ("Tauri release build (offline)", [str(CARGO), "build", "--release", "--offline", "--features", "custom-protocol"]),
)

for label, command in COMMANDS:
    print(f"[{label}] {' '.join(command)}", flush=True)
    result = subprocess.run(
        command,
        cwd=RUST,
        env=ENV,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n", flush=True)
    if result.returncode:
        print(f"{label} failed with exit code {result.returncode}", flush=True)
        raise SystemExit(result.returncode)

print("MUSE_TAURI_RUST_VALIDATION_OK", flush=True)
