
## 2026-11-27
- 真实工作区当前没有由代理创建的待审批高风险票据（本轮未在真实工作区运行任何 Workflow）。
- 可选：启动 `start_diag_watcher.bat`，以允许代理自行触发诊断（见 docs/KNOWN_ISSUES.md KI-001）。

## 2026-11-27 23:1x（T1–T10 完成后）
- 真实工作区 `data/commander/tasks.sqlite3` 不存在 → **没有任何待审批的高风险票据**；`data/tasks/muse.sqlite3` 中也没有 `waiting_approval` 票据（failed 38 / succeeded 9，均为历史）。
- 所有 user:/agent: 审批测试只在诊断临时目录中进行。
- 需要你决定的事项（不是票据）：① 是否在 `config/external_agents.json` 启用 dsh（会向第三方模型外发数据）；② 是否提供 Super Agent Party 地址；③ 复核 3 条 `legacy_unverified` 记忆；④ 如需 L5.M1 实测：安装/开放 nvidia-smi 并在 LM Studio 预先加载一个模型。

## 2026-11-28 持续开发（已按默认处理，供复核）
- D-023 CLI 是否也要全部走统帅票据（当前：登记但豁免）。
- D-024 stop/pause 直通（为保证紧急情况下能停）。
- D-025 Omega 与 Commander 停止开关合并为“任一关闭即全停”。
- D-026 进化回滚改为高风险票据，需你在统帅控制台批准。
- D-028 红线白名单（2 个打包脚本、7 个网络模块）。
- 真实工作区仍没有待审批的高风险票据。
- D-029 单测期间真实库（除 handoff/memory）出现非自检写入即判红并回滚到运行前状态（运行后副本留在 backups/unit-guard-*/stores-evidence）——已按默认处理，供复核：诊断期间请勿真实使用 Muse 写库。
- 3 条 legacy_unverified 旧记忆待你复核：`python scripts\review_legacy_memory.py list` 生成清单，确认用 `confirm --id <id> --by user:<你的名字>`（可 `revert`）。代理不会代为确认。
- D-031 `muse-memory approve` 现在要求 `--by user:<名字>`（或 human:），旧写法 `--by user` 会被拒——已按默认处理，供复核。
- D-033 `muse-memory approve` 被拒时不再抛栈，改为退出码 3（身份不符）/2（候选无效），错误 JSON 在 stderr——已按默认处理，供复核。
