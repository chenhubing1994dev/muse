# B200｜本地交付物 SHA-256 证据验证

日期：2026-11-19

## 实现内容

- 新增 `src/muse/artifact_evidence.py`：`LocalArtifactEvidenceVerifier` 只接受显式 artifact root 下的规范 POSIX 相对路径和普通文件。
- 拒绝绝对路径、盘符路径、反斜线路径、`.` / `..` 路径穿越、非规范路径、符号链接、目录、空文件、超限文件与 SHA-256 / byte-count 不匹配。
- 以 1 MiB 分块流式计算 SHA-256；读取前后比较文件身份、大小和修改时间，防止普通并发替换/写入造成错误验收。默认上限 2 GiB。
- `WorkflowController` 接入显式 `artifact_root`；Muse `UnifiedRuntime` 将工作区根目录传入 Controller，不新增存储、调度器或队列。
- 新增 `record_artifact_evidence()`：要求当前 Ticket lease、已绑定 Plan、与当前 Plan 对应的最新成功 Commander Run、Reviewer 批准；把路径、字节数、摘要、Plan ID 和 Run ID 作为 `workflow.artifact_verified` 事件写入既有 TaskQueue。
- `complete()` 对每个被引用的 artifact 重新计算 SHA-256，并要求同一 Ticket、同一 worker、同一 Plan 和同一 Run 的已验证事件；文件在登记后发生变化、引用伪造或验收标准未关联 artifact 时均拒绝完成。
- `workflow.completed` 保存 artifact 验证摘要；未引用本地 artifact 的既有工作流维持兼容，`artifact_validation.status=not_required`。
- 外部 URL/任意字符串不被解释为本地文件证据；外部系统只应通过未来明确的只读验证适配器接入。

## 验证

- 新增 `tests/test_artifact_evidence.py`：路径穿越、非规范路径、符号链接、根目录符号链接、目录/空文件、大小限制、摘要/字节数不匹配、正常文件验证。
- 新增 `tests/test_workflow_artifact_evidence.py`：成功登记与完成、登记后文件被篡改、无验证事件的伪造引用。
- B200 定向回归（Artifact verifier + Workflow artifact integration + Workflow profiles + UnifiedRuntime）：**79/79 passed**。
- 全项目 Python 3.11 unittest：**1,626 tests，OK，1 skipped**。
- `compileall`：`src/muse` 与 `tests` 通过；完整回归通过。
- 全项目回归中仍有一个 OpenCV missing-image fixture warning 和一个 FFmpeg `moov atom not found` fixture 日志；测试结果仍为成功。

## 当前边界 / 下一步

- 本轮用临时文件 fixture 验证真实文件字节与 SHA-256 及完成时二次复核；尚未把真实 Wan MP4 / product ZIP 的产品 manifest 自动接入 Commander Ticket。
- 当前 `UnifiedRuntime` 已配置工作区根目录，但调用方必须显式调用 `record_artifact_evidence()` 并把返回的 `artifact:<path>#sha256=<digest>` 放入 evidence_refs 与对应 acceptance criterion；不能只凭文件名自动猜测交付物。
- 下一步将从 VideoProductBuilder 的已验证 manifest 读取 MP4 / ZIP / QA 报告清单，按当前 Ticket / Run 注册证据，并在完成时再次校验；只验证 manifest 声明且位于产品目录内的文件。
- 本地 TaskQueue 仍是应用信任边界，不是防篡改的密码学审计日志；拥有本机数据库写权限的代码仍可能伪造事件。
- 本轮未启动 GPU 生成、外部发布或其他不可逆动作。
