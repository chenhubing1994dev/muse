# B145｜粤语旁白视频生产线实机闭环

日期：2026-11-17

## 本轮结果

粤语旁白配方已从“本地 TTS/ASR 单独可运行”推进到通过 Muse Commander 与 Worker 的真实 MP4 产品任务。真实成功任务：

- Commander Task ID：`c968701d3fcc4096989cb279895223a7`
- 输出目录：`data/prometheus/runs/cantonese_pilot_v2/15d2ac0f52e44796b5bf7b9fd8b8b822`
- MP4：`ai-verification-three-steps-9x16.mp4`
- 交付包：`delivery-bundle.zip`
- Creator Product：`data/products/d81700135fe112b661e0176b5f34e12a/1713600cfe751184fc96`

## 实机验收数据

- Worker 任务状态：`succeeded`
- MP4：24.5 秒、1080×1920、24 FPS、H.264/AAC、48 kHz stereo；AMF 硬件编码；22,187,340 bytes；全片解码通过。
- ZIP：52,173,824 bytes、10 个成员、CRC 校验通过。
- 本地 Cantonese TTS：模型 `typangaa/canto-tts-nano`，revision `fe17418a293d9be85685ddf750b0cfef9c88abb4`，zh-HK，18.8 秒。
- 本地 Whisper ASR：模型 `alvanlii/whisper-small-cantonese`，revision `b03a4e5d482f06858ce10ebbabebabec9f447a0d`。
- CER：0.091954，低于 0.10 自动门槛；仍必须人工听审，机器 CER 不等于逐字无误。
- 字幕：6 个 cue，采用真实词级时间戳；对齐器将 Whisper 的零时长 token 仅在其时间点与下一词起点吻合时合并，不伪造时间；其余无效词时间戳退回真实 segment 时间。
- BGM：旁白 side-chain ducking；voice gain 2.0、music gain 0.22，并经 FFmpeg loudnorm 目标 -16 LUFS / -1.5 dBTP。
- 产品验证：MP4/ZIP/hash 校验通过；Creator Initiative 已关联 Research Wiki 的 3 个 Cycle 节点。

## 重要架构与质量改进

1. 视频配方由唯一 Commander TaskQueue 派发，Worker 执行；不另建私有 JSON ledger 或后台线程。
2. 粤语配方只有在本地 TTS/ASR 依赖与固定模型文件存在时才显示可用；ASR 未通过 CER 门槛时禁止产品封装。
3. Whisper 逐词时间戳不完整时，使用模型真实 segment 起止时间作为回退，不因一个零时长词令整段字幕丢失。
4. 视频后期加入轻微动态推拉、背景纹理、旁白驱动 BGM ducking 和响度标准化。
5. 所有任务使用独立 run 目录，不覆盖旧产物；失败任务保留在 Commander 账本供复盘。

## 当前诚实状态

- **真实 MP4 生产：通过。**
- **MP4/ZIP 技术完整性：通过。**
- **ASR 机器门槛：通过（CER 0.091954）。**
- **音频响度与黑帧 QA：本次不再出现相关 warning。**
- **画面 QA：仍有 4 段 potential frozen-frame warning，需人工审片确认是否属于原始 Wan 镜头的低运动片段。**
- **产品状态：testing；ready_to_publish=false。**
- **粤语音色：当前 CantoTTS ONNX 后端只有单一 baked default voice；没有可切换的女声/成熟声线，`ref_audio` 在此后端也不会生效。当前为中性默认音色，不是成熟御姐女声。**
- **素材来源：复用此前六段已验证的 Wan 2.1 镜头；本轮没有重新调用 ComfyUI 生成新镜头。**
- **人工听审、字幕校对、版权/披露/平台审核：尚未完成。**

## 回归

本轮最新全仓库回归：`py -3.14 -m unittest discover -s tests`，**1274/1274 通过**（Python 3.14；使用项目测试依赖目录并绕过本地 HTTP 代理干扰）。本次真实生产成功任务为 `c968701d3fcc4096989cb279895223a7`；全仓库回归在其后完成，代码和文档均纳入最终验证。
