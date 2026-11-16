# Muse 大制作视频工厂：电影化生产线设计

日期：2026-11-14
状态：已获用户批准进入实施；软件安装与模型下载按阶段实测，不盲目重装或覆盖现有资产。

## 1. 目标与验收原则

把 Muse 视频生产从「低分辨率镜头拼接 + 字幕 + 氛围音」升级为可复用的电影化制作系统。首个验收项目仍为《纸鹤渡命人》第一集重制；旧 v4 MP4、ZIP、Story Bible、Prompt Pack 与素材来源文件全部保留，任何候选版另存。

作品质量和技术质量分开验收。FFprobe、完整解码、哈希、黑帧/冻结帧/响度等技术 QA 通过，只能证明文件技术上可交付，不能证明叙事、画面、声音或情绪达标。最终是否满意由创作者实际审片决定。

建议首轮电影化试片目标（不是对硬件产能的承诺）：
- 45–60 秒，9:16，1080×1920，24 FPS 母版；实际 AI 镜头先按显卡稳定能力生成，再进行有损可控的修复/放大/插帧。
- 12–18 个具有叙事功能的动态镜头；禁止用静帧 Ken Burns 伪装成 AI 动作镜头。
- 角色、服装、发型、道具、环境连续性由 Story Bible + 参考资产哈希 + 镜头审查共同约束。
- 全程粤语旁白；另有对白/环境声/动作音效/音乐分轨与响度管理。
- 至少包含多层景深/镜头运动、粒子/雾/光、镜头遮罩/转场、动态字幕/标题等电影化处理。
- 输出无旁白干净版、粤语旁白版、字幕文件、封面、逐镜 provenance、工程/交付 ZIP。
- 用户试听声线、审片后才可标记创作者验收通过；未通过继续返工。

## 2. 已观察到的现状（2026-11-14）

- Remotion 4.0.543、Node 24.20.0、npm 11.19.0 已安装，video-motion/node_modules 存在。
- Remotion 已有 MuseBumper、MuseBrandMotion、MuseSceneTimeline；Python 适配器 src/muse/remotion_motion.py 已支持多镜头 MP4 规范化、Remotion TransitionSeries、交叉淡化/擦除与音轨复用。当前时间线能力仍以镜头拼接/转场为主，不等于完整电影特效系统。
- Story Bible schema 与 storyboard 连续性校验已存在；需继续补参考图/角色资产哈希注册、镜头级 Review/Retry、逐镜镜头指令与音频 cue。
- VideoProductBuilder 负责产品账本、Manifest、QA、ZIP 与哈希；必须继续复用，不另建第二套产品/任务账本。
- FFmpeg/FFprobe、Audacity、Studio One 6、Piper 环境、ChatTTS WebUI 环境存在；Piper 的目标中文模型文件未证实存在。ChatTTS 模型目录仅见 asset/config 目录，不能据此断言模型已可用。
- 当前 ComfyUI 8188 无监听进程；在已检查的 Muse、Tools、software 有限深度目录中未找到 ComfyUI 根目录。历史记录表明 RX 6750 GRE 12GB 曾实机运行 Wan 2.1 T2V 1.3B，但同一 ComfyUI 进程连续加载 CLIP 时出现过原生崩溃；暂时采用单进程单任务/必要时任务间重启的保守策略。
- Blender、DaVinci Resolve 未在已知常见安装路径发现；FFmpeg AMF encoder 在本轮未得到可确认输出，须进一步复核，不能写成 AMF 可用。
- RX 6750 GRE 12GB 是当前已知 GPU；不把 NVIDIA CUDA 工作流直接当成可用。ComfyUI 官方 Windows AMD ROCm 路线仍须针对具体 GPU/驱动/Python/PyTorch 实测。
- 第一集 v4 无粤语旁白；原视频和产品 ZIP 均须保留。用户已明确指出其主观质量不满意。

## 3. 目标软件栈（按职责组装）

### 3.1 AI 镜头生成与修复
- ComfyUI + Wan 工作流：继续作为本地 AI 镜头引擎；先修复定位/启动、依赖兼容与 CLIP 重载稳定性，再逐个测试模型、节点与 workflow。
- 生成流程分为角色/场景参考资产、关键帧/镜头规划、逐镜生成、镜头审查、定点重做。每次记录 workflow hash、模型/版本、seed、prompt ID、分辨率、帧率、步数、耗时、显存与失败分类。
- 12GB VRAM 先以可重复的小样确定上限；不得直接下载/切换大模型或承诺高分辨率长片。模型/节点许可证逐项登记。
- 后处理可评估开源的超分/插帧工具，但必须先验证 AMD Windows 后端、显存占用、时间成本和输出质量；不把插帧/放大当成补救差镜头的魔法。

### 3.2 电影合成与动态图形
- Remotion：承担时间线、动态标题、片头片尾、字幕动画、图形特效、遮罩、镜头节奏与可复现渲染。扩展现有 SceneTimeline/Adapter，不重复建设。
- Blender：作为可选 3D 制作与合成工具，负责需要真实空间/镜头/景深/粒子/布料/道具的镜头；安装到 D 盘前先核验版本、空间和渲染路径。Blender 不强制成为每条视频的依赖。
- FFmpeg：媒体规范化、音频分轨混音、编码、封装、AMF/CPU 编码 fallback、元数据与交付验证。
- 不将 Remotion 说成开源软件：官方为 source-available / proprietary license；个人或不超过 3 人小团队可符合 Free License，但须核对适用资格。Muse 自动化生产需按用户实际组织身份和当前条款确认许可，不因软件已安装就推断许可已覆盖。

### 3.3 粤语配音与声音设计
- 第一基线：独立环境中试验 CPU-first canto-tts，优点是轻量、粤语、ONNX CPU；限制是已发布 ONNX 路线只有单一默认声线，且公开自测不等于听感验收。
- 第二候选：M5-TTS，支持 Chinese + accent=yue 和参考音色克隆；官方建议 Python 3.10/Linux，Windows 需单独适配验证，不将 NVIDIA CUDA/FlashAttention 安装到现有 AMD 环境。
- 第三候选：CosyVoice3/Cantonese 方案，需逐个核对底层模型权重许可、声线授权与 Windows AMD 兼容性。
- 先生成 10–20 秒相同粤语试听文本，让用户判断是否自然、成熟、清冷、女声；未获用户试听认可，不锁定为正式声线。不得未经许可克隆真人声音。
- Audacity 做清理/去噪/剪辑；Studio One 做人声、音乐、环境声、动作音效混音与母带。保留 narration/music/ambience/fx/stems。
- 配乐和音效优先原创或明确商用许可，逐项登记来源、许可、SHA-256。

### 3.4 产品化与质量门禁
- Muse Commander/现有任务队列仍为唯一调度中心；VideoProductBuilder/Creator Initiative/Research Wiki/Attempt Ledger 仍为唯一产品与经验账本。
- Shot-level review：每镜可接受/重做/替换，记录主观缺陷（构图、动作、角色一致性、节奏、光效、清晰度）与客观指标。
- 技术 QA：完整解码、流信息、黑帧/冻结帧、音频峰值/响度/静音、旁白完整性、字幕越界/时间轴、封面、ZIP/哈希。
- 内容 QA：故事连续性、角色一致性、叙事节奏、字幕/旁白一致性、版权与 AI 内容披露。
- 自动状态只允许区分 technical_passed、content_review_pending、creator_accepted、ready_to_publish；技术通过不能自动变成创作者接受或可发布。
- 多平台先生成发布准备包；未取得官方 OAuth/API 的真实发布响应，不声称自动发布；无官方分析/结算数据不声称收益。

## 4. 分阶段实施

### Phase A — Runtime recovery and baseline
1. 找回真实 ComfyUI 安装路径/启动脚本/模型目录；核验 Python、PyTorch、GPU backend、驱动、Wan 模型和工作流。
2. 用小任务验证 ComfyUI health、queue、模型加载、单镜生成、MP4 decode；重现并隔离 CLIP 第二次加载崩溃。
3. 核验 FFmpeg 实际可用编码器、Chrome/Remotion renderer、Audacity/Studio One 路径。
4. 输出机器可读 capability report；没有证据的能力标记 unknown。

### Phase B — Cinematic pipeline
1. 扩展 Story Bible 资产引用：角色参考图/场景/道具哈希、视觉风格、镜头轴线/服装/时间状态。
2. 扩展 ShotSpec：shot size、camera move、lens/look、action beats、composition、lighting、VFX cues、audio cues、continuity refs、prompt/model/seed。
3. 扩展 Remotion cinematic composition：镜头级 scale/position/opacity、parallax layers、light/particle/fog overlays、animated typography、custom transitions、sound cue timing；支持 9:16/16:9/1:1。
4. 新增镜头审查与定点重做，失败镜头只重做对应 shot，不重新生成整集。
5. 所有新能力默认关闭或显式 profile 选择，保持旧调用兼容。

### Phase C — Voice and sound
1. 在隔离 Python 环境安装并测试 Cantonese TTS 基线，不污染 Muse 主 .venv / Piper / ChatTTS 环境。
2. 输出同文多声线候选/基准样本，进行粤语字词、语气、自然度、速度、音色与商用许可检查。
3. 接入 TTS adapter、旁白时间轴、字幕同步、音轨分层、混音与响度 QA。
4. 用户试听并选定声线后，才用正式旁白重制第一集。

### Phase D — Blender and advanced shots
1. 先用一个 3D 场景测试 Blender CLI headless render、合成和资源路径。
2. 只在 AI 镜头不足以表达的关键镜头使用 3D/2.5D；不把整集强制转为 Blender 制作。
3. 对 GPU 渲染、CPU fallback、显存/内存和耗时建立预算。

### Phase E — Rebuild episode 01 and creator review
1. 重写脚本/分镜/镜头提示词与声音设计。
2. 逐镜生成并审查；角色不一致或动作失败的镜头局部重做。
3. Remotion/Blender/FFmpeg 合成，生成旁白、音乐、环境声、动作音效和字幕。
4. 运行技术 QA + 内容 QA + 人工审片；候选版另存，绝不覆盖 v4。
5. 让用户试听与审片，根据反馈逐轮修改；只有用户明确满意后才标记 creator_accepted。

## 5. 风险、成本与边界

- 不保证爆款、平台流量、收益或电影级 AI 原生运动；目标是最大化本机可控质量，并在实际结果不足时清晰暴露限制。
- 不下载多个大型模型/软件作盲目试错；先按兼容性、许可、磁盘、显存和小样验证。
- 不默认使用付费 API；外部模型只作为未来可选后备，需单独授权和成本上限。
- 不在现有用户文件、模型、正式成片上做覆盖性升级；软件与模型安装在 D 盘独立目录。
- 不复制第三方项目中的未授权 vendor/weights/数据；逐项记录开源许可、模型权重许可、声音/字体/音乐许可。
- 所有 GPU 任务由 Muse 既有资源协调器控制；当前机器不因「32 路并发」而同时启动多个 GPU 推理任务。

## 6. 完成定义

本设计的完成不是文档完成，而是：
- 真实 ComfyUI/Wan 本地镜头生成与恢复机制稳定可复现；
- 至少一个可用粤语 TTS 声线通过用户试听；
- Remotion cinematic composition 可运行真实 MP4 多镜头，带动态镜头效果、转场和音频 cue；
- Blender 可选 3D 镜头管线经过实机 smoke test；
- 第一集新候选版达到约定的镜头、旁白、音效和技术标准；
- 用户亲自审片并认可；未认可则保留反馈继续迭代；
- 进度、测试、素材来源、许可与交付哈希写入 Muse 本地账本。

## 7. OpenFilm integration — adopted as the editable film assembly layer

Repository: https://github.com/openfilm/openfilm
Version assessed: 0.3.2 (latest release observed 2026-11-14)
License: MIT for format, CLI, Studio and desktop app; OpenFilm name/logo are trademarks. Third-party media/model/service rights remain separate.

### Why it materially changes Muse
OpenFilm provides a human-editable browser-based Studio and an HTML film format:
- film.html is a multi-track timeline of page shots, footage, stills and independent audio clips.
- Each page exposes deterministic frame(t), allowing CSS/SVG/canvas/WebGL/Three.js/GSAP visuals and motion graphics without making every shot a Remotion composition.
- Studio can edit elements, typography, colors, layers, clips, crop/position/trim/speed/volume, and keep human overrides when the agent updates a film.
- Canvas provides a visual storyboard/workbench for notes, images, video, voice-over and music, then sends generated assets to the timeline.
- CLI supports open/look/render/get/doctor, contact sheets and deterministic frame checks; render supports motion blur, alpha and 4K options.
- Exports include MP4, sound/stems, subtitles/stills/slides/transparent video and editable timelines for external NLEs. Recent Windows releases improve project switching and editing-project exports.

### Muse role split (do not replace everything)
- Muse remains the sole brain/orchestrator, research/story/Story Bible, prompt and shot planner, ComfyUI/Wan local generation, Cantonese TTS, provenance, asset rights, QA, delivery and feedback ledger.
- OpenFilm becomes the optional interactive authoring/review/timeline layer and deterministic browser renderer.
- Remotion remains the existing programmable motion-graphics renderer for current compositions and scripted effects. New cinematic projects may choose OpenFilm pages for complex editable scenes; do not duplicate the same composition in both engines without reason.
- Blender remains optional 3D/CGI scene creation; outputs enter OpenFilm as clips or rendered image sequences.
- FFmpeg remains final packaging/transcoding/verification fallback.
- No dependency on OpenFilm's paid/connected image/video/voice generation services. Use locally generated Wan footage and local TTS/audio; OpenFilm media services are opt-in only.

### Integration contract
1. A Muse Film Exporter converts the existing canonical ShotSpec + asset manifest + audio cue manifest into a separate OpenFilm project folder, with relative media paths and SHA-256 provenance.
2. One film page per editable designed shot; original Wan video clips stay source clips. Browser-generated graphics/VFX are separate editable layers where practical.
3. Independent tracks for footage, titles/subtitles, narration, dialogue, music, ambience and SFX. Moving a shot must keep its shot-linked audio cues aligned.
4. Muse writes only generated/agent-owned project content. Before changing film.html or pages with Studio user overrides, read the latest version and preserve overrides; no blanket regeneration of user-edited elements.
5. The exporter never writes into the original episode v4 directory. It writes to a new candidate folder with versioned manifest.
6. OpenFilm look becomes an additional QA stage: verify deterministic frames, missing assets/fonts and text clipping. It complements, not replaces, Muse's video/audio/content QA.
7. OpenFilm render output is re-probed and fully decoded by Muse's existing VideoProductBuilder before delivery.

### Compatibility and adoption gates
- Runtime prerequisites: Node >=22.18, FFmpeg; Studio first-run downloads a headless Chromium (~100 MB). Existing Node 24.20 and FFmpeg appear compatible. OpenFilm's own manual says Git is needed for Studio saved versions; the Muse root is not a Git workspace, so initialize a separate Git repo only inside the OpenFilm candidate project if the user wants named Studio versions. Never initialize Git at Muse root.
- Install CLI in an isolated D-drive prefix (not global Node environment) and use a separate candidate workspace. First run/doctor/open/look/render smoke test before integrating any production workflow.
- Validate Windows Studio launch, browser rendering, local footage path handling, audio stems, subtitles, 9:16 and 16:9 exports, deterministic look, and edit-project roundtrip.
- OpenFilm's own get generation uses connected providers/services; keep disabled in the default Muse profile to avoid unapproved API spend.
- OpenFilm 0.3.2 is a rapidly evolving project; pin the version and record its package integrity. Do not auto-update production projects.

### Adoption decision
Adopt OpenFilm as an integrated optional film editor/renderer, not as a wholesale replacement for Remotion or Muse. A successful smoke test plus a real 20–30 second Muse-generated candidate should decide whether it becomes the default final assembly path. A failed Windows/AMD/asset-path test falls back to the existing Remotion + FFmpeg path without losing the project.

