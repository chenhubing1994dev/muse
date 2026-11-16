# Muse 视频创作超融合研究与实施方案

最后核对：2026-11-06

## 结论

采用分层融合，不把单一上游项目整套复制进 Muse：

1. Muse Commander：导演与唯一调度中心。负责需求理解、故事/脚本、分镜 JSON、资源预算、任务 DAG、审批、重试/取消、质量验收和最终报告。
2. ComfyUI：本地生成引擎。负责图像、图生视频、文生视频及可复用 workflow；Muse 只调用已登记、已验证的 loopback endpoint 和 workflow，不自动安装未知 custom nodes / 模型。
3. Velorn：优先采用的 AI 视频工作台/时间线执行层。其本地 MCP 服务公开项目、时间线、素材、ComfyUI 连接/生成状态、帧检查、编辑、字幕、导出等工具；默认 endpoint 为 http://127.0.0.1:19790/mcp，ComfyUI 默认 http://127.0.0.1:8188。编辑/导出可以不依赖 ComfyUI，但生成镜头需要本机 ComfyUI。上游文档说明重型视频 workflow 可能需要 24GB+ VRAM，因此本机 12GB 显存必须按模型/分辨率/帧数实测，不能承诺所有 workflow 可运行。
4. FFmpeg：确定性合成与交付层。Muse 已检测到 FFmpeg 8.1.1、FFprobe 及 AMD AMF H.264/HEVC/AV1 encoder。用于场景归一化、拼接、音轨、格式、硬件编码和最终媒体校验。
5. OpenChatCut：备用编辑器研究对象。它有完整多轨时间线、字幕/转录、AI 生成任务、MCP 和 Remotion/FFmpeg 导出；但采用 AGPL-3.0，且部分生成依赖配置的模型/provider 凭证。适合作为第二编辑器候选，不与 Velorn 同时成为主时间线，避免双编辑核心。
6. FunUI：ComfyUI 内的快速实验工作台。有场景时间线、Composer、Pipeline cards 和 Auto Montage；但上游自述为单人维护 hobby project，GPL-3.0，模型支持状态需逐 workflow 验证。定位为生成实验/备用方案，不作为 Muse 的唯一交付核心。
7. Abekyo Editor：可选的程序化模板渲染层。JSON timeline + headless Remotion render 很适合批量模板视频，但不是本地 AI 镜头生成器；可在后续营销短片/字幕模板需求明确时再评估。

## 选型比较

| 项目 | 最适合的角色 | 优势 | 主要限制 | 决策 |
|---|---|---|---|---|
| Velorn | ComfyUI 原生生成 + 时间线 + Agent MCP | 与 ComfyUI 直接协作；本机 MCP；编辑/字幕/导出闭环 | 当前生成依赖单独运行的 ComfyUI；重型 workflow 对显存要求高；GPL-3.0 | 主接入目标 |
| OpenChatCut | 多轨 Agent 编辑器 | 成熟编辑命令、字幕/转录、素材、MCP、导出 | AGPL-3.0；较新；生成 provider 需逐项配置，默认不等于全本地 | 备用/比较 |
| FunUI | ComfyUI 场景创作 UI | ComfyUI workflow 原生；Composer/Auto Montage | 单人 hobby 项目；GPL-3.0；模型/模块支持有实验性 | 可选插件 |
| Abekyo Editor | JSON 时间线模板渲染 | 可由 Agent 直接生成 JSON 并导出 MP4 | 不负责 AI 镜头生成；Remotion 许可需按用途核对 | 后续可选 |

## 已有本机证据

- Muse 的只读工具盘点实际发现 FFmpeg 8.1.1、FFprobe、AMD h264_amf、hevc_amf、av1_amf。
- Node.js/npm 已在 PATH。
- 本次探测 http://127.0.0.1:8188/system_stats 超时；ComfyUI 当前未确认运行。
- 常见 ComfyUI 安装路径及 Muse reference-projects 中 Velorn/OpenChatCut 目录未确认存在。
- 因此当前能立即提供的是本地素材自动合成底座；尚不能宣称本机已经具备 AI 视频镜头生成服务。

## AMD GPU compatibility and model selection

本机显卡为 RX 6750 GRE 12GB，不能把“ComfyUI 官方支持 AMD Windows”直接等同于“这张卡已获官方完整支持”：

- ComfyUI 从 Windows Desktop v0.7.0 起加入 AMD ROCm 支持，当前相关发行说明基于 ROCm 7.1.1；但 AMD Windows GPU 支持表列出 RX 6750 XT（同属 gfx1031 级别）为 Runtime supported、HIP SDK unsupported，RX 6750 GRE 本身也没有单独列名。因此本机必须先做 PyTorch/ROCm device smoke，不能先下载大型模型或默认 GPU 一定可用。
- 有社区用户报告 RX 6750 GRE 12GB 在 Windows 上用自定义 ROCm/PyTorch 组合跑 ComfyUI，但这属于非官方经验，不应当作稳定支持承诺。只有在用户批准后才考虑隔离环境试验，不把第三方安装脚本直接接入 Muse。
- 首轮模型候选：Wan 2.1 T2V-1.3B 480p。ComfyUI 官方公告给出约 8.19GB VRAM 的参考值，但另一个 ComfyUI 介绍给出约 15GB 的数字，说明显存占用高度依赖 offload、精度和 workflow；12GB 只能作为短片、低分辨率、batch=1、CPU offload 的实测候选，不能保证成功。
- LTX-Video 2B distilled 亦是低显存候选，但 ComfyUI 官方模板资料标注约 12GB minimum / 16GB recommended；对 12GB 显卡余量不足，暂不列为首选。
- 首轮验收目标应是 480p、2–3 秒、单镜头、batch=1；GPU 不可用时应停止并报告 blocker，不静默切到云端付费 API。

参考：
- AMD Windows GPU compatibility: https://rocm.docs.amd.com/projects/radeon-ryzen/en/docs-7.1.1/docs/shared/hipsdk/reference/system-requirements.html
- ComfyUI Windows AMD ROCm announcement: https://blog.comfy.org/p/official-amd-rocm-support-arrives
- RX 6750 GRE community setup report (unofficial): https://www.modelscope.cn/posts/437401
- Wan 2.1 ComfyUI support: https://blog.comfy.org/p/wan21-video-model-native-support
- ComfyUI LTX-Video model requirements: https://github.com/Comfy-Org/workflow_templates/blob/main/site/knowledge/models/ltx-video.md

## Muse 一键影片流水线

一句话目标
→ Muse Director / 本机模型生成结构化脚本与分镜
→ Storyboard Validator（镜头数量、时长、画幅、角色/风格一致性）
→ Resource Governor（GPU/VRAM/RAM 并发预算）
→ ComfyUI workflow adapter（生成镜头/图片/音频；缺失模型即阻断）
→ Asset Registry（来源、prompt、workflow、seed、hash、许可）
→ Velorn MCP（项目、素材、时间线、字幕、效果、预览、导出）
→ FFmpeg/FFprobe（编码、混音、时长/分辨率/音轨校验）
→ Review Gate（画面连续性、音画、黑帧、缺失素材、字幕）
→ MP4 + 可编辑项目 + 生产报告

Commander 是唯一调度中心。Velorn/ComfyUI 是被调用的能力，不得再起第二套自动 Agent 调度器。所有外部 MCP 写操作先 preview/validate；模型下载、custom node 安装、云端 Provider、发布/上传仍须逐项授权。

## 当前已落地的第一块

新增 muse-video make INPUT_DIR OUTPUT.mp4：自动按自然文件名排序，混合图片与视频素材，逐镜头统一画幅/FPS/像素格式，拼接成 MP4；可选加入循环 BGM 或旁白轨，最终由 FFprobe 验证视频流、尺寸和时长。auto 模式在检测到 AMD AMF H.264 时优先使用，也可显式选择 libx264 软件编码。源素材只读、不覆盖既有输出。它是素材自动剪辑/合成，不是 AI 镜头生成器。

## 下一步实施顺序
1. **Velorn MCP 实机连接已完成第一步**：v0.3.37 endpoint 127.0.0.1:19790/mcp 实测 130 工具；get_project 调用成功。Muse 用 14 项精确文档只读白名单，未知工具审批门控；桌面应用必须保持打开且 MCP Running。
2. **ComfyUI portable 已下载、校验并解压**：v0.23.0 AMD archive 1,703,012,797 bytes，SHA-256 71768329ad196b7eff81426843bb47ff04f4fe838ccdfa5bd59a3c455dd8f0fb；embedded Python 3.12.10。默认 bundled Torch 2.9.1+rocm7.2.1 在 RX 6750 GRE 上未发现设备；Rinsei gfx103X ROCm 7.14 pinned wheels 已下载，隔离安装正在执行。
3. **Muse ComfyUI API 安全门已补齐**：object_info 摘要、history、queue_status；submit_workflow 需要显式 approval、非空节点白名单、先核验 /object_info 已安装节点、图大小/节点数校验与 prompt_id 校验。尚未向真实 ComfyUI 提交任何 workflow。
4. **分镜 DAG 已复用既有 workflow 模块**：scene 可声明 depends_on；自动拒绝未知依赖/循环依赖，生成确定性并行 wave，传入 CommanderPlan。下一步接入 TaskQueue，持久化每个 shot 状态、产物、重试与 checkpoint。
5. **交付质量门已加强**：FFprobe 校验画幅、FPS、时长和请求的音轨；后续增加黑帧/静音/字幕时长与人工抽帧 Review。
6. GPU device smoke 通过后先做小图生成；再按 Rinsei 适配节点安装 GGUF/Wan22 workflow；模型候选采用官方 Wan 2.2 I2V 基座的量化版本，需核验具体文件与许可证。
7. 一键入口：muse-video create --prompt ...；若 GPU/模型服务缺失，清楚报告 blocker；若只提供素材则走现有 FFmpeg 合成模式。

**当前实测边界（2026-11-06）**：Velorn MCP 可用（应用运行时）；ComfyUI portable 已解压但 8188 尚未启动；RX 6750 GRE bundled Torch device_count=0，ROCm 7.14 替换尚未验收；video engine 22/22、video workflow 12/12、video factory 8/8、全量 662/662。不能声称 AI 镜头生成已可用。

### Rinsei workflow 适配注意
- 已生成本机 D 盘 launcher，不能原样使用上游写死 C:\\comfyui-rinsei 的脚本。
- RDNA2 SVI workflow 有 257 个顶层节点、13 个子图；fp8 CLIPLoader 需换 CLIPLoaderGGUF，SageAttention patch 节点需禁用。依赖包括 ComfyUI-GGUF、KJNodes、VideoHelperSuite、Wan22FMLF、Frame Interpolation/RIFE、rgthree；先用 /object_info 核验节点，不执行自动安装器。
## 许可与边界

- Velorn：上游仓库标注 GPL-3.0；优先以独立本地 MCP 客户端调用，不复制其源码。分发整合版前须审查 GPL 义务及所有依赖许可。
- OpenChatCut：AGPL-3.0；若采用其代码或网络服务形态，需特别审查 AGPL 义务。
- FunUI：GPL-3.0。
- 不把许可证标记为 permissive；不复制上游源码进入 Muse，除非完成逐文件许可证审查。
- 所有模型权重、ComfyUI custom nodes、字体、音乐、素材各自有许可证；上游应用开源不代表模型/素材可任意商用。
