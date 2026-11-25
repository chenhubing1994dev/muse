# B123 — Rust 桌面客户端 MVP 编译与启动验收

日期：2026-11-13；项目：<MUSE_ROOT>

## 已完成与验证
- 沿用既有 Tauri 2 + Rust + Svelte 5/TypeScript + Python API 架构，不新增调度中心。
- 修复 `ui-prototype/api.js` 的 `initiativeTimeline` 语法损坏与 ID 校验。
- Svelte 检查 0 errors / 0 warnings；API 自动化测试 19/19；Vite production build 通过。
- 构建环境需显式设置 RUSTUP_HOME=D:\Tools\rustup、CARGO_HOME=D:\Tools\Cargo，并将 D:\Tools\Cargo\bin 加入 PATH；未更改全局 Rustup 配置。
- cargo fmt --check、cargo check --offline、cargo test --offline 通过；Rust crate 目前 0 个单元测试。
- cargo build --release --offline 通过。
- Release EXE：`desktop/src-tauri/target/release/muse-desktop.exe`；8,683,520 bytes；SHA-256 `6116baaf60da5fc8e929a0fb2038894824ac64596c746a11c5f814d340fd733f`。
- Release 启动 smoke test：进程保持运行 10 秒；观察到 Edge WebView2 和 `py.exe -3.11 -u -m muse.status_api` 子进程；测试后关闭桌面进程和直接子进程。

## 已具备
- Tauri 桌面窗口、本地 WebView、Rust 启动 Python 3.11 状态 API。
- 随机短期 Bearer token，通过窄 Tauri IPC 提供给 Svelte；API 仅绑定 127.0.0.1。
- UI 已包含 Agent/任务/事件、反馈、改进提案、自主灵感、知识/作品时间线和本地视频产品工作流。

## 尚未完成
- `tauri.conf.json` 的 `bundle.active` 仍为 false：本轮是可运行 Release EXE，不是 MSI/NSIS 安装包。
- 系统托盘、桌面通知、开机启动、安装升级/回滚尚未实现。
- 长期 BackendSupervisor、健康探测、异常退出自动重启和脱敏日志尚未实现。
- Rust crate 无单元测试；还需人工桌面交互验收、签名和安装包验收。

## 下一阶段
1. BackendSupervisor：启动握手、健康探测、进程退出检测、有限退避重启、退出清理。
2. 前端断线后重新获取 IPC 连接并恢复 SSE。
3. 系统托盘、最小化到托盘、退出确认。
4. Windows NSIS/MSI 打包与安装/卸载/升级验收。
5. 继续补齐 Commander 任务与审批入口的真实契约；写操作仍由 Python Commander 授权和审计。

本轮未改 Python 业务核心，未改变 Muse Commander 的唯一调度中心地位，也未重跑全量 Python 回归。