#!/usr/bin/env python3
"""Stage a self-contained Muse Server package.

The Rust `muse-server` binary is a reverse proxy: it needs a Python Status API
upstream. A bare binary + web-portal (the old package) cannot serve anything on
a fresh machine. This packs the minimal Python project plus cross-platform
launchers that start the upstream API and then the Rust server.

Requires Python 3.11+ on the target machine; large models, runtime data and
secrets are intentionally excluded.
"""

from __future__ import annotations

import argparse
import shutil
import stat
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def copy_python_project(dest: Path) -> int:
    count = 0
    pyproject = ROOT / "pyproject.toml"
    if pyproject.is_file():
        shutil.copy2(pyproject, dest / "pyproject.toml"); count += 1
    muse_src = ROOT / "src" / "muse"
    target_src = dest / "src" / "muse"
    target_src.mkdir(parents=True, exist_ok=True)
    for pattern in ("*.py", "*.yaml"):
        for f in sorted(muse_src.glob(pattern)):
            shutil.copy2(f, target_src / f.name); count += 1
    cfg = dest / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    for example in sorted((ROOT / "config").glob("*.example.*")):
        shutil.copy2(example, cfg / example.name); count += 1
    return count


LAUNCH_SH = """#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "$0")" && pwd)"
export PYTHONPATH="$HERE/src${PYTHONPATH:+:$PYTHONPATH}"
PY="${MUSE_PYTHON:-python3}"
echo "[muse] starting Python Status API..."
PY_PID=""
cleanup() { [[ -n "$PY_PID" ]] && kill "$PY_PID" 2>/dev/null || true; }
trap cleanup EXIT
"$PY" -m muse.status_api \\
  --task-db "$HERE/data/tasks/muse.sqlite3" \\
  --progress-db "$HERE/data/progress/muse.sqlite3" \\
  --capability-db "$HERE/data/capabilities/muse.sqlite3" \\
  --agent-db "$HERE/data/agents/muse.sqlite3" \\
  --improvement-db "$HERE/data/improvements/muse.sqlite3" \\
  --port 8766 &
PY_PID=$!
mkdir -p "$HERE/data"
sleep 2
echo "[muse] starting muse-server (proxy) ..."
export MUSE_SERVER_UPSTREAM="http://127.0.0.1:8766"
export MUSE_SERVER_PORTAL_DIR="$HERE/portal"
exec "$HERE/muse-server" "$@"
"""

LAUNCH_BAT = """@echo off
setlocal
set HERE=%~dp0
set PYTHONPATH=%HERE%src;%PYTHONPATH%
if "%MUSE_PYTHON%"=="" set PYTHONPATH_PY=python
if not "%MUSE_PYTHON%"=="" set PYTHONPATH_PY=%MUSE_PYTHON%
echo [muse] starting Python Status API...
start /B "" "%PYTHONPATH_PY%" -m muse.status_api --task-db "%HERE%data\\tasks\\muse.sqlite3" --port 8766
if not exist "%HERE%data" mkdir "%HERE%data"
timeout /T 2 /NOBREAK >NUL
echo [muse] starting muse-server (proxy) ...
set MUSE_SERVER_UPSTREAM=http://127.0.0.1:8766
set MUSE_SERVER_PORTAL_DIR=%HERE%portal
"%HERE%muse-server.exe" %*
"""

README = """# Muse Server（自包含包）

本包包含：Rust `muse-server` 代理、Python 核心源码、Web Portal 静态资源与启动脚本。
目标机器需预装 **Python 3.11+**（无需安装第三方包，核心使用标准库）。

## 启动
- Linux / macOS：`./start.sh`（可用 `MUSE_PYTHON` 指定解释器）
- Windows：`start.bat`

启动脚本会先在 8766 端口拉起 Python Status API，再由 `muse-server` 代理到 8767。
浏览器打开 `http://127.0.0.1:8767/`（server 默认绑定，可用 `MUSE_SERVER_BIND` 修改）。

## 安全边界
默认只监听 loopback，单用户。公网部署需前置 TLS 反向代理并显式设置
`MUSE_SERVER_PUBLIC_URL`。本包不含模型、运行数据、密钥或第三方引擎。
玄学内容仅作灵感假设；插件执行默认关闭；AI 视频受 Commander 审批门禁约束。
"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bin", required=True, help="Built muse-server binary")
    p.add_argument("--label", required=True)
    p.add_argument("--ext", required=True, choices=["tar.gz", "zip"])
    args = p.parse_args(argv)

    stage = ROOT / "_server_stage_tmp"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    bin_path = Path(args.bin)
    if not bin_path.is_file():
        raise SystemExit(f"binary not found: {bin_path}")
    target_bin = stage / ("muse-server.exe" if str(args.bin).lower().endswith(".exe") else "muse-server")
    shutil.copy2(bin_path, target_bin)
    target_bin.chmod(target_bin.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    n = copy_python_project(stage)
    shutil.copytree(ROOT / "web-portal", stage / "portal")
    shutil.copy2(ROOT / "docs" / "GITHUB_RELEASES.md", stage / "GITHUB_RELEASES.md")

    sh = stage / "start.sh"
    sh.write_text(LAUNCH_SH, encoding="utf-8")
    sh.chmod(sh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (stage / "start.bat").write_text(LAUNCH_BAT, encoding="utf-8")
    (stage / "README.md").write_text(README, encoding="utf-8")

    out = ROOT / f"muse-server-{args.label}.{args.ext}"
    if args.ext == "zip":
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(stage.rglob("*")):
                if f.is_file():
                    zf.write(f, arcname=str(f.relative_to(stage)))
    else:
        with tarfile.open(out, "w:gz") as tf:
            for f in sorted(stage.rglob("*")):
                if f.is_file():
                    tf.add(f, arcname=str(f.relative_to(stage)))

    shutil.rmtree(stage)
    print(f"server package: {out.name} ({out.stat().st_size} bytes, {n} python files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
