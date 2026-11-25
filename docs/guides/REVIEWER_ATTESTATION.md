# 操作指南：密码学独立审查（Reviewer Attestation）

**它做什么**：任何“审查通过”都必须是一份 Ed25519 签名文件，签名覆盖 `ticket_id + 制品 SHA-256 + 测试证据 SHA-256 + 提案者`。只写 `actor="reviewer"` 不算审查。启用后，Worker **无法**直接把票据改成 `succeeded`，只能停在 `waiting_approval`，等你提交签名。

**真实入口**
- 主干接线（B392）：`muse-worker --signed-review-kind <任务类型>`（可重复）；票据状态转换在 `TaskQueue.finish_pending_signed_review` / `complete_signed_review`
- 审查操作：`muse-signed-review list | export | submit`
- 签名：`muse-reviewer-attest sign`；密钥仪式：`scripts/provision_reviewer_keys.py keygen | revoke | show`
- 测试：`tests/test_reviewer_attestation.py`（17 例）、`tests/test_signed_review_gate.py`（15 例）

**使用步骤**
1. 生成生产密钥（仅一次，私钥放仓库外）：`docs/HUMAN_IGNITION_RUNBOOK.md` 步骤 2。
2. 启动 Worker 并指定要签名审查的任务类型，例如：`muse-worker --signed-review-kind product_build --loop`。任务 payload 里必须带 `_test_evidence`（完整测试日志），否则直接失败为 `review.test_evidence_missing`。
3. 查看待签：`muse-signed-review list`
4. 导出审查材料：`muse-signed-review export --task-id <ID> --out-dir review-<ID>` → 得到 `artifact.json`、`test_evidence.txt`、`binding.json`（字节与哈希逐一核对，不会覆盖已有文件）。
5. 你亲自检查这两个文件后签名（命令在 export 输出的 `sign_command` 里）：
   `muse-reviewer-attest sign --ticket-id <ID> --artifact review-<ID>\artifact.json --test-evidence review-<ID>\test_evidence.txt --proposer-id muse-worker --private-key E:\MuseKeys\rk.json --out review-<ID>\attestation.json`（不同意就加 `--verdict reject`）
6. 提交：`muse-signed-review submit --task-id <ID> --attestation review-<ID>\attestation.json` → 同意 = `succeeded`，拒绝 = `failed (review.rejected)`。

**会被拒绝的情况**：提案者给自己签名；签名与制品、测试日志或票据不符；票据结果在停放后被改过（export 时发现）；过期（TTL ≤ 15 分钟）；重放；吊销的密钥；测试密钥冒充生产；信任库被改（SHA-256 钉扎不符）；未配置信任库；回执库和任务库是同一个文件；用 `muse-task approve` 重新批准（会被拒绝，以免重跑 handler）。

**撤销与回滚**：不想要这次结果就 `muse-task cancel <ID>`；吊销密钥 = `provision_reviewer_keys.py revoke` 并更新钉扎；回执表只能追加，不能修改或删除。

**诚实边界**：只在沙箱里测过（`LOCAL_SANDBOX_RAN`），生产密钥还没生成，所以真实签名审查目前**不可用**。默认不开启：不加 `--signed-review-kind` 时行为和以前完全一样。`approved_code_change`（自我进化）在 handler 内部就已写入源码，Worker 层签名来得太晚，**不能**用这个门保护它，需要另做（待办 BL-WS4-EVO）。视频流水线仍使用既有的 `video_reviewer_attestation`。
