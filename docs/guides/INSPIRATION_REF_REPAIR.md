# 操作指南：inspiration_refs 临时路径审计与修复

**它做什么**：找出 initiatives 库里指向已删除临时目录（`AppData\Local\Temp\tmp*`）的灵感引用；默认只读统计；修复时先备份、整事务改写、原值永久留档，绝不删除任何行。

**真实入口**：`scripts\audit_and_repair_inspiration_refs.py`（或 `muse-ref-repair`）；代码 `src/muse/inspiration_ref_repair.py`；测试 `tests/test_inspiration_ref_repair.py`（8 例）。

**使用步骤**
1. 只读审计：`python scripts\audit_and_repair_inspiration_refs.py --out tmp\ref-audit.json`
2. 克隆演练（推荐）：`python scripts\audit_and_repair_inspiration_refs.py --apply-on-db tmp\clone.sqlite3 --dry-run`
3. 真修复（停止 Muse 写入者后）：`... --apply-on-db data\initiatives\muse.sqlite3 --allow-production --confirm-sha256 <审计输出的 source_sha256_before>`

**安全控制**：缺少 `--allow-production` 或哈希不符 → 拒绝；`-wal`/`-journal` 非空 → 拒绝；备份 SHA-256 必须等于修复前；任何异常整体回滚；`inspiration_ref_repairs` 表只追加。

**回滚**：报告中的 `rollback_command`（用备份文件覆盖原库）。

**诚实边界**：只修引用文本，不判断这 3,954 条 `tool/proposed` 提案本身是否应停用（它们疑似测试污染）——是否把它们改为 `parked` 属于你的产品决策，工具不会自动做。
