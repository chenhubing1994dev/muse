# B353 — Tauri 桌面后端数据库路径修正

日期：2026-11-22

## 本轮修复

- 只读审计发现 `desktop/src-tauri/src/desktop.rs` 启动 `muse.status_api` 时，`--progress-db` 错误地复用了 `data/tasks/muse.sqlite3`，使 TaskQueue 与 ProgressEventStore 指向同一个 SQLite 文件。
- 修正为独立路径常量：
  - TaskQueue：`data/tasks/muse.sqlite3`
  - ProgressEventStore：`data/progress/muse.sqlite3`
  - CapabilityRegistry：`data/capabilities/muse.sqlite3`
- 新增 Rust 单元测试 `desktop_uses_distinct_task_progress_and_capability_databases`，断言三个路径精确且互不相同。
- ProgressEventStore 构造函数会创建数据库父目录，因此 `data/progress/` 不存在时可按现有存储行为创建；未修改 Python Status API 或其他 Session 的文件。

## 本轮验证

- Python 静态契约检查：9 项全部通过，确认启动参数引用正确的独立路径常量。
- `git diff --check -- desktop/src-tauri/src/desktop.rs`：通过。
- 当前 Runner 环境找不到 `cargo` / `rustc`，常见 `.cargo/bin/cargo.exe` 路径也不存在；Android SDK、NDK、JDK 环境变量未配置。因此本轮新增 Rust 单测尚未执行，不能宣称 Rust 编译或 Android/iOS 构建通过。
- Python 3.11 全仓回归：2,385 tests / 0 failures / 0 errors / 1 skipped。
- Svelte 检查：0 errors / 0 warnings；Vite production build：149 modules transformed，成功。
- Node 全套测试现已由跨平台 UI Session 修复并复验：`ui-prototype` **66/66 passed**；PWA Service Worker 专项 5/5 通过。此前记录的 `api.test.js` 语法错误已不再复现。

## 未完成与边界
- 必须在具备 Rust toolchain 的 Windows Runner 上执行 `cargo fmt -- --check`、`cargo test --offline`、Clippy 与 release build，确认本轮 Rust 修改。
- Android/iOS SDK 与设备实测仍未完成；未生成 APK/AAB 或 iOS 包。
- B353 本身只纠正桌面后端数据库路径；B354 已补上桌面 bundle 配置与图标资产，但因 Rust 工具链缺失，仍未完成安装包构建或发布。- 未部署公网、未合并/覆盖主项目文件。
