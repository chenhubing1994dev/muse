# 2026-11-12 B-111 Initiative Ledger

## 目标

为 Muse 的自主创作平台补齐从创意提案到真实产品交付的可追溯生命周期，不新增第二调度器或重复任务队列。

## 实现

- `InitiativeStore` 新增 append-only `initiative_events`，记录 `created`、`status_changed`、`feedback_recorded`、`product_built`。
- 事件保存前后状态、actor、source、有限 JSON details、序号与时间戳；SQLite trigger 拒绝 UPDATE / DELETE。
- Initiative 创建、状态变更、评审反馈及对应事件在同一 SQLite transaction 中提交。
- 反馈事件只记录 feedback ID 与 decision，不复制反馈正文。
- `LocalProductBuilder` 在离线产品验证后登记相对路径、package SHA-256 与 validated 状态；同一 initiative + package hash 幂等去重。
- Windows 路径统一规范化为项目相对 POSIX 路径，拒绝绝对路径、空组件、当前目录和父目录穿越。
- 作品登记保持 `testing`，不自动变成 `shipped`。
- 旧数据库只创建新表和触发器，不回填无法证明的历史事件。

## TDD 与验证

- RED：新增测试首先因缺少 `list_events` 与 `record_product_artifact` 按预期失败。
- GREEN：InitiativeStore 专项 6/6 通过。
- LocalProductBuilder 专项 4/4 通过。
- Python 3.11 全量 unittest：1,069/1,069 通过，约 105.928 秒。
- `.venv\Scripts\python.exe -m compileall -q src tests` 通过。
- Windows 路径反斜杠最初被误拒；已修复规范化逻辑，并在修复后重新运行产品专项及全量回归。

## 尚未完成

- 研究图谱的 idea / experiment / claim 尚未与 initiative、evolution_cycle_id、视频产品和产品产物统一关联。
- VideoProductBuilder 尚未写入 Initiative Ledger；现有视频产物仍由产品库 manifest/哈希独立追踪。
- 历史 Initiative 没有回填事件；旧历史保持未知，不伪造时间线。
- 项目是 non-Git 工作区，不能提供 Git diff/review 证据。

## 下一步

1. 将 ResearchKnowledgeStore 的 idea / experiment / claim 与 initiative / evolution_cycle_id 关联。
2. 将 VideoProductBuilder 产物写入同一交付账本，保留视频 MP4、ZIP 和 manifest 哈希。
3. 把评审/测试/经验反馈汇总到 Evolution Ledger，并生成主动汇报。
4. 对真实产品做端到端展示/下载验收，仍不自动发布。
## API 与 Dashboard 时间线接通

- 新增只读接口：`GET /api/v1/initiatives/{initiative_id}/timeline?limit=100`；从既有 products 目录定位 initiatives 数据库，以 SQLite URI `mode=ro` 打开，不创建或迁移数据库。
- API 仅返回 allowlist 字段；评审正文不出现在时间线；产品相对路径与 SHA-256 再次校验；限制每次最多 200 条事件。
- `history_complete` 只有在存在真实 created 事件时才为 true。旧 Initiative 即使有后续事件，也不会假装拥有升级前的完整历史；缺少事件表时只读返回空事件和 incomplete。
- Dashboard 产品库新增“创作时间线”展开入口，按需读取并显示创建、状态变化、反馈决策和产品交付；可看到作品相对路径与哈希摘要，不展示反馈正文。
- 前端 API 客户端新增 initiativeTimeline 方法与 ID/limit 校验；Node API 测试 **16/16**；Svelte-check **0 errors / 0 warnings**；Vite production build 成功。
- 后端新增当前/旧库时间线验收：状态 API 当前产品流与旧库只读测试均通过；旧库测试确认不会自动创建 initiative_events 表。

## 记录限制

`docs/PROGRESS.md` 当前存在混合 LF/CRLF 行结束符，结构化编辑器拒绝修改该文件；为避免绕过 revision fencing 或重写整份历史，本轮以本补充记录保存进度，并同步更新 `docs/CAPABILITY_BACKLOG.md`。
