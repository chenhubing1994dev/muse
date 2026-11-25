# ISSUES.md — Muse 问题清单

> 生成：2026-11-25（阶段 2）· 方法：只读静态分析（AST 语法/导入/名称核对、编码与换行扫描、硬编码路径、异常处理、重复代码、依赖声明、日志取证）。
> 未执行项目代码（环境限制见 PROGRESS.md）；敏感配置文件未读取内容。
> 状态图例：⬜ 待修复 · 🔧 修复中 · ✅ 已修复 · ⏸ 需用户处理 · 📝 仅记录

## 汇总

| 严重程度 | 数量 |
|---|---:|
| 致命 | 2 |
| 重要 | 7 |
| 一般 | 8 |
| 建议 | 5 |

## 修复状态（2026-11-25 16:40 更新，详见 CHANGELOG.md）

| 编号 | 状态 | 说明 |
|---|---|---|
| F1 | ✅ 已修复（待运行验证） | 第 180 行拆行；**另发现第 283–285 行丢失一整行**（反驳分支 `elif` 无语句体）且有重复语句，保守恢复为 `conclusion = "refuted"`。⚠ 该文件未入 Git、无原件可对照，**请人工复核 F1-b**；冒烟脚本 S7 含反驳场景验证 |
| F2 | ✅ 已修复 | 已提交版本即损坏，仅恢复换行 |
| I1 | ✅ 已修复 | video_product_builder 7 处 FFmpeg/FFprobe 调用加 `encoding="utf-8", errors="replace"`；model_supervisor 2 处 schtasks 加 `errors="replace"` |
| I2 | ✅ 已修复 | 695 行乱码回转为正常中文；2,727 个原损坏时已丢失的字符标为 `�`（无法恢复，不猜测）。原文件完整备份 |
| I3 | ✅ 已修复 | research_wiki_audit 命令改为下划线模块名；快速开始统一 `py -3.11` |
| I4 | ✅ 已修复 | pyproject 新增可选依赖组 speech / metaphysics / imaging / local-llm（未安装任何包） |
| I5 | ⏸ 需你处理 | 未读取该文件 |
| I6 | ⏸ 需你处理 | 今日 13:33–15:01 另有 Agent 修改 research/lease/video 模块；请暂停后再跑测试 |
| I7 | ↘ 降级为建议 | 与 Git HEAD 对比：66 处同行拼接中，除今日新建的 research_state_machine（已修 131/185/313 行）外，**全部与已提交版本一致**，属历史代码风格、语义无误，不再改动以免与其他 Agent 冲突 |
| G1 | ✅ 已修复 | `local_dashboard` 早已处理（日志为旧记录）；为 `status_api.BoundedStatusServer` 补上同样的 `handle_error` |
| G2 | 📝 已核对，不改 | desktop/ruflo/software_crafting/video_capabilities/open_design 均已有 `MUSE_*` 环境变量覆盖；media.py 为“候选探测路径”，不存在即报告不可用，且被 test_media 断言；改动收益低、风险高 |
| G3 | 📝 已核对，不改 | 14 处均为清理/遥测路径的有意吞没（部分有注释说明），改动会影响 fail-safe 语义 |
| G4 | 📝 暂缓 | 涉及文件正被其他 Agent 编辑，统一换行会产生大面积 diff；建议在 `.gitattributes` 层面处理 |
| G5 | ✅ 已解决 | 新增 `scripts/muse_smoke.py`，日志直接以 UTF-8 写入 `reports/qw_smoke/` |
| G6 | 📝 仅记录 | 不删除 |
| G7 | 📝 暂缓 | 显式 `[build-system]` 会让下次 `pip install -e .` 联网拉取 setuptools，按约束不改 |
| G8 | 📝 仅记录 | |

### 新发现（修复过程中）
- **N1（重要）系统性写入损坏的来源**：research_state_machine / research_task_handler / test_research_* 均为今天 13:55–14:00 由其他 Agent 新建且未入 Git；同一批写入中出现“换行丢失 + 整行丢失 + 重复行”。说明该 Agent 使用的写文件工具存在缺陷，**今天其他未入 Git 的新文件也可能有同类问题**（candidate_enqueuer.py、lease_manager.py 等语法通过，但无法与原件对比）。建议让该 Agent 复核其 13:00 之后的输出。
- **N2（一般）`ember-fire/package.py` 损坏已被提交**：说明 CI/提交前没有对 ember-fire 子工程做 `py_compile`。

---

## 致命

### F1 ⬜ `src/muse/research_state_machine.py` 语法错误（换行丢失）
- **位置**：第 180 行 `start_char = citation.get("start_char")            end_char = citation.get("end_char")`（两条赋值语句被拼到同一行 → SyntaxError）；第 185 行 `if not _valid_sha(...):                 continue` 同类损坏（语法上合法，但属同一次损坏）。
- **影响**：模块无法导入 → `research_task_handler` 无法导入 → Worker 执行研究类任务时在延迟导入处失败；`tests/test_research_state_machine.py`、`tests/test_research_task_handler.py` 整个模块加载失败。
- **补充**：该文件**未被 Git 跟踪**（.git/index 中不存在），也没有找到任何备份副本，只能按上下文恢复。
- **修复方案**：把两处拆回独立行，保持原缩进；不改逻辑。

### F2 ⬜ `ember-fire/package.py` 语法错误（换行丢失）
- **位置**：第 5 行 `INCLUDE=[...]with zipfile.ZipFile(...)`，列表结尾与 `with` 语句拼在一起。
- **影响**：《火种不灭》工程打包脚本无法运行（范围限于 ember-fire 子工程）。该文件已被 Git 跟踪，可与 HEAD 对照。
- **修复方案**：在 `]` 与 `with` 之间恢复换行。

---

## 重要

### I1 ⬜ 子进程文本解码未指定编码（Windows 中文系统风险）
- **位置**：`video_product_builder.py` 第 151、175、1224、1881、2226、2246、2259 行；`model_supervisor.py` 第 285、298 行 —— `subprocess.run(..., text=True)` 未传 `encoding`。
- **原因**：中文 Windows 默认按 cp936（GBK）解码；FFmpeg/FFprobe 的 stderr 是 UTF-8，素材路径或元数据含非 GBK 字符时会抛出 `UnicodeDecodeError`，且当前未捕获 → 主流程 C 的探测/QA 环节崩溃。
- **修复方案**：FFmpeg/FFprobe 调用统一加 `encoding="utf-8", errors="replace"`；`schtasks` 调用保持系统编码但加 `errors="replace"`。

### I2 ⬜ `docs/PROGRESS.md` 约 689 行乱码（UTF-8 被按 GBK 误解码后回存）
- **位置**：全文 3,724 行中约 32 个片段（如第 1–200 行附近），典型乱码如 `鎸佺画杩涘害璁板綍`（原文“持续进度记录”）。
- **影响**：文档自述“每次新会话必须先读本文件”，交接主文档部分不可读。
- **修复方案**：逐行检测，对可逆的行做 `gbk→utf-8` 回转；不可逆（含 `?` 丢字）的行保留原样并标注。**修改前单独备份原文件**。

### I3 ⬜ README 运行命令错误 / 版本口径不一致
- `py -3.11 -m muse.research-wiki-audit`：模块名含连字符，`-m` 无法运行；应为 `-m muse.research_wiki_audit`（或已安装的 `muse-research-wiki-audit` 命令）。
- 快速开始混用 `py -3.14` 与 `py -3.11`，而 `.venv` 为 3.11.9、`pyproject` 要求 ≥3.11。
- **修复方案**：修正命令；统一为 `py -3.11`（或 `.venv\Scripts\python.exe`），并说明 3.14 仅用于粤语 TTS 隔离环境（`.muse-canto-tts-py314`）。

### I4 ⬜ 可选第三方依赖未声明
- 代码中使用但 `pyproject.toml` 未声明：`faster_whisper`（cantonese_asr）、`canto_tts`（cantonese_tts）、`celestial_calendar`、`llama_cpp`（gemma_intent）、`PIL`/Pillow（kinetic_explainer、smart_crop）、`piper`（piper_tts）、`ziwei`（ziwei_adapter）。
- 已声明：cryptography、scrapling、playwright、numpy、opencv-python-headless。
- 无 `requirements*.txt`。
- **修复方案**：在 `[project.optional-dependencies]` 增补分组（speech / metaphysics / imaging / local-llm），生成 `requirements-core.txt`（空依赖说明）与 `requirements-optional.txt`；**不自动安装**。

### I5 ⏸ 疑似敏感本地配置被 Git 跟踪：`config/opencode.local.jsonc`
- `.gitignore` 排除了 `config/muse.toml`、`config/model-runtime.json`，但没有排除 `opencode.local.jsonc`，且该文件**已在 Git 索引中**。
- 按约束本次**未读取其内容**。若其中含 API Key/Token，可能已随提交推送到 GitHub（项目有 GitHub 同步脚本）。
- **需要你处理**：自行检查该文件；如含密钥，请轮换密钥，并决定是否 `git rm --cached` + 加入 .gitignore（我可在你确认后修改 .gitignore）。

### I6 📝 并发修改风险：今天有其他 Agent 在改这个仓库
- `docs/PROGRESS.md` 末尾记录了 2026-11-25 的 video_factory “模块 3” 工作（`.muse-backups/video_factory/2026-11-25/...`），说明今天另有自动化/Agent 在写这个目录。
- **影响**：如果同时修改，可能互相覆盖；“换行丢失”类损坏也可能来自某个写文件工具。
- **建议**：阶段 3 开始前，暂停其他 Agent/定时同步（如 `Muse-GitHubWatch.ps1`、`Install-Muse-GitHubBackupTask.ps1` 安装的任务）。

### I7 ⬜ 同类“换行丢失”损坏的其他位置（语法合法，但已被破坏）
以下 10 处都是 `header:` 后跟大量空格再接语句，或两段被拼接；AST 能解析，语义未变，但说明存在系统性写文件损坏，需要恢复可读性：
- `src/muse/autonomous_research.py:2635`、`:3084`
- `src/muse/autonomous_runtime.py:1223`
- `src/muse/lease_manager.py:333`
- `src/muse/process_limits.py:58`
- `src/muse/research_state_machine.py:131`
- `tests/test_local_dashboard.py:197`、`tests/test_video_product_builder.py:2063`
- `scripts/validate_commercial_assets.py:89`
- `docs/ARCHITECTURE.md`（“Obsidian …”与“Tools …”两条、“Outbox”条目被拼接）
- **修复方案**：逐处拆行，仅改空白，不改逻辑。

---

## 一般

### G1 ⬜ 本地 Dashboard 未处理客户端断开
- `logs/dashboard-task.log`：`ConnectionAbortedError`×30、`ConnectionResetError`×4、`TimeoutError`×2，栈在 `local_dashboard.py:124 _send_bytes`、`:281 _proxy_api`、`status_api.py:404 _send`。
- 浏览器刷新/关闭时产生完整 traceback，污染日志（功能不受影响）。
- **修复方案**：`_send_bytes`/`_send` 捕获 `ConnectionAbortedError/ConnectionResetError/BrokenPipeError` 并静默结束本次响应。

### G2 ⬜ 硬编码绝对路径（29 处）
- 主要：`media.py`（OBS、Audacity、Studio One、Piper 可执行文件与模型路径）、`desktop_adapter.py:43`、`ruflo_adapter.py:101`、`software_crafting.py:32`、`video_capabilities.py:196`、`open_design.py:228`（已有环境变量兜底）、`inspiration_evolution.py:639`（`<MUSE_ROOT>\.venv\...` 测试命令）、`scripts/muse-inspiration-flagship-video.py:40-41`（`%USERPROFILE%\...`）、`scripts/validate_desktop_rust.py:11`。
- **修复方案**：保留现值作默认值，增加 `MUSE_*` 环境变量覆盖；`inspiration_evolution` 改为基于 `sys.executable` 与项目根推导。

### G3 ⬜ 吞掉异常（`except Exception: pass`，14 处）
- capability_radar:390、chatanywhere_smoke:78、contextual_inquiry_security:100、lightpanda_adapter:118/262/267、open_design:334、status_api:1528/2970、test_runner:69、unified_runtime:132/1502/1512、worker:691。
- 多为清理/关闭路径，合理但无日志。**修复方案**：逐处确认，必要时改为 `logging.debug(..., exc_info=False)`，不改变控制流。

### G4 ⬜ 混合换行符（CRLF/LF 混用，7 个文件）
- `src/muse/open_design.py`、`open_design_evidence.py`、`tests/test_narration_prosody_bridge.py`、`tests/test_open_design.py`、`docs/OPEN_DESIGN_INTEGRATION_AUDIT_20261116.md`、`docs/PROGRESS.md`、`docs/PROGRESS_B125_COSMIC_DESKTOP_UI.md`。
- **修复方案**：统一为 LF，并在 `.gitattributes` 中声明 `*.py text eol=lf`。

### G5 ⬜ 全量测试日志为 UTF-16 且被 PowerShell 包装成 NativeCommandError
- `full-suite.log` 是 PowerShell 5.1 `*>` 重定向产生的 UTF-16LE，stderr 每行被包装成错误记录，难以 grep/对比。
- **修复方案**：提供 Python 编写的一键测试脚本，直接以 UTF-8 写日志（阶段 4 交付）。

### G6 📝 根目录杂项文件（疑似误生成）
- `=`（内容是一段 Ubuntu `os-release`/tmux 信息，像是 shell 重定向写错了文件名）、`@{consecutive_link_misses=0}`（PowerShell 哈希表被当成文件名）、`tmp3s0ocv0y/`、`agent-execute-schema.txt`、`agent-spawn-schema.txt`、`679BDD59….jpg`。
- 按约束**不删除**。建议由你确认后移动到 `archive/root-strays/`。

### G7 ⬜ `pyproject.toml` 缺少 `[build-system]`
- 依赖 pip 的隐式回退；建议显式声明 `setuptools>=68` 与 `[tool.setuptools.packages.find] where=["src"]`。

### G8 📝 外部环境依赖较多，主流程 C 无法在无 GPU/模型时完整运行
- Wan/ComfyUI、粤语 TTS/ASR、LM Studio/llama.cpp 都是可选外部服务；代码已普遍 fail-closed。冒烟测试将使用“本地素材 + 确定性 handler”路径。

---

## 建议

### S1 重复函数：`model_capabilities._timestamp` 与 `usage_ledger._timestamp` 完全相同，可抽到公共工具模块（低优先级，避免牵动面）。
### S2 死代码/重复职责：`interfaces` 模块零引用；`prototype_builder`（无调用方）与 `product_builder` 职责重叠；`pipeline/` 已在 README 标注弃用。建议标注 deprecated，不删除。
### S3 仓库体积 44 GB：`desktop/`（14 GB，主要是 Rust `target/`）、`tts-engines/`（12.6 GB）、`data/`（9.3 GB）。建议把构建产物与引擎移出项目根或用 junction 指向其他盘，便于备份。
### S4 缺少统一的“一键冒烟/端到端”入口：README 列了很多命令，但没有单条命令验证主流程（阶段 4 交付 `scripts/muse_smoke.py`）。
### S5 `docs/PROGRESS.md` 已 47 万字符，建议按批次归档拆分，根目录 `PROGRESS.md` 只保留当前状态。

---

## 已核对且未发现问题的项
- 65 个 `muse-*` CLI 入口全部指向存在的模块和 `main()`。
- 除 F1 外，src/tests/scripts/tools 中的 muse 内部导入（模块与名称）全部可解析。
- 全部源码兼容 Python 3.11 语法（`feature_version=(3,11)` 解析通过）。
- 源码中没有裸 `except:`；983 个文本文件均为合法 UTF-8（无 UTF-16/非法字节）。
- 无 `TODO/FIXME` 遗留；3 处 `NotImplementedError` 均为有意的能力拒绝。
