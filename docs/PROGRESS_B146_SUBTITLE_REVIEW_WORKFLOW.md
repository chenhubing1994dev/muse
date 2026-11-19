# B146｜粤语字幕人工校对工作流

日期：2026-11-17

## 本轮交付

- 新增 `src/muse/subtitle_review.py`，提供 `build_subtitle_review()`，把旁白参考稿、ASR 原始转写、逐段时间戳与识别置信信息组织为可审计校对单。
- `scripts/muse-high-value-short.py` 在 `cantonese_pilot_v2` 成功完成 ASR 后生成 `subtitle-review.json`，并把它加入 `delivery-bundle.zip`。
- `generation.json` 增加校对单路径和状态：`pending_human_review`、`human_approved=false`。
- 校对单保留原始 ASR 文本，不覆盖原字幕；每段提供 `corrected_text=null` 与 `review_status=pending`，供人工核听后填写。
- 校对单记录 ASR 模型版本、音频 SHA-256、CER 与时间段。对无效时间、重叠/倒序片段、非有限 CER/置信值及路径穿越文件名采取拒绝策略。

## 关键原则

- CER 只是一项机器指标；通过 CER 门槛不代表逐字准确，也不代表已完成听审。
- ASR 原文与人工修订必须分开保存，不能把模型猜测伪装成人工确认。
- 本轮没有重新生成视频，也没有重新调用 ComfyUI/Wan；既有成片仍保留 4 段潜在静帧警告，必须审片确认，不能通过放宽检测门槛掩盖。
- 当前 CantoTTS ONNX 默认音色仍不是用户要求的成熟女声；本轮没有替换 TTS 引擎，也没有声称已支持参考音频克隆。

## 验证

- `tests/test_subtitle_review.py`：3/3 通过，覆盖原始转写/脚本分离、默认不批准、异常时间戳、路径穿越、无效 CER 与非有限置信值。
- `py_compile`：`src/muse/subtitle_review.py` 与 `scripts/muse-high-value-short.py` 通过。
- 全仓库：`py -3.14 -m unittest discover -s tests`，**1277 tests passed**，退出码 0。
- `git diff --check`：新增/修改代码范围通过；仅有既有 LF/CRLF 转换提示，不是 diff 错误。

## 下一步

1. 为校对单补充实际 UI/CLI 审阅入口，并在人工批准前保持发布阻断。
2. 分别检查四段静帧警告对应的原始镜头和最终成片画面，判断是否需要重生成/重剪，而不是仅靠过滤器阈值处理。
3. 调研可本地部署且许可清晰的粤语女声 TTS；逐项验证音色样本、商用许可、参考音频支持与真实合成效果。
4. 在资源与模型状态确认后，安排新的 ComfyUI/Wan 镜头生成实测。
