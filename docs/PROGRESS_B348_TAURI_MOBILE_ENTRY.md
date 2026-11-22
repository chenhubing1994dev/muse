# B348 — Tauri 桌面 / Mobile Companion 入口拆分

日期：2026-11-22

## 已落地
- Cargo 新增 `muse_desktop_lib` Library target，crate types 为 `staticlib`、`cdylib`、`rlib`，为 Tauri mobile shared-library 构建提供标准产物。
- 原桌面 Rust 入口重命名为 `desktop.rs`；原有 Python API 启动、Supervisor、loopback Bearer 健康检查、桌面窗口尺寸/关闭逻辑保留在桌面模块。
- `src/lib.rs` 使用 `#[cfg_attr(mobile, tauri::mobile_entry_point)]` 作为共享入口；桌面二进制 `src/main.rs` 仅调用 `muse_desktop_lib::run()`。
- 新增 `mobile.rs`：Mobile Companion 不启动桌面 Python 子进程；未配对时 `get_status_connection` 明确返回未配对错误；新增 `is_mobile_companion` 命令。
- WindowChrome 调用平台能力识别命令，移动端不渲染桌面窗口按钮和拖动/缩放行为。App 启动时识别 Companion 未配对状态，不启动无意义的轮询/SSE 重试；网关遥测显示“等待安全配对”，不再误报为 127.0.0.1 Bearer。

## 验证
- Rust `cargo fmt -- --check`、`cargo test --offline`（9 tests，包含 Mobile Companion 未配对 fail-closed、Windows/macOS venv 优先级测试）、`cargo check --offline`、`cargo build --offline`、`cargo check --release --offline`：通过。
- Rust Clippy `cargo clippy --offline --all-targets -- -D warnings`：通过。先修复了 3 项现存 lint（窗口几何 tuple 类型别名、两处尺寸 `clamp`），之后 0 warnings；新增 Windows/macOS venv 优先级单测后再次复验通过。
- Svelte check：0 errors / 0 warnings。
- UI TAP tests：56/56 passed。
- Vite production build：145 modules transformed，build passed。
- Windows Release 二进制实际构建成功：`desktop/src-tauri/target/release/muse-desktop.exe`，9,012,736 bytes；SHA-256 `d7ae0fcc6292a4d68cbb86c0dd7b1f14a7a3927814fa7c523e7821106220893b`。
- Python 全仓回归（Python 3.11）：**2,319 tests / 0 failures / 0 errors / 1 skipped**，`compileall` PASS，Job `wc_job_7WXqacjoM-wVzMkv` exit 0。
- Mobile UI 状态更新后的复验：Svelte check 0 errors / 0 warnings；UI tests 56/56；Vite build 145 modules transformed，全部通过。

## 未完成与限制
- 当前机器未安装 Android SDK/NDK/JDK/ADB，未生成 APK/AAB，未在 Android 设备实测。
- 尚未实现 Host 地址录入、一次性配对码验证、TLS/可信隧道校验、设备级凭据签发/撤销、事件游标续传；当前移动端只提供安全的未配对占位状态。
- macOS 未在 macOS 主机编译或签名；Windows 构建通过不等于 macOS/Android 目标构建通过。
- Clippy component 已安装到现有 D 盘 Rust toolchain；没有安装 Android SDK/NDK/JDK，也没有更改系统级环境变量。
- 现有 Android Companion 安全要求详见 `docs/ANDROID_COMPANION_SECURITY_DESIGN.md`。不得将 loopback bearer token 复用于移动端。
- `docs/PROGRESS.md` 本轮未追加：编辑器检测到该历史文件混用 LF/CRLF 并拒绝事务性编辑；为避免整文件换行重写风险，保留原文件，使用本 B348 独立报告记录本轮状态。
