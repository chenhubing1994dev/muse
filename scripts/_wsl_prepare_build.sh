#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
ROOT="$HOME/work/Muse-linux-native"
echo "=== NODE TOOLCHAIN ==="
node --version
npm --version
echo "=== INSTALL LINUX NPM DEPENDENCIES ==="
(cd "$ROOT/ui-prototype" && npm ci)
echo "=== FETCH LOCKED RUST DEPENDENCIES ==="
(cd "$ROOT/desktop/src-tauri" && cargo fetch --locked)
echo "=== PREPARATION COMPLETE ==="
