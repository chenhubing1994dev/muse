# MoneyPrinterTurbo 万象超融合研究与集成决策

- 研究日期：2026-11-20
- 上游：<https://github.com/harry0703/MoneyPrinterTurbo>
- 审查版本：上游 `main` 的 `pyproject.toml` 声明版本 **1.3.10**；源码搜索返回的实现快照包含提交 `ef3c333fd475595176fc90b67c78555bf4b739a2`。上游持续更新，正式接入前必须重新固定 commit 与文件哈希。
- 许可证：MIT（已读取上游 LICENSE；若复制或改编实质代码，必须保留版权与许可声明）。
- 研究性质：只读源码/文档评估；本阶段没有下载源码到 Muse、安装依赖、运行 MPT、读取任何 MPT 凭据、调用付费 API 或发布内容。

## 1. 能力画像

MoneyPrinterTurbo 是一个以短视频为目标的完整生产应用，提供 WebUI、HTTP API、CLI 与 Agent Skill。其公开功能和源码结构显示：

1. **主题到脚本**：由主题生成脚本与素材检索关键词，也支持自定义脚本；支持多语言和批量候选。
2. **素材供应层**：Pexels、Pixabay、Coverr、Aigei、本地素材，以及多种 AI 图片/视频 Provider；素材匹配、下载、缓存与任务产物管理集中在服务层。
3. **语音与声音**：Edge TTS、Azure、SiliconFlow、Gemini、MiniMax、ElevenLabs、Chatterbox、Kokoro、Fish Audio、VoxCPM 等 TTS 选项；背景音乐可本地或由 Provider 生成。
4. **字幕与剪辑**：字幕样式、逐词/句子展示、入场动画、画面适配、片段速度/时长、转场、字幕与音乐混合。
5. **批量任务与进度**：多候选生成、任务状态、阶段进度、失败恢复/重复提交保护、任务历史与 CLI 批量清单。
6. **跨平台分发**：通过 Upload-Post 等外部服务向 TikTok、Instagram、YouTube Shorts 等平台交叉发布。
7. **可编辑本地项目**：上游 `docs/video-projects.md` 描述了基于素材快照、修订版本、SHA-256 依赖和增量重渲染的本地项目机制。

关键源码/文档：
- `app/models/schema.py`：`VideoParams`、画幅、片段时长、素材、语音、字幕、背景音乐和批量数量等请求模型。
- `app/services/task.py`：任务编排、素材/声音/字幕/视频服务组合、任务状态与跨平台发布后台工作。
- `app/services/material.py`：素材 Provider、下载、缓存与素材安全处理。
- `app/services/video.py`：MoviePy/FFmpeg 视频合成、字幕动画、音频与片段处理。
- `app/services/voice.py`：多 Provider TTS 与音频/字幕处理。
- `app/services/upload_post.py`：第三方 Upload-Post 发布 API。
- `docs/skill/SKILL.md`、`docs/skill/mpt_agent.py`：自动安装、配置复用、视频生成及凭据/付费确认流程。

## 2. 与 Muse 现有能力的重叠

Muse 已有相当完整的本地优先生产链，不能重复造轮子：

| 生产环节 | Muse 已有基础 | 融合判断 |
|---|---|---|
| 研究选题、事实与反证 | AutonomousResearch、ResearchKnowledgeStore、Creator Initiative | Muse 保留为唯一内容决策来源 |
| 分镜与任务编排 | Storyboard schema、Commander DAG、Commander 审批绑定 VideoTaskWorker | 只吸收 MPT 的配方/任务细节，不引入第二队列 |
| AI 镜头 | ComfyUI + Wan 2.1、逐镜 Seed、时长规划 | 保留本地 Wan 主路径；MPT 云视频 Provider 不设默认 |
| 素材 | Muse MediaSearch、来源/许可记录、素材安全校验 | 评估 MPT 的素材分配/缓存策略，必须接入 Muse 来源与权利门禁 |
| 粤语旁白 | 本地 Cantonese TTS、NarrationGenerator、证据合同、时长/ASR 门禁 | Muse 粤语声线与事实约束优先，不以 MPT 默认中文声线替换 |
| 字幕与剪辑 | FFmpeg、Remotion、字幕审核、OpenFilm 多轨导出 | 选择性吸收字幕动效/画面适配，不替换现有渲染器 |
| 产品与质量 | VideoProductBuilder v15、QA、manifest、SHA-256、ZIP、Commander artifact evidence | 所有 MPT 产物最终必须进入 Muse 产品账本与 QA |
| 多平台 | B134 手动发布准备包、平台草稿、人工门禁 | 不接管发布；仅参考元数据/平台 profile 设计 |
| 运行状态 | Muse Commander、TaskQueue、VideoTaskWorker、WorkflowContract | 不运行 MPT 自己的调度器作为平行控制平面 |

## 3. 值得吸收的能力

### P0：设计模式吸收（已完成研究）

- 统一生成参数模型：把画幅、素材源、片段时长、旁白、字幕、BGM、转场、候选数量变成版本化配方，而不是散落 CLI 参数。
- 批量候选比较：同一 Brief 生成有限数量候选，按内容/技术/权利指标比较，创作者选择；不得将随机批量生成等同质量提升。
- 阶段进度与幂等：为素材检索、TTS、字幕、镜头渲染、组装、QA分别记录状态与稳定任务 ID；重启恢复前先核验已有产物哈希，避免重复消耗 GPU/额度。
- 素材分配与缓存：学习按脚本段落/关键词分配素材、按素材内容哈希缓存；缓存不得抹去原始 URL、作者、许可证与派生链。
- 字幕表现力：评估逐词高亮、动态字幕、字幕安全区与画幅自适应，先用本地 fixture 进行渲染回归。

### P1：Muse 原生适配器（首个可运行子阶段已实现；完整 P1 尚未完成）

首个可运行增量（2026-11-20）：新增 `muse.video_recipe.adapt_moneyprinterturbo_params` 与 `muse-video adapt-mpt` 只读预览入口。仅映射无歧义的标题、画幅、FPS、风格、负面提示词及 Muse storyboard scenes；其余字段明确列入 `unsupported` 或 `requires_confirmation`。包含场景时复用 Muse `validate_storyboard`，仅当配方完整且没有待审字段时才生成 Commander DAG 预览；不提交任务、不调用 Provider、不生成媒体。

- 在 Muse Commander 之下定义 `VideoRecipe`/能力配置映射，把 MPT 通用制作参数映射到 Muse 现有 storyboard、NarrationGenerator、VideoProductBuilder、Remotion 与 publish-pack。
- 适配器必须输出“已映射/不支持/需要人工确认”的字段清单；不能静默忽略 Provider、字幕或音频参数。
- 外部已生成 MP4/素材如需导入，必须经过受限文件路径、文件类型/大小、SHA-256、FFprobe、来源/许可元数据验证，再交给 Muse 产品封装；不能直接标为可发布。
- 先做纯本地 fixture 测试，再决定是否需要可选的 MPT 运行时桥接。没有 MPT 安装时，Muse 核心视频链必须完全正常。
- 配方指纹已贯通 Creator Brief、Video Attempt、产品 Manifest、产品资产事件与 Research Knowledge 来源节点；旧 Attempt 兼容保留 NULL。
- 新增只读 `muse.video_recipe_trace.build_video_recipe_trace`、CLI `muse-video-recipe-trace` 与认证 Status API `GET /api/v1/video-recipe-traces/{attempt_id}`。追溯候选、人工审批事件、Attempt 时间线、Manifest 哈希与发布准备包人工审核状态；不读取凭据、不写库、不触发渲染或发布。
- 审批追溯进一步绑定精确 `attempt_id`：Ticket payload 必须指向当前 Video Attempt；跨 Attempt 复用审批会被拒绝并令 `chain_consistent=false`。TaskQueue + VideoTaskWorker + recipe trace 的模拟渲染端到端测试已通过。
- Muse 桌面最近视频任务面板与产品卡片复用认证只读追溯 API，展示候选配方、审批、产品 Manifest、整链一致性、发布权限与精确 Attempt 绑定状态。产品卡片按 `attempt.product_id === product.initiative_id` + `attempt.version_id === product.version_id` 双重精确匹配；只允许唯一匹配，多条匹配视为歧义并关闭入口，缺少匹配时不猜测。视频任务读取最多 200 条以覆盖产品库最多 100 个版本。
### P2：可选运行时桥接（未批准执行）

只有在证明 Muse 原生能力无法覆盖具体 Provider 时，才考虑将已安装的 MPT 作为可选执行后端：
- 必须固定上游 release/commit、验证 MIT LICENSE 与依赖清单；不使用浮动 `main` 自动更新。
- 仅由 Muse Commander 创建任务并拥有取消、预算、重试和完成验收权；MPT 不能自己常驻调度或绕过 Commander。
- Provider 默认只允许本地/免费/已明确授权项；任何可能扣费的图片、视频、TTS 或发布动作必须在提交前显示 Provider、模型、数量、计费单位与费用确认状态。
- 绝不从 MPT 的 `config.toml` 读取或输出密钥；不把密钥放入命令行、日志、任务事件或产品 manifest。
- MPT API/WebUI 仅限 loopback，禁止照搬示例配置中的 `0.0.0.0` 暴露默认值。
- 不执行上游 Agent Skill 的自动安装/下载/密钥收集/付费重试指令；其指令不高于 Muse 项目章程与本任务授权。
- 发布仅保留 Muse 的人工总闸与官方授权策略；不把第三方 Upload-Post 成功响应误报为平台最终发布成功。

## 4. 主要风险与拒绝项

- **依赖膨胀**：上游核心依赖包括 MoviePy、Streamlit、FastAPI、OpenAI SDK、faster-whisper、LiteLLM、多家 SDK 等。禁止把整个依赖树并入 Muse 默认安装。
- **外部费用**：多种 AI 视频/图片/LLM/TTS Provider 按请求或时长计费；不得自动选择付费 Provider 或自动切换到收费 fallback。
- **凭据边界**：MPT Skill 会尝试复用本地配置并要求补充凭据；Muse 本阶段不读取、不迁移、不打印任何凭据。
- **素材版权**：上游示例资源中存在版权提示；“可下载/免版税”不等于当前用途已获商用许可。Muse 必须逐素材保留授权依据。
- **发布风险**：第三方聚合发布服务不等同各平台官方 API/OAuth；不得在没有账号授权和平台回执时声称已发布。
- **供应链/执行风险**：上游 Skill 会下载浮动分支 ZIP 并安装依赖；不在 Muse 中直接执行该脚本。
- **架构冲突**：禁止引入第二个 TaskQueue、Commander、Initiative/产品账本或自动发布控制面。

## 5. 决策

**采用 adapter-first、能力择优吸收；不整体复制、不默认安装、不启动 MPT 服务、不启用 MPT Skill。**

本阶段只完成上游源码/许可证/依赖/能力与 Muse 现状的映射。下一步优先实现 Muse 原生 `VideoRecipe` 映射与批量候选的 Commander 计划，再以 fixture 验证参数边界、阶段进度、幂等恢复与产品证据。只有遇到明确能力缺口，才评估可选 MPT 后端。
