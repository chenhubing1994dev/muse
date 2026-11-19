# B171｜MemPalace 检索信任边界强化

日期：2026-11-19

## 目标

把 MemPalace 的外部记忆召回与 Muse 的指令/事实权威明确隔离，防止检索内容被误当成系统指令、已验证事实或可自动执行的建议。

## 实现

- `MuseRuntime.search_memories()` 返回的每条 MemPalace 候选新增 `trust_boundary=untrusted_external_memory`。
- 新增 `instruction_authority=false`：外部记忆内容不得覆盖 Muse 的系统策略、审批规则或当前用户指令。
- 新增 `requires_source_verification=true`：候选在用于事实判断、开发决策或知识入库前必须回查来源并核验。
- 保留 `authority=unverified_retrieval_candidate`、`canonical_store=Muse MemoryStore` 和 `write_performed=false`；未改变检索排序、评分、写入或 outbox 发送行为。
- 在 UnifiedRuntime 回归测试中验证三个新字段。

## 验证

- `tests.test_unified_runtime`、`tests.test_mempalace_adapter`、`tests.test_mempalace_projection`：**71 tests passed**。
- Python 3.11 `py_compile`：通过。
- 首次测试命令因未设置 `PYTHONPATH=src` 未能导入模块；修正执行环境后完整定向测试通过。期间修复了测试断言行格式问题，最终源码与测试均已通过。

## 边界

- 没有把 MemPalace 设为权威记忆库；没有自动导入私人对话或用户 Track。
- 没有更改生产 palace 数据，没有触发 checkpoint 或自动同步。
- 本次是检索信任元数据强化，不代表已实现 UI 中的安全提示或自动事实核查。

详见 `docs/PROGRESS_B164_MEMPALACE_FUSION.md` 的基础集成说明。
