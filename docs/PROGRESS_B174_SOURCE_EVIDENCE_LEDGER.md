# B174｜来源证据账本与反证链

日期：2026-11-19

## 目标

为 Muse 原生 MemoryStore 增加可追溯的来源证据记录，让一条记忆/主张可以挂接支持、反驳、限定和相关材料，并把“已收录”与“已核验”分开。

## 实现

- 新增 SQLite 表 `memory_evidence`，随 MemoryStore 初始化自动创建；不修改现有 memories / revisions / candidates 记录结构。
- `MemoryStore.add_evidence()` 将证据绑定到一条仍然有效的 canonical memory；关系类型限定为 `supports`、`refutes`、`qualifies`、`related`。
- 每条证据保存来源标题、可选 HTTP(S) URL、有限长度摘录、摘录 SHA-256、创建者与时间戳；新证据一律 `unverified`。
- `list_evidence()` 按记忆读取证据；`verify_evidence()` 需要显式状态（verified/rejected）和审核者，并记录审核说明与时间。
- 对不存在/已删除的记忆、非法关系、非 HTTP(S) URL、超长摘录、非法审核状态作 fail-closed 验证。
- 反证只作为独立证据记录，不会自动改写、删除或降低原记忆置信度。
- 修正 MemPalace 检索候选信任字段的格式，字段语义保持不变。

## 验证

- 定向测试：`test_memory` + `test_unified_runtime` + `test_mempalace_adapter` + `test_mempalace_projection` **91/91 passed**。
- Python 3.11 `py_compile`：通过。
- 使用临时 SQLite 数据库；未触碰生产记忆库，未把 MemPalace 检索结果自动写入 evidence ledger。

## 当前边界

- 这是底层证据账本，不是完整 Research Wiki UI；来源真实性、独立性和上下文仍需人工核验。
- 当前 SHA-256 仅校验保存的摘录内容完整性，不代表来源网站真实性，也不代表摘录准确。
- 证据尚未自动注入研究报告、知识图谱或 Commander 决策上下文。

下一步：将 evidence ledger 接入 Research Wiki 的 claim/source 图谱和状态 API，确保未经核验的外部材料始终显示为候选。
