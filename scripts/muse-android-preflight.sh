#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$(uname -s)" != Linux ]]; then
  echo "Android preflight must run in the Linux-native Muse workspace." >&2
  exit 2
fi
case "$ROOT" in
  /mnt/*)
    echo "Refusing Android build preflight from a WSL /mnt/<drive> checkout." >&2
    echo "Run scripts/_wsl_create_native_copy.sh, then run this script in $HOME/work/Muse-linux-native." >&2
    exit 2
    ;;
esac

failures=0
check_path() {
  local label="$1" path="$2"
  if [[ -e "$path" ]]; then
    printf 'PASS  %-24s %s\n' "$label" "$path"
  else
    printf 'MISS  %-24s %s\n' "$label" "$path"
    failures=$((failures + 1))
  fi
}
check_command() {
  local label="$1" command_name="$2"
  if command -v "$command_name" >/dev/null 2>&1; then
    printf 'PASS  %-24s %s\n' "$label" "$(command -v "$command_name")"
  else
    printf 'MISS  %-24s %s not found in PATH\n' "$label" "$command_name"
    failures=$((failures + 1))
  fi
}

echo "Muse Android build preflight"
echo "Workspace: $ROOT"
check_command "Java runtime" java
check_command "Java compiler" javac
check_command "Rust toolchain" cargo

if command -v java >/dev/null 2>&1; then
  java_version="$(java -version 2>&1 | awk -F '"' '/version/ {print $2; exit}')"
  java_major="${java_version%%.*}"
  if [[ "$java_major" == 1 ]]; then java_major="$(printf '%s' "$java_version" | cut -d. -f2)"; fi
  if [[ "$java_major" =~ ^[0-9]+$ ]] && (( java_major >= 17 )); then
    printf 'PASS  %-24s Java %s\n' "JDK version" "$java_version"
  else
    printf 'MISS  %-24s Java 17+ required (detected %s)\n' "JDK version" "${java_version:-unknown}"
    failures=$((failures + 1))
  fi
fi

SDK_ROOT="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-}}"
if [[ -n "${ANDROID_HOME:-}" && -n "${ANDROID_SDK_ROOT:-}" && "$ANDROID_HOME" != "$ANDROID_SDK_ROOT" ]]; then
  echo "MISS  ANDROID SDK vars       ANDROID_HOME and ANDROID_SDK_ROOT point to different directories"
  failures=$((failures + 1))
fi
if [[ -z "$SDK_ROOT" ]]; then
  echo "MISS  Android SDK root       set ANDROID_HOME or ANDROID_SDK_ROOT"
  failures=$((failures + 1))
else
  check_path "Android SDK root" "$SDK_ROOT"
  SDKMANAGER="${ANDROID_SDKMANAGER:-$SDK_ROOT/cmdline-tools/latest/bin/sdkmanager}"
  ADB="${ANDROID_ADB:-$SDK_ROOT/platform-tools/adb}"
  check_path "SDK manager" "$SDKMANAGER"
  check_path "ADB platform tools" "$ADB"
  if [[ -d "$SDK_ROOT/platforms" ]] && find "$SDK_ROOT/platforms" -mindepth 2 -maxdepth 2 -name android.jar -print -quit | grep -q .; then
    echo "PASS  Android platform       android.jar found"
  else
    echo "MISS  Android platform       install at least one SDK platform (android.jar)"
    failures=$((failures + 1))
  fi
  if [[ -d "$SDK_ROOT/build-tools" ]] && find "$SDK_ROOT/build-tools" -mindepth 1 -maxdepth 1 -type d -print -quit | grep -q .; then
    echo "PASS  Android build-tools    installed"
  else
    echo "MISS  Android build-tools    no build-tools version found"
    failures=$((failures + 1))
  fi
  NDK_ROOT="${ANDROID_NDK_HOME:-}"
  if [[ -z "$NDK_ROOT" && -d "$SDK_ROOT/ndk" ]]; then
    NDK_ROOT="$(find "$SDK_ROOT/ndk" -mindepth 1 -maxdepth 1 -type d | sort -V | tail -n 1)"
  fi
  if [[ -n "$NDK_ROOT" && -x "$NDK_ROOT/ndk-build" ]]; then
    check_path "Android NDK" "$NDK_ROOT"
  else
    echo "MISS  Android NDK           install an NDK under $SDK_ROOT/ndk and set ANDROID_NDK_HOME if needed"
    failures=$((failures + 1))
  fi
fi

if command -v rustup >/dev/null 2>&1; then
  installed_targets="$(rustup target list --installed 2>/dev/null || true)"
  for target in aarch64-linux-android armv7-linux-androideabi i686-linux-android x86_64-linux-android; do
    if grep -Fxq "$target" <<<"$installed_targets"; then
      printf 'PASS  %-24s %s\n' "Rust Android target" "$target"
    else
      printf 'MISS  %-24s %s\n' "Rust Android target" "$target"
      failures=$((failures + 1))
    fi
  done
fi

if command -v cargo >/dev/null 2>&1 && cargo tauri --version >/dev/null 2>&1; then
  printf 'PASS  %-24s %s\n' "Tauri CLI" "$(cargo tauri --version 2>&1 | head -n 1)"
else
  echo "MISS  Tauri CLI              cargo-tauri v2 is not available"
  failures=$((failures + 1))
fi

if (( failures > 0 )); then
  echo
  echo "Preflight blocked: $failures prerequisite check(s) missing."
  echo "No SDK packages were installed, no Android project was initialized, and no release signing was performed."
  exit 2
fi

echo
echo "Preflight passed. Next: inspect generated Android project configuration, then run a debug build and device security tests."
