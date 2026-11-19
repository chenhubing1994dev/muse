# B169｜Wan 2.1 × 粤语旁白双镜头成片演示

日期：2026-11-19

## 1. 两段真实 Wan 2.1 GPU 镜头

镜头 A：
- 原生 ComfyUI Wan 2.1 T2V 1.3B FP16 + UMT5 XXL FP8 + Wan VAE。
- 832×480、49 帧、16fps、20 steps、CFG 6、shift 8。
- 3.0625 秒，H.264；完整 FFmpeg 解码通过。
- 49/49 帧有不同 framemd5；帧亮度差中位数 7.0904；-40dB / 250ms freezedetect 无事件；blackdetect 无黑帧段。
- 4 帧抽查显示人物跑动、姿态变化、沙尘和背景视差；作为 staging 镜头通过初步运动检查，不代表发布级画质。

镜头 B：
- 832×480、33 帧、16fps、20 steps、CFG 6、shift 8。
- 2.0625 秒，H.264；完整 FFmpeg 解码通过。
- 33/33 帧不同；帧亮度差中位数 3.30921；-40dB / 250ms freezedetect 无事件；blackdetect 无黑帧段。
- 作为低机位近景补充镜头；需要后续人工视觉审片。

## 2. 粤语旁白和字幕

- 两次新 TTS 短句试验的 ASR CER 分别为 0.666667 与 0.125，均未达到 0.10 门槛，未用于最终演示。
- 最终选用项目已有的粤语旁白样本 `data/narration/cantonese-tts-asr-3.wav`，脚本为「如果你有一個好點子就由而家開始將佢變成真正嘅作品」。
- 对最终 MP4 抽取音轨后重新执行本地 Whisper Cantonese；识别文本 CER=0.0，20 个词级时间戳，机器门通过。仍要求人工听审。
- 最终字幕使用一个完整句子 cue，避免在缺少人工短语边界真值时擅自切成多个 cue。
- FFmpeg loudnorm 测量输入约 -16.18 LUFS / -1.42 dBTP；归一化输出测量约 -16.04 LUFS / -1.50 dBTP。

## 3. 最终 MP4 与打包

最终演示文件：
- `data/handoff/prosodic-boundary-audit-20261119/wan21_cantonese_narrated_demo_v2.mp4`
- 832×480、16fps、82 帧、5.125 秒。
- H.264 + AAC、48 kHz stereo；完整解码退出码 0。
- SHA-256：`9db315b344f800fd4842680a0d773d406d1c521b2609fd4dbb43667dff64964c`
- 旁白音轨约 5.12 秒；视频与音频时长差小于一帧。

可分享的测试包：
- 最终推荐测试包：`data/handoff/prosodic-boundary-audit-20261119/wan21_cantonese_narrated_demo_final_bundle_v2.zip`
- 3,145,859 bytes；14 个成员；ZIP CRC test=ok。
- 修正版明确包含实际使用的 `data/narration/cantonese-tts-asr-3.wav` 与最终 MP4 抽取音轨，并排除 CER 未达标的 TTS 试验候选；README_v2、MP4、SRT、QA、manifest、两个源镜头和工作流 JSON 均已纳入。旧 final_bundle.zip 保留为审计历史，不建议分享。

## 4. 验收边界

- 这是真正的 Wan 2.1 GPU 生成镜头 + 本地粤语旁白 + 烧录字幕 + AAC/H.264 MP4 + ZIP 交付演示。
- 不是完整短片：约 5 秒，无 BGM、无多平台输出、未接入正式 VideoProductBuilder 发布/收益复盘闭环。
- 当前固定 CantoTTS ONNX 版本只有 neutral default voice，没有可选成熟女声/御姐音控制；不能宣称满足该声音偏好。
- 需要用户/粤语母语者试听、检查字幕显示与画面叙事连贯性。
- `production_acceptance=false`；不能标记正式产品完成或用于正式宣传发布。

## 下一步

1. 把两个镜头与旁白交给人工审片，记录画面连贯、人物一致性、粤语发音、字幕可读性。
2. 将已验证的两镜头 + 旁白 + 字幕 + loudness + decode QA 流程接入现有 VideoProductBuilder，避免旁路脚本成为第二套生产管线。
3. 增加 BGM ducking、多平台 9:16/16:9 导出、ZIP/manifest/下载验证，再以更长 15–30 秒片段做真实端到端验收。
