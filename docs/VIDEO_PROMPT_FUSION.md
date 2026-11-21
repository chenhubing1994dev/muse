# Muse Video Prompts 方法融合记录

更新时间：2026-11-22

## 上游参考与使用边界

参考项目：[apimodels/muse-video-prompts](https://github.com/apimodels/muse-video-prompts)。

该项目归纳了多镜头节拍剪辑、参考图人物/服装锁定、Master/Clip 结构、手机真实感、系列连续性、长视频分段和对话式指令等方法。README 与 GitHub About 的条目统计略有差异（205 / 204），Muse 不依赖条目数量，只吸收**可迁移的方法和结构**，不复制、镜像或批量入库创作者的完整提示词。上游明确声明每条提示词与成片仍属于创作者；CC BY 4.0 仅覆盖整理者自有的方法说明、标题、分类、数据文件和浏览页面等材料，不能据此推定创作者提示词可任意商用。

## 方法卡与 Muse 现有能力映射

| 上游方法 | 可复用的通用方法 | Muse 对应能力 | 当前状态 |
|---|---|---|---|
| 参考图模板 | 人物、服装、道具、场景分离；固定身份资产；每镜头重复关键锁定 | Story Bible 的 canonical asset、outfits、locations、props；VideoPromptComposer 的 master/clip locks | 已有基础映射 |
| 多镜头节拍剪辑 | BPM、首拍偏移、镜头时间线、切点容差、按节拍检查镜头边界 | VideoPromptSpec tempo/beat grid、clip_manifest、audio_plan | 本轮新增节拍锁定和切点校验 |
| Format / Master / Clip | 全片格式锁定；主提示词定义全局约束；每段复用身份与连续性约束 | VideoPromptComposer master_prompt、逐镜头 storyboard prompt、Story Bible continuity | 已有基础映射 |
| 手机真实感 | 明确拍摄媒介与允许的自然瑕疵，避免过度精修 | VideoPromptSpec capture_profile=phone_realism | 本轮新增可选档案 |
| 系列连续性 | 固定人物、服装、地点、道具与状态；每集只变更剧情变量 | Story Bible canonical assets、scene_states、continuity validation | 已有基础映射 |
| 长视频/多段脚本 | 将长内容拆成有依赖关系的镜头/片段，保留统一母版和时间线 | VideoWorkflow DAG、VideoStudio batch preview、VideoProductBuilder | 规划层已有；真实统一调度仍未打通 |
| 一句话/对话式指令 | 从自然语言意图提取创作目标、分镜、参考资产、限制和验收条件 | ChatGPT Bridge / Prompt Engine + VideoPromptSpec | 需继续建设自然语言到严格 Spec 的安全转换 |

## 本轮落地

- `VideoPromptSpec` 新增可选节拍配置：`tempo_bpm`、`beat_offset_seconds`、`beat_alignment_tolerance_ms`、`align_scene_cuts_to_beats`。
- 编译结果在 Master Prompt、每镜头 Prompt、clip_manifest 和 audio_plan 中保留同一节拍网格；要求卡点时，非末镜头切点不满足容差会在生成前拒绝。
- 新增 `capture_profile`：`cinematic`、`phone_realism`、`documentary`。手机真实感档案在 Master 与每个 Clip 中重复，避免只写在总提示词而逐镜头丢失。
- 新增测试覆盖 BPM/偏移/容差边界、切点偏拍拒绝、手机真实感锁定重复和未知档案拒绝。
## 验证记录

- 提示词相关专项：Composer、VideoPromptStudio、VideoWorkflow、PromptEngine **44/44 passed**，Python 3.11；修改源码/测试文件 compileall 通过。psmux Job `603fcb4373dc4a13b7a3c52e8d7b060c` exit 0。
- 单镜头 Worker handoff 后的历史全仓回归：2,270 tests / 0 failures / 0 errors / 1 skipped（B337）。随后 B339 BPM/节拍 handoff guard 最新共享快照再次全仓复验：Python 3.11 compileall 扫描 `src/muse`、`tests`、`scripts` 全部通过；**2,271 tests / 0 failures / 0 errors / 1 skipped**，206.402 秒；内层 psmux Job `a7cd4be771284a23b1ac41bf50bd1a63` `result.txt=0`，外层 WebCodex Job `wc_job_PH_H1o0RVbSOKTta` exit 0；最终 `muse-dev inspect` 为 `active_job_id=null`。
- Composer → Worker → narration/TTS/Builder/CheckpointAdapter/VideoStudio/visual-evidence 跨模块专项：**155/155 passed，0 failures/errors/skips**，Python 3.11，同一快照 compileall + unittest；内层 psmux Job `9becc6a0c55748d2a6d58f967690d367`、外层 WebCodex Job `wc_job_KUpd7ElKK8rqR9ZY` 均 exit 0。该组覆盖接口与模拟/单元流程，不代表真实 Wan/GPU 渲染。


## Composer → VideoStudio → Worker handoff

- `video_prompt_studio` 现在额外输出 `worker_handoff`，把编译分镜转换为当前 `creator_brief.video` schema 的候选，不创建 Initiative、不写 TaskQueue、不提交 Commander、不启动 Worker。
- 支持 1 个镜头的 Worker single-prompt 模式，或 2–20 个镜头的 multi-scene 模式；镜头均须 1–5 秒、总时长不超过 60 秒，依赖只能指向前序镜头。单镜头的 scene negative_prompt 会与全局负向提示合并并受 1,000 字符上限约束；多镜头逐镜头 negative prompt 与音频 cue 仍阻断 handoff。
- 多镜头逐镜头 negative prompt、超出 Wan 1.3B 单镜头时长、音频 cue/mood、BPM/节拍网格（当前 Worker 无音轨/节拍 sidecar 接口）或不安全依赖顺序会明确标记 `blocked`，不静默丢字段。全局 negative prompt 保留在 Worker 支持的顶层字段。
- 候选只包含 Worker 当前接受的 scene 字段（`prompt`、`duration_seconds`）；每镜头 seed 由 Worker 根据 Attempt ID 确定性生成。ready_for_review 仍不等于批准或可执行，Commander 是唯一调度/派发 authority。
- Composer + VideoPromptStudio + VideoWorkflow + PromptEngine 在 BPM/节拍 handoff guard 新增后最新定向回归 **45/45 passed**，修改的 VideoPromptStudio 源码/测试 compileall 通过；psmux Job `e7db2843d6364b8e9812eba71d25b4a9` exit 0。本次未执行真实 Wan/GPU 渲染。

## 仍需打通的接口

1. 多镜头 Composer 输出的逐镜头 `negative_prompt` 当前未完整穿过 Worker → Builder → CheckpointAdapter → Wan。必须将其传到 Wan 的负向 conditioning，并纳入每镜头 checkpoint input digest，防止负向约束变化后复用旧镜头。
2. VideoPromptStudio 当前仍是 preview-only；不能将 Commander DAG 预览误称为真实提交或可执行。InitiativeStore owner 已接手 Attempt admission grant 原子 bind / one-shot consume / revoke 契约；等待精确 API、实现与回归证据后，由主 Worker Session 接入消费端及 capability/role authorization。
3. 结构化旁白段落仍被 Worker flatten 成纯文本；当前 TTS runtime public API 只接受 text 并重新按标点切段，底层仅能插入 trailing PCM pause。主 Worker Session 已邀请 M5-TTS Session 接管新增 structured-segment runtime API；在契约与实现通过前，情绪/语速/音高/呼吸仍标记 unsupported，不得在 Manifest 中标记为已应用。
4. 长片分镜需要按具体视频模型的实际限制拆分/适配；不能仅因 Composer 接受 20 秒镜头，就假设本地 Wan 1.3B 的 1–5 秒单镜头生成器可直接执行。

## 验收原则

- 只把上游方法抽象为自己的结构化字段、校验规则和模板，不保存未获授权的完整创作者提示词。
- 保留 source_refs 的作者、URL、许可证、完整性与采集日期；来源不等于模型能力证明。
- 模型能力未知时保持 unknown；Prompt Composer 只负责编译，不调用模型、不派发任务、不启动 GPU。
- 只有 Composer → Story Bible → VideoWorkflow → Commander → Attempt → Worker → Builder → QA 的授权与证据链全部成立，才可把任务标记为可执行。
