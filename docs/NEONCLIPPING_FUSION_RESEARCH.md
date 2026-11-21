# NeonClipping → Muse：本地智能切片能力融合研究

日期：2026-11-21  
来源：<https://github.com/iamajmeena/neonclipping>  
许可证：上游仓库声明 MIT；本轮仅提取公开设计思路并独立实现 Muse 适配，不复制其源码。上游许可证仍须随未来任何代码 vendoring 一并核验与保留。

## 结论

NeonClipping 的高价值不只是“自动剪短视频”，而是把剪辑成本最高的第一步（全片找高光）从人工观看改成音频优先的低成本候选检索。其 Fast 路线先分析音轨、复用平台已有逐词字幕、只下载候选区间；Deep 路线再用光流和镜头切换补充视觉信号。随后才做语义重排、句界对齐、竖屏构图、字幕与创作者反馈学习。

上游 README 公开说明其启发式信号包括局部响度突变、2–4 kHz 声能、光流/剪辑密度与本地 LLM 文本评分；同时明确不保证爆款、LLM 不理解画面、CPU Whisper 可能慢、Deep 模式长视频会先下载全片。上游给出的 85 分钟 97 秒等数字来自单台 RTX 2060 机器，不能当作 Muse 的性能承诺。

## 与 Muse 现有能力的融合边界

Muse 已有 FFmpeg/FFprobe 工具发现、AMD AMF 编码探测、固定本地粤语 ASR（逐词时间戳与 SRT）、VideoProductBuilder、视频 QA、字幕人工审核、发布质量门禁、Creator Initiative/Attempt/Manifest 追溯。不要再造第二套视频产品账本、TaskQueue 或调度器。

| NeonClipping 玩法 | Muse 复用/新增方向 | 状态 |
|---|---|---|
| 音频优先高光检测 | 新增标准库音频 RMS 局部基线评分与候选区间选择 | 本轮已实现基础版 |
| 句界/逐词对齐 | 已调用现有本地粤语 ASR，候选排序直接消费逐词时间戳；输出片段级相对时间 SRT | 已接通粤语链路 |
| 9:16 竖屏渲染 | FFmpeg 竖屏裁切、片段级 SRT 烧录；CLI 成片后调用 Muse 全解码技术 QA | 已完成真实合成素材 smoke |
| 产品化与 Attempt 追溯 | 复用 VideoProductBuilder Manifest/ZIP/QA、Creator Initiative 与 InitiativeStore Video Attempt | 已接通并实测 |
| 字幕样式/逐词动画 | 复用 SRT 审核与 Remotion 字幕动画，扩展模板需后续完成 | 待融合 |
| 标题与 Hook | 接现有本地模型能力/Creator Brief，不把 Ollama 设为硬依赖 | 待融合 |
| 用户评分学习 | 复用反馈/演化账本，建立有样本量门槛、可回滚的权重更新 | 待融合 |
| 光流、人脸/Facecam | 复用可选 OpenCV；高光切片新增 Haar 人脸构图提示，保留中心裁切回退 | 部分接入；连续跟踪/Facecam 仍待融合 |
| 直播聊天峰值 | 作为独立直播来源适配器；须显式授权、遵守平台访问规则 | 后续阶段 |

## 本轮实现

新增 `muse.video_highlights`：
- FFprobe 验证本地输入含有效音轨；FFmpeg 只解码音频并用 astats 产生逐秒 RMS 指标，不下载/解码整条视频画面。
- 使用最多 120 秒尾随局部基线的中位数/MAD 计算响度突变和安静转响评分；可接入外部归一化 shout 特征，但默认无额外依赖。
- 选取不重叠的候选区间；如果提供有效逐词时间戳，可在容差内对齐起点与句末标点。
- 生成 1080×1920 FFmpeg 渲染命令；可选烧录 SRT，优先 AMD AMF H.264，未检测到时回退 libx264；禁止覆盖已有输出。
- 提供 `muse-video-clips rank`、`rank-cantonese`、`render` 与 `register` 四个本地 CLI 阶段。`rank-cantonese` 复用 Muse 固定本地粤语 ASR；`render --captions-from-analysis` 生成片段相对时间 SRT 并烧录；`register` 把人工选定成片写入既有产品库与 Attempt 账本。render/register 均强制执行 Muse 技术 QA；仍需用户选择候选索引。

## 第二阶段：粤语逐词字幕与既有 QA 接通

- `analyze_video_with_cantonese_asr` 先以 FFmpeg 提取本地 16 kHz 单声道 WAV，再调用 `muse.cantonese_asr.transcribe_cantonese`；复用已固定/校验的模型，不下载权重、不调用云 API。
- 候选排序直接消费 ASR 的真实 word timestamps，并将候选逐词文本写入分析 JSON；片段 SRT 将原片绝对时间换算为片段相对时间，严格裁去边界外词，不估算缺失时间。
- `render --captions-from-analysis` 可由候选 JSON 生成独立 SRT；烧录成 1080×1920 MP4 后调用 Muse `inspect_video_file`，校验容器、尺寸、帧率、音轨、完整解码与音频指标。QA 不通过即删除 MP4，不将其作为合格成片保留。
- 本轮 psmux 验证 **11/11 tests passed**；真实 FFmpeg smoke 完成音频分析、字幕烧录、竖屏渲染及 Muse full-decode QA。Runner 实际检测并使用 `h264_amf`，AMF 成片 **4,164,091 bytes**，1080×1920、5 秒，QA `passed`。这是该工作站本次 smoke 的实测，不代表所有素材/驱动都必然成功。
- B237 完成本地切片分析与渲染/技术 QA；产品库、Creator Initiative 与 Attempt 接线已在 B241 完成，产品仍保持 testing 并等待人工审核。

## 第三阶段：正式视频产品与 Attempt 追溯接通
- 新增 `muse.clip_product_adapter.register_highlight_clip`，不另建产品目录/数据库；复用 `VideoProductBuilder` 的版本目录、Manifest、SHA-256、ZIP 完整性校验、Creator Initiative 事件与既有 `InitiativeStore`。
- CLI 增加 `muse-video-clips register SOURCE CLIP ANALYSIS_JSON TITLE`；默认发现与成片同名的 SRT sidecar，也可显式指定 `--captions`。可传 `--initiative-id` / `--evolution-cycle-id` 绑定既有创作计划。
- 产品包保存 `video.mp4`、`clip-captions.srt`（如有）、`storyboard.json`、`qa-report.json`、`README.md` 和 `product.zip`。Manifest 保存源视频 SHA-256/大小/文件名、候选起止秒、评分、分析模式、ASR 引擎、转写文本与字幕哈希。
- 注册前再次 FFprobe 源视频时长，拒绝越界片段；打包时复核源视频/片段/SRT 哈希；调用 VideoProductBuilder 的全解码 QA，核对 9:16、30 fps、音轨与成片时长。产品版本由输入哈希与剪辑元数据确定，重复登记复用同版本。
- 复用现有 Creator Initiative 记录产品事件；再以确定性 `clip-...` ID 在现有 `initiative_video_attempts` 中创建/完成 `local_assets` Attempt。重复运行复用已完成 Attempt，不产生平行账本。
- 产品状态固定为 `testing`，`ready_to_publish=false`；不触碰发布权限。通过技术 QA 不等于内容、字幕、版权、肖像或平台审核。
- psmux 专项测试 **17/17 passed**；真实 FFmpeg smoke 实际使用 `h264_amf`，渲染 1080×1920 / 5 秒 MP4，随后真实运行 register CLI，成功生成并由 `VideoProductBuilder.verify_path` 复核产品包，确认 SRT sidecar 入 ZIP，并在现有 InitiativeStore 中读回唯一 `succeeded` Attempt。临时工作区自动清理。


## B246：可选 2–4 kHz 频带辅助评分
- `rank` 与 `rank-cantonese` 新增 `--spectral-2-4khz`；默认关闭，保持旧版评分权重与执行成本。
- 显式启用时额外运行一次 FFmpeg 音频-only 带通分析（2 kHz high-pass + 4 kHz low-pass），按逐秒 RMS 计算频带能量，再用独立尾随局部中位数/MAD 得到 0..1 的 prominence。
- 频带信号只占总评分 15%，保留原始 dB 与 prominence 供人工解释；不将其解释为情绪、语义、说话人识别或爆款预测。
- 该分支不解码视频画面、不增加第三方 Python 依赖、不调用云 API。专项 19/19 测试与真实合成 3 kHz FFmpeg 带通 smoke 均已通过；真实 Wan 2.1 粤语旁白样片上的 `rank-cantonese --spectral-2-4khz` CLI 也已跑通：20 个词级时间戳、1 个候选及频带指标。
- `rank-cantonese` 需要 Python 3.14，并将项目隔离依赖目录加入 `PYTHONPATH`；不会自动下载模型权重。CPU ASR 速度取决于素材时长与硬件。
- Windows PowerShell 示例：
```powershell
$env:PYTHONPATH = "src;.muse-canto-tts-py314"
py -3.14 -m muse.video_highlights rank-cantonese "D:\素材\原视频.mp4" ".\analysis.json" --model-dir "models\whisper-small-cantonese" --device cpu --compute-type int8 --spectral-2-4khz
```
- 默认仍采用确定性中心裁切。显式 `render --face-aware-crop` 时，使用 OpenCV 自带 Haar frontal-face cascade 对候选区间最多采样 30 帧，取每帧最大人脸中心的中位数，计算单个稳定横向裁切位置；不下载额外权重、不识别身份、不做连续跟踪。没有正脸、OpenCV/cascade 缺失或检测不可用时自动回退中心裁切。多人、侧脸、遮挡与镜头内主体切换仍需人工检查；后续再评估真正的主体跟踪和模糊背景布局。
- “两分钟”只作为上游特定硬件/来源的实测宣传，不对 Muse 或用户硬件作承诺。
- 任何来源 URL 下载需独立实现域名/协议白名单、yt-dlp 参数固定、路径隔离、大小/时长限制、版权与平台条款提示；直播聊天采集需单独授权与安全评估。

## B256：可选本地人脸构图提示
- 新增 `muse.video_face_framing.detect_clip_face_center`：仅在用户显式传入 `render --face-aware-crop` 时启用；复用 `video-diagnostics` 可选依赖中的 OpenCV 与其自带 frontal-face cascade，不下载模型、不调用云 API。
- 在候选片段内均匀抽样 3–30 帧（默认上限 12），逐帧选择最大正脸框，取横向中心中位数作为整条片段的固定裁切中心；FFmpeg 使用有边界约束的 crop 表达式。默认评分与默认中心裁切路径保持不变。
- `no_face`、依赖缺失、cascade 缺失或时间信息不可用均不猜测坐标，继续中心裁切；输出记录 detector、采样覆盖、center spread，并明确 `identity_recognition=false`、`continuous_tracking=false`。
- 这是弱正脸构图提示，不是人物身份识别、多人跟踪或语义主体识别；镜头内人物切换、侧脸、遮挡和边缘主体仍需人工审片。真实人像素材上的准确率/覆盖率 A/B 尚未验收。
- 新增 adapter 复用既有 VideoProductBuilder、Creator Initiative 与 Attempt 账本，不改变 Commander、TaskQueue 或发布权限；产品状态保持 testing，技术 QA 通过仍不代表内容、字幕、权利或平台审批通过。

## 动态构图增量：稀疏人脸关键帧裁切
- 在 `--dynamic-face-crop` 显式启用时，使用通过覆盖率门槛的抽样 Haar 正脸位置生成片段相对时间关键帧，再由 FFmpeg crop 时间表达式逐帧线性插值；旧 `--face-aware-crop` 固定中心模式与默认中心裁切不变。
- 真实 FFmpeg 试验确认 crop 滤镜默认逐帧求值，不支持 `eval=frame` 参数；现用引号包围含逗号的表达式，并已通过真实 1080×1920 full-decode QA。
- 该功能是稀疏独立检测点的插值，不是光流或连续身份跟踪；尚未在人像真实素材上验证主体跟随准确率/观感。快速移动、遮挡、侧脸与多人切换必须人工审片。
- `rank`、`rank-cantonese` 与 `render` 默认分析/裁切行为保持不变；动态模式仅在 `render --dynamic-face-crop` 显式开启时执行。无脸或低置信时真实 CLI smoke 已确认回退中心裁切并通过技术 QA。

## B266：短时漏检的稀疏 Lucas–Kanade 光流桥接
- 在 Haar 检测锚点之后，若下一采样帧短暂漏检且间隔不超过 1.5 秒，尝试在上一人脸框内提取角点，使用 OpenCV `calcOpticalFlowPyrLK` 做前向/反向跟踪。
- 只有至少 4 个有效点、前后向回环误差不超过 1.5px、有效点比例达到 35%，且位移未超过画面宽度 12% 或人脸宽度 2 倍的较大值，才插入一个 `lucas_kanade` 关键帧；否则忽略光流结果。
- 该桥接不改变 Haar 检测覆盖率门槛，不延长追踪锚点，且只处理不超过 1.5 秒的间隔；没有独立镜头切换检测器，切镜后的不一致通常会被回环/位移门槛拒绝，但不能保证零误跟踪。OpenCV/NumPy 或 LK 不可用时安全回退。`continuous_tracking=false` 保持不变，不能用于身份判断或自动发布。

## B267：动态裁切轨迹限速与平滑
- 新增 `stabilize_face_keyframes()`，保留关键帧时间戳与来源标签；默认把横向中心变化限制在每秒 0.9 个归一化画面宽度，并用 0.75 的平滑系数减轻相邻采样抖动。
- 同时保留原始 `face_keyframes` 与独立的 `stabilized_face_keyframes`，输出限速次数、原始/稳定后最大速度等诊断；`render --dynamic-face-crop` 使用稳定后的关键帧，缺少该字段时兼容回退原始关键帧。低层 FFmpeg builder 的显式关键帧输入不被隐式改写。
- 限速会带来一定跟随滞后，不能解决人物切换、遮挡、镜头切换或错误 Haar 检测；真实人像素材仍需通过 Contact Sheet 人工审片。
