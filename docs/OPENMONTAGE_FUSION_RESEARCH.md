# Muse × OpenMontage：AI 视频制片厂融合研究

日期：2026-11-21
上游：<https://github.com/OpenMontageInc/OpenMontage>
上游许可证：AGPL-3.0（以仓库 LICENSE 为准）研究边界：本轮只读检查 README、架构文档与 Backlot 文档；未 clone、安装、运行或复制上游源码。

## 结论

OpenMontage 值得吸收的是一套可读、可审查的制片方法，而不是把它整个项目复制进 Muse：

1. **声明式流水线**：用 YAML 描述阶段、工具、检查与人工创意门；阶段指导与执行工具分离。
2. **可恢复检查点**：每阶段保存产物、状态、审查与成本快照；失败后从最后完成的阶段恢复。
3. **真实素材优先**：纪录片路线从开放档案/免费素材检索真实动态镜头，而不是把静态图片冒充视频。
4. **逐镜创意门**：素材生成后先展示镜头 contact sheet、提示词、质量与成本，人工批准后才继续最终渲染。
5. **预算治理**：调用前估算、预留、调用后核对；未知价格/配额必须阻断付费调用。
6. **渲染器按视觉语法选择**：Remotion 适合数据驱动/React 场景；HyperFrames 适合 HTML/CSS/GSAP 动态图形；FFmpeg 负责后期。
7. **双层 QA**：先做 pre-compose 合约检查，再做 render 后 probe、全解码、抽帧、音频与交付承诺核验。
8. **本地可观察看板**：Backlot 从产物/检查点/事件推导制作进度，使用 SSE 更新，并能回放历史运行。

上游 README 当前写的是“10+ production pipelines”，项目宣传数字并非稳定契约；Muse 不把宣传数字当作已验证实现清单。本项目建立的是自己的 12 个工作流模板，不声称与上游 12 条流水线一一对应。

## 与 Muse 现有能力的映射

| OpenMontage 机制 | Muse 现有可复用模块 | 融合判断 |
|---|---|---|
| Agent-first stage planning | video_workflow、CommanderPlan、唯一 Commander | 保留 Commander；不复制上游无代码编排器 |
| Declarative pipeline profiles | 12 个 profile 收敛到一个 YAML 1.2 JSON-compatible catalog，配套 stdlib strict loader 与 plan-only DAG | Profile catalog/loader 已落地；完整 stage manifest 与 Commander 执行 admission 仍待接线 |
| Story/script/shot continuity | video_prompt_composer、story_bible | 直接复用 |
| Tool registry / FFmpeg skill | ffmpeg_skill、media、video_factory | 复用现有 typed argv 与 probe-first |
| Real footage corpus | media_search、video_highlights | 仅部分具备；开放档案语义检索/embedding corpus 待做 |
| TTS/music/subtitles | cantonese_tts、chimera_vocal、video_audio_mix、ASR 与 subtitle review | 复用现有本地链路；不增加强制云 API |
| Product/checkpoint provenance | VideoProductBuilder、Creator Initiative、Video Attempt、recipe trace | 复用现有产品账本，不建第二个数据库 |
| Post-render quality | video_qa、ffmpeg_skill probe、哈希/ZIP 校验 | 已有核心技术门禁；审美/权利审查仍需人工 |
| Backlot living board | 现有 status API、ProgressEventStore、桌面 UI | 尚未形成逐镜实时看板；列入后续阶段 |

## 本轮实现

- 新增 src/muse/video_studio.py：建立 12 个可读制片 profile（参考再创作、科普、纪录片、预告片、产品广告、口播、屏幕演示、播客再利用、切片工厂、角色动画、3D 动画、本地化配音）。
- src/muse/video_pipelines.yaml 是 12 个 profile 的唯一声明式来源，使用 YAML 1.2 的 JSON-compatible 子集；标准库 JSON parser 可读取，无需新增 PyYAML 依赖。严格 loader 拒绝重复键、未知字段、无效/重复/保留阶段、重复 ID 和超限文件；Python dataclass 仅作为校验后的运行时对象。阶段角色、目标与 capability hint 已迁入独立 video_stages.yaml；loader 以 CommanderPlanner.ALLOWED_ROLES 做角色枚举校验。capability_hints 只是计划元数据，不是执行授权；真实能力存在性、角色能力映射与成本仍须在 Commander admission 阶段核验。
- 新增 build_video_studio_plan()：复用 Muse 的 storyboard 校验与 Commander DAG；支持镜头依赖、独立镜头并行波次，并把创意门/最终发布审批显式列入计划。该 DAG 当前仅为预览；准入评估按 Commander 默认 16-step 上限 fail-closed 标记超限计划。即使未超限，仍须核验本机 capability registry、角色-能力授权、资源/成本与人工审批。
- 新增 muse-video-studio pipelines|plan CLI。仅输出 profile 目录或 Commander 计划预览，明确 execution=not_started、commander_submission=not_submitted；不生成媒体、不创建任务、不花费 API 额度。
- 不新增依赖、不 clone 上游、不安装包、不调用云 API、不启动服务；不修改发布权限。

## 许可证与供应链边界

OpenMontage 仓库标记 AGPL-3.0。本轮只借鉴架构思想与公开接口行为，没有复制源文件或上游技能内容。若未来需要直接复用/修改其代码、工具或 Backlot 前端，必须先完成 AGPL 义务与分发/网络服务方式的专项审查，保留来源、版本、修改记录及许可证文本；不能把 AGPL 代码误当 MIT/Unlicense 处理。

## 尚未完成 / 下一阶段

1. 将 stage 角色/目标/工具提示进一步迁入声明式 manifest，并设计与 Commander role allowlist、本机 capability registry 的安全映射；当前 CLI 仍只做预览。
2. 设计基于现有 InitiativeStore/Video Attempt 的阶段 checkpoint，不另建独立队列或产品账本；定义崩溃恢复与幂等边界。
3. 实现真实素材 corpus：开放档案元数据、许可/署名、下载安全、时长/大小限制、语义检索和镜头候选审核。
4. 建立本地逐镜 Backlot 风格看板，复用 Status API/ProgressEventStore；逐镜审批与暂停/恢复必须由 Commander 决定。
5. 统一预算估算/预留/对账；本地免费能力优先，云 Provider 仅在已验证免费额度与明确授权时可选。
6. 做真实端到端样片验收：研究→脚本→分镜→素材→旁白/音乐/字幕→剪辑→技术 QA→创意/权利审核；分别记录机器测试与人工观感结论。

## 上游参考

- README：<https://github.com/OpenMontageInc/OpenMontage/blob/main/README.md>
- Architecture：<https://github.com/OpenMontageInc/OpenMontage/blob/main/docs/ARCHITECTURE.md>
- Backlot：<https://github.com/OpenMontageInc/OpenMontage/blob/main/backlot/README.md>
- License：<https://github.com/OpenMontageInc/OpenMontage/blob/main/LICENSE>
