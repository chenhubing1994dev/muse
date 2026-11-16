# PROJECT PROMETHEUS｜普罗米修斯计划

> 让知识之火不断传递，让未知成为探索的起点。  
> 把思想带到思想从未抵达的地方，再把灵感带回现实。

## 计划定位

PROMETHEUS 是 Muse 的长期使命与创造计划，不是另一个调度器、独立 Agent 或平行产品。它为 Muse 提供共同的精神方向：发现知识之火、理解其结构、跨领域传递、重新组合，并让新的想法成为可体验的作品与能力。

“普罗米修斯”在这里是知识、创造、勇气与传承的象征，不是要求无边界执行。探索可以大胆，现实行动必须可审计、可停止、可验证。

核心治理依据：`docs/MUSE_CONSTITUTION.md`。

## 五个行动领域

1. **点火｜Ignition**：从用户灵感、知识缺口、历史研究、失败经验、自然现象、文化符号、艺术作品与新兴技术发现探索种子。
2. **求知｜Discovery**：跨领域搜索、阅读、比较、复核来源，寻找反例、替代解释和未知问题。证据不足时仍可保留大胆假设。
3. **融合｜Synthesis**：提取概念、机制、结构、关系和审美语言，进行远距离联想、矛盾碰撞、类比迁移、逆向设计与组合实验。
4. **创造｜Creation**：将高价值想法编译为方案、任务 DAG、原型、影片、音乐、艺术、游戏、工具或软件产品；由 Muse Commander 统一编排本地能力与专业 Agent。
5. **传承｜Transmission**：把来源、决策、提示词、工作流、实验、失败、测试、成品和复盘写入长期知识与进度账本，让下一轮从已有成果继续。

## Muse 的创造工坊：Idea-to-Reality

灵感输入  
→ 创意解构与多视角扩展  
→ 研究/素材/授权检查  
→ 导演或产品策划  
→ 分镜、设计稿、任务图与资源预算  
→ ComfyUI/Wan、图像、音频、语音、剪辑等能力执行  
→ FFmpeg/媒体工具合成与质量检查  
→ 产品封装（成品、工程资料、字幕、封面、manifest、ZIP）  
→ Dashboard 展示、预览、下载  
→ 人工审片/验收与反馈回流。

这是目标工作流。每个环节必须由实际接通的模块支撑；缺失的环节要明确标为规划或阻塞，不可用静态演示冒充执行。

## 视频创作工具融合策略（当前盘点基线）

| 能力 | 当前发现 | Muse 中的定位 |
|---|---|---|
| Muse Commander / video workflow DAG | 已有分镜校验与确定性 DAG 计划 | 唯一导演/调度入口；仍需确认完整执行 DAG 接线 |
| ComfyUI 0.23.0 + Wan 2.1 T2V 1.3B | 本机实机生成曾成功；当前 engines 探测 ready | 本地 AI 镜头生成主引擎 |
| FFmpeg 8.1.1 + FFprobe | 已发现；AMD AMF H.264/HEVC/AV1 可用 | 确定性剪辑、转码、混音、编码与验收 |
| VideoProductBuilder | 已接入产品库、MP4/ZIP/manifest 封装与校验 | 统一视频产品交付 |
| Muse Dashboard | 已有视频产品任务、预览/下载 API 与产品库 UI | B-089 teaser 与 B-090 66 秒横/竖版完整片：列表、预览、MP4、ZIP 实机验收均通过；已修复 10 MiB 代理限制，六条大文件路由 HTTP 200 且 SHA-256 一致 |
| Velorn MCP | 当前探测 unavailable | 可选时间线/字幕/导出编辑层；恢复前不作为必需依赖 |
| OBS Studio | 本机路径探测存在 | 屏幕录制、功能实机演示素材 |
| Audacity | 本机路径探测存在 | 旁白/音乐清理、音频修整 |
| Studio One 6 | 本机路径探测存在 | 音乐混音与母带候选 |
| ChatTTS WebUI | 2026-11-12 复核：启动脚本/代码存在，但 models/ 下 11 个必需文件全部缺失，无服务进程；README 标注 NVIDIA，当前 AMD 主机不匹配 | 暂不可用；不启动以免触发未授权模型下载，另评估 AMD/CPU 可用的本地中文 TTS |
| Piper TTS 1.5.0 | 独立运行时与 `piper.exe` 已安装；Muse 侧支持 Python API 与 CLI 子进程两种本地执行路径，CLI 参数已按本机 `--help` 核验；模型缺失，媒体路由状态为 partial | 本地 CPU 中文旁白执行适配已接入并通过 mock WAV 验证；待本地 ONNX/JSON 语音资产到位后进行真实合成 |
| Boilsoft Video Splitter | 本机路径探测存在 | 快速素材裁切候选 |
| HD Video Converter Factory Pro | 本机路径探测存在 | GUI 转码/格式兼容候选 |
| 专业非线性时间线编辑器 | 当前 inventory 未确认 | 现阶段以 FFmpeg 自动化剪辑；不得声称具备完整 NLE |

**注意：**“路径存在”只表示发现候选程序，不代表本轮已启动、已验证质量或已接入自动流水线。Velorn 当前 unavailable；ChatTTS 模型与服务未验证。不要重复安装同类工具，不要同时引入两个主时间线编辑核心。
### 2026-11-12 第二版素材复核

- 已从真实运行的 Muse Dashboard（Chrome 实际窗口）保存首页截图：`data/prometheus/film-v2/real-dashboard-home.jpg`，1920×1036，SHA-256 `17e8fa0003ec041d247feaa6954c594a4ba6e4bfbaecfff0148d347439fda85f`。
- 此素材是实际 UI 快照，尚非动态录屏；未合成到第二版影片。产品库、研究工作台、Commander/Agent 等实际页面仍需逐个采集，不得用 AI 镜头代替真实操作证据。
- ChatTTS 当前无模型资产且主机为 AMD，系统无可用 SAPI 语音；旁白暂缓，不静默切换云端服务。
- Piper TTS 1.5.0 的独立 CPU 运行时已准备并通过 import smoke test；选择 CC0 的 `zh_CN-chaowen-medium`（官方模型卡标注 CC0，需 Piper 1.5+），但模型下载被安全检查拦截，未生成语音、未接入 Muse。Xiao Ya 因模型卡标注 Non-commercial 未选用。
- B-093 新增 `src/muse/piper_tts.py`、`muse-piper-tts` CLI、Piper 能力路由及 4 个适配器测试；媒体/TTS 专项 9/9 通过。适配器仅读取本地模型，不会自动联网下载；模型缺失时能力状态是 `partial`。
- B-094 新增 `scripts/record_dashboard_capture.py`；实录 Muse 产品库页面 12 秒，H.264/1920×1080/15 FPS，SHA-256 `bbd39813e1f3f1d0041a05ed9a3bc50349cd85e9d1a1051105fd9b052f478255`，FFmpeg 全片解码通过。另裁出真实 PROMETHEUS 产品行并合成 5 秒 UI 镜头 `real-dashboard-product-card.mp4`（24 FPS，SHA-256 `bf060fecb3f17546eb7e9ceb2f473e1b859dc5434fcf26b03857922dc58d4d9a`）。
- 配对码保护：原始首页快照包含临时 LAN pairing code，不得进入宣传片/交付；另有遮挡版 `real-dashboard-home-redacted.jpg`。产品库录屏在全屏产品页进行，不含配对码；正式剪辑只使用裁出的 PROMETHEUS 单条产品卡镜头，避免展示其他研究条目。
- 窗口级 gdigrab 因 Chrome 硬件合成输出黑帧而废弃；最终使用无控制台的桌面合成录制工具。全屏录制前须人工确认页面和桌面无隐私内容。
- B-095 第二版 66 秒横/竖屏成片已实机完成，使用 15 个已验证 Wan 缓存镜头 + 3 秒真实 Dashboard 产品库录屏；ComfyUI 当前 unavailable，因素材完整无需重跑 GPU。交付包 13 项，ZIP CRC 通过；两版本 Dashboard preview/video/download 六条路由均 HTTP 200，下载 hash 一致。
- 第二版产品版本：横屏 `efb5e8a59143de4a8257fc9e0ca71310/d08fd2fee7ff793ca778`；竖屏 `115bad928c9d849def302d2205075640/0ac60526a9ce535c7141`。状态仍为 testing，需人工审片。
- 第二版尚无 TTS 旁白；本机未找到中文 Piper ONNX 模型，保持原始氛围音乐版本。
- 第二版旁白草稿位于 `data/prometheus/film-v2/voiceover-script.md`，16 段，明确区分已验证功能与示意/愿景镜头；仍未生成 WAV。

## 2026-11-12 B-096 Piper CLI 子进程适配

- `src/muse/piper_tts.py` 新增显式 Piper CLI 执行路径，参数使用 argv 列表，不经 shell；支持独立 executable、model、config、timeout。
- CLI 生成后验证 WAV 容器、PCM 编码、单声道、16-bit、采样率与帧数；失败、超时、模型缺失或无效 WAV 均不会留下伪成功产物，也不会自动下载模型。
- `muse-piper-tts` 增加 `--engine cli --executable` 参数；Piper 专项 8/8 通过。
- 全量 Python 3.11 unittest suite：969/969 通过、0 failures、0 errors（112.440 秒）。
- 当前仍缺少本机中文 `.onnx` 与 `.onnx.json`，所以尚未完成真实中文语音端到端生成；能力仍标记 partial。

## 本次宣传片任务

片名建议：**《MUSE｜点燃一个宇宙》**

目标：介绍 Muse 的产品定位、当前真实功能、创作能力和未来规划。以真实功能演示为主，以未来愿景镜头为辅；所有未来概念明确标注“未来愿景 / 规划中”。

叙事建议（60–90 秒完整版）：
- 0–8 秒：未知宇宙与一粒知识火种，建立问题和情绪钩子。
- 8–18 秒：Muse 出现；展示灵感入口、任务/研究工作台。
- 18–34 秒：真实能力：研究与知识沉淀、Commander/Agent 编排、进度与审计。
- 34–50 秒：真实创作链：Wan 生成镜头、FFmpeg 合成、产品库预览与下载。
- 50–65 秒：PROMETHEUS——不同学科、文明与艺术的思想火花相互连接。
- 65–80 秒：未来愿景：更完整的自主研究、跨媒介创作工坊、可替换 Agent 生态。
- 80–90 秒：品牌收束与宣言。

执行策略：
- 先完成 15–30 秒可审 teaser，验证视觉、节奏、声音和信息准确性，再扩展完整版。
- 优先使用已验证本地 ComfyUI/Wan 2.1 与 FFmpeg；旁白候选先检查 ChatTTS，不可用时先使用已批准的本地音频方案或字幕叙事，不静默调用云端服务。
- 至少输出横屏 16:9 与竖屏 9:16；字幕 SRT、封面、分镜 JSON、制作说明、manifest、ZIP 与成品 MP4。
- 宣传真实功能时使用实际界面/真实生成过程/实际产品文件；未来愿景镜头使用独立视觉语言和明确文字标识。
- 产品默认保持 testing，经过人工审片与下载验收后再决定发布。

## 验收标准

- MP4 可由 FFprobe 读取，并由 FFmpeg 全片解码；尺寸、FPS、时长、音轨、字幕与目标画幅符合分镜。
- 所有素材有来源/生成提示/seed/工具/版本/哈希记录；许可不明素材不得作为公开商业素材。
- ZIP 可 CRC 检查，包内文件与产品 manifest 哈希一致。
- Dashboard 的列表、预览、MP4 下载、ZIP 下载通过真实授权请求，下载副本哈希与原件一致。
- 真实能力和未来概念有明确区分；不存在夸大功能、未经验证的 TTS 或自动发布声明。
- 人工审片通过后才允许将产品状态从 testing 推进。

## 进度与演化记录

每个生产 cycle 使用稳定 cycle ID，写入 Muse SQLite evolution ledger、`docs/PROGRESS.md` 和产品 manifest。保存脚本、分镜、素材引用、提示词、seed、生成任务 ID、失败与重试、测试、人工反馈和最终产物路径。
