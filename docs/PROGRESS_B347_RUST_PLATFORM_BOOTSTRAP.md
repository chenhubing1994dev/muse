# B347 — Rust 跨平台启动策略第一步

日期：2026-11-22

## 已完成
- 新增 desktop/src-tauri/src/platform.rs，将 Python 可执行文件选择抽为纯函数。
- MUSE_PYTHON 显式覆盖优先；Windows 使用项目 venv 或 py -3.11；macOS/Linux 使用项目 .venv/bin/python3 或系统 python3。
- 现有桌面 Supervisor 统一消费解析结果，仍使用参数数组，不经过 shell。
- 新增 scripts/muse-macos-dev.sh，使用现有依赖并以 Cargo offline 模式构建。
- 新增 Android Companion 安全设计，明确 Commander 单一调度权、TLS/可信隧道、一次性配对、设备凭据撤销、权限 scope、nonce 防重放及事件续传。

## 验证
- Rust cargo test --offline：6/6 passed，其中新增平台解析单测 3 项。
- Rust cargo check --offline：exit 0。
- 初次 cargo fmt --check 发现新增模块格式不符合 rustfmt，已运行 cargo fmt 修正。
- UI 验证最终通过：svelte-check 0 errors / 0 warnings；Node TAP tests 56/56 passed；Vite production build 成功（145 modules transformed）。初次子进程未解析 npm、随后遇到 GBK/UTF-8 输出编码问题，已通过 cmd.exe 调用 npm.cmd 并设置 UTF-8 解码/输出错误替换解决。
- Rust cargo fmt -- --check、cargo test --offline（6/6）、cargo check --offline 与 cargo build --offline 均通过；通过 Python subprocess 捕获真实 stdout/stderr 与退出码，避免 PowerShell 将 native stderr 误包装成外层失败。
- 最新全仓 Python 3.11 基线（B346 前）为 2,318 tests / 0 failures / 0 errors / 1 skipped；本轮未改 Python 源码。

## 未完成与边界
- Tauri mobile entry 的 lib.rs/main.rs 拆分尚未做；现有 main.rs 含桌面窗口尺寸 API，必须在 cfg 边界内拆分，不能直接复制 Builder 入口。
- Android SDK/NDK/JDK 未安装；未构建 APK/AAB。
- macOS 未实机验收；macOS shell 脚本已用 Git Bash 执行 bash -n 语法检查通过。Windows 本轮完成 Rust debug build，但未生成安装包。
- 现有 HTTP LAN 配对不适用于公网，本轮没有放宽监听或传递本机 token。
