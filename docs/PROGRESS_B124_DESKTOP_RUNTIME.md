# B124 — Muse Windows 桌面运行链路修复与实机验收

日期：2026-11-13
项目：D:\AI AGENT\Muse

## 本轮修复

1. 修复 Tauri 生产构建仍引用 Vite 开发地址 http://127.0.0.1:1420 的根因：Cargo package 增加 custom-protocol feature，并默认启用；生产 EXE 使用 Tauri 内嵌前端资源，不再依赖开发服务器。
2. 保留 frontendDist = ../../ui-prototype/dist，由 scripts/Muse-Desktop.ps1 先运行 npm run build，再用已安装的 D:\Tools Rust toolchain 编译 Release EXE。
3. 新增 scripts/Muse-Desktop.ps1 与 scripts/Install-Muse-Desktop-Shortcut.ps1。快捷方式 Muse.lnk、Muse-Desktop.lnk 均指向桌面客户端构建/启动器；旧浏览器控制台由原 Muse-Start.ps1 独立保留。
4. 修复前端刷新与 SSE 断线逻辑：失效时清空旧 API client，通过 Rust IPC 重新取得新端口与短期 token。
5. Rust BackendSupervisor 已实现启动握手、认证健康探测、子进程退出检测、连续健康失败后重启、1/2/4/8/16/30 秒有限退避，以及桌面退出时清理子进程。
6. Rust 单元测试覆盖重启退避、bootstrap loopback/token 校验、健康响应结构。

## 实际验收证据

- npm run check：0 errors / 0 warnings。
- npm test：19/19 passed。
- npm run build：Vite production build passed。
- cargo fmt -- --check：passed。
- cargo test --offline：3/3 passed。
- cargo build --release --offline --features custom-protocol：passed。
- 完整 Python 回归：py -3.11 -m unittest discover -s tests -v，1114 tests passed。
- 实际运行 scripts/Muse-Desktop.ps1：前端构建、Rust Release 编译、快捷方式创建、桌面进程启动全部成功。
- Windows 实机进程观察：桌面主窗口标题 MUSE · 灵枢调度中心；WebView2 子进程正常；Python muse.status_api 子进程正常；没有要求 1420 端口监听。
- 后端自愈测试：运行中强制结束 Python API 子进程，Supervisor 在测试窗口内拉起新 PID；桌面主窗口保持运行。
- 关闭桌面窗口后，Supervisor 完成 Python API 子进程清理；未发现遗留 muse.status_api 进程。
- 快捷方式检查：桌面 Muse.lnk 与 Muse-Desktop.lnk 均指向 scripts/Muse-Desktop.ps1。

## 构建产物

- desktop/src-tauri/target/release/muse-desktop.exe- 最终文件大小：8,676,352 bytes；SHA-256：447EEBC8419E1196FBF8DB11819BB90DF16E4EADDBB1E4560F2D421C25253CD5。
- 构建命令：cargo build --release --offline --features custom-protocol
- Python 3.11 仍是本地后端运行时要求；启动器会调用系统 py -3.11。
- UI 依赖使用项目现有 ui-prototype/node_modules；启动器不执行 npm install，也不自动下载依赖。

## 尚未宣称完成的事项

- tauri.conf.json 的 bundle.active 仍为 false；当前交付的是 Release EXE + 一键构建启动脚本 + Windows 桌面快捷方式，不是 MSI/NSIS 安装包。
- 系统托盘、最小化到托盘、桌面通知、开机启动、自动升级/回滚尚未实现。
- 本轮验证了桌面窗口、WebView2、Python API 子进程启动、进程异常退出自动重启和关闭清理；尚未通过人工逐项点击每个业务面板完成全部交互验收。
- 视频生成能力仍以 ComfyUI/Wan 真实运行环境和成片验收为准，不因桌面端可启动而标记为视频功能完工。

## 下一步

1. 在用户真实桌面确认窗口内容和各面板操作，必要时补充 UI 自动化/截图验收。
2. 评估并完成 Windows MSI/NSIS 安装包。
3. 增加托盘、通知、开机启动与升级/回滚。
4. 继续完善 Commander 任务/审批入口及研究—知识—灵感—创作闭环的真实端到端验收。
