# PROGRESS.md — Muse 修复与打通主流程进度（断点续作用）

> 本文件由 AWS Quick 维护，与 `docs/PROGRESS.md`（项目历史进度）分开。
> **自 2026-11-25 16:30 起，唯一状态入口为 `QUICK_STATE.md`（含功能矩阵、问题清单、下一步）；本文件仅保留早期记录。**

最后更新：2026-11-25 16:13 CST

## 硬性约束（每次续作前复核）
1. 不调用/委派任何外部 Coding agent（Kiro、Claude Code、Codex、Cursor、Copilot、Amazon Q Developer 等），也不通过扩展/MCP/终端间接调用。
2. 只在 `<MUSE_ROOT>` 内操作；不删除文件；不读取/外传 .env、密钥、token（`config/muse.toml`、`config/model-runtime.json`、`config/opencode.local.jsonc` 均不读）。
3. 修改任何已有文件前，必须先完成整目录备份到 `<MUSE_ROOT>_backup_<日期>`。
4. 安装依赖/联网下载前先列清单征得同意。

## 阶段状态

| 阶段 | 状态 | 产出 |
|---|---|---|
| 0 项目盘点 | ✅ 完成 | `MUSE_PROJECT_MAP.md` §1–5 |
| 1 流程梳理 | ✅ 完成 | `MUSE_PROJECT_MAP.md` §6（主流程 A/B/C + 断点 B1–B8） |
| 2 问题清单 | ✅ 完成 | `ISSUES.md`（含“修复状态”表） |
| 3 逐项修复 | ✅ 致命/重要项完成 | `CHANGELOG.md`（22 条记录）；逐文件原件备份 `.qw-backups/20261125/` |
| 4 打通全流程 | 🔧 进行中：等待用户运行冒烟脚本 | `scripts/muse_smoke.py` |
| 5 优化完善 | ⬜ 未开始 | |
| 6 交付总结 | ⬜ 未开始 | |

## 备份决定
- 2026-11-25 16:03 用户决定：空间有限，**不做整目录备份**。
- 替代方案：每个文件首次修改前，原件复制到 `.qw-backups/20261125/<相对路径>.orig`，SHA-256 记录在 `.qw-backups/20261125/MANIFEST.txt`。
- 修改工具：`.qw-backups/20261125/_tools/fixlib.py`（唯一匹配替换 + 并发变更检查 + 语法检查 + 自动写 CHANGELOG）。

## 已修改的文件（全部有 .orig 备份）
src/muse/research_state_machine.py、ember-fire/package.py、src/muse/video_product_builder.py、src/muse/model_supervisor.py、src/muse/status_api.py、README.md、pyproject.toml、docs/PROGRESS.md
新建：MUSE_PROJECT_MAP.md、ISSUES.md、PROGRESS.md、CHANGELOG.md、scripts/muse_smoke.py

## 验证方式（环境限制）
AWS Quick 的执行环境禁止子进程、sys.path、importlib，无法自己运行测试。循环为：用户运行脚本 → 结果写入 `reports/qw_smoke/latest.log|latest.json` → AWS Quick 读取并修复 → 再运行。

用户需运行的命令（PowerShell，项目根目录）：
```
cd "<MUSE_ROOT>"
.\.venv\Scripts\python.exe scripts\muse_smoke.py
```
（若 .venv 不可用：`py -3.11 scripts\muse_smoke.py`；需要全量测试再加 `--full`）

## 待用户处理
1. 运行上面的冒烟命令（运行前请暂停其他 Agent 与 GitHub 同步任务）。
2. I5：自查 `config/opencode.local.jsonc` 是否含密钥（Git 已跟踪）。
3. 人工复核 F1-b：`src/muse/research_state_machine.py` 约第 284 行恢复的 `conclusion = "refuted"`。
4. 让今天 13:00 后工作的那个 Agent 复核它新建的未入 Git 文件（N1）。

## 下一步（续作从这里开始）
1. 读取 `reports/qw_smoke/latest.log` 与 `latest.json`；如有 FAIL，按日志定位修复（继续用 fixlib.py + CHANGELOG），请用户重跑，直到全部 PASS。
2. 全部通过后进入阶段 5：整理 requirements 文件、README 增补“冒烟测试/常见问题”、补充 F1-b 反驳分支的单元测试。
3. 阶段 6 交付总结。
