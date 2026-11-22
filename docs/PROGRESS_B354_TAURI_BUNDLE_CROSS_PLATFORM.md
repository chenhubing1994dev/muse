# B354 — Tauri 桌面安装包与跨平台图标配置

日期：2026-11-23

## 本轮落地

- 新增 `scripts/generate_tauri_icons.py`，使用 Python 标准库生成统一的 Muse 深色八方星图标资源，不增加第三方依赖。
- 生成 `desktop/src-tauri/icons/` 下的 32×32、128×128、256×256、512×512 PNG，以及 PNG-backed ICO 与 ICNS 容器。
- `desktop/src-tauri/tauri.conf.json` 从 `bundle.active=false` 改为启用打包，目标设为 `all`，并显式配置 Windows ICO、macOS ICNS、Linux/通用 PNG 图标。
- 保持 Tauri v2 现有桌面/移动入口分流；本轮没有改变 Companion 配对或移动端权限逻辑。

## 验证

- 图标生成脚本运行成功，生成 6 个资源文件。
- JSON 解析与打包配置静态断言通过：`bundle.active=true`、`targets=all`、配置引用的 5 个图标全部存在。
- PNG 所有 chunk CRC、ICO PNG payload 边界、ICNS 总长度验证通过。
- `ui-prototype` Svelte 检查：0 errors / 0 warnings。
- `ui-prototype` Vite production build：149 modules transformed，成功。
- `ui-prototype` Node tests：66/66 passed。

## 尚未验证 / 阻断

- Runner 未安装/未暴露 `cargo`、`rustc`、`rustup`；本轮不能执行 Tauri config schema validation、Rust tests、Clippy 或 installer build。
- Android SDK、NDK、JDK 未配置；未生成 APK/AAB。iOS 构建需要 macOS/Xcode，当前环境不能产出或验收 iOS 包。
- `targets=all` 已配置，但 Windows MSI/NSIS、Linux DEB/RPM/AppImage、macOS DMG 仍须在各自工具链环境完成真实构建与安装验收。
- 服务器 Web Edition 尚未部署到用户域名/公网；本轮不代表公网发布。
- 移动端 `mobile.rs` 当前仍是未配对时 fail-closed 的 Companion shell；真实安全配对闭环已交由跨端 Session 审计实现。

## 边界

仅修改 Tauri bundle 配置、图标生成脚本及图标资产；没有启动真实第三方插件代码，没有修改 Web Portal/Companion API，也没有覆盖用户主目录之外的文件。
