# PROGRESS B391 — Omega Cycle 0–2：密码学审查门 · inspiration_refs 修复 · 已结算收入账本 · 点火包

日期：2026-11-25（CST）· 执行者：AWS Quick（无 Coding Agent 委派）· 完整证据：`docs/BUILD_LEDGER_OMEGA.md`

| 交付 | 文件 | 测试 | 等级 |
|---|---|---|---|
| Ed25519 Reviewer 证明 + 密钥仪式 | `src/muse/reviewer_attestation.py`、`scripts/provision_reviewer_keys.py` | 17/17 | LOCAL_SANDBOX_RAN |
| inspiration_refs 审计/修复 | `src/muse/inspiration_ref_repair.py`、`scripts/audit_and_repair_inspiration_refs.py` | 8/8 + 生产克隆演练 3,954→0 | LOCAL_SANDBOX_RAN（真实数据克隆；生产库未改，SHA-256 前后一致） |
| 已结算收入 / 净贡献账本 + HMAC webhook | `src/muse/revenue_ledger.py` | 9/9 | LOCAL_SANDBOX_RAN (fake_external_boundary: payment_provider_webhook) |
| 人类点火包 | `scripts/ignite_live_gates.py`、`docs/HUMAN_IGNITION_RUNBOOK.md` | 设备端 check 函数实测 | LOCAL_SANDBOX_RAN |
| 章程 / 账本 / 指南 | `docs/AGENT_BUILD_CHARTER.md`、`docs/BUILD_LEDGER_OMEGA.md`、`docs/guides/*.md` | — | — |

新增测试 34 例（测试文件 +3）；pyproject 新增 `muse-reviewer-attest`、`muse-ref-repair`、`muse-revenue-ledger`。
发现但未修改：`src/muse/opportunity_radar/` 4 个语法错误（另一 Agent 在建，修复方案见账本 C1-1）。
未完成：真机 3.11 全量回归、git 提交（命令见 Runbook §0）、新审查门接入 review_gate 主干。
