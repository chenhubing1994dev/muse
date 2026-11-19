# B162｜语义呼吸剪辑实验与 Wan 2.1 实机质量门

日期：2026-11-19

## 1. 研究证据补充

- 对原灵感「语义呼吸剪辑：让镜头切换跟随句意闭合」继续审阅全文。
- 已把 2008 年 Cantonese preboundary lengthening 研究与 2021 年 PLOS ONE spontaneous-speech prosodic-boundary 研究的全文审阅摘要写回 ResearchKnowledgeStore。
- 2008 研究：7 位香港粤语母语者、8 对歧义句；边界前延长与停顿有统计关联，但音高重置受粤语声调、语境、焦点等因素干扰。
- 2021 研究：方法依赖 ASR/forced alignment 的词级时间点，结合语速变化和静音；300–400ms 静音阈值只带来有限 precision 增益。研究对象是英语自发口语，不能直接视为粤语剪辑效果证据。
- 2023 Pauses and Parsing 全文审阅仍作为反证：孤立停顿不能自动代表句法/语义边界。
- 所有来源仍为 quality=unassessed、independence=unassessed；原 claim 保持 proposed、review_state=not_researched、confidence=unassessed。全文审阅不等于证据质量评审或主张成立。

## 2. 真实粤语词级时间戳实验

使用项目固定版本 Whisper Cantonese 模型，在 CPU/int8 下重新转录三段已有 WAV，保留原始音频与字幕不变，输出到 `data/handoff/prosodic-boundary-audit-20261119/`。

| 样本 | 词级时间戳 | CER | 字幕 cue |
|---|---:|---:|---:|
| 1 | 9 | 0.1875（需复核） | 1 |
| 2 | 22 | 0.125（需复核） | 2 |
| 3 | 20 | 0.000（机器门通过，仍需试听） | 2 |

- 词级时间戳已真实取得，但属于 Whisper approximate word alignment，不等同于经过独立校准的 forced alignment。
- FFmpeg 在 -35dB / -40dB、最短 120ms 条件下检测到静音；样本 1 有一个 337ms 静音与词间间隔相交，但现有 SRT 仍只有一个 cue。
- 样本 2 的 330ms 静音落在 Whisper「一個」词时间跨度内；样本 3 的多个静音也落在词时间跨度内。这些位置应标记为可能的对齐冲突，不得直接当作切点。
- 已生成机器候选分析与人工真值标注模板；目前没有人工确认的 phrase/semantic boundary，因此不能计算 precision/recall，也不能宣称检测器通过。

产物：
- `data/handoff/prosodic-boundary-audit-20261119/word-alignment-results.json`
- `data/handoff/prosodic-boundary-audit-20261119/boundary-candidate-analysis-v2.json`
- `data/handoff/prosodic-boundary-audit-20261119/manual-boundary-annotations-v2.json`

## 3. 最小安全剪辑门

新增：
- `src/muse/prosodic_boundary.py`
- `tests/test_prosodic_boundary.py`

当前模块只负责从候选中选择切点，不负责自动识别语义或声学边界：
- 必须先有人类确认 semantic boundary。
- 必须有 pause / preboundary lengthening / phrase-initial acceleration 等非音高线索。
- pitch reset 单独不能触发切点；考虑粤语词汇声调与语境混淆。
- high visual continuity risk 阻止吸附。
- 候选不在窗口内或没有可靠候选时，原切点保持不变。
- 默认吸附窗口 0.6 秒；不对候选做未经验证的综合置信度打分。
- 尚未接入 `media_compositor.py`，没有改变生产剪辑路径。

## 4. Wan 2.1 实机输出与验收

- ComfyUI 0.23.0 / RX 6750 GRE 12GB 环境中，观察到 `muse-b160-motion-test` 的真实 Wan 2.1 T2V 输出。
- MP4：17 帧、384×384、8fps、2.125 秒、H.264、41,550 bytes；完整 FFmpeg 解码通过。
- 未检测到超过设定阈值的黑帧段；无音轨。
- -40dB / 250ms near-freeze 检测标记 0.875 秒近静止段；四帧检查图显示视觉运动很弱。
- 该片只证明 GPU 推理和 MP4 封装链路实际执行成功，不满足镜头质量验收，`production_acceptance=false`，不可接入宣传片或正式产品。
- 另一个 `muse_staging/wan21_smoke_20261119` 请求曾被中断，耗时约 505.85 秒；不把该中断任务算作成功产物。
- 当前 ComfyUI 队列 0 running / 0 pending；服务仍在线。

产物：
- `data/handoff/prosodic-boundary-audit-20261119/wan21_t2v_motion_test.mp4`
- `data/handoff/prosodic-boundary-audit-20261119/wan21_t2v_motion_test_qa-v2.json`
- `data/handoff/prosodic-boundary-audit-20261119/wan21-motion-test-contact-v4.jpg`

## 5. 回归验证

- 新增剪辑安全门专项 14 项测试通过。
- 旁白管线与媒体剪辑专项合计 54 项测试通过。
- Python compileall 通过。
- 全仓库：`py -3.11 -m unittest discover -s tests -q`，1417 tests，154.808 秒，OK，1 skipped，退出码 0。

## 下一步

1. 先由粤语母语者在人工标注模板中确认短语/语义边界，并校正 ASR 错字与词级时间。
2. 研究真正的 forced alignment 或人工校准路径；不能把 Whisper word timestamp 当成语义边界真值。
3. 有足够标注后再开发声学候选检测、precision/recall 与 A/B 测试；之后才把安全门接入媒体编排。
4. Wan 2.1 下一轮重点解决低运动：检查采样器/shift/步数/帧率与运动提示词，逐项对照实验；在达到运动质量门前不替换生产镜头。
