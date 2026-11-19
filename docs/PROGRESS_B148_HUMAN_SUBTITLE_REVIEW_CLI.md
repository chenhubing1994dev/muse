# B148｜人工字幕校对与批准 CLI

日期：2026-11-17

## 本轮交付

- 扩展 src/muse/subtitle_review.py：parse_srt_cues() 解析实际交付 SRT，build_subtitle_review() 以字幕 cue 为审阅粒度；apply_human_subtitle_review() 记录逐条人工确认/修订；render_approved_subtitles() 只允许从完整人工批准的 review 生成 SRT。
- 新增 scripts/muse-review-subtitles.py，读取交付包内的 subtitle-review.json 与人工填写的 answers JSON，输出新的 reviewed JSON；只有显式 --approve --confirm APPROVE-HUMAN-REVIEW 且每一段都被标记为已听审时，才会生成 approved SRT。
- 原始 ASR segment/word 时间戳、原始总转写、脚本参考稿和字幕 cue 时间戳分别保存。每条 cue 可独立修订；修订以 corrected_text 和审计记录保存，记录 reviewer 与 UTC 时间。
- CLI 拒绝覆盖现有输出、拒绝输入/输出 symlink 路径；未经完整人工批准不生成可替换字幕的 SRT。
- tests/test_subtitle_review.py 覆盖：SRT BOM/多 cue 解析、cue 时间重叠/序号错误拒绝、ASR segment 保留、部分审阅不能批准、完整审阅后才可批准、原始 ASR 不覆盖、重新确认时清除旧修订文本但保留审计记录。

## CosyVoice 只读审计结论

- 粤语 fork README 声称提供女声/男声预设、3–30 秒参考音频克隆和 Windows 一键安装，但其明确 GPU 支持写的是 NVIDIA。
- 只读下载脚本审计确认 snapshot_download() 没有模型文件白名单；Hugging Face API 返回 20 个模型文件、总计 9,747,516,745 bytes（约 9.75 GB）。README 的约 1.5 GB 不是完整快照体积。
- requirements.txt 加入 CUDA 12.1 PyTorch extra index，并固定 torch/torchaudio 2.3.1；没有明确 CPU-only 依赖锁定。不能把 upstream CPU fallback 误当作该 Windows fork 的已验证 CPU 安装方案。
- 当前上游已迁至 QwenAudio/CosyVoice。上游 CLI 代码显示无 CUDA 时关闭 JIT/TensorRT/FP16，前端部分 ONNX 模型可选 CPUExecutionProvider，证明上游存在 CPU fallback。
- 但官方安装文档以 Linux/Conda 为主，没有正式 Windows 安装流程；fork 的 Windows 安装是否可 CPU-only、是否强制 CUDA PyTorch、实际下载哪些模型，仍需直接审计脚本。不能把 CPU fallback 等同于本机 Windows 已兼容，也不能宣称 RX 6750 GRE 可 ROCm/DirectML 加速。
- 本轮未执行第三方安装脚本，未下载模型，未改动本机 TTS 环境。

## 验证

- tests/test_subtitle_review.py：9/9 通过。
- 全仓库 Python 回归：py -3.14 -m unittest discover -s tests，**1283/1283 通过**，退出码 0。
- py_compile：字幕模块与 CLI 脚本通过；CLI --help 正常显示。
- CLI 六条字幕 cue 端到端验证通过：临时目录内 6/6 审阅、1 条修订、生成 approved review JSON 与 SRT；ASR 原文仍保留。
- 真实 Commander → TaskQueue → Worker → TTS/ASR → FFmpeg → ProductBuilder 集成任务 5b412698c3a2450bba26c0cbb56882e8 成功；输出目录 data/prometheus/runs/cantonese_pilot_v2/b0c67132117a4e99a48f0c07302a31b9。delivery-bundle.zip 共 11 项，含 6 条字幕 cue 的 subtitle-review.json，CRC 通过，校对状态 pending_human_review、human_approved=false。
- 最新 MP4 仍复用六段既有 Wan 镜头，没有新 Wan 推理；QA 保留 4 个静帧警告，产品状态 testing、ready_to_publish=false。
- 注意：CLI 只生成经过人工批准的独立 SRT 和审阅记录；尚未自动替换 MP4 内烧录字幕，也不会自动把 Creator 产品状态改为可发布。

## CLI 输入格式

answers.json 示例（六条 cue 全部已人工听审，其中第 1 条作文字修订）：

```json
{
  "reviewed_segment_indexes": [1, 2, 3, 4, 5, 6],
  "corrections": [
    {"segment_index": 1, "corrected_text": "你有冇試過見到人工智能講得好肯"}
  ]
}
```

显式批准命令需要 `--approve --confirm APPROVE-HUMAN-REVIEW`；没有这两个参数不会生成 approved SRT。

## 下一步

1. 研究是否能在独立 Python 3.10 venv 中建立 CPU-only CosyVoice 依赖，先做 pip dry-run/依赖解析与 provider 检查；不要下载 9.75 GB 模型，直到 CPU 路径得到验证。
2. 若 Windows CPU 路径可用，独立 Python 3.10 venv 做短句基准；否则转向 GPT-SoVITS Windows CPU 试验。
3. 将 approved SRT 接入视频重封装/字幕替换步骤，并在人工批准前保持产品发布阻断；目前 CLI 只产出独立 SRT，不会改写 MP4 或 Creator 状态。
4. 静帧 QA 已定位到 shot-01、shot-03、shot-04、shot-05，详见 docs/PROGRESS_B149_VIDEO_FREEZE_SOURCE_MAPPING.md；优先重生成 shot-05/04/01，不放宽阈值。
