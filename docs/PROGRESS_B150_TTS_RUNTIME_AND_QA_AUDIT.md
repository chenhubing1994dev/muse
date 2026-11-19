# B150｜粤语 TTS 本机运行基线与视频 QA 复核

日期：2026-11-17

## 本轮范围

只读核验 Muse 当前 Windows 环境、CosyVoice-Cantonese 上游公开说明、已有字幕复核与 MP4 QA 产物。未执行第三方安装脚本、未安装依赖、未下载模型、未启动 Wan 推理，也未更改现有视频或数据库。

## CosyVoice-Cantonese：本机可运行性结论

- 上游 fork README 说明其 Windows 一键环境使用 Python 3.10，并将 NVIDIA GPU/CUDA 作为明确支持路径；README 的 quick start 声称首次 setup 下载约 1.5 GB 模型。该 README 没有声明 AMD ROCm 或 DirectML 支持。
- 本机 `py -0p` 实测可用 Python 为 3.11.9、3.12.8、3.13.14、3.14.5；没有注册 Python 3.10 解释器。因此不能直接照搬 fork 的 Python 3.10 安装脚本。
- Python 3.11 当前环境只读探测结果：torch 2.12.0+cpu，`torch.cuda.is_available() == false`，`torch.version.cuda == null`，`torch.version.hip == null`；onnxruntime 1.24.3 的可用 provider 只有 `AzureExecutionProvider` 与 `CPUExecutionProvider`。没有 DirectML/CUDA provider。
- Python 3.12/3.13/3.14 环境未发现 torch、torchaudio、onnxruntime、onnxruntime-directml、modelscope、cosyvoice 或 gradio。
- Windows 视频适配器实测包含 AMD Radeon RX 6750 GRE 12GB，驱动 32.0.21045.16010；但当前 Python 3.11 语音运行环境是 CPU-only，不能据显卡存在推断 CosyVoice fork 能调用 AMD GPU。
- 本机检查的候选目录 `D:\AI AGENT\Muse\vendor\CosyVoice-Cantonese`、`D:\AI AGENT\CosyVoice-Cantonese`、`D:\AI AGENT\Muse\external\CosyVoice-Cantonese` 与 `.cache` 路径均不存在。因此本轮无法在本机对 fork 的 `setup_cantonese_ui_venv.ps1`、`requirements.txt`、`scripts/download_model.py` 做逐行源码审计；不得把上轮文档记录冒充成本轮重新读取的脚本证据。
- 公开 README 声称支持 Python 3.10 + Windows venv，并明确写出 NVIDIA GPU 支持；这只能证明项目作者公开声明的目标，不构成本机 AMD/CPU 兼容性证明。README 的约 1.5 GB 下载说明与此前记录的 Hugging Face 完整快照约 9.75 GB 存在口径差异，未确认具体下载过滤范围前不能估算实际磁盘需求。

### 决策

暂不安装 CosyVoice-Cantonese。下一步必须先取得 fork 源码（不执行脚本），静态审计 setup/requirements/download_model；然后在独立 Python 3.10 环境中先做 CPU-only 依赖解析与 provider 检查。只有依赖解析可控、实际下载文件清单和许可清楚后，才评估小规模模型试验。不得将当前 CPU-only 环境描述成 AMD GPU 加速成功。

## 现有视频 QA 复核

- 产品 QA 报告：`data/products/f57e8f7277a85dca44055aa71df1cbb1/1713600cfe751184fc96/qa-report.json`。
- 对应 MP4 SHA-256：`07e0d650f885a87b42458fd57d5fe750d4c7c47d6ea45ccd799590523b1cadc4`；24.5 秒、1080×1920、24 FPS、H.264/AAC；全片解码通过。
- 技术检查项（容器、音视频流、尺寸、帧率、时长、文件大小、完整解码）均通过；黑帧和静音区间为空。
- 仍有 4 个 `frozen_frames` 警告：3.416667s/1.666667s、8.166667s/1.083333s、9.375s/1.958333s、11.458333s/4.958333s。警告是启发式信号，不能自动等同于错误镜头。
- 对应 `storyboard.json` 与 B149 源片分析已将区间定位到 shot-01、shot-03、shot-04、shot-05；shot-05、shot-04、shot-01 优先替换，shot-03 先检查合成缩放/裁切效果。当前无需重复执行相同映射，也不得放宽 QA 阈值掩盖问题。
- `generation.json` 明确记录 motion disabled、六段素材均为 existing_wan21_ai_source；因此这不是新 Wan 推理产物。产品仍为 testing，`ready_to_publish=false`，字幕 `pending_human_review` 且 `human_approved=false`。

## 已观察到的真实进展

- 字幕校对 CLI 与模块已有 1,281 项全仓 Python 回归通过记录；单独字幕校对测试 9/9 通过。
- 实际 Commander/TaskQueue/Worker 任务 `fa28e1bbeb92465388b3b8494e9935ae` succeeded，并产出 22,187,340-byte MP4 与 11 项 delivery bundle；bundle CRC 通过。
- 该任务复用既有六段 Wan 镜头，没有启动新 Wan 推理。人工字幕批准仍未完成。

## 本轮结论与下一步

1. CosyVoice-Cantonese 暂不适合直接在当前 Muse 环境安装：缺 Python 3.10，当前可观察到的 TTS/ONNX 运行环境仅 CPU provider，且 fork 的源码尚未本地取得审计。
2. 先以无安装、无模型下载方式取得并审计 fork 源码，再决定是否建立隔离 Python 3.10 CPU 环境。
3. 视频端下一项可验证工作是针对 shot-05/04/01 获取新的合规源素材，逐段运行同一 freezedetect 预检，再重新剪辑和复跑最终 QA；shot-03 先做原始帧与成片时间码的视觉核验。
4. 继续保留人工审核门槛；没有人工逐段听审与批准，不生成最终 approved SRT，不把产品改为可发布。

## 证据边界

本轮本机解释器、模块/provider、GPU 适配器和路径存在性来自 WebCodex Runner 只读命令；MP4、QA 与 storyboard 结论来自项目现有 JSON 产物。CosyVoice fork 安装脚本和依赖文件未在本机找到，故其具体实现仍标记为未审计。
