# Muse GitHub Actions：推送即验证 + 定时发布

本仓库有两条互补的流水线：

| 流水线 | 触发 | 职责 | 是否发布 Release |
|---|---|---|---|
| [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) | 每次 push / PR 到 main（忽略纯文档改动） | 编译与质量门禁：Python 全量测试 + 编译、前端类型检查/测试/构建、Rust 桌面与 Server 的 fmt/test/clippy | 否 |
| [`.github/workflows/scheduled-release.yml`](../.github/workflows/scheduled-release.yml) | 每 3 天定时 + 手动 | 全平台构建并汇总发布到 Releases | 是 |

- **推送即验证**：每次代码推送都会触发 CI，四个 job 并行执行；同一分支的新 push 会取消尚未完成的旧 CI（`cancel-in-progress: true`），节省 runner。任一 job 失败则该次提交标记为失败。
- **定时发布**：保证即使没有新提交，也每 3 天产出一个可下载版本。发布版本号规则见下文。

## CI 验证范围与已知例外

- Python：`scripts/validate_ci_suite.py` 跑全量 unittest（约 2,494 项）+ `compileall`，并安装 `cryptography`、玄学三引擎（`ziwei`/`qimen-rs`/`kinliuren`）与 `ffmpeg` 等必需依赖。
- 前端：`npm ci` → `npm run check` → `npm test` → `npm run build` → `node --check web-portal/sw.js`。
- Rust 桌面：先构建前端（`tauri::generate_context!` 编译期需要 `ui-prototype/dist`），并装 GTK/WebKit 系统库，再 `cargo fmt/test/clippy`。
- Rust Server：`server/rust` 独立 crate 的 `cargo fmt/test/clippy`。
- **已知失败例外**：`MUSE_CI_SKIP_TESTS` 当前排除 `test_creator_inspiration_inbox_and_research_report_download`——其修复（把 `upsert_node` 的 `summary` 改为关键字参数）已在工作区但未提交。该名单精确、显式打印在日志中（当前 `MUSE_CI_SKIPPED_COUNT=1`）；对应修复提交后必须从 `ci.yml` 中移除该变量，CI 不掩盖其他任何失败。

## 发布节奏

- 每 3 天 UTC 00:00（北京时间 08:00）自动触发：`cron: 0 0 */3 * *`。
- 也可在 GitHub Actions 页面手动 `Run workflow`；勾选 `force` 可在当天 Release 已存在时强制重建。
- 并发限制为 1，同一次发布未结束前不会叠加新任务。
- 触发后先检查 `v<年.月.日>` 标签对应的 Release 是否已存在，已存在则跳过整个构建矩阵。

## 版本号

采用 CalVer：`YYYY.M.D`（无前导零，示例 `2026.11.24`），标签为 `vYYYY.M.D`。

- 由 `scripts/release_prepare.py` 根据 UTC 日期计算。
- 构建阶段会把版本写入三处 manifest：
  - `pyproject.toml`
  - `desktop/src-tauri/Cargo.toml`
  - `desktop/src-tauri/tauri.conf.json`
- 版本写入只发生在 CI 工作副本，不会自动提交回主分支；仓库源码版本保持 `0.1.0` 作为开发基线。

## 构建矩阵

| 平台 | Runner | 产物（构建成功时） |
|---|---|---|
| Windows | `windows-latest` | `.exe` + `Muse-Windows-portable.zip` 便携版（无 `.msi`） |
| Linux x86_64 | `ubuntu-22.04` | `.deb` / `.AppImage`（+ 签名文件） |
| Linux aarch64 | `ubuntu-24.04-arm`（原生 ARM runner） | `.deb` / `.AppImage`（aarch64） |
| macOS Intel | `macos-13` | 仅 `.dmg`（无 `.app.tar.gz`）— 当前临时禁用 |
| macOS Apple Silicon | `macos-14` | 仅 `.dmg`（无 `.app.tar.gz`）— 当前临时禁用 |

另有独立的 `server` job 构建 Muse Server（Rust）原生服务二进制：

| 平台 | 产物 |
|---|---|
| Linux x86_64 | `muse-server-linux-x86_64.tar.gz` |
| Linux aarch64 | `muse-server-linux-aarch64.tar.gz` |
| Windows x86_64 | `muse-server-windows-x86_64.zip` |
| macOS Intel | `muse-server-macos-intel.tar.gz` — 当前临时禁用 |
| macOS ARM | `muse-server-macos-arm.tar.gz` — 当前临时禁用 |

每个 server 包是**自包含包**（由 `scripts/stage_server_package.py` 生成）：Rust 二进制 + 完整 `src/muse/` Python 源码（217 个文件）+ `web-portal/` 静态资源 + 跨平台启动脚本（`start.sh` / `start.bat`）+ README + `.sha256`。启动脚本会先拉起本机 Python Status API（8766），再启动 Rust 代理（8767）。目标机需预装 Python 3.11+（核心仅标准库）；**不含**模型、运行数据、密钥与第三方引擎。

Windows 便携版（由 `scripts/stage_desktop_bundles.py` 生成）是真正的绿色包：裸 `muse-desktop.exe` + `pyproject.toml` + `src/muse/` + 配置样例 + README（219 个文件，约 3.7 MB），解压即用（需本机 Python 3.11+），不再是只含安装器的假便携版。

> 为什么安装包远小于 Muse 工作区：`.gitignore` 与 Tauri `resources` 只纳入 `src/muse/`（约 4 MB）+ 前端 + Rust 二进制；`data/`（数据库）、`models/`、`tts-engines/`（CosyVoice 等）、`third_party/`、`target/` 编译缓存、`.venv/` 全部排除。这是有意设计——大型资产与隐私数据不随分发包传播。

每个平台共同产出：

- Python `wheel` + `sdist`
- Web Portal 静态站点 ZIP
- `SHA256SUMS.<platform>.txt` 校验清单

构建步骤包含：Python 包构建、前端类型检查/测试/构建、Rust fmt/test/Clippy；桌面包由 `tauri-action` 构建。单个平台失败不会阻断其他平台（fail-fast 关闭）。

## 发布流程

1. `prepare` 计算版本，检查当天 Release 是否已存在。
2. `build` 矩阵并行构建四个平台，分别上传 artifacts。
3. `release` 汇总 artifacts 与校验清单，生成 `RELEASE_NOTES.md`。
4. 复用 `tauri-action` 创建的草稿 Release（不存在则新建），上传所有产物并正式发布。

发布说明开头固定写明：自动构建产物尚未逐项人工验收，测试通过不等于完成端到端发布或商业闭环。

## 权限与密钥

- Workflow 使用 `permissions: contents: write`，通过内置 `GITHUB_TOKEN` 创建/编辑 Release。
- 可选配置仓库 Secrets：
  - `TAURI_SIGNING_PRIVATE_KEY`：Tauri 更新签名私钥
  - `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`：私钥密码
- 未配置签名密钥时仍会构建 unsigned 安装包，**不会伪造签名文件**。Tauri updater JSON 未启用（`includeUpdaterJson: false`）。

## 已知边界

- 未配置 Apple 开发者 ID / 公证凭据，macOS `.dmg` 为 ad-hoc 签名，首次打开可能触发 Gatekeeper 提示。
- Windows `.exe` 与便携版未配置代码签名证书，可能出现 SmartScreen 提示。
- **macOS 构建当前临时禁用**：该 GitHub 账户没有可用的 macOS runner 配额，macOS job 会一直 queued 并阻塞 `release` job。workflow 中保留了 macos-13/macos-14 的注释配置，runner 配额恢复后取消注释即可重新启用；产物格式要求（桌面仅 `.dmg`、server tar.gz）已就绪。
- Android 手机版 / Android TV 版：已列入规划（按全平台 Rust/Tauri 路线），当前缺少 Android SDK/NDK 与 Tauri Android 构建条件，未纳入本流水线；iOS 同样未纳入。
- Muse Server 首版为单用户、本机/可信网络优先，默认绑定 `127.0.0.1:8767`；公网需前置 TLS 反向代理并显式配置 `MUSE_SERVER_PUBLIC_URL`。
- Rust 服务器首版没有内建鉴权，鉴权与业务读写全部代理给现有 Python `web_portal.py` / `status_api.py`；它不写业务 SQLite，也不是第二调度器。
- 流水线不运行全仓 Python 测试套件（约 2,475 项，耗时较长），只运行与构建直接相关的检查；完整回归仍需按 `docs/PROGRESS_B384_LINUX_NATIVE_CROSS_PLATFORM.md` 在开发环境执行。
- 若某个平台构建失败，Release 会只包含成功平台的产物；发布说明中的“若对应平台构建成功”即指此情况。

## 手动验证

推送后可在 Actions 页面用 `workflow_dispatch` 手动触发一次，并在 Release 页面确认：

- 标签名是否符合 `vYYYY.M.D`
- 各平台产物是否齐全
- `ALL_SHA256SUMS.txt` 是否包含每个平台的清单
- Release 是否为 published 而非 draft
