# B125 · Muse Cosmic Command UI

## 目标
将桌面端从普通运营面板升级为“宇宙意识体”愿景下的文明级智能体指挥界面。这里的“意识体”是产品愿景和交互隐喻，不代表系统具有主观意识。

## 设计系统
- 视觉主题：深空星图、东方玄枢符号、克制的未来科技感。
- 主色：虚空黑 `#050914`、星云蓝 `#111b32`、冰青 `#a8e7e8`、灵玉绿 `#83d9bb`、星砂金 `#e8d2a0`、幽紫 `#9b91d8`。
- 主视觉：中心核心与三重轨道，分别隐喻感知、记忆、进化；不把动画当作真实意识指标。
- 信息架构：意识中枢、智能体舰队、灵感创作、成果宇宙、进化实验室、星轨日志。

## 已实现
- 重构桌面侧栏导航语义。
- 新增宇宙主视觉、轨道核心、状态条和产品愿景说明。
- 主视觉状态绑定现有真实 API：本地核心连接、活跃智能体数量、灵感与产品记录数量。
- 保留既有业务功能，不重写 Python 后端。
- 扩展桌面与窄屏样式，补充键盘焦点和减少动态效果支持。

## 真实性约束
- 不创建“意识分数”“自我觉醒百分比”等虚构指标。
- 未接入后端的数据不伪装成实时功能。
- 核心状态来自本地 API；连接中断时明确展示离线/过期状态。

## 验证
- Svelte check：0 errors / 0 warnings。
- Frontend tests：19/19 passed。
- Vite production build：passed。
- Playwright screenshot：未完成，不能声称视觉回归验收通过。
- Tauri Release EXE：已重打包，文件 `desktop/src-tauri/target/release/muse-desktop.exe`，大小 8,678,400 bytes；启动脚本执行成功。Runner 收尾后未观察到进程持续存活。


Playwright screenshot was not captured: the provided helper script was not present in the project and the Runner Python command alias was unavailable. This remains a visual acceptance gap.
