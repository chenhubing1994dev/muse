# Muse × ffmpeg-skill：本地剪辑能力融合记录

日期：2026-11-21  
上游：<https://github.com/kajisho5/ffmpeg-skill>  
上游最新版本：v3.12.1（GitHub Releases 页面，2026-11-21 核对）；许可证：MIT。当前采用独立适配实现，没有复制或执行上游源码。
## 结论

值得吸收的核心不是把 42 个脚本全部塞进 Muse，而是四个工程机制：

1. **Probe first**：以 ffprobe 的真实媒体事实（时长、流、帧率、分辨率、像素格式、色彩/HDR、音轨布局）制定剪辑方案，不猜测文件属性。
2. **Typed operations**：调用方传递受校验的结构化参数，不允许用户输入任意 shell 字符串或不受限 filtergraph。
3. **Capability doctor**：检查 FFmpeg encoders/decoders/filters/bitstream filters；检查失败必须标记 unknown，不可伪装成 missing 或 available。
4. **Verify last**：输出必须重新 probe、完整解码；画面发生变化时增加视觉检查。技术 QA 不等于编辑审美、版权或发布批准。

上游 42 个工具覆盖素材探测、镜头分析、剪切/拼接、静音、画幅适配、字幕/图形、音频、调色、交付、批处理、多机位和验证。上游 MCP schema 从 argparse contract 生成，默认只暴露常用核心工具以控制上下文体积。详见上游 README 与 contract 文档。

## Muse 已有能力映射

| 上游工具域 | Muse 当前复用点 | 当前状态 |
|---|---|---|
| probe | media inventory、FFprobe metadata | 已有基础；本轮新增统一 probe 输出 |
| scenes / look / contact sheet | video highlights、motion diagnostics、`muse.video_contact_sheet.create_contact_sheet` | 部分 |
| cut | video highlights 与安全 MP4 Cut 执行器 | 部分 |
| fit | `muse.ffmpeg_skill.fit_media`，cover/contain、人工焦点、可选 Haar 正脸焦点建议、VFR 显式 FPS、QA 原子发布 | 首阶段部分实现 |
| silence | video QA 静音检测 | 检测已有，自动剪静音待做 |
| caption | 粤语 ASR 词级时间戳、SRT 生成/烧录、字幕审核 | 部分 |
| loudness | video QA EBU R128 / astats 测量 | 测量已有，归一化执行待做 |
| check / verify | video_qa 全解码、产品哈希复核 | 已有核心门禁 |
| export / render / report | VideoProductBuilder、可编辑导出、发布质量包 | 部分 |
| join | `muse.ffmpeg_skill.join_media`，严格兼容性门禁 + concat filter re-encode + QA 原子发布 | 首阶段部分实现 |
| multicam / batch | 现有 Commander / TaskQueue 只负责调度，不代表具备上游同等编辑语义 | 待集成 |

## 本轮落地

新增 `muse.ffmpeg_skill`：

- 维护上游 42 个工具族的结构化目录，并对每项标注 Muse 适配模块、native/partial/planned 状态、所需 FFmpeg 组件及是否需要视觉复核。
- `muse-ffmpeg-skill contract` 输出机器可读契约。
- `muse-ffmpeg-skill doctor` 探测 FFmpeg/FFprobe、encoders、decoders、filters、bsfs，并按工具给出 readiness。
- 探测命令失败时状态为 unknown；只有实际可读的组件列表中缺少某 filter 才标记 missing。
- `muse-ffmpeg-skill probe <file>` 输出媒体事实、VFR 疑似信号、HDR 提醒、音频布局和编辑建议；不写文件、不改动原素材。
- 将统一 probe 接入 `video_highlights.render_vertical_clip`：渲染前检查源视频流、时长与剪辑范围，渲染后复查输出分辨率及目标时长；CLI 继续调用 Muse Video QA 做完整解码门禁。
- 新增 `muse-ffmpeg-skill cut INPUT OUTPUT --start N --end N`：提供 accurate（默认，解码后精确 seek）与 copy（快速、关键帧对齐）两种模式；`--encoder auto` 优先尝试 `h264_amf`，AMF 进程启动失败自动回退 `libx264`，也可显式指定编码器；`--dry-run` 只 probe 并输出 argv 计划。执行前验证源文件/时间范围/输出路径，拒绝同路径和覆盖。
- Cut 在同目录临时目录渲染，复查 FFprobe 流/时长，复用 `muse.video_qa.inspect_video_file` 做完整解码；源文件 stat 指纹未变化后才用同卷原子 hard-link 发布，目标竞争创建时失败关闭。超时/QA 失败只清理临时产物。
- Join 首阶段：`plan_join()` / `join_media()` / `build_join_command()` 与 CLI `muse-ffmpeg-skill join OUTPUT INPUT...`；最多 32 段，逐段 probe，要求视频尺寸/FPS/色彩元数据、音轨存在性及采样率/声道布局一致；拒绝 VFR、旋转、HDR、多视频流、多音轨及不兼容素材。固定 concat filtergraph 重新编码（不是无损 stream copy），AMF 自动优先、失败回退 libx264；临时输出通过 FFprobe 时长/流复核、full-decode QA、源文件指纹检查后原子发布，禁止覆盖。
- Fit 首阶段：`plan_fit()` / `fit_media()` / `build_fit_command()` 与 CLI `muse-ffmpeg-skill fit INPUT OUTPUT`；支持 cover（按焦点裁切）与 contain（黑边适配），画布偶数尺寸 128–3840，焦点坐标归一化 0–1。VFR 要求显式目标 FPS，旋转/HDR 源暂时拒绝；AMF 自动优先、失败回退 libx264；输出经 FFprobe 尺寸/时长/音轨复核、full-decode QA、源指纹检查后原子发布。可选 `--face-aware` 调用已有 OpenCV Haar 正脸启发式，只有 confidence gate 通过才覆盖手动 focus_x；未检测、低置信或依赖不可用时保留手动焦点。结果标记 visual_review_required；不做身份识别/连续跟踪，也不宣称自动主体理解。
- Contact Sheet 复用已有 `muse.video_contact_sheet.create_contact_sheet`，由 `muse-ffmpeg-skill contact-sheet INPUT OUTPUT` 提供统一入口；支持 3–30 个均匀时间采样帧、1–6 列、96–640px 缩略图宽度。FFmpeg 只映射视频流并禁用音频/字幕/数据流；先 FFprobe 输入、生成临时 PNG，再检查 PNG 签名、FFprobe 图像尺寸与完整图像解码，确认源指纹未变后原子发布。联系表只用于人工审片，不自动判定主体、连续性或发布许可。
- HDR 源在 Cut 阶段拒绝默认 accurate 重编码；只有用户显式选择 copy 才允许保留原编码，并在计划中提示“无 tone mapping，需复核目标播放”。Join 暂时拒绝 HDR，不擅自转 SDR。
- 本机验证：FFmpeg/FFprobe 8.1.1 可用；42 项依赖组合中 16 项就绪、26 项缺少至少一项依赖、0 项 unknown。`h264_amf` 独立编码 smoke 与真实 Cut 均完成编码、FFprobe 和 full-decode QA，自动模式选中 AMF 且未触发软件回退；该结果只适用于当前已验证机器。
- B238 历史基线专项测试 6/6（ffmpeg_skill）与 14/14（video_highlights）通过；B251 Cut 验证 6/6 + 14/14（Job `28414b10ae01405d8ef841666bf76f21`）。B255 Join 验证 6/6，真实双素材 AMF Join 通过（Job `f29aea9036444e83bb057ef3e3e44bba`）。B257 Fit 验证 8/8；B259 Fit+face framing 为 9/9 + 5/5；整套专项 6 + 14 + 6 + 9 + 5 = 40 项全过，真实 AMF Fit 180×320、no_face 安全回退、FFprobe 与 full-decode QA 通过（Job `2b9fa64a85c142bf93f71d0cdfb8d2ca`）。全项目回归不在本轮范围内。

- B265 验证：FFmpeg Skill 专项 **49/49 passed**（skill 6、Cut 14、Join 6、Fit 9、Contact Sheet 7、face framing 7）；`muse-ffmpeg-skill contact-sheet` CLI help/dry-run、真实 FFmpeg 联系表 8 samples / 4 columns / 680×204 PNG、PNG signature、FFprobe 尺寸与 full-image decode 全部通过。新增 probe 前后源文件指纹比较，覆盖 probe 期间源文件变化时 fail-closed。真实 Cut/Join/Fit 继续以本机 `h264_amf` 成功且 fallback=False；AMF 独立编码 smoke 通过。psmux Job `dd5876b1d64043c5bb4ea0f03887b8d5`，exit 0。此为定向验证，不代表全项目回归。
## 尚未实现（不能把目录覆盖率当作工具实现率）
目前没有实现上游所有 42 项操作的同等参数与语义。优先后续顺序：

1. 自动主体构图与视觉复核：Fit 已接入可选 Haar 正脸焦点提示与低置信回退；Contact Sheet 已接入 FFmpeg Skill；visual_content 人工审核 API、追加式哈希链账本、成果库审核表单/历史、发布包质量报告消费与配方追溯实时叠加均已接通。仍需在真实人像素材上评估误检/漏检、人工审片误判与遮挡场景；Haar 不是持续跟踪或语义主体识别。审核人身份仍为本机自报，摘要不是数字签名，发布权限仍独立关闭。Join 已有受限 MP4 首阶段实现，支持 2–32 段兼容素材。
2. silence / loudness / audio：静音剪除时间映射、两遍 EBU R128 归一化、音轨布局与 true-peak 复测。
3. caption / graphics / overlay：复用粤语逐词时间戳，安全区、字体检测、ASS 动态字幕与视觉抽帧复核。
4. scenes / look：镜头切分、场景变化评分与视觉主体锚点；Contact Sheet 已有首阶段实现，启发式候选不得伪装成审美判断。
5. render / export / report：复用 VideoProductBuilder、Attempt、Manifest、字幕审核与发布质量门禁，禁止另建账本。
6. batch / multicam：由唯一 Muse Commander 调度，复用现有 TaskQueue；不引入第二个调度中心。

## 安全与验收边界

- 原素材只读；新产物默认不覆盖已有文件。
- FFmpeg 通过 literal argv 调用，不经过 shell 拼接。
- 组件检测 unknown 时 fail-closed；编码器列表不等于 GPU/驱动端到端成功，只有完成真实编码、FFprobe 与 full-decode QA 的当前本机 `h264_amf` 路径才视为已验证；其他硬件编码器仍待实测。
- 每次渲染需要输出 probe 与 full-decode QA；视觉变化还需视觉复核。
- 人工字幕、肖像/音乐/素材权利审核与发布总闸保持现有机制；QA 通过不授予发布权限。
