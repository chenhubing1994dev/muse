# B247｜M5-TTS 上游固定与 Windows 粤语文本规范化兼容性

日期：2026-11-21

## 本轮范围与边界

本轮继续 B246 的只读研究，重点核实 M5-TTS 当前源码 commit、粤语 Yue 文本规范化调用链，以及 Windows/Python 3.11 的 Pynini 安装证据。

- 新增本独立研究记录；没有修改 `docs/PROGRESS.md`、B244/B245/B246 或生产源文件。
- 没有创建 Conda/venv 环境，没有安装依赖，没有下载或加载模型，没有运行 M5-TTS 推理。
- 本轮项目 Runner/psmux 曾观察到另一 Workflow Session 的一个 Job；该 Job 后来完成且 exit code=0，Runner 当前 active Jobs=0。本 Session 没有启动该 Job，也没有把其测试输出记作本 Session 的验证。
- 多个 Workflow Sessions 仍活跃；本轮只新增独立文档，不触碰共享生产源文件。

## 上游源码固定

GitHub Connector 查询 `ASLP-lab/M5-TTS` 最近提交，取得当前默认分支最新可见 commit：

- Commit：`4e7cc91545ad5ecd368d7726129cefb41fc6e1d7`
- 提交说明：`Extend text normalization to Japanese Korean German and French`
- 提交时间：2026-11-12T14:01:59Z

在该 commit 上重新读取并确认以下 Blob SHA：

| 文件 | Blob SHA |
|---|---|
| `text_normalization.py` | `4407b82ffe1bb360f7c66884fdb6c326e1697503` |
| `requirements.txt` | `e48f42d7b0a12b1a827649bf0d8d826f01c1c339` |
| `inference.py` | `c6c24d8d0a356df784adf829800c6861ff09f9f1` |

以上是可复核的源码快照基准；Blob SHA 与仓库 commit SHA 是不同概念。后续实验必须把此 commit SHA 作为源码 revision 固定值，不再使用浮动的 `main`。

## Yue 文本规范化调用链

已直接读取固定 commit 的 `text_normalization.py`、`inference.py`，并读取 WeTextProcessing 的中文 Normalizer：

1. `M5TTS.infer(..., lang="Chinese", accent="yue")` 调用 `normalize_text(text, lang=lang, accent=accent)`。
2. `normalize_text` 对 `lang=Chinese/accent=yue` 仍调用 `tn.chinese.normalizer.Normalizer`，保留 interjections、erhua、标点及全半角设置。
3. 中文 TN 完成后，Yue 路径调用 OpenCC 的 `OpenCC("s2hk")` 转为香港繁体。
4. 源码注释明确指出：Yue 这一步是 Chinese TN + Hong Kong Traditional conversion，**不是粤语专属口语化/读音转写**。
5. 随后 `inference.py` 把 `accent` 继续放入生成上下文的 `Segment`；方言声学条件由模型的 accent conditioning 承担，不能把 OpenCC 文本转换误当成粤语语音能力本身。

### Pynini 是否可以从上游 TN 中移除？

当前上游精确路径不能直接移除 Pynini：

- WeTextProcessing 的 `tn/chinese/normalizer.py` 顶层直接导入 `pynini.lib.pynutil.delete`。
- 中文 Normalizer 构建 cardinal、date、fraction、measure、money、time、math、char、range、whitelist 等 WFST 规则，再用 tagger/verbalizer 组合生成文本规范化图。
- WeTextProcessing 的 `pyproject.toml` 把 `pynini>=2.1.6` 列为核心依赖；M5-TTS 自己的 requirements 固定 `WeTextProcessing==1.3.0` 与 `pynini==2.1.7`。

因此，不应通过删除依赖或只保留 OpenCC 来声称保留了相同 TN 行为。若未来想做无 Pynini 轻量 fallback，必须作为独立实现，针对数字、日期、时间、金额、单位、标点和中英混排建立回归样本，并与 WeTextProcessing 结果逐项对照；首轮 Windows 实验不建议走这条分叉。

## Windows / Python 3.11 兼容性：风险从“无 wheel”下调为“可验证候选”

本轮进一步核实 conda-forge / Anaconda 文件索引：

- Anaconda 文件列表明确列出 `win-64/pynini-2.1.7-py311h3fd045d_2.conda`，约 870.57 KB；同一批次也列出 Python 3.10、3.12、3.13 的 Windows 构建。该文件的确切 build string 与 Python ABI 3.11 对应。
- 该 Windows Pynini 构建还依赖 OpenFst 1.8.4、Graphviz、dlfcn-win32、UCRT 与 VC runtime；因此不能只下载单个 pynini 包就认为安装完成，Conda solver 需要一起解决其原生依赖。
- WeTextProcessing 的官方 Windows GitHub Actions 执行 `conda install -c conda-forge pynini` 后再安装 WeTextProcessing；其 CI matrix 示例使用 Python 3.9，不能单独证明 Python 3.11 的整条链。
- WeTextProcessing 1.3.0 在 PyPI 提供通用 `py3-none-any` wheel（约 1.8 MB），依赖元数据要求 Pynini >=2.1.6；与 M5 固定的 Pynini 2.1.7 版本约束兼容。
- PyPI 的 Pynini 2.1.7 CPython wheels 是 manylinux 平台包，不是 Windows wheel；Windows 应走 conda-forge。

**更新后的判断：** Windows x64 + Python 3.11 + conda-forge Pynini 2.1.7 是有明确 Windows CPython 3.11 二进制包证据的候选路径。但 Conda 求解、OpenFst 原生依赖、WeTextProcessing import、中文 TN 图构建与 OpenCC Yue 转换仍未在 Muse 主机验证。
## 依赖隔离建议（仍未执行）

后续经用户明确批准后，建议：

1. 在 D 盘创建单独 Conda 环境，优先 Python 3.11；Pynini 通过 conda-forge 精确安装 2.1.7，并让 solver 同步解决 OpenFst、Graphviz、UCRT/VC runtime 等依赖。
2. 再安装 WeTextProcessing 1.3.0、OpenCC 0.1.7，以及经核实的 CPU-only PyTorch 2.6.0 / TorchAudio 2.6.0 wheel。
3. 不安装上游默认 CUDA 12.4 Torch、GPU ONNX Runtime 或 FlashAttention；不把 M5 上游完整 requirements.txt 原样 pip install。
4. 固定 M5 源码 commit 与 Hugging Face 模型/codec revision；使用 `M5TTS.from_pretrained(device="cpu", attention="sdpa", local_files_only=True, revision=..., codec_revision=...)`。官方 API 明确支持 CPU、SDPA、缓存目录和 local-files-only；这使离线复现可行，但 SDPA 是显式替代路径，不等价于 NVIDIA FlashAttention 2。
5. CLI 的 `--local-files-only`、`--cache-dir`、`--revision`、`--codec-revision` 可用于本地缓存与版本锁定；首次下载完成后再断网运行 smoke test。
6. 先跑独立环境 smoke：import Pynini、WeTextProcessing、OpenCC；构建一次中文 Normalizer；测试 Yue 的 s2hk 转换；再执行最小音频 A/B。
7. 任何求解冲突或缺少 Windows 运行库时停止，保留日志，不升级 Muse 现有 Python/TTS 环境。
## Hugging Face 模型资产清单（revision 与权重校验值已核实）

本轮通过 Hugging Face 文件树和 Xet 文件详情页核实两个必需仓库；两个模型仓库均标注 Apache-2.0。

| 资产 | 固定 revision（完整 commit SHA） | 仓库页面总量 | 必需权重文件 | 权重 SHA256 |
|---|---|---:|---|---|
| `ASLP-lab/M5-TTS-50M-VoiceClone` | `22a225e506c7f107503103fd386092f76af5bc5b` | 约 318 MB | `model.safetensors`，约 300 MB | `6c1ac8578aafb07d444e284c76d16d6529e87e1971189c76ff1795bf1252d623` |
| `ASLP-lab/M5-TTS-Audio-Tokenizer` | `1466860350eac199280b6eaa8e90e55dc5cf051e` | 约 682 MB | `model.safetensors`，约 682 MB | `836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258` |

### VoiceClone 文件明细

- `model.safetensors`：300 MB；Xet hash `b0bcb5778ca5ddac70a9d0dcdce2d50604ff9f334cc39635b002b513e2ecc7bc`；SHA256 见上表。
- `tokenizer/` 目录：约 17.9 MB，其中 `tokenizer.json` 为 17.5 MB；Xet hash `0c38c1da9af74f33dc90658877ffc11f961a2c8e49d58fa3dca941c0cda2c833`；SHA256 `c8e344fb99b05f0a3f8a126a137d6a21a4af73470548c1a4031c89935e67713d`。
- 其余必要元数据：`config.json` 约 150 B、`conditioning.json` 约 895 B、`tokenizer_config.json` 约 369 KB、`special_tokens_map.json` 约 21.8 KB。
- M5 的 `inference.py` 下载过滤器明确包含 `model.safetensors`、`config.json`、`conditioning.json`、`tokenizer/*`；无需把 README 和仓库管理文件计入模型运行负载。

### Audio Tokenizer 文件明细

- `model.safetensors`：682 MB；Xet hash `2770552d9ac6119e00cc15f368f2e08a4ae4787b1f94234e639af510f8eb2ea0`；SHA256 见上表。
- 元数据：`config.json` 约 2.34 KB、`configuration.json` 约 76 B、`preprocessor_config.json` 约 234 B。
- M5 的 PyTorch 推理入口按 `*.json` 与 `*.safetensors` 过滤下载；codec 与 VoiceClone checkpoint 是两个独立仓库，不能只下载 300 MB 主模型就认为已具备推理条件。

### 容量结论

两个仓库的公开页面总量合计约 **1,000 MB（约 1 GB）**，这是模型与 codec 的原始资产量级，不是完整安装预算。D 盘最终预算仍需加上 Conda/Python 环境、PyTorch CPU wheels、其余依赖、下载临时空间、缓存和输出 WAV；Windows 缓存实际占用也需在目标环境中确认。不得把 1 GB 误报成全部部署所需空间。

首轮实验只需 VoiceClone + Audio Tokenizer；不下载 CustomVoice、ONNX 包或训练数据。模型权重下载后必须按上述 SHA256 校验，并将两个 HF revision 与 M5 源码 commit 一起写入实验 manifest。


## 本轮结论与状态

- M5 源码 commit 已固定为 `4e7cc91545ad5ecd368d7726129cefb41fc6e1d7`。
- Yue 文本规范化明确为中文 WFST TN + OpenCC 香港繁体转换；并非粤语专属 verbalizer。
- Pynini 在上游中文 TN 路径中是实际必需项；已找到 conda-forge Windows x64 / Python 3.11 / Pynini 2.1.7 精确构建文件及其原生依赖信息。
- M5 PyTorch API 明确提供 CPU + SDPA、固定模型/codec revision、cache_dir 和 local_files_only；不需要依赖 NVIDIA GPU 才能走这条显式实验路径。
- 兼容性仍属候选，未在 Muse 主机安装或运行验证。
- 项目生产代码改动：无；默认 `canto-tts-nano` 未变；B244 prosody contract 未接入生产链。
- 模型/依赖下载、安装、推理：均未执行。
- 本轮观察时 Runner 项目级 active Jobs=1，来自另一个 Workflow Session，Job 仍在运行；本 Session 未启动测试或命令。
## 来源与可复核链接
- M5-TTS 固定 commit：https://github.com/ASLP-lab/M5-TTS/commit/4e7cc91545ad5ecd368d7726129cefb41fc6e1d7
- M5-TTS text_normalization.py：https://github.com/ASLP-lab/M5-TTS/blob/4e7cc91545ad5ecd368d7726129cefb41fc6e1d7/text_normalization.py
- M5-TTS inference.py：https://github.com/ASLP-lab/M5-TTS/blob/4e7cc91545ad5ecd368d7726129cefb41fc6e1d7/inference.py
- WeTextProcessing Windows CI：https://github.com/wenet-e2e/WeTextProcessing/blob/master/.github/workflows/unittest.yml
- WeTextProcessing 中文 Normalizer：https://github.com/wenet-e2e/WeTextProcessing/blob/master/tn/chinese/normalizer.py
- WeTextProcessing 1.3.0：https://pypi.org/project/WeTextProcessing/1.3.0/
- conda-forge Pynini files：https://anaconda.org/channels/conda-forge/packages/pynini/files
- conda-forge Pynini 2.1.7 Windows Python 3.11 package list：https://anaconda.org/conda-forge/pynini/files
- Pynini 2.1.7 PyPI release files（无 CPython Windows wheel）：https://pypi.org/project/pynini/2.1.7/
- M5-TTS README（Yue、CPU/ONNX 与安装建议）：https://github.com/ASLP-lab/M5-TTS/blob/4e7cc91545ad5ecd368d7726129cefb41fc6e1d7/README.md
- VoiceClone Hugging Face revision：https://huggingface.co/ASLP-lab/M5-TTS-50M-VoiceClone/commit/22a225e506c7f107503103fd386092f76af5bc5b
- VoiceClone model weights：https://huggingface.co/ASLP-lab/M5-TTS-50M-VoiceClone/blob/main/model.safetensors
- VoiceClone tokenizer：https://huggingface.co/ASLP-lab/M5-TTS-50M-VoiceClone/blob/main/tokenizer/tokenizer.json
- Audio Tokenizer Hugging Face revision：https://huggingface.co/ASLP-lab/M5-TTS-Audio-Tokenizer/commit/1466860350eac199280b6eaa8e90e55dc5cf051e
- Audio Tokenizer weights：https://huggingface.co/ASLP-lab/M5-TTS-Audio-Tokenizer/blob/main/model.safetensors