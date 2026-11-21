# B245｜M5-TTS 粤语后端接入审查

日期：2026-11-21

## 本轮范围

只读审查 B244 韵律契约、NarrationGenerator、现有 Cantonese TTS 运行时桥接、VideoTaskWorker 与 VideoProductBuilder。没有修改生产代码、共享 Worker/Builder、主进度文档；没有安装依赖、下载模型、加载模型或启动推理。

## 结论摘要

M5-TTS 值得作为**可选实验后端**继续评估，但当前不具备安全接入生产旁白链的条件。默认后端继续保持已部署的 `canto-tts-nano`。

## 现有链路事实

1. `NarrationGenerator.generate()` 输出分段文本、情绪和预计时长；风格配置另存于 `synth_params()`。
2. `apply_voice_style()` 只是把 `voice` 配置附加到 segment；`synthesize_cantonese_styled()` 也只在返回结果附加 `voice_style` 元数据，不把参数传给底层 TTS。
3. `VideoTaskWorker` 当前把生成的各段文本合并为单一脚本，再传给 `VideoProductBuilder.build_from_prompt()`；分段情绪和声学意图没有随生产调用传递。
4. `VideoProductBuilder.package_generated_clip_with_cantonese_narration()` 直接调用 `synthesize_cantonese_with_runtime()`。它在现有 Python 3.14 隔离子进程中运行固定版本 canto-tts ONNX，合成完成后可选执行粤语 ASR，再进入现有视频封装与 BGM 混音。
5. 因此，B244 目前确实是独立契约，尚未接入生产旁白链；现有 `yujie_canto` 的语速/音高/气声目前只是意图元数据，不是实际声学效果。

## B244 与旧配置的字段映射

| B244 字段 | 现有风格配置 | 当前结论 |
|---|---|---|
| `emotion` | 每段生成情绪 | 可保留为意图；后端无已验证控制 |
| `rate_scale` | `speed_scale` | 需要显式映射，不能静默混用 |
| `pitch_semitones` | `pitch_semitones` | 名称一致，但现有后端不支持已验证的音高控制 |
| `breath_hint` | `breath_hint` | 只作提示，当前后端不支持 |
| `pause_before_ms` / `pause_after_ms` | 尚无生产级字段 | 可在后续分段音频组装层研究实现；不能冒充 TTS 原生能力 |

接入时每段应先经过 `build_prosody_plan()`。能力列表必须由具体后端实现和测试证明；不能因 README、模型论文或配置字段存在就宣称支持情绪、语速、音高、气声或停顿。契约的 `audio_effects_verified` 必须保持 false，直到真实音频验证完成。

## M5-TTS 上游能力与边界

- 官方项目明确列出粤语 Yue；PyTorch API 路径可传入 `accent="yue"`，配合 Chinese 条件。
- 官方 README 推荐 Python 3.10 与 Linux x86_64；本机 Windows AMD + Python 3.11 的兼容性和速度尚未验证。
- 官方文档描述 CPU 推理；PyTorch 路径应显式评估 CPU + SDPA，不能默认依赖 FlashAttention 2/BF16 NVIDIA 路径。
- 当前 ONNX CLI 路径没有完整暴露 `accent=yue` 条件，不能把 ONNX CPU 路径当成已验证的粤语方案。
- 官方报告中的 CPU RTF/首包延迟来自其指定 Xeon 测试配置，不可外推为本机性能。
- 零样本音色克隆需要参考音频及准确转录；只允许使用本人拥有或明确授权的录音。
- README 指明模型权重许可应逐个查看模型仓库/发布文件；在固定具体 checkpoint、codec 与依赖前，不得笼统宣称所有权重均为同一许可证。

## 依赖与模型包复核（上游源码 + 模型卡）

- 已通过 GitHub 连接器读取上游 `requirements.txt`、`inference.py` 与 `infer_onnx.py`。当前 requirements 固定 `torch==2.6.0+cu124`、`torchaudio==2.6.0`、`onnxruntime-gpu==1.23.2`，并包含 `pynini==2.1.7`、`WeTextProcessing==1.3.0` 等文本规范化依赖；不能原样安装到 Muse 的 AMD/Windows 环境，也不能污染现有 Python 3.14 TTS 环境。
- 上游 PyTorch `M5TTS.from_pretrained()` 接受显式 `device="cpu", attention="sdpa"`；`M5TTS.infer()` 确实接受 `accent`，并将其传入文本规范化与 `Segment` conditioning。因此，若做粤语实验，PyTorch CPU+SDPA 路径比当前 ONNX CLI 更符合 Yue 条件需求。
- `M5-TTS-50M-ONNX` Hugging Face 模型卡显示完整包约 **1.86 GB**、Apache-2.0；但其当前 ONNX 推理入口明确不暴露方言/口音条件，故不应因为它有 CPU 路径就下载并接入 Muse 粤语主流程。
- `M5-TTS-50M-VoiceClone` 模型卡标注 Apache-2.0、BF16、约 0.1B 参数；该模型卡没有提供可核实的完整下载字节数。PyTorch codec 仓库的文件级清单/准确下载总量也尚未从权威文件清单确认。故目前不能给 PyTorch 粤语方案报出可信总下载量或 D 盘空间预算。
- 下一次准备工作应先固定 GitHub commit 与 Hugging Face revision，并获取 PyTorch checkpoint、codec 的确切文件清单/字节数及各自许可；依赖安装方案必须移除 NVIDIA-only 假设并验证 Windows CPU 兼容性，特别是 `pynini`/文本规范化组件。

## 推荐的最小接入设计（尚未实施）

1. 新增独立、可选的 M5-TTS backend adapter；不改写现有 canto-tts adapter，也不替换默认后端。
2. 运行时采用与 Muse 主进程隔离的专用 Python 环境和本地模型目录；参考现有 `cantonese_tts.py` 子进程桥接方式，使用结构化 JSON 结果、超时、错误码和无控制台窗口选项。
3. 初始实验只启用已验证的 `Chinese + accent=yue`；未证实的韵律控制全部标为 unsupported。禁止在推理期间自动联网下载。
4. 首轮只对同一组粤语短句做离线 A/B：现有 canto-tts 与 M5-TTS；检查字词、粤英夹杂、专名、停顿、自然度、音色一致性、时长、峰值/响度和 CPU RTF。
5. 只有通过 Windows AMD 真实推理、ASR/CER、人工盲听及视频产品封装回归后，才允许把 M5-TTS 标记为可选可用后端；默认切换需另行决策。

## 尚未批准或未执行

- 没有创建 D 盘隔离环境。
- 没有安装 M5-TTS 依赖或编译任何原生扩展。
- 没有下载 M5-TTS checkpoint、codec 或 Hugging Face 模型文件。
- 没有使用任何参考音频做音色克隆。
- 没有修改 `NarrationGenerator`、`VideoTaskWorker`、`cantonese_tts.py`、`VideoProductBuilder`、`pyproject.toml` 或 `docs/PROGRESS.md`。
- 本轮没有运行代码测试；本轮为只读源码审查与设计记录。

## 下一步

在用户明确批准模型/依赖下载前，继续进行非破坏性准备：固定上游 commit、逐项核对 requirements 与模型/codec 仓库许可及文件大小；随后形成 D 盘空间预算和隔离安装清单。获得批准后，才建立独立环境并进行 CPU+SDPA 的真实粤语 A/B 测试。
