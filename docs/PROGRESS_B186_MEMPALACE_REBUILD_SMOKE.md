# B186｜MemPalace 备份恢复与索引重建实机 smoke

日期：2026-11-19

## 本轮完成

- 新增 `src/muse/mempalace_lifecycle.py`：`backup_palace()`、`verify_backup()`、`restore_palace()`。
- 备份采用独立目录、SHA-256 文件清单、临时目录校验后原子落位；拒绝路径嵌套、符号链接、特殊文件、清单路径穿越和覆盖已有目标。
- 恢复前验证备份完整性；只恢复到全新目录，不覆盖任何现存 Palace。调用方必须明确传入 `quiesced=True`，表示 MemPalace 写进程已停止；模块不代替进程监督，也不触碰 Muse canonical SQLite。
- 新增断点恢复测试：模拟远端 checkpoint 已成功、Muse 本地 delivered 更新在提交前失败；关闭并重新打开 Outbox 后重试，远端返回 exact-content duplicate 和同一 drawer ID，Outbox 正确恢复为 delivered。

## 本机隔离 Palace 验证

测试源仅为此前创建的合成回执 Palace：

`D:\AI AGENT\MuseData\MemPalace\ack-contract-smoke-20261119`

- 备份：`D:\AI AGENT\MuseData\MemPalace\lifecycle-smoke-20261119-backup`
- 恢复并重建索引副本：`D:\AI AGENT\MuseData\MemPalace\lifecycle-smoke-20261119-restored2`
- 备份清单核验通过：8 个文件、1 个目录、356,608 bytes；备份复核仍为 verified。
- 原 Palace 与恢复 Palace 的 SQLite `PRAGMA integrity_check` 均为 `ok`。
- 第一次重建尝试使用了错误的 HF_HOME 目录，因离线缓存无法找到模型文件而失败；失败副本和上游自动归档均保留，未触碰源 Palace。随后改用 Muse 配置中的 D 盘 `hf-cache` 和 profile，离线 CPU 重建成功。
- 上游 `mempalace repair rebuild-index` 实际重建 1 条 drawer；FTS5 rebuild、VACUUM、FTS5 integrity-check 与 SQLite quick_check 均通过。
- 重建后 `status` 显示 1 drawer；语义搜索 `idempotent checkpoint replay drawer` 返回目标记录（cosine_sim 0.608、BM25 0.575）。
- 重建后再次核验备份清单仍 verified，原源 Palace SQLite integrity 仍为 `ok`。

## 测试

- lifecycle **9/9 passed**、projection **12/12 passed**、ack contract **7/7 passed**，本轮三组 **28/28 passed**。
- 合并定向回归：lifecycle **9/9**、adapter **16/16**、projection **12/12**、ack contract **7/7**、UnifiedRuntime **46/46**，合计 **90/90 passed**；Python 3.11 `py_compile` 通过。
- 首轮全仓 Python 3.11 unittest **1512 tests** 曾出现 2 failures、1 error，均在视频旁白测试；同两组视频模块随后独立 **44/44 passed**。Runner 报告 active jobs=0 后再次完整回归，最终 **1512 tests in 156.342s，OK，9 skipped**；`compileall -q src/muse tests` 通过。首轮波动根因尚未确认，本轮没有修改视频代码。

## 边界与未完成项

- 全部真实 Palace 操作都在独立合成测试副本进行；生产 Palace 仍未初始化、未写入，未导入 User Track、私人对话或真实用户记忆。
- 没有启动 HTTP daemon、remote hub 或后台同步。
- MP-M5 的 UI 健康状态、索引新鲜度、暂停/恢复和失败可视化仍未完成；正式大样本中文/粤语召回 benchmark、耐久操作者身份和生产数据生命周期验收仍是启用前置条件。
