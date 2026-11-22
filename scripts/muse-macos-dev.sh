#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UI="$ROOT/ui-prototype"
TAURI="$ROOT/desktop/src-tauri"
export MUSE_PROJECT_ROOT="$ROOT"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
if [[ -n "${MUSE_PYTHON:-}" ]]; then PYTHON="$MUSE_PYTHON"
elif [[ -x "$ROOT/.venv/bin/python3" ]]; then PYTHON="$ROOT/.venv/bin/python3"
elif command -v python3.11 >/dev/null 2>&1; then PYTHON="$(command -v python3.11)"
elif command -v python3 >/dev/null 2>&1; then PYTHON="$(command -v python3)"
else echo "Python 3.11+ is required; set MUSE_PYTHON or create .venv." >&2; exit 2; fi
for tool in node npm cargo; do command -v "$tool" >/dev/null 2>&1 || { echo "$tool is required." >&2; exit 2; }; done
[[ -d "$UI/node_modules" ]] || { echo "ui-prototype/node_modules is missing; restore dependencies first." >&2; exit 2; }
export MUSE_PYTHON="$PYTHON"
echo "[1/3] Building Muse frontend"
(cd "$UI" && npm run build)
echo "[2/3] Checking Rust crate offline"
(cd "$TAURI" && cargo check --offline)
if [[ "${1:-}" == "--release" ]]; then echo "[3/3] Building release"; (cd "$TAURI" && cargo build --release --offline --features custom-protocol);
else echo "[3/3] Building debug"; (cd "$TAURI" && cargo build --offline); fi
