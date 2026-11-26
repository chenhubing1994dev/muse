# 成品质检报告｜AI 自动化赚钱

- 制作日期：2026-11-26（本机时区）
- 交付范围：抖音 / 小红书 / 微信视频号 / 哔哩哔哩 / YouTube Shorts；每个平台独立文件夹和独立编码成片。
- 账号定位假设：中文内容；25–35 岁职场人、自由职业者、小店主；干货、节奏快、反夸大承诺。平台列表沿用任务示例。以上是制作假设，不是用户已确认的账号画像。
- 结果类型：本机命令执行与文件读取；未执行任何平台发布。

## 0. Muse 摸底与实际调用

### Sidecar 状态

- [已亲自执行验证] 桌面端 `Muse.exe → muse-engine.exe → sidecar-runtime\python.exe`；当前公开健康端口 `54882`，`GET /__muse/health` 返回 `status=ok`、`version=1.0.3`、`agent_registry_configured=true`、`stores_healthy=true`。
- [已亲自执行验证] Sidecar 启动认证自检记录（2026-11-26 17:53:43，同一仍运行的 v1.0.3 实例）：`/api/v1/agents` HTTP 200，`count=0`、`agents=[]`；`/api/v1/health` HTTP 200，四项 Store healthy；`/api/v1/store-health` HTTP 200，四项 Store healthy。完整响应见 `muse_api_snapshot.json`。
- [已亲自执行验证] 本轮无 bearer 的直接请求返回 401；因此本轮没有伪称绕过认证直接调用了私有 API。认证 200 使用的是该运行实例的启动自检真实响应记录。
- [已亲自执行验证] Agent Registry 配置健康，但注册 Agent 数量为 **0**；无 Agent 名称、能力、输入/输出 Schema 可列，也没有 Agent 被调用。

### Muse 流水线覆盖

| 环节 | Muse 真实状态 | 本轮实际执行 |
|---|---|---|
| 选题 | [部分覆盖] CinematicFactory 可输出研究/钩子计划 | 调用 `python -m muse.cinematic_factory` 生成 `muse_pipeline/cinematic_factory_plan.json`；该计划 `ready_for_render=false` |
| 脚本/分镜 | [部分覆盖] 有结构化计划骨架；VLM 审查为 human_only | 本轮人工编排 17 个画面段（16 个口播句段，其中第 11 句中段增加一次镜头切换）与逐字稿，保存 `voiceover_script.md` / `voiceover_timing.json` |
| 画面素材 | [未覆盖] 未找到 ComfyUI 8188 监听、ComfyUI/Wan 进程或 Wan 模型文件 | 使用 Pillow 生成 16 张原创程序化动态图文底图，并用 FFmpeg 做轻微推拉；不是 Wan AI 视频 |
| 配音 | [覆盖] 本地 Cantonese TTS ONNX | Muse `synthesize_cantonese_segment_payload_with_runtime` 成功，16 段，zh-HK，Apache-2.0，模型 `typangaa/canto-tts-nano` revision `fe17418a293d9be85685ddf750b0cfef9c88abb4`；输出与最终混音使用的 `voiceover.wav` SHA256 相同 |
| 字幕 | [部分覆盖] TTS 分段可对齐；无 Agent | 生成 ASS/SRT 并硬烧录到 5 个成片，16 条字幕时间戳来自 TTS 分段时间 |
| BGM/音效 | [未发现可用 Muse 音乐 Agent] | 本地程序化合成原创电子氛围节拍，无第三方采样 |
| 合成 | [部分覆盖] Muse 视频计划尚未达到 render gate | FFmpeg 8.1.1 完成 5 份独立平台编码 |
| 封面 | [未发现 Muse 封面 Agent] | Pillow 原创生成 5 张平台色彩适配封面 |
| 文案 | [无注册 Agent] | 本轮人工编写 5 套独立标题、简介、标签、置顶评论与建议发布时间段 |

### 本机工具

- [已亲自执行验证] `ffmpeg`：ffmpeg version 8.1.1-full_build-www.gyan.dev Copyright (c) 2000-2026 the FFmpeg developers
- [已亲自执行验证] `ffprobe`：ffprobe version 8.1.1-full_build-www.gyan.dev Copyright (c) 2007-2026 the FFmpeg developers
- [已亲自执行验证] Python：3.11.9（生产/QC）、3.14.5（TTS runtime）
- [已亲自执行验证] `comfy` 命令未发现；8188 无监听；未发现 ComfyUI/Wan 进程；`models` / `data/models` 未发现 Wan2.1 权重。因此不声称本轮生成了 AI 视频镜头。

## 1. 爆款设计（不是播放量承诺）

| 概念 | 受众痛点 / 前 3 秒钩子 / 分享收藏理由 | 评分（1–5） | 总分 |
|---|---|---:|---:|
| 免费AI工具清单：5个工具帮你省时间 | 想知道工具怎么选的人，前三秒直接抛出“别再收藏工具了”；容易转发但缺少付费场景。 | 钩子强度 4 / 信息密度 4 / 情绪转折 3 / 可复述性 3 / 平台适配度 4 | **18/25** |
| 别卖AI，先卖一个可验收的结果 | 自由职业者/小店主担心AI副业无从收费，前三秒“想靠AI赚钱？先唔好买模型”；把需求、试点和交付讲清楚，适合收藏照做。 | 钩子强度 5 / 信息密度 5 / 情绪转折 5 / 可复述性 5 / 平台适配度 5 | **25/25** |
| 一个人搭建Agent团队 | 好奇Agent能否替代团队，前三秒展示多Agent协作；视觉吸引强但容易泛化和夸大能力。 | 钩子强度 4 / 信息密度 3 / 情绪转折 4 / 可复述性 4 / 平台适配度 4 | **19/25** |

选择概念 2：把“AI 自动化赚钱”从空泛工具清单收敛为一条可执行的验证链：**重复痛点 → 小示范 → 付费试点 → 稳定后自动化**。它最可复述，也能避免暗示保证收入。

### 口播结构

- 0–3 秒钩子：想靠 AI 賺錢？先唔好買模型。
- 冲突/悬念：不是先搭一个无人使用的 Agent；先找每周重复发生、有人愿意付费解决的问题。
- 价值交付：以小店客户查询、报价、跟进为示例；先做小 Demo，展示前后差异。
- 反转/高潮：客户不为模型数量付钱，而为节省时间、减少错误和业务机会付钱。
- 收尾/互动：先卖结果，再自动化；留言说出最想自动化的工作。
- [已亲自执行验证] TTS 第一段结束时间 **2.56s**，钩子在 3 秒内出现。
- 完整粤语逐字稿：`voiceover_script.md`；逐段时间码：`voiceover_timing.json`。

## 2. 素材来源登记

| 素材 | 来源 | 许可/风险处理 |
|---|---|---|
| 17 张视觉底图、流程卡片、封面 | 本轮本机 Pillow 程序化原创绘制 | 无第三方图片/视频素材；无外部水印 |
| 粤语人声 | Muse 本地 ONNX TTS `typangaa/canto-tts-nano` | 模型声明 Apache-2.0；原始合成 WAV 保留 |
| BGM | 本轮 Python 正弦波/合成节拍生成 | 原创合成，无采样、无商业曲目 |
| 字体 | Windows Microsoft YaHei 系统字体 | 仅用于本地渲染，未单独分发字体文件 |
| 用户素材 | 无 | 本轮未使用用户未提供/未授权素材 |

## 3. 平台规格（2026-11-26 查询）

- [已读官方文档] 抖音开放平台发布方案：竖版 720p 及以上、MP4/WebM 推荐、15 分钟、总文件 4GB；上传接口说明超过 128MB 必须分片，单次上传限制与总文件限制分开。来源：<https://open.douyin.com/platform/resource/docs/ability/content-management/douyin-publish-solution/>、<https://open.douyin.com/platform/resource/docs/openapi/video-management/douyin/create/upload/>。
- [已读官方文档] YouTube Shorts：正方形或竖版，最长 3 分钟，最大 1080p；上传标题最多 100 字符。来源：<https://support.google.com/youtube/answer/15424877>、<https://support.google.com/youtube/answer/10059070>。
- [未知] 小红书当前登录上传页的正式规格未能公开读取；标题/时长/文件上限/封面比例不猜测。
- [未知] 微信视频号官方帮助页面的详细技术限制本轮未能直接读取；不把第三方汇总当官方门禁。
- [未知] B站创作中心当前上传技术限制未能从公开页面读取；不沿用旧规格。
- 完整逐平台字段、来源与 unknown 标记见 `specs.json`。本轮统一输出基线 1080×1920、9:16、H.264、AAC、30fps；这是导出选择，不冒充所有平台的强制规格。

## 4. 成片与质检证据

### FFprobe / 解码

| 平台 | 时长 | 分辨率 | 视频 | 帧率 | 音频 | 文件大小 | 完整解码 |
|---|---:|---|---|---|---|---:|---|
| douyin | 51.60s | 1080×1920 | H.264 | 30/1 | AAC, 48kHz, 2ch, 192679 bps | 2.59 MiB | PASS |
| xiaohongshu | 51.60s | 1080×1920 | H.264 | 30/1 | AAC, 48kHz, 2ch, 192679 bps | 2.57 MiB | PASS |
| wechat_channels | 51.60s | 1080×1920 | H.264 | 30/1 | AAC, 48kHz, 2ch, 192679 bps | 2.55 MiB | PASS |
| bilibili | 51.60s | 1080×1920 | H.264 | 30/1 | AAC, 48kHz, 2ch, 192679 bps | 2.59 MiB | PASS |
| youtube_shorts | 51.60s | 1080×1920 | H.264 | 30/1 | AAC, 48kHz, 2ch, 192679 bps | 2.58 MiB | PASS |

- [已亲自执行验证] FFprobe 对每个 MP4 读取分辨率、时长、编码、帧率、音频流、容器总码率和文件大小；视频流单独 `bit_rate` 字段未被该 MP4 容器报告，故不伪造视频流码率。容器总码率约 420.5 kbps，音频流 192.7 kbps。
- [已亲自执行验证] 5 个文件均完整解码 `DECODE_OK`，均为 51.60s、1080×1920、H.264、30fps、AAC 48kHz 双声道；均小于 3MB。
- [已亲自执行验证] 每个平台均抽取 0s、1s、3s 帧；检查接触表：`qc_contact_sheet.jpg`；单帧证据在各平台目录 `qc/`。
- [已亲自执行验证] 画面在 0–3s 没有片头/Logo/寒暄；第一帧即进入钩子。17 个画面段（16 个口播句段，其中第 11 句中段增加一次镜头切换），段长 2.52s–4.00s，镜头/信息变化间隔不超过 5 秒。
- [已亲自执行验证] 字幕为硬烧录 ASS，16 条字幕与 TTS 分段逐句对齐；字幕采用高对比大字与暗色底板，位于画面下方安全区；0s/1s/3s 与封面抽帧已检查。

### 音频

- [已亲自执行验证] `loudnorm` 输入测量：原始音轨复测发现配音只有 -26.85 LUFS，而旧背景音乐高达 -3.94 LUFS、True Peak +1.15 dBTP，存在严重的音乐压人声和削波；本次已替换背景音乐并重新混音。新配乐 -25.50 LUFS，混音前衰减约 10.46 dB，并由人声侧链动态压低。最终混音实测 **-14.49 LUFS / -1.62 dBTP**（再测目标归一化为 -16.02 LUFS / -3.34 dBTP）。
- [已亲自执行验证] `volumedetect`（新混音）：mean **-16.6 dB**，max **-1.8 dB**；峰值未到 0 dBFS。
- [已亲自执行验证] `silencedetect=noise=-50dB:d=0.45`：未检出持续 0.45 秒以上的静音段；短停顿来自逐句 TTS 设计。新混音对人声做 75 Hz 高通、12.5 kHz 低通、轻度 FFT 降噪、3:1 压缩与 -16 LUFS 归一化；背景音乐改为 92 BPM 原创电影感电子氛围（和弦铺底、柔和琶音、圆润低频），去掉原来带噪声的随机打击采样，并启用人声侧链 ducking。
- [未知] 本轮没有真人耳机/音箱主观试听，不能宣称已人工听审人声自然度或手机外放效果；技术响度与静音门禁已执行。请在实际播放设备上做最后听感确认。

### 内容核对

- [已亲自执行验证] 逐字稿与硬烧录字幕来源一致；钩子 2.56 秒内结束。
- [已亲自执行验证] 文案不含具体收益数字、稳赚/保证收入/必爆等承诺；核心方法是可验证的试点交付。
- [已读文档推断] “客户愿意为节省时间、减少错误或业务机会付费”作为商业方法论表述，不是某个案例的统计结论；视频没有编造案例数据或客户成果。

## 5. 平台成品目录

- `douyin/`：MP4、cover.png、title.txt、description.txt、hashtags.txt、publishing_suggestion.md、qc/。
- `xiaohongshu/`：同上，独立文案与色彩封面。
- `wechat_channels/`：同上，独立文案与色彩封面。
- `bilibili/`：同上，独立文案与色彩封面。
- `youtube_shorts/`：同上，独立文案与色彩封面。

## 6. 未完成 / 未验证

1. Muse Agent Registry 当前注册 Agent 数量为 0，因此没有 Agent 能力被调用；本轮只调用了 Muse CinematicFactory 规划模块与 Muse Cantonese TTS 运行时。
2. Muse CinematicFactory 的 `ready_for_render=false`，其 VLM 评审队列为 human_only；ComfyUI/Wan2.1 运行时/模型缺失，本轮用原创程序化动态图文替代，**不是 AI 生成电影画面**。
3. 小红书、视频号、B站的当前账号上传规格未从官方登录态页面验证；`specs.json` 保留 unknown。抖音上传权限和各账号的实际上传页校验未执行。
4. 未在真实手机端逐个平台播放/预览；未人工听审音质；未执行平台上传/发布。
5. 发布时段是可测试建议，不是由该账号历史数据推导的最优时段。

## 7. 复现命令

```powershell
# 读取成片规格
& 'D:\software\ffmpeg-release-full\ffmpeg-8.1.1-full_build\bin\ffprobe.exe' -v error -show_entries format=duration,size,bit_rate:stream=codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,sample_rate,channels -of json 'out\ai-automation-money\douyin\douyin_ai-automation-money.mp4'
# 完整解码
& 'D:\software\ffmpeg-release-full\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe' -v error -i 'out\ai-automation-money\douyin\douyin_ai-automation-money.mp4' -f null NUL
```

## 最终结论

**本机制作与技术质检：5/5 成片通过。** 交付的是原创动态图文 + 粤语 TTS + 原创合成 BGM 的可上传短视频包；并非真人实拍或 Wan AI 视频。平台端限制仍须在登录上传页最终确认；没有自动发布。


## 音频反馈修订记录（第二版）

- 已定位根因：旧 BGM 峰值 +1.15 dBTP，综合响度 -3.94 LUFS；原始配音仅 -26.85 LUFS，旧混音让音乐明显压过人声且发生削波。
- 已替换 `original_bgm.wav`，改为原创 92 BPM 柔和电影感电子氛围，无随机噪声采样。
- 五个平台 MP4 已全部重新混音；视频流直接无损复制，避免画面再次编码。
- 新混音：人声先降噪/压缩/归一化，背景音乐降低并由人声侧链自动 ducking；最终峰值限制器防止削波。
- 新一轮 FFprobe 与完整解码：5/5 PASS；AAC 48 kHz 双声道；实际时长 51.7 秒。
- 试听限制仍保留：未真人试听。
