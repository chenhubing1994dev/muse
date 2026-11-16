# Muse Git 工作区与安全开发流程

## 目标

Git 管理 Muse 的源代码、测试、文档和可复现工程配置；它不替代 SQLite 快照、模型备份或成片归档。

## 仓库边界

默认纳入版本管理：
- `src/`、`tests/`、`scripts/`
- `docs/`、`AGENTS.md`、`README.md`
- 配置模板（例如 `config/*.example.*`）
- 桌面端、Dashboard、Remotion 等可复现源码及锁文件
- 能够合法分发、且确属项目源码的轻量资源

默认不纳入版本管理：
- `data/`、`artifacts/`、`models/`、`vault/`
- SQLite 数据库、运行日志、研究运行结果、视频成片和工程 ZIP
- API Key、Token、`config/muse.toml`、机器专属 `config/model-runtime.json`
- `.venv/`、`node_modules/`、Rust `target/`、缓存
- `external/` 与 `third_party/` 的本地克隆、安装包和模型依赖

这些目录仍留在本机。需要备份时使用独立快照/归档流程；不要通过 Git 清理或重建它们。

## 日常开发

1. 开始任务前执行 `git status --short --branch`，确认当前分支和工作区。
2. 一个独立功能或修复使用一个短期分支：`feature/<topic>` 或 `fix/<topic>`。
3. 多智能体任务分配不同分支或独立 worktree；禁止多个 Agent 同时直接写同一工作树/同一文件。
4. 写入前读取目标文件；小步修改；运行与改动匹配的测试。
5. 检查 `git diff --check`、`git diff`、`git status`，确认没有密钥、数据库、模型、成片或无关文件。
6. 测试通过后提交。提交信息使用清晰的动词和范围，例如 `feat(research): add Crossref fallback`。
7. 合并前重新运行相关测试；主分支保持可验证、可构建。

## 回滚与恢复

- 查看历史：`git log --oneline --decorate -20`
- 查看改动：`git diff` 或 `git show <commit>`
- 丢弃单个已提交文件的改动前，先确认是否包含用户工作；优先用新提交修复，不轻易重写历史。
- 回滚已发布提交优先使用 `git revert <commit>`。
- 不对共享分支执行未经明确授权的 `reset --hard`、强制推送或清理命令。
- Git 只回滚已纳入版本管理的文件；数据库和媒体必须从独立备份恢复。

## 发布与验收

每个可交付版本应记录：
- Git commit / tag
- 相关测试命令及实际结果
- 关键产物路径、SHA-256 和媒体/包验证结果
- 已知限制与未完成项

没有实机证据，不把功能标记为实机完工。
