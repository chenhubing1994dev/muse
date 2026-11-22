#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"

if ! command -v rustup >/dev/null 2>&1; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -o /tmp/muse-rustup-init.sh
  sh /tmp/muse-rustup-init.sh -y --profile minimal
fi
source "$HOME/.cargo/env"
rustup toolchain install stable --profile minimal
rustup default stable
rustup component add rustfmt clippy

NODE_OK=0
if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
  node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (!((major === 20 && minor >= 19) || (major === 22 && minor >= 12) || major >= 24)) process.exit(1)'
  npm --version >/dev/null
  NODE_OK=1
fi
if [[ "$NODE_OK" -ne 1 ]]; then
  export NVM_DIR="$HOME/.nvm"
  if [[ ! -s "$NVM_DIR/nvm.sh" ]]; then
    curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
  fi
  # shellcheck disable=SC1091
  source "$NVM_DIR/nvm.sh"
  nvm install 20.20.2
  nvm alias default 20.20.2
fi

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.12

echo "=== TOOLCHAIN VERSIONS ==="
rustc --version
cargo --version
node --version
npm --version
uv --version
uv python find 3.12
