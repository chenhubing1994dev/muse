# B-138｜视频音频质量检测

日期：2026-11-16

## 目标

将音频质检从“有无音轨、是否静音”提升为可复核的响度、真峰值、峰值电平与削波风险检测；不把指标缺失解释为通过。

## 实施

- 扩展 `src/muse/video_qa.py`：完整解码时对有音轨的视频串接 FFmpeg `ebur128=peak=true` 与 `astats=metadata=0:reset=0`。
- 从 EBU R128 最终 Summary 段提取 Integrated LUFS 与 True Peak，避免误读开头短时窗口的滚动响度。
- 从 astats Overall 段提取 sample peak 与 RMS；sample peak 达到 -0.1 dBFS 时标记 `audio_clipping_suspected`。
- FFmpeg 8.1.1 的 astats 未提供可靠的 clipped-sample count，因此不伪造计数；报告明确标注 clipping 是峰值启发式，不是逐样本精确计数。
- 缺少任何必需指标时，`audio_quality.status=unavailable` 并加入 `audio_metrics_incomplete` 警告；没有音轨时标记 `not_applicable`。
- 响度参考区间暂设 -24 至 -14 LUFS，True Peak 复核上限 -1 dBFS。超出只生成审阅警告，不擅自自动增益或改变原音频。

## TDD 与验证

- 新测试先 RED：原实现没有 `audio_quality` 报告；修正后测试覆盖 Summary 优先级、LUFS、True Peak、RMS、峰值削波风险与指标缺失。
- `tests.test_video_qa`：10/10 通过；B-139 后视频/粤语旁白相关定向回归 42/42 通过。
- B-139 后全项目回归：**1,229 项通过、1 项跳过、0 项失败**；`compileall -q src tests` 通过。
- 真实 FFmpeg 8.1.1 合成测试片段：完整 MP4 解码成功；读到 Integrated -21.8 LUFS、True Peak -16.3 dBFS、sample peak -16.318709 dBFS、RMS -21.08914 dBFS；指标状态 observed。该文件是合成测试音，不是正式作品。
## 限制与下一步

- 削波目前是 sample-peak 启发式，不输出 clipped-sample 数量；如需精确计数，下一步评估流式解码 PCM 逐样本统计。
- 旁白感知 BGM ducking 已由 B-139 接入产品封装；详见 `docs/PROGRESS_B139_VOICE_AWARE_BGM_DUCKING.md`。
- 粤语 Whisper 模型下载超时，真实 ASR/CER/SRT 与人工听感仍未验收。