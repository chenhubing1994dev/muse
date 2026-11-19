# 2026-11-19｜B-063 独立 Reviewer：真实本地模型冒烟验证

## 结果

- WebCodex 本机 Runner 已恢复在线，项目读取、修改与命令执行均成功。
- 实测 `http://127.0.0.1:1235/v1/models` 不可达；Muse 当前 `config/muse.toml` 的 Builder 配置仍指向该端点，配置文件未修改。
- 实测 `http://127.0.0.1:1234/v1/models` 可达，返回 5 个模型 ID，其中包括 `gemma-4-e4b-it-claude-opus-4.5-heretic-uncensored-thinking`。
- 通过显式 `MUSE_REVIEWER_URL=http://127.0.0.1:1234/v1` 与 `MUSE_REVIEWER_MODEL`，实际装配 `AutonomousSupervisor._build_feedback_reviewer()`，并调用真实本地模型。
- 第一轮真实模型回复把 findings 输出为字符串数组；ReviewGate 的严格 schema 校验会拒绝这种格式，不会保存为审查通过。
- 收紧 Reviewer system prompt：明确 JSON schema、每条 finding 必须为包含 severity/title/detail 的对象、无 findings 时用空数组。
- 第二轮经真实模型调用及完整 `IndependentReviewGate.review()` 冒烟测试通过：生成 review ID，返回结构化结果，并从临时 SQLite ReviewStore 读回同一记录（count=1）。该合成样本的模型判定为 approved；它只证明链路/协议/持久化可用，不代表生产代码审查质量或外部事实正确。
- 另一次 live review 将本轮改动摘要（不是完整源码 diff）作为 artifact，真实模型返回 `approved=true`，同时列出 info/low/medium findings；审查记录在临时 ReviewStore 写入并读回成功。此结果仅是摘要级审查，不能替代完整源码审查。
- Scope 为 `distinct_model_other_loopback_endpoint_provider_unverified`：不同模型、不同 loopback endpoint 已验证；底层是否为独立 Provider / 独立进程仍未验证。

## 本轮代码与测试

- `src/muse/autonomous_runtime.py`：加强 Reviewer system prompt 的结构化 JSON 契约。
- `tests/test_review_gate.py`：新增 timeout fail-closed 与 string findings 拒绝测试。
- 定向回归：
  - `test_review_gate.py`：15/15 通过。
  - `test_autonomous_runtime.py`：18/18 通过。
  - `test_autonomous_research.py`：56/56 通过。
- 全仓库回归：项目 `.venv/Scripts/python.exe` + `unittest discover`，**1,497 项运行，1,488 通过、9 项跳过、0 failures / 0 errors**（280.719 秒）；`compileall -q src tests` 通过。
- ReviewStore 冒烟数据写入临时目录，测试结束后清理；没有把合成审查结果写入 Muse 生产数据库。
## 未完成 / 安全边界

1. 不修改 Builder 当前配置；`1235` endpoint 恢复后再验证 Builder 真实在线状态。
2. 不把不同模型/端点等同于 Provider 独立性；需要确认模型服务进程、Provider 与资源隔离。
3. 尚未将完整源码 diff 与真实、非破坏性的工程回归报告交给 Reviewer 审查；下一步需执行完整源码级 review，并核对引用证据、拒绝路径及 ReviewStore。
4. Reviewer 只能记录审查意见；不得自行批准代码变更、晋升外部 Claim 或执行补丁。
5. 工作区已有大量既存修改与未跟踪文件，所有改动均基于读取版本进行，不做清理或重置。
