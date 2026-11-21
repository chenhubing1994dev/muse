# B244｜分段旁白韵律意图契约

日期：2026-11-21

## 目标

将“想要怎样说”与“后端实际上能怎样合成”分开。现有 Cantonese ONNX TTS 不支持的情绪、语速、音高、呼吸控制，不得仅因配置里出现字段就宣称已生效。

## 本次新增

- `src/muse/prosody_contract.py`：纯函数式、无外部依赖的韵律意图规范化器。
- `tests/test_prosody_contract.py`：覆盖默认值、边界、顺序保留、后端能力映射及 fail-closed 情况。
- 输入按段保存文本、情绪、语速意图、音高意图、呼吸提示和前后停顿。
- 后端必须显式声明 `emotion/rate/pitch/breath/segment_pause` 能力；只有声明支持的控制才会列入 `applied_controls`，其他均列入 `unsupported_controls`。
- 输出始终标记 `audio_effects_verified=false`，因为契约本身不是 TTS 实际执行或音频试听证据。

## 重要边界

这只是独立的数据契约和验证层，**尚未接入** `NarrationGenerator`、`VideoTaskWorker`、`cantonese_tts` 或 `VideoProductBuilder`。没有改变默认粤语合成、字幕、BGM 或成片流程。现阶段现有 ONNX 后端不应声明任何尚未实现的声学控制能力。

## 下一步验收

1. Python 3.11/3.14 分别运行该测试文件。
2. 为 M5-TTS 做只读能力探测：粤语 `--lang Chinese --accent yue`、本地权重/codec、CPU 路径、输出 24 kHz mono；官方文档推荐 Linux x86_64/Python 3.10，因此 Windows AMD 需单独验证。
3. 只有在选定后端确实应用某控制并通过音频对比后，才将其能力加入适配器清单。
4. 后续由旁白流水线负责人审阅接入点，再单独修改共享流程；本次不触碰共享 worker/build 文件。

## M5-TTS 上游接口核对

- 上游 Python API 的 `M5TTS.infer` 接受 `accent="yue"`，并将 accent 传入条件化文本段；粤语路径会做中文文本规范化及 OpenCC 香港繁体转换。
- `M5TTS.from_pretrained(device="cpu", attention="sdpa")` 是代码允许的显式 CPU/SDPA 路径；默认 `flash_attention_2` 则要求 BF16 NVIDIA GPU，不能依赖自动回退。
- 当前 `infer_onnx.py` 的 CLI 和 `generate` 路径只有 `lang`，未接入 `accent` 参数；因此不能把其 CPU ONNX 快速路径误认为已经验证粤语 Yue 条件。
- 官方 README 推荐 Python 3.10/Linux x86_64；模型输出 24 kHz mono。M5-TTS 还需要参考音频及准确转录文本进行零样本音色克隆，须使用有权使用的声音素材。
- 结论：先探测 Muse Windows 主机的 Python/依赖/本地 codec 与模型缓存；优先评估 PyTorch CPU+SDPA 的粤语路径，不下载任何模型、不改当前生产环境。

## 本机只读预检

- 新增可重复运行的 `scripts/check_m5_tts_readiness.ps1`，仅检查 Python 安装、Rust/Bun/Node、依赖包清单和本地模型缓存；不导入模型、不联网、不安装。
- psmux Job `1259f2eb47754a1987221f34ced1fa6e` exit 0。
- 本机有 Python 3.14、3.13、3.12、3.11；Python 3.14 目前仅检测到 NumPy。Python 3.11 检测到 PyTorch 2.12.0、Transformers 5.5.4、NumPy、ONNX Runtime 1.24.3、Safetensors 0.8.0、SoundFile 0.13.1；未检测到 torchaudio、huggingface-hub、librosa、pynini、WeTextProcessing。
- 未发现 M5-TTS Hugging Face 模型缓存；本地已有 canto-tts-nano 和 Whisper 粤语 ASR 资源目录。
- Rust、Cargo、Bun 未发现，Node 可用。CHIMERA 的本机构建条件尚未满足。
- 结论：**当前不具备直接运行 M5-TTS 的完整依赖与模型条件**。不要把已有 PyTorch/ONNX Runtime 当作模型已可用；也不要向 Python 3.14 主环境塞入一套新的 TTS 依赖。

## 验证记录

- Python 3.11：`tests/test_prosody_contract.py` **10/10 passed**，psmux Job `a9ba7b7dcf1549a792b6ebe7d18e4e8c`，exit code 0。
- 首轮测试暴露默认停顿被误报为不支持；已修正为仅匹配调用者显式请求的控制字段，并补充 capability 类型校验。
- 此测试只验证纯数据契约，不验证任何 TTS 声学效果，也不证明 M5-TTS 已在本机运行。
- Python 3.14：同一测试文件 **10/10 passed**，随只读 readiness probe 一起执行；psmux Job `b83437f7ed444a5893669f4df304dff8`，exit code 0。
