# B354 — Linux 桌面端构建基线

日期：2026-11-22

## 本轮范围

Linux 纳入 Muse 桌面端正式支持矩阵。此 Session 仅维护 Rust/Tauri/Companion 范围；Web Portal、PluginHub 与 `web-portal/*` 由专门 Web Edition Session 维护，本轮没有修改其拥有文件。

## 已完成

- 确认 `desktop/src-tauri/src/platform.rs` 已有 Linux Python 解析分支：优先项目 `.venv/bin/python3`，否则使用 `python3`，不调用 Windows `py -3.11` Launcher。
- 将 `main.rs` 的 Windows subsystem 属性限定为 Windows Release 构建，避免 Linux Release 构建错误继承 Windows 专属属性。
- 新增三个 Linux resolver 单元测试，覆盖项目虚拟环境优先级、系统 Python fallback 与 `MUSE_PYTHON` 显式覆盖。
- 新增 `scripts/muse-linux-dev.sh`：
  - 只允许 Linux 原生文件系统构建；拒绝 WSL `/mnt/<drive>`，防止 Windows/Linux 原生 `node_modules` 混用。
  - 检查 Node.js 20.19+、Python 3.11+、npm、Cargo、pkg-config。
  - 检查 GTK 3、WebKitGTK 4.1、OpenSSL、librsvg 开发库。
  - 缺依赖时给出 Ubuntu/Debian 安装包提示。
  - 构建 UI，执行 Rust fmt/test/check，并生成 debug 或 release 可执行文件。
  - 不安装依赖、不联网、不更改系统设置。

## 验证证据

- Windows Rust host 上运行 Linux resolver 单元测试：**13/13 passed**，覆盖 `.venv/bin/python3` 优先、系统 `python3` fallback 与 `MUSE_PYTHON` 显式覆盖。这验证目标平台解析逻辑，不等同 Linux 原生编译。
- Windows Rust host 上 `cargo fmt -- --check`、`cargo clippy --offline -- -D warnings`、`cargo check --offline`、`cargo build --offline` 全部通过；Clippy 0 warnings。
- 修改后 Windows Release 构建 `cargo build --release --offline --features custom-protocol` 通过，确保 Windows 桌面构建未回归。
- WSL2 Ubuntu 已探测到，但当前环境只有 Node.js 20.20.2、Python 3.10.12；没有 Cargo、pkg-config、GTK 3、WebKitGTK 4.1、OpenSSL/librsvg 的 Linux 开发依赖。
- Windows Rust toolchain 当前只安装 `x86_64-pc-windows-msvc` target，没有 `x86_64-unknown-linux-gnu`；本轮未安装额外 target 或系统包。
- WSL2 Bash `bash -n` 语法检查通过；执行脚本时正确命中 `/mnt/<drive>` 防护并以预期 exit 2 退出。
- 因此没有在 WSL 中执行 Linux 原生 Rust 编译或 Tauri 启动。
- 没有生成 `.deb`、AppImage 或 RPM。

## 打包阻塞与安全决定

- 当前 `tauri.conf.json` 的 `bundle.active=false`。
- `desktop/src-tauri/icons` 目前只有 `icon.ico`，缺少 Linux 打包通常需要的 PNG 图标资产。
- 当前环境没有 `cargo-tauri` CLI。
- 本轮不启用一个无法验证的 Linux bundle 配置，避免误导用户或破坏现有 Windows 构建设置。
- Linux 打包下一步需要：确认/生成规范 PNG 图标、安装 Tauri CLI、在 Linux 原生环境完成 deb/AppImage/RPM 构建与安装启动验收，再增加独立 Linux bundle overlay。

## 下一步

1. 在 Linux 原生 checkout/Runner 安装 Python 3.11+、Rust stable、Node 20.19+ 与 Tauri Linux 系统依赖，执行本脚本。
2. 准备 Linux PNG 图标与发行版元数据，添加独立 Tauri Linux bundle 配置。
3. 构建并安装 deb/AppImage/RPM，验证桌面启动、Supervisor、loopback API、设备凭据与退出清理。
4. 将 Linux 构建纳入持续集成；浏览器/PWA 则继续复用同一 Server Web Portal 与 Commander API。
