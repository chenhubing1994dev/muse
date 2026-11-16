# 2026-11-15｜P0 研究实验账本：稳定 run_id 不可变与幂等重放

## 根因

`ResearchKnowledgeStore.upsert_node()` 对同一 `external_ref` 合并并更新 metadata；`ExperimentBridge.record_result()` 未在写入前校验既有 experiment-run。因此，重复 `run_id` 的冲突结果可能覆盖旧状态、指标与证据哈希，并重复追加知识事件，破坏实验结果的可审计性。

## TDD 与实现

- 新增 `test_experiment_run_id_is_idempotent_and_rejects_conflicting_replay`。
- 首次运行按预期失败：完全相同的重放令该 run 的知识事件数由 1 增至 2，证明重复写入不是幂等。
- `ExperimentBridge.record_result()` 现先查找稳定 `experiment-run:{run_id}`。
- 同一 run_id 的 manifest、状态、摘要、时长、指标、环境/代码指纹及原始证据哈希完全一致时，返回原 node，不重复追加事件。
- 同一 run_id 的任一结果字段冲突时抛出 `ValueError`，保留原始终态与证据哈希，不允许覆盖。

## 验收

- `tests.test_experiment_bridge`：8/8 通过。
- 全仓库 `python -m unittest discover -s tests -v`：1,219 项运行，1,218 通过、1 项可选依赖测试跳过，0 failures / 0 errors，126.701 秒。
- 首轮全量运行因测试代理拦截 loopback HTTP 请求出现 502；设置 `NO_PROXY=*` 后完整重跑通过。
- `compileall -q src tests` 已执行。

## 边界与后续

- 本修复保护知识账本中的实验结果不被冲突重放覆盖。
- 这不代表真实第二模型 Reviewer、外部科学事实核验或 OS 级 sandbox 已完成。
- 项目是 non-Git 工作区；Runner source freshness 仍为 unproven。
- `docs/PROGRESS.md` 当前存在混合 LF/CRLF 行结束符，安全编辑器拒绝追加；本记录先以独立进度附录保存，并由 B-062 Backlog 链接引用，未对原进度文件做整文件重写。

## 2026-11-15 增量｜精确重放修复缺失图谱边

- 新增 `test_exact_replay_repairs_missing_manifest_edge`，先删除 run 的 `tested_by` 边模拟“节点写入后、关系写入前崩溃”。旧实现重放后仍无边，测试按预期失败（0 条，预期 1 条）。
- `ExperimentBridge.record_result()` 在确认已有 run 的所有结果字段完全一致后，调用 `add_edge(manifest, run, tested_by)` 再返回。`ResearchKnowledgeStore.add_edge()` 通过唯一关系约束和 `INSERT OR IGNORE` 保证边重建幂等；仅首次补边追加 edge event。
- 测试进一步确认第二次重放不会重复追加知识事件。
- 最新定向验证：`tests.test_experiment_bridge` **9/9 通过**（Python 3.11）。
- 本轮只读检查本机模型端点：`127.0.0.1:1235/v1/models` 不可达；`127.0.0.1:1234/v1/models` 可达，暴露多个模型 ID（含 Gemma 与 DeepSeek 别名）。这只证明 endpoint/model inventory 可见，不证明两模型真实审稿质量或 Provider 独立性；B-063 仍 pending，未启动真实 Reviewer 调用。
- 本轮交叉回归：`test_experiment_bridge`、`test_feedback_experiments`、`test_autonomous_research`、`test_research_knowledge` 合计 **51/51 通过**；全项目 `py -3.11 -m unittest discover -s tests -v` **1,221 项运行，1 项可选依赖跳过，其余 1,220 项通过，0 failures / 0 errors**（127.485 秒）；`py -3.11 -m compileall -q src tests` 通过。