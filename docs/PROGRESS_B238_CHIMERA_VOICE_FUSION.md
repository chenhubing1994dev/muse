# B238｜CHIMERA 物理建模人声引擎融合与声音制作升级

日期：2026-11-21

## 决策摘要

采用 CHIMERA（https://github.com/jamesondh/chimera-vocal-synth）作为**可选、离线、实验性声音合成器**，不替换 Muse 现有粤语 TTS，也不宣称它已经解决真人感配音。上游仓库声明 MIT；CMU Pronouncing Dictionary 与字体等随附第三方资源保留各自许可。

上游源码/文档核验：
- README：Rust 物理声道模型、WASM AudioWorklet、原生 CLI；可输出 WAV。
- CLI：除 render text / render speech 外，上游原生支持 render notes <in.json> <out> [male|female|child]；输入包含 bpm、lyrics、notes: [[midi, beats], ...]，可选 vibrato / t0，也可直接传 syllables。
- render song 只输出上游内置 demo songs；Muse 自定义歌曲使用 render notes，不能误把 demo 命令当作用户歌词入口。CLI 原生采样率 48 kHz。
- 上游歌词自动路由覆盖英语、韩语、俄语、普通话（Han 字符）等前端；Han 走普通话而非粤语。土耳其语需显式语言选择，Muse 当前适配器暂不开放该参数。
- 引擎有男女童声体型预设；这不是已验证的真人声线克隆，也不等于成熟御姐声线。
- 上游当前语言前端文档列出英语、韩语、俄语、普通话、土耳其语；没有粤语前端。README 的语言清单较旧，融合以实际语言前端文档/源码为准。
- 上游自己的设计文档仍把“尽可能接近真人”列为研发目标，并保留 Vocaloid 式音色；因此不得把物理建模等同真人录音。

## 本轮实现

新增 src/muse/chimera_vocal.py：
- 仅允许显式配置本地 CHIMERA CLI（MUSE_CHIMERA_CLI 或参数），不下载、不安装、不自动更新、不执行 shell。
- 支持英语文本、调用者提供的 IPA，以及自定义旋律/逐音节歌词 render notes 路径；只接受 male/female/child 体型。歌曲先调用上游 syllables 做音节计数预检，再把冻结的 syllables 与 MIDI/节拍写入临时 JSON。
- 输出必须是新建 WAV；拒绝覆盖已有文件、符号链接、错误扩展名和非安全输出目录。歌曲在同目录私有临时目录渲染，校验后用 O_EXCL 发布，防止渲染期间覆盖并发创建的用户文件。
- 复核 WAV 可读性、48 kHz、单声道/立体声、16/32-bit PCM、非空帧数；记录 SHA-256、时长、字节数与引擎/语言元数据。
- 元数据明确 cantonese_supported=false、human_naturalness_accepted=false，保留创作者试听门槛。
- 提供模块 CLI：python -m muse.chimera_vocal --mode song --melody-json melody.json --output song.wav；melody.json 仅接受 bpm/lyrics/notes/vibrato/t0，限制 MIDI、节拍、时长和文件大小。需要用户自行取得并审计本地 CLI 后显式配置。

## B242 增量：自定义歌曲渲染与验证

- 新增 render_chimera_song：先调用 CHIMERA syllables 前端，核对音节数与非休止 MIDI 音符数，再冻结音节数据调用 render notes；支持 BPM、逐音符 MIDI/拍长、休止、vibrato 与起始偏移。
- 输入边界：只接受英语/韩语/俄语/普通话自动路由脚本，拒绝未支持脚本；MIDI 0 表示休止，限制音符数、拍长、BPM、总时长和 JSON 文件大小。Han 字符仍是普通话，不是粤语。
- 渲染在输出目录内的私有临时目录完成；WAV 通过 48 kHz PCM 校验后，以独占创建方式发布，避免覆盖渲染期间并发创建的用户文件。
- Python 3.11 专项测试 **13/13 passed**，detached psmux Job `0c5f9f5766c044e5bd20f427fb2b7905`，exit code 0；覆盖歌曲 CLI 路由、音节/音符数不匹配、输入边界、时长/JSON 大小上限及并发文件保护。测试使用模拟 CLI，不代表上游真实二进制已构建或试听。
- 未 clone/build/install CHIMERA；没有真实声音自然度、成熟女声、粤语前端或盲听结论。当前粤语默认 TTS 未变。
## B243 增量：动态 BGM 混音接入视频封装

- `VideoProductBuilder.package_generated_clip` 已接入 `video_audio_mix`：SRT 存在时解析受限语音时间窗，按 base_gain=0.22 / duck_gain=0.10、attack=80ms / release=450ms 生成平滑音量包络；无 SRT 时使用平直 BGM 基线并保留真实语音 sidechain。
- 有旁白+BGM 的成片在片尾自动淡出 1.5 秒；原有 sidechaincompress（threshold=0.025、ratio=8、attack=20ms、release=450ms）与 -16 LUFS loudnorm 不变。FFmpeg 音量表达式每帧求值。
- `storyboard.json` 和 `manifest.json` 增加 `background_music_mix` 记录，包含时间来源、语音窗数量、增益/淡出参数与表达式 SHA-256。
- Python 3.11：helper 测试 **7/7 passed**（Job `03db84db74f147f2b920275042042869`）；VideoProductBuilder **52/52 passed**（Job `a9763c94890341a6866e05a3c4b54d93`）；本机 FFmpeg 完整 voice/BGM/sidechain/amix/loudnorm 合成 MP4 mux smoke 通过（Job `29723f02a36d4f99a2222185176aa93b`），验证 `-shortest` 收尾。
- 尚未用真实粤语旁白、真实 BGM 和完整用户视频做 A/B 听感验收。字幕包络是粗粒度预判，sidechain 仍负责真实信号瞬态；不宣称人工听感验收完成。
## 对 Muse 声音系统的融合定位

1. 粤语对白/旁白：继续走现有本地 Cantonese TTS，不能把英语 G2P 或 IPA 输入误当成粤语声调前端。
2. 英语角色台词、实验性旁白、歌声/拟声/Beatbox：CHIMERA 可作为额外候选；歌声与说话分开验收。
3. 物理建模启发：吸收“语言前端→统一音素/IPA→发声后端”的分层；未来粤语前端必须有母语者校验声调、声母、韵母、入声/韵尾与连读，不能仅逐字转 IPA。
4. 配音自然度：继续比较不同引擎的真实听感；停连、呼吸、语调、重音、情绪和角色连续性必须通过试听与盲测，不得仅靠 EQ、压缩或音高变换宣称解决。
5. 背景音乐：复用 B-139 已有 sidechaincompress、原始 BGM 哈希/封装和参数追溯；下一步应增强基于对白响度/场景语义的自动目标增益、平滑恢复、转场包络与多轨总线，而不是再造第二套 ducking。
6. 混音/母带：复用现有 EBU R128、astats、LUFS/True Peak/RMS QA；按目标平台配置响度，限制峰值并报告测量值。技术通过不代表听感通过。
7. 音乐权利：只用原创、用户提供或逐项许可核验的音乐/音效；保留来源、许可、SHA-256。不得因 CHIMERA MIT 就推断输出音乐、词曲或第三方素材权利。

## 风险与未完成

- 本轮未 clone、build、install 或运行 CHIMERA；未下载其 Rust/Bun 依赖，也未占用 GPU。
- 本机是否具备 Rust/Bun、CLI 是否可构建、真实 CLI 输出 WAV 的格式/音质，仍待单独只读预检和本机 smoke。
- 未完成粤语前端、粤语声调建模、成熟女声听审、盲测或真实用户试听。
- 新适配器是可选生成入口，尚未接入 VideoTaskWorker 的默认粤语旁白流程；不会改变现有默认声音或成片。
- 上游 CLI 没有被证明提供 Muse 所需的细粒度情绪/呼吸/停顿控制；不能虚构参数。需要未来以明确的可用接口为基础设计控制层。

## 验收计划

1. 只读检查本机 CLI/Rust/Bun 环境与安装路径；用户确认后才克隆/构建，不自动安装。
2. 使用同一段英语台词及一段用户自有旋律制作 CHIMERA 与现有引擎 A/B WAV，记录版本、参数、SHA-256、响度、时长和声学指标；歌曲需核对每个音符对应一个音节。
3. 由创作者盲听评估自然度、咬字、停连、情绪、角色一致性；失败样本保留。
4. 粤语仍以当前 TTS 为基线，另行建立粤语 phonology front-end 研究与母语者标注，不把 Mandarin front-end 当粤语。
5. 复用 VideoProductBuilder 分轨封装、BGM ducking 与视频 QA；通过技术检查后仍须人工试听/审片。
