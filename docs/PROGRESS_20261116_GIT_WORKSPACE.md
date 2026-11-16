# 2026-11-16｜Muse Git 工作区初始化

## 结果

- 项目路径：`D:\AI AGENT\Muse`
- 已从非 Git 目录初始化为本地 Git 仓库。
- 默认分支：`main`
- 首次基线提交：`a8ac2f2c7c2704fc50645ccda44d2c90e43312b4`
- 基线标签：`baseline-2026-11-16`
- 基线提交：429 个文件、92,291 行新增；Git loose objects 约 9.5 MiB。
- 完成后 `git status --short --branch` 只显示 `## main`，工作区干净。
- 尚未配置远程仓库，没有推送任何代码。

## 本次建立

- 根目录 `.gitignore`：排除本地数据库、模型权重、媒体成片、运行日志、研究运行报告、个人 Obsidian vault、密钥与机器专属配置、虚拟环境、node_modules、Rust target、第三方克隆等。
- 根目录 `.gitattributes`：启用自动文本换行识别。
- `docs/GIT_WORKFLOW.md`：定义分支、并行开发、测试、审查、提交、回滚与发布流程。
- 本地 Git 配置：Windows `core.autocrlf=true`、`merge.conflictstyle=zdiff3`、`rerere.enabled=true`、`pull.ff=only`、`fetch.prune=true`。

## 数据保护边界

所有被忽略的本机目录和文件都保留在原位置，没有执行删除或清理：
- `data/`、`artifacts/`、`models/`、`vault/`
- `external/`、`third_party/`
- `config/muse.toml`、`config/model-runtime.json`
- `logs/`、`reports/half-hourly/`
- `node_modules/`、`target/`、`.venv/`、模型和媒体输出

Git 不是数据库或大文件备份工具。SQLite 继续使用独立快照；视频、模型和交付 ZIP 继续保存在本机数据目录。

## 验证与已知事项

- Git 2.56.0.windows.4 可用。
- `git status`：基线提交后工作区干净。
- `git check-ignore` 已验证数据库、模型、第三方运行时、node_modules、target、机器配置、Obsidian vault 与定时报告均按规则忽略。
- `git diff --cached --check` 在首次基线检查中报告既有文件的尾随空格/空白行（主要来自历史进度文档与既有代码）。没有批量格式化历史文件，以免改变原有记录；后续新改动应尽量保持无此类问题。
- 本次只建立版本控制与保护规则，没有修改 Muse 业务逻辑，也没有重新运行全量业务测试。

## 后续约定

新功能先开 `feature/*` 或 `fix/*` 分支；并行 Agent 不得直接同时写同一工作树。每次合并前运行对应测试、审查 diff，并确认没有数据库、密钥、模型或媒体产物混入提交。
