# B384/B385 — Linux 原生构建、跨平台回归与视频审批安全闭环

最新 Worker 失败诊断：`docs/PROGRESS_B390_FAILURE_DIAGNOSTICS.md`（安全记录异常类型，不持久化异常消息）。
自主运行状态修复：`docs/PROGRESS_B389_AUTONOMY_STATUS_RECOVERY.md`（停止状态不再误报 stale、运行记录审计与安全恢复说明）。
TaskQueue 租约时钟修复：`docs/PROGRESS_B388_TASK_QUEUE_LEASE_CLOCK.md`（writer-lock 时间采样、heartbeat fencing 与并发回归）。
Web Portal 产品化补充：`docs/PROGRESS_B387_PORTAL_PRODUCT_VAULT.md`（产品库、受控预览/下载与任务完成后自动刷新）。

日期：2026-11-24

## 本轮目标

为 Muse 建立可复现的 Linux 原生开发/发布路径，验证服务器 Web Portal、PWA、插件登记、研究/灵感链路与桌面客户端在 Linux 环境中的可构建性；修复本轮回归中确认的跨平台问题。此记录只描述已实际执行的验证，不将未构建的平台写成已交付。

## 已完成

### 1. Linux 原生工作区与开发脚本

- 新增/完善 `scripts/_wsl_create_native_copy.sh`：从 Windows 项目建立独立 Linux 文件系统工作副本，避免 Windows 与 Linux 的 `node_modules`、原生模块和 Cargo 构建产物混用。
- 工作副本包含 `desktop/`、`ui-prototype/`、`web-portal/`、`src/`、`tools/`、`deploy/`、`config/`、`docs/`、`tests/`、`scripts/`、`pyproject.toml` 与 `AGENTS.md`；不复制正式项目 `data/`。
- Linux 虚拟环境使用 Python 3.12；测试所需 `cryptography` 单独安装，不依赖未正确解析的 extras 路径。
- `scripts/muse-linux-dev.sh` 现在按顺序执行 Python 全仓测试、前端类型检查/测试/构建、Rust fmt/test/Clippy/check；`--release` 会构建 Debian 安装包，而不只是裸可执行文件。
- `_wsl_create_native_copy.sh` 从 Windows 项目源建立 `$HOME/work/Muse-linux-native`；`muse-linux-dev.sh` 只允许在 Linux 原生文件系统工作区构建，并拒绝在 `/mnt/<drive>` 挂载路径直接构建。

### 2. Python 与 Web/插件回归

- Linux Python 全仓：**2,475 tests passed，0 failures，0 errors，59 skipped**；`compileall` 通过。
- 全仓测试在隔离副本 `/home/muse/work/Muse-linux-native` 执行；该副本没有正式项目的 `data/` 目录，未对 `D:\\AI AGENT\\Muse\\data` 执行测试写入。
- Web Portal HTTPS 登录/会话、代理配置文档、配置路径、灵感演化 API 的深层 JSON 请求限制均有专项回归验证。
- Node 前端：类型检查通过；**66/66 tests passed**；Vite 生产构建成功（149 modules transformed）。
- PWA Service Worker 与 API 测试包含在 Node 测试集内。
- 当前共享源码快照关键回归：视频 API、Status API、TaskQueue、Plugin Platform、Watchdog **156/156 PASS**；灵感产品化、灵感演化、Commander 视频 Admission、视频配方溯源、VideoTaskWorker 与 Worker 产物复验 **83/83 PASS**。
- B383/B385 负责人在独立测试工作目录 `.muse-test-sandbox` 完成最新全量回归：**2,475 tests / 0 failures / 0 errors**（373.965s）；该次隔离测试后只读核对 canonical Initiative DB：count=4,073、`quick_check=ok`，未发现本次隔离测试造成新的正式库变更。
- B385 最终共享源码的关键专项在本会话隔离 Linux 副本复测：**156/156 + 83/83 PASS**；没有对正式 `data/` 运行这些测试。
- 数据完整性注意：B385 记录另有一次更早的并行全仓测试曾在项目根目录误写正式 Initiative/Philosophy/WebsiteCatalog 数据（22 knowledge links、66 experiences、204 philosophy links、2 pending proposals、42 website events）；该次写入未删除或回滚。随后已修复 10 个测试的 workspace 隔离，并在 sandbox 重跑通过。此处仅确认后续隔离测试没有新增正式库写入，不代表早期测试记录已清理；本会话没有删除或改写这些正式数据。

### 3. Rust/Tauri 与 Linux 安装包

- `cargo fmt -- --check`：通过。
- `cargo test --offline`：**15 passed / 0 failed**。
- `cargo clippy --offline --all-targets -- -D warnings`：通过。
- `cargo check --offline`：通过。
- `cargo tauri build --bundles deb --ci --no-sign -- --offline`：成功。
- Linux Debian 包 smoke test 从安装包提取捆绑的 Status API，并以隔离用户数据目录启动；loopback、Bearer 认证和 `/api/v1/health` 均通过。
- 生成 Linux x86_64 Debian 包：`artifacts/linux/Muse_0.1.0_amd64.deb`。
- B385 合并 Commander Admission 修复后已重新构建并替换安装包；最终 SHA-256：`cd2b20cea19411798993191e9b4281e0d15b42c6d35b53e9250ce384b7ef6be1`，文件大小 6,432,880 bytes。
- 已从 Debian 包中提取并以隔离用户数据目录启动捆绑 Python Status API；验证了源码导入、用户数据目录可写、loopback 启动、Bearer 认证与 `/api/v1/health` 成功返回 `status=ok`。
- B385 重新打包后的最终 Debian 包再次通过相同 smoke test：`BUNDLED_SOURCE_IMPORT`、`USER_DATA_DIRECTORY_WRITABLE`、`LOOPBACK_BOOTSTRAP`、`AUTHENTICATED_HEALTH_API` 全部 PASS；health 返回 `status=ok`、`api_version=1`。

### 4. 本轮修复

- `src/muse/video_product_builder.py`：并发发布同一非空版本目录时，Linux 可能返回 `ENOTEMPTY`，而 Windows 常见 `FileExistsError/EEXIST`。现在只在错误码属于目标已存在且目标目录确实存在时，复用并验证已发布版本；其他错误继续抛出。Linux 并发发布回归通过。
- `src/muse/status_api.py`：灵感演化 API 在 JSON schema 校验前增加字符串感知的 JSON 嵌套深度预检，超过 64 层返回明确的 nesting 错误；不会把字符串内部的括号误计为结构层级。原端点回归通过。
- `tests/test_open_design_local_config.py`：以 `PureWindowsPath` 解析 Windows 本地配置路径，使文件名断言在 Linux 测试宿主上仍能正确表达其 Windows 目标。
- Linux 工作副本曾因未同步最新 `web-portal/`、`docs/`、`config/` 等目录而出现测试输入陈旧；现已完善复制/同步清单，并重新通过完整回归。

### 5. 玄学灵感引擎隔离依赖验证

在三个互不共享依赖的 Python 虚拟环境中安装并核对指定版本：

- `ziwei==0.4.1`
- `qimen-rs==0.2.1`
- `kinliuren==0.1.2.9`

这只是依赖安装与版本核验，不代表三个引擎已全部接入 Muse 生产工作流。易经/紫微/奇门/大六壬仍只作为灵感假设来源，不能替代事实证据。

## 未完成 / 明确限制

- 本轮仅完成 **Linux x86_64** 原生构建与 Debian 包；没有完成 Android APK/AAB、iOS/iPadOS 应用或 macOS 包。
- 当前 Linux 环境只安装 `x86_64-unknown-linux-gnu` Rust target；未配置 Android SDK/NDK、Java、Gradle、aapt，也没有 Apple Xcode/macOS 构建环境。因此 Android/iOS/macOS 仍需独立构建机与实机验收。
- Windows 原生安装包、Android/iOS 真机、不同浏览器的 PWA 安装/离线切换尚未在本轮重新验收。
- Web Portal 尚未绑定真实公网域名、DNS、证书、防火墙与正式反向代理；没有宣称公网部署完成。
- 动态插件代码执行仍未开放；插件发现/签名登记不等于启用。沙箱、权限审批、资源限制、回滚与版本兼容仍需完成。
- B383/B385 已封堵 `POST /api/v1/video-products` 的 `prompt_to_video` 直通 Wan 生成路径：请求现在 fail closed，返回 HTTP 409 `video.commander_admission_required`；旧线程执行器内部也拒绝 AI 视频直通调用。AI 视频必须经持久化 Commander 审批与 Video Admission 后由 `VideoTaskWorker` 执行；纯本地 `local_assets` + FFmpeg 路径保留。负责人报告视频/Admission 相关专项 105/105 PASS。
- B381/B385 负责人另行完成 TaskQueue heartbeat 原子审计与租约竞态修复；`test_task_queue.py` 51/51、`test_plugin_platform.py` 33/33、`test_watchdog.py` 18/18（合计 102/102）专项通过。本会话未修改 `task_queue.py` 或 `plugin_platform.py`。

## Android build preflight (B386)

- 新增 `scripts/muse-android-preflight.sh`：只读检查 Linux 原生工作副本中的 JDK 17+、Android SDK/SDK Manager、platform-tools/ADB、Android platform/build-tools、NDK、四个常见 Android Rust target 与 Tauri CLI。
- 脚本拒绝在 WSL 的 `/mnt/<drive>` 正式项目目录运行；缺项时只报告缺失项并退出，不自动下载 SDK、不初始化 Android 工程、不生成签名密钥，也不触碰正式 `data/`。
- 在隔离 Linux 副本执行脚本：Bash 语法检查通过；preflight 正常 fail-closed，报告 JDK（java/javac）、SDK root 与四个 Android Rust target 共 7 项缺失，Rust/Cargo 与 Tauri CLI 2.10.0 可用。由于 SDK root 未配置，SDK Manager、ADB、platform/build-tools、NDK 子项尚不能继续核验；APK/AAB 未构建。
## 下一轮优先级

1. 为 Linux 构建建立固定版本 CI 与依赖缓存，并在 CI 中保留全量/专项回归与 Debian 包 smoke test。
2. 准备 Android SDK/NDK + JDK 环境，生成 APK/AAB 并完成 Android 真机验收；再由 macOS/Xcode runner 完成 iOS/macOS 构建与签名。
3. 完成公网部署演练、反向代理安全验收与 Android/iOS/PC 浏览器实机 PWA 安装、离线与恢复验收。
4. 插件执行层继续保持默认禁用，补齐受限沙箱、资源配额、审计和回滚后再开放；不允许插件绕过 Commander。
5. 持续监控 TaskQueue heartbeat、GPU lease、视频审批撤销与成本门禁边界；任何后续改动都需复验相关专项。

