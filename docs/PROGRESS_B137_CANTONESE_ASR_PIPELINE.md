# B137｜粤语旁白 ASR、自动字幕与产品账本接通

日期：2026-11-15

## 本轮目标

把已经真实合成的粤语旁白从“能生成 WAV 并合并 MP4”推进到可机器对稿、按音频时间戳生成 SRT、并将字幕与 ASR 证据一起交付的闭环。模型下载和真实语音验收仍单独计量，不把模拟测试当成真实 ASR 通过。

## 实施内容

- 新增 `src/muse/cantonese_asr.py`：本地-only faster-whisper 适配器，固定仓库 `alvanlii/whisper-small-cantonese` 与 commit `b03a4e5d482f06858ce10ebbabebabec9f447a0d`。
- 加载前检查 CTranslate2 `cts/model.bin` 的精确字节数和 SHA-256，并检查配置/词表文件；模型缺失或不完整时 fail-closed，不在转录时联网下载。
- 以粤语文本字符错误率（CER）对照原旁白脚本；比较时忽略标点与空白。超过阈值返回 `needs_review`，不会自动声称语音准确。
- SRT cue 时间直接来自 ASR segment 的 start/end 时间戳，不按文本长度估算；输出使用同目录临时文件和原子替换。
- `VideoProductBuilder.package_generated_clip` 增加可选 `subtitle_path` / `narration_asr`，将 SRT、模型版本、音频哈希、转录文本和 CER 报告写入 storyboard/manifest 与 ZIP，并在 `verify_path` 校验文件哈希、ZIP 内容及旁白/ASR/字幕关联。
- `package_generated_clip_with_cantonese_narration(..., asr_model_dir=...)` 增加可选本地 ASR 阶段。未提供 ASR 模型目录时维持原有 TTS+封装路径；提供但模型未就绪时失败关闭，不静默省略字幕。
- `VideoProductBuilder.BUILDER_VERSION` 升至 6，使新增字幕/ASR 元数据进入产品版本指纹。

## TDD 与验证

- 新增 `tests/test_cantonese_asr.py`，覆盖缺失/不完整模型拒绝、固定模型调用、CER 比较、SRT 时间戳输出。
- 新增产品封装测试，验证字幕和 ASR 证据进入 ZIP，且哈希/音频来源匹配。
- 新增 TTS→ASR→产品封装编排测试，验证 ASR 在临时 WAV 上运行、字幕被传入封装、临时 WAV/SRT 在交付后清理。
- 首轮 RED 测试按预期失败：ASR 模块尚不存在；产品集成测试因 `subtitle_path` 参数尚未实现而失败。完成实现后复测。
- Python 3.14 定向回归：`tests.test_cantonese_asr` + `tests.test_cantonese_tts` + `tests.test_video_product_builder`，**31/31 通过**。
- 该 31 项包含真实文件系统/ZIP/哈希与编排结构测试；ASR 模型加载通过 mock 隔离，因此不是实际模型推理验收。

## 模型下载与真实推理

- 固定模型 `alvanlii/whisper-small-cantonese` revision `b03a4e5d482f06858ce10ebbabebabec9f447a0d` 已下载到本地；`cts/model.bin` 精确大小 967,072,097 bytes，SHA-256 为 `d343caa7ef827ddb60bc2c1c8c741b29d2bd99d5387aa5c6f96cbd26f6804b34`。下载采用严格 TLS 校验，没有关闭证书验证。
- 已用真实本地 TTS WAV 执行 Whisper 推理，不再是 mock-only：三条评测样本均产生了识别文本与时间戳。样本 1 将专有名词 Muse 识别为“WS”；样本 2 有替换/插入错误（一次评估 CER 为 0.125）；样本 3 与脚本完全匹配（CER 0.00）。
- 解码参数经实测调整为 `vad_filter=False`、`condition_on_previous_text=True`、`temperature=0.0`、`compression_ratio_threshold=2.4`、`log_prob_threshold=-1.0`、`no_speech_threshold=0.6`。默认 CER review threshold 收紧到 0.10；机器分数不替代人工听审。
- 三条样本及早期评估记录位于 `data/narration/`；较严格 0.10 门槛的评估重跑曾提交为 Job `wc_job_ri0G3ZtO7np9NJL1`，其最终结果尚待恢复观察，不把旧阈值 JSON 冒充新门槛结果。

## TDD 与验证补充

- 定向回归覆盖 `tests.test_cantonese_asr`、`tests.test_cantonese_tts`、`tests.test_video_product_builder`、`tests.test_video_qa`：**43/43 通过**；`python -m compileall -q src tests` 通过。
- 真实本地 TTS → Whisper ASR → 时间戳 SRT → BGM sidechain mix → MP4/ZIP 产品封装流程已用临时合成测试视频实际运行；`verify_path`、ZIP CRC、旁白/BGM/SRT 文件成员与 provenance 校验通过。临时测试目录已清理。
- 上述 E2E 使用 FFmpeg 合成测试画面，不是 Wan AI 生成素材；其音频响度偏低并出现黑帧/静音类 QA 提醒，产品门仍为 `ready_to_publish=false`。这只证明管线接通，不代表成片可发布。

## 未完成验收

1. 恢复并检查 0.10 CER 严格门槛评估 Job，核对每条样本最终状态与 SRT。
2. 人工试听三条粤语旁白，特别核对 Muse 专有名词、口语助词、数字、英文夹杂及同音字。
3. 将通过人工审核的旁白/SRT 接入真实 Wan 生成片段，验证画面全片解码、音画时长、字幕同步、音量/真峰值与 ZIP 完整性。
4. 运行全项目回归；本轮仅确认 43 项定向测试通过，不能据此宣称全量回归通过。
5. 评估不同授权的自然粤语声线；当前 TTS 是中性默认音色，不应宣传为成熟女声。

## 诚实状态

**代码接通：ASR/SRT 与产品封装完成；模型下载/哈希：真实通过；本地真实 ASR 推理：通过；三条样本机器对稿：一条精确、一条专有名词错误、一条需复核；人工听感：未验收；定向回归：43/43 通过；全项目回归：待执行；真实 Wan 成片：未验收；自动旁白默认启用：否；发布就绪：否。**
