# B147｜粤语成熟女声 TTS 方案评估

日期：2026-11-17

## 结论

当前 CantoTTS ONNX 单一 baked voice 无法满足成熟女声/参考音频需求。候选方案中，**CosyVoice 3 + 粤语 UI fork** 最接近目标体验；**GPT-SoVITS** 的粤语与少样本克隆能力明确，但 Windows AMD GPU 与权重/前端资产许可需要更谨慎处理。本轮只完成资料核验，没有安装、下载模型或改动本地 TTS 环境。

## 候选 A：CosyVoice 3 + CosyVoice-Cantonese UI

- 上游：`https://github.com/FunAudioLLM/CosyVoice`
- 粤语 UI：`https://github.com/wai-lok-tong/CosyVoice-Cantonese`
- 上游 Fun-CosyVoice3-0.5B-2512 模型卡标注 Apache-2.0；上游说明支持广东话/多种中文方言、跨语言 zero-shot voice cloning、情绪/语速/音量指令。
- 粤语 UI fork README 声称提供女声/男声/情绪预设，以及 3–30 秒自定义参考音频克隆；Windows 一键脚本，依赖 Python 3.10，明确列出的 GPU 支持为 NVIDIA。
- 上游主仓库现位于 QwenAudio/CosyVoice（旧 FunAudioLLM 地址重定向）。上游 cosyvoice/cli/cosyvoice.py 在 torch.cuda.is_available() == False 时会关闭 JIT、TensorRT、FP16；cosyvoice/cli/frontend.py 为部分 ONNX 子模型选择 CPUExecutionProvider。因此引擎代码存在 CPU fallback，但这不等于粤语 fork 的 Windows 安装链已验证可用。
- 官方上游安装说明以 Linux/Conda/Python 3.10 为主，README 没有正式 Windows 安装流程；fork 的 Windows 脚本明确面向 NVIDIA。当前没有证据证明 RX 6750 GRE 可通过 ROCm/DirectML 加速该 fork，也没有完成本机 CPU 实测。
- 资源实测：只读检查 fork 的 scripts/download_model.py，调用 snapshot_download() 时没有 allow_patterns/ignore_patterns 白名单，因此下载整个模型快照。查询 Hugging Face 文件树得到 20 个文件、合计 9,747,516,745 bytes（约 9.75 GB）；README 所写约 1.5 GB 与完整快照大小不一致，可能只代表子集/旧说明。
- requirements.txt 配置 --extra-index-url https://download.pytorch.org/whl/cu121，同时固定 torch==2.3.1、torchaudio==2.3.1；这不是明确的 CPU-only 安装配置，不能保证 Windows + AMD 用户会得到合适的 CPU wheel，也不能据此认为支持 ROCm。

## 候选 B：GPT-SoVITS

- 上游：`https://github.com/RVC-Boss/GPT-SoVITS`
- 文档明确支持粤语、5 秒参考音频 zero-shot、约 1 分钟 few-shot 微调；官方安装说明有 Windows CPU 模式，ROCm 安装选项只出现在 Linux 脚本。
- 对本机的现实路径是先以 Windows CPU 做短句基准，不假设 AMD GPU 可直接驱动。官方 README 的 RTF 数据来自 RTX 4060Ti/4090 与 M4 CPU，不代表本机 Ryzen CPU 的速度。
- 许可注意：代码仓库和 Hugging Face 预训练仓库标注 MIT，但 G2PW 等额外资产的逐制品来源/许可仍需核验；维护者仓库已有公开 issue 专门提出该许可/来源问题。商业视频生产不能仅凭主仓库 MIT 标签就推断所有模型、训练语料与前端资产均可商用。

## 建议路线

1. 现有证据显示完整模型快照约 9.75 GB，且 requirements 加入 CUDA 12.1 wheel 源；本机 D 盘可用空间与 Python 3.10 独立环境需再次检查。不要直接执行一键安装；先设计 CPU-only 依赖解析/安装试验，验证 PyTorch wheel 来源和 ONNX Runtime provider。
2. 若 CosyVoice 不支持本机 AMD/CPU，建立独立 Python 3.10 环境评估 GPT-SoVITS CPU 短句合成；不与 Muse 当前 Python 3.14/ASR 环境混装。
3. 只使用用户本人或已取得明确授权的参考音频；不得把第三方声音克隆成可误导他人的真实身份。
4. 通过同一段粤语旁白做 A/B 听审：粤语声调、咬字、停连、自然度、成熟女性声线、时长、RTF、峰值/响度与 CER。通过听审与许可审计后，才接入 Muse `TTSAdapter`。
5. 任何新模型下载前，先列出实际文件、大小、许可与目标 D 盘路径，再执行；本轮没有下载任何模型。

## 参考资料

- CosyVoice 官方仓库及方言/zero-shot 能力：`https://github.com/FunAudioLLM/CosyVoice`
- Fun-CosyVoice3-0.5B-2512 模型卡（Apache-2.0）：`https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512`
- 粤语 UI fork（女声预设、3–30 秒参考音频、Windows/NVIDIA 说明）：`https://github.com/wai-lok-tong/CosyVoice-Cantonese`
- GPT-SoVITS 官方 README（粤语、参考音频、Windows CPU 与 Linux ROCm 安装路径）：`https://github.com/RVC-Boss/GPT-SoVITS`
- GPT-SoVITS 预训练模型仓库：`https://huggingface.co/lj1995/GPT-SoVITS`
- G2PW 许可/来源待澄清 issue：`https://github.com/RVC-Boss/GPT-SoVITS/issues/2835`

## 状态

- 本轮：只研究与记录；未下载、未安装、未启动任何 TTS 新模型。
- 已完成只读脚本审计；下一步：在不安装、不下载模型的前提下评估可否把 CUDA 依赖替换为 CPU-only，并检查实际 Windows ONNX Runtime provider；如果依赖链不可靠，再验证 GPT-SoVITS CPU 环境。上游 CPU fallback 证据：https://github.com/QwenAudio/CosyVoice/blob/main/cosyvoice/cli/cosyvoice.py 与 https://github.com/QwenAudio/CosyVoice/blob/main/cosyvoice/cli/frontend.py。模型文件树：https://huggingface.co/api/models/FunAudioLLM/Fun-CosyVoice3-0.5B-2512/tree/main?recursive=true&expand=true。
