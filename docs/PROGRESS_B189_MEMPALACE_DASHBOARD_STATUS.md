# B189｜MemPalace 只读健康 API 与控制台面板

日期：2026-11-19

## 已实现

- 新增 loopback、Bearer-authenticated GET `/api/v1/mempalace/status`，挂接现有 Status API；不增加新服务、不改变 Commander 调度边界。
- 从 `config/muse.toml` 与既有环境变量读取启用/模型/设备/Palace 配置状态；仅报告本地 Palace 目录和 `chroma.sqlite3` 文件是否存在。目录存在不代表 MCP daemon 在线，`index_freshness` 固定标记 `not_measured`。
- Outbox 使用 SQLite URI `mode=ro` + `PRAGMA query_only=ON` 直接读取；不实例化会创建表的 Store，不写入、不迁移、不返回 payload。
- 投影摘要包含 `healthy / pending / attention_required`、总量、pending/failed/delivered 数量、最早 pending 时间、最近 delivered 时间，并显式 `content_included=false`；与 B188 的隐私字段约定一致。
- 新增赛博控制台「记忆殿堂 · MemPalace」面板：展示配置、目录/向量索引文件、索引 freshness 未知状态、MemoryStore 可读状态和 Agent Track Outbox 摘要。面板不提供未经授权的写入/同步/远程启动按钮。
- 前端 API 客户端增加 authenticated no-store GET；不改变现有 LAN/loopback 连接策略。

## 验证

- Svelte check：**0 errors / 0 warnings**。
- Vite production build：**134 modules transformed，成功**。
- UI Node 测试：**46/46 passed**，包含 MemPalace status API 客户端认证与只读路由测试。
- MemPalace Status API 三项测试：**3/3 passed**，已纳入全仓 Python 回归；Python 3.11 `py_compile` 对 `status_api.py` 与其测试文件通过。
- 最新全仓 Python 3.11 unittest：**1,558 tests in 202.004s，OK，9 skipped**。随后同一工作区 `compileall -q src/muse tests` 通过。此前几次 compileall 在并行写入窗口捕获到临时语法状态；待写入落盘后复跑成功。

## 边界与下一步

- 状态接口只读，不启动 MemPalace MCP 子进程/daemon，不触碰生产 Palace，不读取私人记忆正文，不写入 User Track。
- 本地目录/向量文件存在性不是在线探测；索引 freshness 仍明确未知。后续应通过受控、限频、显式启用的只读 MCP health probe 获取远端状态，并设计可审计的索引新鲜度信号。
- MP-M5 下一步：补充限频、显式启用的只读 MCP health probe 和可审计索引 freshness 信号；随后实现显式 pause/resume 状态机和故障报告。任何实际投影仍需精确 Commander Ticket + 持久化人工批准。
