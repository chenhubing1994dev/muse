# B246｜M5-TTS 资产清单与隔离部署风险

日期：2026-11-21

## 本轮范围与执行边界

本轮继续 B245 的非破坏性审查。没有下载模型/依赖，没有安装、导入或加载 M5-TTS，没有创建虚拟环境，没有运行推理或修改生产代码。只新增本记录；没有修改 `docs/PROGRESS.md`。

## 上游源码身份（已读取文件 Blob）

GitHub Connector 从 `ASLP-lab/M5-TTS` 默认分支读取：
- `requirements.txt` Blob SHA：`e48f42d7b0a12b1a827649bf0d8d826f01c1c339`
- `inference.py` Blob SHA：`c6c24d8d0a356df784adf829800c6861ff09f9f1`
- `infer_onnx.py` Blob SHA：`2a82ec5b078878e31ace3bcb261ca19f6ee5c5b0`

说明：这是文件 Blob SHA，不是仓库 commit SHA。当前审查尚未固定同一 commit 的完整源码快照；不能把默认分支的后续变更视为已锁定。

## 模型/codec 资产核对结果

| 资产 | 已核实信息 | 未核实/限制 |
|---|---|---|
| `ASLP-lab/M5-TTS-50M-VoiceClone` | Hugging Face 模型页显示 Apache-2.0、BF16、约 0.1B 参数 | 当前检索页面没有可核实的完整文件列表和下载字节数 |
| `ASLP-lab/M5-TTS-50M-CustomVoice` | 上游 README 明确为独立 preset-speaker checkpoint；不是 VoiceClone 的 speaker_id 切换 | 未核实其文件清单、下载字节数和独立许可文本 |
| `ASLP-lab/M5-TTS-Audio-Tokenizer` | PyTorch inference 默认依赖此 codec；模型和 codec 分开 | 未核实完整文件清单、下载字节数及独立模型仓库许可 |
| `ASLP-lab/M5-TTS-50M-ONNX` | Hugging Face 页面显示完整包 1.86 GB、Apache-2.0；包含 TTS graphs、embeddings、tokenizer、参考音频 encoder/decoder，不需另下 codec | 当前 ONNX entrypoint 不暴露 accent/dialect conditioning，因此不适合作为 Muse 粤语 Yue 实验首选 |

资产清单结论：现在只可对 ONNX 包报出公开页面显示的约 1.86 GB；不能对 PyTorch VoiceClone + codec 或 CustomVoice + codec 报出可靠总下载量，也不能给出完整 D 盘空间预算。不要用“50M 参数”推算权重文件大小——50M 是语言模型参数口径，codec 是另一个独立资产。

## 依赖风险核对

当前上游 `requirements.txt` 固定：
- `torch==2.6.0+cu124`、`torchaudio==2.6.0`
- `onnxruntime-gpu==1.23.2`
- `transformers==4.57.6`、`accelerate==1.13.0`、`huggingface-hub==0.36.0`
- `pynini==2.1.7`、`WeTextProcessing==1.3.0`
- 以及 numpy、scipy、librosa、numba、soundfile、tokenizers 等依赖

风险判断：
1. 不能原样安装：requirements 把 CUDA 12.4 PyTorch 和 GPU ONNX Runtime 放进同一份默认清单；Muse 目标主机是 AMD GPU，且本轮优先验证 CPU + SDPA。
2. PyTorch API 允许显式 `device="cpu", attention="sdpa"`，但这是代码路径存在，不等于完整依赖在 Windows 已可安装或推理已可用。
3. `pynini`、`WeTextProcessing` 是重点兼容性风险点；必须先验证所选 Python/Windows wheel 与文本规范化依赖链，不应先批量安装再排错。
4. 项目推荐 Python 3.10/Linux x86_64；Muse 现有 Python 3.11/3.14 环境不能据此认定受官方支持。若后续实验，使用 D 盘独立 Python 3.10 或经预检选定的隔离解释器，不污染主环境。
5. 不应默认安装 FlashAttention；它针对上游 NVIDIA BF16 GPU 路径，CPU+SDPA 实验应明确排除。

## Windows CPU 依赖兼容性补充（官方安装矩阵 + PyPI 元数据）

- PyTorch 官方 previous-versions 页面提供 PyTorch 2.6.0 + TorchAudio 2.6.0 的 CPU-only wheel 安装入口；TorchAudio 官方兼容矩阵列出 torch 2.6 / torchaudio 2.6.0，Python 3.9–3.13。故 Python 3.11 + CPU 版 torch/torchaudio 在版本矩阵上是可行候选，不必为了 CPU+SDPA 强行安装 CUDA 12.4 wheel。仍须在隔离环境验证实际依赖解析和 import，不代表 M5-TTS 整套依赖已兼容 Windows。
- PyPI 的 pynini 2.1.7 文件列表目前展示 source distribution 和 PyPy/Linux wheel，没有显示 CPython Windows wheel；因此 Windows CPython 很可能需要本地编译或替代文本规范化方案。这是当前最主要阻塞项，不可在用户机器上直接尝试构建。需先确认 WeTextProcessing 对 pynini 的实际导入/使用路径及可替代性。
- PyTorch 官方说明 Windows 支持 Python 3.10–3.14，但这只是 PyTorch 自身的支持范围，不能外推到 M5-TTS 全部依赖。
- 参考来源：PyTorch 2.6 previous versions（https://pytorch.org/get-started/previous-versions/）；TorchAudio 2.6 Windows build/compatibility（https://docs.pytorch.org/audio/2.6.0/build.windows.html）；PyPI pynini 2.1.7（https://pypi.org/project/pynini/）；PyTorch Windows install notes（https://pytorch.org/get-started/locally/）。

## 推荐的分阶段隔离方案（尚未执行）

**阶段 A：纯清单与兼容性**
- 固定一个明确 Git commit SHA 与 HF model/codec revision。
- 逐个读取 VoiceClone、codec（如确实需要再读 CustomVoice）的文件名、大小、LFS/Xet 对象、revision 和许可。
- 仅下载前检查 D 盘可用空间、缓存路径和临时文件占用；明确权重、环境、构建缓存、输出 WAV 的分项预算。
- 将 NVIDIA 专属项从实验依赖集剥离；逐项验证 Windows Python 3.10 CPU wheel，特别是 pynini/WeTextProcessing。

**阶段 B：经明确批准后才执行**
- 在 D 盘新建独立 venv/缓存目录；不得复用或覆盖 Muse 现有 TTS 环境。
- 首次只下载 VoiceClone + 匹配 codec，不下载 CustomVoice、ONNX 包或训练数据。
- 关闭推理期间自动联网；权重固定 revision，下载后校验文件 hash。
- CPU + SDPA 做最小 Yue 样本；与 canto-tts-nano 做同文 A/B，保留 CER/MER、人工盲听、RTF、峰值/响度和失败记录。
- 任何 Windows wheel 不可用时停止并报告，不尝试静默换包或升级系统 Python。

## 当前状态

- B246：只读资产/依赖风险审查及文档记录。
- 项目代码改动：无。
- 模型/依赖下载、安装、推理：均未执行。
- Runner Jobs：本 Session 查询为 0。
- 下一步：继续核实 Hugging Face 的确切文件级元数据与 revision；若官方页面/API仍不给出可验证大小，就保留“未知”，不猜总量。取得完整容量清单后，再向用户请求一次明确的 D 盘隔离下载批准。
