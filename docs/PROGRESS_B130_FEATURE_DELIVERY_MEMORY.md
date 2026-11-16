# B130 · 功能逐项交付原则写入开发与本地记忆

日期：2026-11-13

## 用户明确决策

用户要求 Muse 的所有功能按「逐一介绍、逐一实现、逐一图文教学、逐一验收」推进，并将该要求写入程序开发路线和 Muse 本地长期记忆。

## 已完成写入

1. 新增 `docs/MUSE_FEATURE_DELIVERY_DOCTRINE.md`：定义每个功能的介绍、真实状态、图文教学、操作步骤、预期结果、边界风险、测试验收和记忆沉淀标准。
2. 更新 `docs/CAPABILITY_BACKLOG.md`：新增 B129+ 长期用户要求及模块逐项交付节奏。
3. 更新 `docs/MUSE_CONSTITUTION.md`：新增第十三条「逐项交付与可教会使用」。
4. 写入 SQLite 本地长期记忆 `data/memory/muse.sqlite3`：Project=Muse，Track=user，Kind=preference，Source=`explicit_user_instruction:2026-11-13`，Confidence=1.0，Memory ID=3，Revision=1。

## 执行约束

- 一个模块一个可验收垂直切片，不用假数据或空壳 UI 冒充功能。
- 每个模块具备真实后端/API、持久化、失败处理、可复现测试、图文手册。
- 真实截图优先；不可取得时明确标注示意图，不伪造。
- 用户事实/偏好写入 User Track；工程经验、研究与测试结果写入 Agent Track/工程进度。
- 完成一个模块后更新状态矩阵和教学，再推进下一模块。

## 后续顺序

以 B129 用户手册为现状基线：任务中心 → Research Wiki 图谱/反证工作台 → 双轨记忆中心 → Routine/提醒/Watchdog → Agent/Provider/工具/资源管理 → 设置/托盘/安装更新与实机验收。依赖审计允许调整顺序，但必须记录理由。

## 验证

- WebCodex 文件编辑返回 3 个文件全部 changed=true。
- SQLite 写入返回成功；新记忆 ID=3、Revision=1，数据库路径为 `data/memory/muse.sqlite3`。
- 本轮没有修改运行时代码；未宣称新增产品功能已经实现。