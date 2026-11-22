#!/usr/bin/env bash
set -euo pipefail

SOURCE="/mnt/d/AI AGENT/Muse"
DEST="$HOME/work/Muse-linux-native"

[[ -d "$SOURCE/desktop/src-tauri" ]]
[[ -d "$SOURCE/ui-prototype" ]]
[[ -d "$SOURCE/src" ]]
if [[ -e "$DEST" ]]; then
  echo "Refusing to overwrite existing native work copy: $DEST" >&2
  exit 3
fi

mkdir -p "$DEST"
rsync -a --exclude='target/' "$SOURCE/desktop/" "$DEST/desktop/"
rsync -a --exclude='node_modules/' --exclude='dist/' "$SOURCE/ui-prototype/" "$DEST/ui-prototype/"
rsync -a "$SOURCE/web-portal/" "$DEST/web-portal/"
rsync -a --exclude='__pycache__/' --exclude='.pytest_cache/' "$SOURCE/src/" "$DEST/src/"
cp "$SOURCE/pyproject.toml" "$DEST/pyproject.toml"
rsync -a --exclude='__pycache__/' --exclude='.pytest_cache/' "$SOURCE/docs/" "$DEST/docs/"
cp "$SOURCE/AGENTS.md" "$DEST/AGENTS.md"
rsync -a --exclude='__pycache__/' --exclude='.pytest_cache/' "$SOURCE/tests/" "$DEST/tests/"
rsync -a --exclude='__pycache__/' --exclude='.pytest_cache/' "$SOURCE/scripts/" "$DEST/scripts/"
mkdir -p "$DEST/scripts"
cp "$SOURCE/scripts/muse-linux-dev.sh" "$DEST/scripts/muse-linux-dev.sh"
chmod +x "$DEST/scripts/muse-linux-dev.sh"

echo "=== NATIVE COPY ==="
du -sh "$DEST"
echo "=== CARGO LOCKFILES ==="
find "$DEST/desktop" -maxdepth 4 -name Cargo.lock -print
echo "=== CREATE PYTHON 3.12 VENV ==="
uv venv "$DEST/.venv" --python 3.12
echo "NATIVE_COPY=$DEST"
