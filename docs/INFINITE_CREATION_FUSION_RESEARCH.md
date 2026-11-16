# Infinite Creation 超融合评估与商业试点方案

日期：2026-11-14
上游：<https://github.com/SwotAtmk/infinite-creation>
上游技能规范：<https://github.com/SwotAtmk/infinite-creation/blob/main/SKILL.md>
状态：研究完成；未克隆、未安装、未执行上游代码；未改变运行配置。

## 1. 决策摘要

**建议有选择地融合，不建议整仓替换或并行部署第二个总控。**

Infinite Creation 的价值在于把长文本转成连续的视听叙事：剧本/大纲、人物圣经、风格指南、角色/场景/道具/声音资产、分镜、逐镜生成、自审重试、音乐/字幕、合并导出、断点续跑。它尤其补足 Muse 当前尚未通用化的故事连续性和分镜级生成/重做方法。

Muse 已经有更适合充当长期产品底座的能力：唯一 Commander、GPU 资源仲裁、Wan/ComfyUI adapter、VideoProductBuilder、Remotion 动效、FFmpeg/FFprobe、技术 QA、Creator Initiative/Research Wiki、视频 attempt ledger、Dashboard 和四平台发布准备包。应由 Muse 继续拥有调度、权限、任务状态、资产账本、验证和交付。

## 2. 能力映射

| Infinite Creation 能力 | Muse 现状 | 融合决策 |
|---|---|---|
| 小说/想法转剧本与大纲 | 有高价值短片脚本和 research pipeline；尚未形成通用 story pipeline | 提取流程与结构模板，做 Muse Story Bible/Script/Storyboard schema |
| 人物圣经、风格指南、连续性记录 | 尚未形成通用资产模型 | 高优先级吸收；人物 canonical reference、服装/道具/场景状态必须版本化 |
| 资产生成与分类 | Muse 有 ComfyUI/Wan、本地素材输入与产品 manifest | 适配现有 ComfyUI allowlist；不引入上游 DB 作为第二真相源 |
| MiniMax H3 Ref2VA 逐镜生成 | Muse 当前已实测 AMD RX 6750 GRE 12GB + Wan 2.1 T2V 1.3B；H3 工作流尚未验证 | 只把 H3 当可选 provider；先做兼容性探针，不承诺当前 AMD 环境可运行 |
| 分镜级预览、重生成、自审 | Muse 有 storyboard DAG 与失败/attempt 账本，但完整镜头 Review 仍有缺口 | 吸收 shot-level state、feedback、retry/idempotency 设计，落到 Muse 的 ledger |
| 长任务断点续跑 | Muse TaskQueue/Worker 有 lease、heartbeat、recovery | 复用 Muse Worker；禁止复制上游后台任务状态机 |
| BGM、字幕、合并 | Muse 有 FFmpeg/Remotion 与媒体 QA；中文 TTS 尚未实机验收 | 复用现有媒体链；粤语 TTS 单独评估声音质量、授权与成本 |
| Web 工作台 | Muse 有桌面 Dashboard 和本地 API | 只吸收必要交互，不平行引入 Next.js 管理台 |
| Skill 工厂 | Muse 有 skill/provider registry 概念 | 逐个审查可复用 skill；不盲目导入 vendor 或官方模型技能 |

## 3. 运行环境与兼容性实测

本轮只读检查结果（2026-11-14）：

- Windows Runner 在线，协议/构建对齐；Runner 报告并发上限 32，当前 active jobs=0。
- Node.js v24.20.0、pnpm 9.15.0、FFmpeg/FFprobe 8.1.1 可用。
- Bun 命令当前不可用；上游 README 说明首次 `pnpm install` 会调用 Bun 构建内置 Agent 内核。
- 对 `http://127.0.0.1:8188/system_stats` 与 `/queue` 的只读探测均无法连接；本轮 ComfyUI 未确认运行。
- Muse 的既有文档记录 RX 6750 GRE 12GB 曾真实跑通 Wan 2.1 T2V 1.3B（低分辨率短镜头）；这不证明 H3、Qwen Image 2.1 或上游全部 workflow 可在当前机器运行。
- 上游 README 的示例硬件为 RTX 3080 20GB，并称 8GB 以上“理论上”可尝试，同时明确低显存适配仍在计划；这不是对 RX 6750 GRE / Windows ROCm 路径的兼容承诺。
- Muse 当前工作区为非 Git 项目；所有变更继续采用 read-before-write、文件版本 fence、最小变更和逐步验证。

**结论：不能现在整仓启动。** 缺 Bun、ComfyUI 当前不可达，H3/AMD workflow 未验证。禁止为“融合”而自动下载模型、安装未知 custom nodes、安装 Bun 或启动付费云 Provider。

## 4. 上游许可与供应链边界

- 上游仓库标示 MIT；商业复用须保留版权与许可声明。
- 上游 README 明确指出部分 `skills/` 来源于 MiniMax-H3 官方仓库、`vendor/` 为第三方代码，均不属于该仓库 MIT 授权范围，必须逐项遵循各自许可。
- 不复制整个 `vendor/openclaude`，不复制上游 Agent 内核/任务数据库/配置凭证/模型文件。
- 后续若引入源码：固定 commit SHA；记录文件来源、许可证、修改记录、依赖与安全审查；对无法确认许可的文件默认不纳入商业构建。
- 上游的无鉴权本机 API 只适合 loopback；不得把它改成 0.0.0.0 或直接暴露局域网/公网。若做 adapter，只允许 loopback、明确 API allowlist、请求体/路径边界和审计。

## 5. 推荐超融合架构

Muse Commander（唯一调度中心）
→ Muse Story Planner（新能力：故事大纲/人物圣经/风格指南/连续性账本）
→ Muse Storyboard Validator（版本化分镜 schema、依赖 DAG、时长/台词/画幅约束）
→ Muse Asset Registry（人物/服装/场景/道具/参考图/参考音频，哈希与授权状态）
→ Muse Generation Providers（现有 Wan/ComfyUI；未来可选 H3、其他 provider；每个 provider 有能力/显存/费用/许可证探针）
→ Muse Shot Review & Retry（分镜级预览、结构化反馈、幂等重做）
→ Remotion + FFmpeg（确定性转场、字幕、配音/音乐混音、横竖屏重构）
→ VideoProductBuilder + QA（技术/内容/版权门禁）
→ Creator Initiative + Research Wiki（作品、来源、实验、反馈、失败和演化周期）
→ 发布准备包 / 经授权官方发布 adapter
→ 官方分析与真实收益账本
→ 下一轮选题和创作实验。

**数据主权：** Muse 的 TaskQueue、Commander、GPU lease、Initiative Ledger、Research Wiki 和 ProductBuilder 是唯一权威；上游仅作为方法/技能/可选 provider 来源。不能复制上游数据库作为第二份事实源。

## 6. 以真实收入为目标的商业试点

### 先验证可销售的内容产品，不先追求无限时长

首个试点建议沿用 Muse 已有的研究型短片方法：围绕一个明确受众问题，做 10 条原创系列短视频。首个候选主题可从现有样片“别把 AI 的自信当成事实：三步查证法”扩展，但在制作前仍要重新核验资料和受众需求。

每条视频的交付标准：
1. 选题记录：目标受众、痛点、搜索/评论问题、差异化角度。
2. 研究档案：原始来源、支持/反证、事实与推断分离。
3. 脚本与分镜：开头 1–3 秒钩子、单一核心价值、粤语旁白优先（无可用且通过试听的 TTS 时不得假称有配音）。
4. 作品：9:16 母版、字幕、封面、来源/版权清单、平台文案。
5. QA：完整解码、音轨/响度、黑帧/冻结帧信号、字幕时序、封面和哈希。
6. 人工审片后手动发布；首轮不自动上传，不买流量，不使用未授权素材/声音/人物肖像。

### 收入路径排序

1. **可控的产品/服务转化**：把高价值内容沉淀为模板包、研究清单、工作流/提示词包、定制视频制作服务或小型课程；先验证有人愿意付费，再扩大生产。
2. **合规联盟合作**：只推荐确实使用过、适合受众且披露商业关系的工具/产品；用专属链接记录点击与有效转化。
3. **平台创作者分成/广告收益**：作为长期补充，按平台官方后台的合格播放、资格、RPM 和已结算金额记录；不把播放量折算成到账收入。

### 30 天验证计划

- 第 1 周：选一个细分受众，研究 20 个真实问题/竞争内容；产出 3 条可发布样片，完成粤语音频试听与人工审片。
- 第 2–3 周：发布 7 条，测试不同开头/标题/封面；记录平台后台曝光、有效播放、平均观看时长、完播/留存、关注、评论问题和链接点击。
- 第 4 周：发布复盘，建立首个可售小产品或服务页；只有在真实点击/询问/购买出现后才扩展产能。
- 预算：优先本地和免费额度；未知额度/收费 provider 一律关闭，不允许自动付费 fallback。

### 收益定义

必须分别记录：曝光、播放、留存、互动、关注、链接点击、线索、订单、退款、联盟佣金、平台预估收益、已结算收益、实际到账、制作成本和净收益。没有官方报表/支付凭证时写 unavailable，不推算成收入。

试点决策门槛（内部实验阈值，不是行业基准或收入承诺）：10 条完成发布；至少 2 条相对系列中位数在留存或有效互动上有可重复提升；出现至少 3 个明确购买意向/咨询，或 1 笔真实付费订单，才进入下一轮产品化。若无需求信号，调整受众/题材，不继续无意义批量生成。

## 7. 实施分期

- IC-M0（本轮完成）：上游研究、架构映射、硬件/运行状态探测、许可边界与商业试点设计。
- IC-M1（第一切片完成）：新增 `src/muse/story_bible.py`，定义 schema v1 的 Story Bible、Style Guide、角色/服装/地点/道具引用与逐场景 Continuity；提供 `validate_story_bible()` 和 `validate_story_continuity()`。`muse-video plan STORYBOARD.json --story-bible STORY_BIBLE.json` 可只读校验并将摘要纳入 Commander DAG 预览；无效引用会阻断计划，不会启动生成。新增 9 项 Story Bible/CLI 测试；与既有视频工作流定向组合 21/21 通过，全项目 unittest 1,176/1,176 通过。该切片不生成图像/视频，也不保存或复制外部素材。
- IC-M2：把 Muse 现有 storyboard DAG、Wan/ComfyUI 和 VideoProductBuilder 串成一个最小故事型垂直切片；验证断点续跑、单镜重做和资产一致性。
- IC-M3：补齐粤语旁白/字幕、镜头质量 Review、横竖屏重构和 Remotion 动效配方。
- IC-M4：完成账号最小权限 OAuth/官方发布、状态回读、分析与收益账本；每个平台单独核验资格、接口、政策和成本。

## 8. 本轮未做事项

未 clone 上游、未安装依赖、未安装 Bun、未安装 ComfyUI 节点/模型、未启用云端/付费 Provider、未改 Muse 运行配置、未发布内容、未连接社交账号、未产生或宣称任何收入。

## 9. 参考来源

- SwotAtmk/infinite-creation README：<https://github.com/SwotAtmk/infinite-creation>
- 上游主 Skill：<https://github.com/SwotAtmk/infinite-creation/blob/main/SKILL.md>
- Muse 现有方案：`docs/MUSE_HIGH_VALUE_VIDEO_FACTORY.md`
- Muse 现有架构研究：`docs/VIDEO_FUSION_RESEARCH.md`
- Muse 现有进度：`docs/PROGRESS_B122_HIGH_VALUE_SHORT.md`、`docs/PROGRESS_B134_MULTI_PLATFORM_DRAFT_PACKAGE.md`
