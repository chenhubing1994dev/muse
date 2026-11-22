#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
UI="$ROOT/ui-prototype"
TAURI="$ROOT/desktop/src-tauri"

if [[ "$(uname -s)" != Linux ]]; then
  echo "This script must run on a native Linux host." >&2
  exit 2
fi

case "$ROOT" in
  /mnt/*)
    echo "Build from a Linux-native filesystem checkout, not a WSL /mnt/<drive> path." >&2
    echo "Windows node_modules and Linux native modules must not share one directory." >&2
    exit 2
    ;;
esac

[[ -f "$ROOT/pyproject.toml" && -f "$ROOT/src/muse/status_api.py" && -d "$ROOT/web-portal" && -f "$ROOT/scripts/validate_python_suite.py" && -d "$ROOT/tests" ]] || {
  echo "Native copy is incomplete; recreate it with scripts/_wsl_create_native_copy.sh." >&2
  exit 2
}

for tool in node npm cargo pkg-config python3; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "Missing required tool: $tool" >&2
    exit 2
  }
done

NODE_VERSION="$(node -p 'process.versions.node')"
node -e 'const [major, minor] = process.versions.node.split(".").map(Number); const supported = (major === 20 && minor >= 19) || (major === 22 && minor >= 12) || major >= 24; if (!supported) process.exit(1)' || {
  echo "Node.js 20.19+ is required (or a supported newer LTS)." >&2
  exit 2
}

if [[ -n "${MUSE_PYTHON:-}" ]]; then
  PYTHON="$MUSE_PYTHON"
elif [[ -x "$ROOT/.venv/bin/python3" ]]; then
  PYTHON="$ROOT/.venv/bin/python3"
elif command -v python3.11 >/dev/null 2>&1; then
  PYTHON="$(command -v python3.11)"
else
  PYTHON="$(command -v python3)"
fi

"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || {
  echo "Python 3.11+ is required; set MUSE_PYTHON or create .venv." >&2
  exit 2
}

MISSING=()
for package in gtk+-3.0 webkit2gtk-4.1 openssl librsvg-2.0; do
  pkg-config --exists "$package" || MISSING+=("$package")
done
if (( ${#MISSING[@]} > 0 )); then
  printf 'Missing Linux development libraries: %s\n' "${MISSING[*]}" >&2
  echo "On Ubuntu/Debian install: build-essential pkg-config libwebkit2gtk-4.1-dev libgtk-3-dev libssl-dev librsvg2-dev libxdo-dev" >&2
  exit 2
fi

[[ -d "$UI/node_modules" ]] || {
  echo "ui-prototype/node_modules is missing; run npm ci from the Linux-native checkout." >&2
  exit 2
}

export MUSE_PROJECT_ROOT="$ROOT"
export MUSE_PYTHON="$PYTHON"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1

echo "Muse Linux build environment"
echo "  Node.js: $NODE_VERSION"
echo "  Python: $("$PYTHON" --version 2>&1)"
echo "  Cargo: $(cargo --version)"
echo "  Root: $ROOT"

echo "[1/9] Running Muse Python regression suite"
(cd "$ROOT" && "$PYTHON" scripts/validate_python_suite.py)

echo "[2/9] Checking frontend types"
(cd "$UI" && npm run check)

echo "[3/9] Running frontend tests"
(cd "$UI" && npm test)

echo "[4/9] Building Muse frontend"
(cd "$UI" && npm run build)

echo "[5/9] Checking Rust formatting"
(cd "$TAURI" && cargo fmt -- --check)

echo "[6/9] Running Rust tests"
(cd "$TAURI" && cargo test --offline)

echo "[7/9] Running Rust Clippy"
(cd "$TAURI" && cargo clippy --offline --all-targets -- -D warnings)

echo "[8/9] Checking Rust crate"
(cd "$TAURI" && cargo check --offline)

if [[ "${1:-}" == "--release" ]]; then
  echo "[9/9] Building Linux release executable"
  (cd "$TAURI" && cargo build --release --offline --features custom-protocol)
elif [[ $# -eq 0 ]]; then
  echo "[9/9] Building Linux debug executable"
  (cd "$TAURI" && cargo build --offline)
else
  echo "Usage: $0 [--release]" >&2
  exit 2
fi

echo "Linux desktop build checks completed."