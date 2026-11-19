# B184｜MemPalace 备份恢复保真修复

日期：2026-11-19

## 问题

- 恢复过程需要从备份目录排除根级 `.muse-mempalace-backup.json` 元数据清单，但不能把 Palace 内部子目录中同名文件误当成备份清单。
- 原忽略逻辑若作用于所有层级，会丢失嵌套同名文件；路径比较也必须按当前 Windows 主机路径规则规范化。

## 修复

- `restore_palace()` 的 `copytree` ignore callback 现在仅在源目录等于备份根目录时排除清单文件，并用 `os.path.normcase(os.path.abspath(...))` 规范化比较。
- 保留原有备份校验、拒绝符号链接、禁止覆盖目标、临时目录验证后原子落位等保护。
- 新增回归：Palace 子目录包含 `.muse-mempalace-backup.json` 普通文件时，备份→恢复必须保留其内容。
- 既有回归继续验证根清单不会进入恢复后的 Palace、文件字节完全一致、损坏备份拒绝恢复、路径穿越拒绝、符号链接拒绝和目标不覆盖。

## 验证

- `tests/test_mempalace_lifecycle.py`：**9/9 passed**。
- `tests/test_mempalace_adapter.py`：**16/16 passed**。
- `tests/test_mempalace_projection.py`：**12/12 passed**。
- `tests/test_mempalace_projection_ack.py`：**7/7 passed**。
- MemPalace 四组相关专项合计 **44/44 passed**；`py_compile src/muse/mempalace_lifecycle.py` 通过。

## 边界

- 所有测试均使用临时合成目录/数据库；未备份、恢复或修改用户的生产 MemPalace Palace。
- 本轮未进行任何 User Track 迁移；MemPalace 仍是可选派生记忆层，不取得 Muse canonical memory、Research Wiki 或 Commander 的所有权。
