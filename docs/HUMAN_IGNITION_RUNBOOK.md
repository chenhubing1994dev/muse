# HUMAN_IGNITION_RUNBOOK.md — 创作者实机点火手册

> 本手册列出**只能由你本人**在真机上执行的步骤。AI 侧已把代码、测试、模拟边界都做完，并保证默认全部只读。
> 一条总入口：`scripts\ignite_live_gates.py`（不带参数 = 只读体检，什么都不会改）。

## §0 先做：复验与提交（约 3 分钟）

```powershell
cd "<MUSE_ROOT>"
# 1) 若 .venv 启动器仍损坏，先修复（会自动备份）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Repair-MuseVenv.ps1 -Apply
# 2) 真机跑 B391 新测试（3.11）
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m unittest tests.test_reviewer_attestation tests.test_signed_review_gate tests.test_inspiration_ref_repair tests.test_revenue_ledger -v
#    预期：Ran 49 tests … OK
.\.venv\Scripts\python.exe -m unittest tests.test_task_queue tests.test_worker tests.test_review_gate
#    预期：全部 OK（B392 改了 task_queue.py / worker.py；沙箱里有 18 例因环境限制无法运行，必须真机确认）
# 3) 只读体检
.\.venv\Scripts\python.exe scripts\ignite_live_gates.py
# 4) 切到 Omega 分支并只提交本批文件（分支与 main 指向同一提交，工作区改动会保留）
git switch agent/omega-build-20261125
git add docs/AGENT_BUILD_CHARTER.md docs/BUILD_LEDGER_OMEGA.md docs/HUMAN_IGNITION_RUNBOOK.md `
        docs/PROGRESS_B391_OMEGA_REVIEW_REPAIR_LEDGER.md docs/guides/REVIEWER_ATTESTATION.md `
        docs/guides/INSPIRATION_REF_REPAIR.md docs/guides/REVENUE_LEDGER.md `
        src/muse/reviewer_attestation.py src/muse/signed_review.py src/muse/task_queue.py src/muse/worker.py `
        src/muse/inspiration_ref_repair.py src/muse/revenue_ledger.py tests/test_signed_review_gate.py `
        scripts/audit_and_repair_inspiration_refs.py scripts/provision_reviewer_keys.py scripts/ignite_live_gates.py `
        tests/test_reviewer_attestation.py tests/test_inspiration_ref_repair.py tests/test_revenue_ledger.py `
        pyproject.toml QUICK_STATE.md
git commit -m "B391-B392: Ed25519 reviewer attestation wired into Worker/TaskQueue, inspiration_refs repair CLI, settled revenue ledger, ignition kit"
```
不要用 `git add -A`：另一 Agent 的在建文件（如 `src/muse/opportunity_radar/`，目前有 4 个语法错误）不应混入本次提交。
安全回退点：`git switch main` 或 `git reset --hard omega-pre-20261125`（标签已建好）。

## 步骤 1 — inspiration_refs 只读审计与可选修复（闸门 G6）

```powershell
.\.venv\Scripts\python.exe scripts\ignite_live_gates.py --step 1
```
1. 先自动跑**只读**审计，输出 `source_sha256_before`、3,954 条临时路径引用的分类统计。
2. 停止所有 Muse 进程（Muse-Stop / 计划任务），确保没有写入者。
3. 粘贴上一步的 SHA-256，并输入 `IGNITE 1 REPAIR`。
4. 工具会：持写锁 → 字节级备份（`data\initiatives\muse.pre-ref-repair-<时间>.sqlite3`，SHA-256 必须等于修复前）→ 改写为 `missing-temp-report:<文件名>` → 原值写入只追加表 `inspiration_ref_repairs` → integrity_check。
5. 回滚：报告里的 `rollback_command`（`copy /Y <备份> <原库>`）。
已在生产库的一次性克隆上演练过：3,954 行改写、剩余 0、行数不变、二次运行 0 行（幂等）。

## 步骤 2 — 生产 Reviewer 密钥仪式（闸门 G9，密钥只由你持有）

```powershell
.\.venv\Scripts\python.exe scripts\ignite_live_gates.py --step 2
```
- 私钥目录必须在 Muse 仓库**之外**（脚本会拒绝仓库内路径），建议加密 U 盘。
- 完成后按屏幕提示执行两条 `setx MUSE_REVIEWER_TRUST_STORE…` / `…_SHA256`，重开终端生效。
- 之后审查签名：`muse-reviewer-attest sign --private-key <私钥> --ticket-id <票据> --artifact <制品> --test-evidence <测试日志> --proposer-id <提案者> --out att.json`
- 轮换：新 key_id 生成后 `provision_reviewer_keys.py revoke --key-id <旧>`，并更新 SHA-256 钉扎。

## 步骤 3 — ComfyUI + GPU 预检 + 单镜头 Wan 2.1 实机验收（闸门 G3/G2）

```powershell
scripts\muse-comfyui-start.cmd          # 你亲自启动 ComfyUI（127.0.0.1:8188）
.\.venv\Scripts\python.exe scripts\ignite_live_gates.py --step 3
```
- 预检读取 `/system_stats`，空闲 VRAM < 7.7 GiB 或未知时 fail-closed。
- 通过后输入 `IGNITE 3 GPU`，运行 `scripts\run_wan_gpu_acceptance.py`（RX 6750 GRE 实机验收脚本）。
- 本轮会话中只读探测结果：ComfyUI **未运行**。AI 超分工作流尚未实现（待办 BL-WS6-*），720P 发布门仍关闭。

## 步骤 4 — Web Portal / Status API 本地启动与局域网配对（G7 保持关闭）

```powershell
.\.venv\Scripts\python.exe scripts\ignite_live_gates.py --step 4
```
列出本机 LAN 地址与 `muse-status-api` / `muse-web-portal` 命令；参数见 `docs/WEB_PORTAL_SERVER_DEPLOYMENT_V1.md`。不会改动公网 DNS 或防火墙。

## 步骤 5 — 有监督地启用 Commander 例程（闸门 G4）

```powershell
.\.venv\Scripts\python.exe scripts\ignite_live_gates.py --step 5
muse-routines --help
muse-autonomous --help      # pause / stop / kill-switch
```
后台守护进程默认 `stopped`；任何脚本都不会替你启动。

## 收入账本（G5 永久对 AI 关闭）

```powershell
.\.venv\Scripts\python.exe -m muse.revenue_ledger summary          # 未导入回执前恒为 US$0.00
.\.venv\Scripts\python.exe -m muse.revenue_ledger import-statement --file <平台结算单.csv> --sha256 <你核对过的哈希> `
        --provider gumroad --external-id <结算单号> --amount-minor <分> --currency USD
```
浏览量、点赞、线索、下单未结算都**不会**计入收入。
