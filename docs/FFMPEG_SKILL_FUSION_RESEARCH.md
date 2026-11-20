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
| scenes / look | video highlights、motion diagnostics | 部分 |
| cut / fit | video highlights 的片段渲染与 9:16 中心裁切 | 部分 |
| silence | video QA 静音检测 | 检测已有，自动剪静音待做 |
| caption | 粤语 ASR 词级时间戳、SRT 生成/烧录、字幕审核 | 部分 |
| loudness | video QA EBU R128 / astats 测量 | 测量已有，归一化执行待做 |
| check / verify | video_qa 全解码、产品哈希复核 | 已有核心门禁 |
| export / render / report | VideoProductBuilder、可编辑导出、发布质量包 | 部分 |
| join / multicam / batch | 现有 Commander / TaskQueue 只负责调度，不代表具备上游同等编辑语义 | 待集成 |

## 本轮落地

新增 `muse.ffmpeg_skill`：

- 维护上游 42 个工具族的结构化目录，并对每项标注 Muse 适配模块、native/partial/planned 状态、所需 FFmpeg 组件及是否需要视觉复核。
- `muse-ffmpeg-skill contract` 输出机器可读契约。
- `muse-ffmpeg-skill doctor` 探测 FFmpeg/FFprobe、encoders、decoders、filters、bsfs，并按工具给出 readiness。
- 探测命令失败时状态为 unknown；只有实际可读的组件列表中缺少某 filter 才标记 missing。
- `muse-ffmpeg-skill probe <file>` 输出媒体事实、VFR 疑似信号、HDR 提醒、音频布局和编辑建议；不写文件、不改动原素材。
- 将统一 probe 接入 `video_highlights.render_vertical_clip`：渲染前检查源视频流、时长与剪辑范围，渲染后复查输出分辨率及目标时长；CLI 继续调用 Muse Video QA 做完整解码门禁。
- 新增 `muse-ffmpeg-skill cut INPUT OUTPUT --start N --end N`：提供 accurate（默认，解码后精确 seek + H.264/AAC 重编码）与 copy（快速、关键帧对齐）两种模式；`--dry-run` 只 probe 并输出 argv 计划。执行前验证源文件/时间范围/输出路径，拒绝同路径和覆盖。
- Cut 在同目录临时目录渲染，复查 FFprobe 流/时长，复用 `muse.video_qa.inspect_video_file` 做完整解码；源文件 stat 指纹未变化后才用同卷原子 hard-link 发布，目标竞争创建时失败关闭。超时/QA 失败只清理临时产物。
- HDR 源在本阶段拒绝默认 accurate 重编码；只有用户显式选择 copy 才允许保留原编码，并在计划中提示“无 tone mapping，需复核目标播放”。本阶段不擅自将 HDR 转 SDR。
- 本机验证：FFmpeg/FFprobe 8.1.1 可用；42 项依赖组合中 16 项就绪、26 项缺少至少一项依赖、0 项 unknown。检测到 AMD AMF 等硬件编码器，但不把 encoder 列表等同于 GPU 端到端成功。
- 专项测试 6/6（ffmpeg_skill）与 14/14（video_highlights）通过；合成测试视频的真实 FFmpeg 生成及 FFprobe probe smoke 通过。## 尚未实现（不能把目录覆盖率当作工具实现率）
目前没有实现上游所有 42 项操作的同等参数与语义。优先后续顺序：

1. cut / join / fit：无损优先、VFR/keyframe 感知、输入指纹计划、输出时长/帧率验收。
2. silence / loudness / audio：静音剪除时间映射、两遍 EBU R128 归一化、音轨布局与 true-peak 复测。
3. caption / graphics / overlay：复用粤语逐词时间戳，安全区、字体检测、ASS 动态字幕与视觉抽帧复核。
4. scenes / look：镜头切分、关键帧 contact sheet、视觉主体锚点；启发式候选不得伪装成审美判断。
5. render / export / report：复用 VideoProductBuilder、Attempt、Manifest、字幕审核与发布质量门禁，禁止另建账本。
6. batch / multicam：由唯一 Muse Commander 调度，复用现有 TaskQueue；不引入第二个调度中心。

## 安全与验收边界

- 原素材只读；新产物默认不覆盖已有文件。
- FFmpeg 通过 literal argv 调用，不经过 shell 拼接。
- 组件检测 unknown 时 fail-closed；硬件编码器出现在列表中不等于 GPU/驱动端到端成功。
- 每次渲染需要输出 probe 与 full-decode QA；视觉变化还需视觉复核。
- 人工字幕、肖像/音乐/素材权利审核与发布总闸保持现有机制；QA 通过不授予发布权限。
