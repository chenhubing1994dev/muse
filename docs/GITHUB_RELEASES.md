# Muse 自动跨平台发布（GitHub Actions）

发布流水线：[`.github/workflows/scheduled-release.yml`](../.github/workflows/scheduled-release.yml)

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
| Windows | `windows-latest` | `.msi` / `.exe`（+ 签名文件，若配置了 Tauri 签名密钥） |
| Linux | `ubuntu-22.04` | `.deb` / `.AppImage` / `.rpm`（+ 签名文件） |
| macOS Intel | `macos-13` | `.dmg` / `.app.tar.gz`（+ 签名文件） |
| macOS Apple Silicon | `macos-14` | `.dmg` / `.app.tar.gz`（+ 签名文件） |

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

- 未配置 Apple 开发者 ID / 公证凭据，macOS 包为 ad-hoc 签名，首次打开可能触发 Gatekeeper 提示。
- Windows `.msi` / `.exe` 未配置代码签名证书，可能出现 SmartScreen 提示。
- Android APK/AAB、iOS 应用未纳入本流水线：当前环境缺少 Android SDK/NDK 与 Xcode/macOS 签名条件（见 `docs/PROGRESS_B384_LINUX_NATIVE_CROSS_PLATFORM.md`）。这里的“全平台”指 Muse 当前具备构建条件的 Windows / Linux / macOS 桌面平台 + Python/Web 包。
- 流水线不运行全仓 Python 测试套件（约 2,475 项，耗时较长），只运行与构建直接相关的检查；完整回归仍需按 `docs/PROGRESS_B384_LINUX_NATIVE_CROSS_PLATFORM.md` 在开发环境执行。
- 若某个平台构建失败，Release 会只包含成功平台的产物；发布说明中的“若对应平台构建成功”即指此情况。

## 手动验证

推送后可在 Actions 页面用 `workflow_dispatch` 手动触发一次，并在 Release 页面确认：

- 标签名是否符合 `vYYYY.M.D`
- 各平台产物是否齐全
- `ALL_SHA256SUMS.txt` 是否包含每个平台的清单
- Release 是否为 published 而非 draft
