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

## 模型下载现状

- 本地 Python 3.14 项目隔离依赖可导入：`faster-whisper 1.2.1`、`huggingface_hub 2.4.0`、`onnxruntime 1.32.0`、`soundfile 0.13.1`。
- 固定模型 `cts/model.bin` 预期 967,072,097 bytes，SHA-256：`d343caa7ef827ddb60bc2c1c8c741b29d2bd99d5387aa5c6f96cbd26f6804b34`。
- 下载 Job `wc_job_BFvgB6jBoFejfsaG` 最终 timeout（3600 秒）；日志显示 peer closed connection、TLS handshake failure，累计下载仅有中断的 partial 文件；最终 `cts/model.bin` 未生成。未关闭 TLS 校验，也未将 partial 文件当成可用模型。
## 未完成验收

1. 完成模型下载并校验精确大小与 SHA-256。
2. 对真实 `canto-eval-1/2/3.wav` 运行本地 ASR，保存原文、识别结果、CER、逐段时间戳与 SRT。
3. 完成人工试听；核对粤语口语、专有名词、数字、英文夹杂和同音字。
4. 将真实旁白与 SRT 封装到真实 Wan 生成片段，验证音画时长、字幕同步、MP4 全片解码与 ZIP CRC。
5. B-138 音频响度/真峰值/峰值电平/RMS 质检与 B-139 旁白感知 BGM sidechain 已实现；仍需人工试听与真实 Wan 成片验收。详见 B-138/B-139 进度文档。
## 诚实状态

**代码接通：ASR/SRT 结构完成；全项目回归：1,229 项通过、1 项跳过；真实 Whisper 模型：下载超时；真实 ASR/CER：未验收；人工听感：未验收；B-138 音频 QA 与 B-139 BGM ducking：代码和合成素材实机验证通过；自动旁白默认启用：不允许；发布就绪：否。**