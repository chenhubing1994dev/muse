# B159｜本地视觉模型能力审计与内容感知运镜验收门槛

日期：2026-11-18

## 本轮目标

承接 B158 的低层 OpenCV 视觉启发式，确认 Muse 当前机器是否已有可调用的语义视觉模型；在没有可靠模型证据前，不把显著性估计称为人物/物体识别。

## 实机检查结果

### 1. Runner 与现有服务

- WebCodex Runner 在线，版本/协议兼容，构建与服务端对齐。
- Muse 项目当前有 1 个已知 detached Dashboard 服务 Job 仍在运行；本轮只观察，没有停止或重启。
- Dashboard 服务日志包含客户端断开连接导致的 `ConnectionAbortedError [WinError 10053]`，但服务仍报告 ready。此异常是连接写回失败，不能据此断言 Dashboard 主服务崩溃。

### 2. LM Studio 本机模型清单

通过本机 `http://127.0.0.1:1234/api/v0/models` 查询到：

- `gemma-4-e4b-it-claude-opus-4.5-heretic-uncensored-thinking`：架构 `gemma4`、Q4_K_M、状态 `not-loaded`，接口公布的 capabilities 只有 `tool_use`。
- `deep`：Qwen 3.5，Q6_K，`not-loaded`，公布 `tool_use`。
- `eva-abliterated-ties-qwen2.5-14b-i1`：Qwen2，Q3_K_S，`not-loaded`，公布 `tool_use`。
- `voxcpm2`：MiniCPM4，`not-loaded`；未公布图像能力。
- `text-embedding-nomic-embed-text-v1.5`：embedding 模型，`not-loaded`。

`/v1/models` 也能看到 Gemma 4 等模型登记，但这不代表模型已加载或具备视觉输入。当前 API 元数据没有给出可验证的图像输入 capability；因此本轮不尝试将它接入视觉链路，也不发送图像请求来猜测能力。

### 3. 本地模型文件检查

- 在已检查的 `D:\Models`、`D:\AI` 和 Muse 项目模型目录中发现了多个 GGUF、Wan 相关模型与 ONNX 语音组件。
- 未发现可确认已安装的 YOLO/RT-DETR/SAM 等目标检测/分割权重。
- OpenCV/NumPy 的实际使用已在 B158 测试中验证；此前没有 OpenCV Saliency 模块。
- 本轮没有安装依赖、下载模型或更改用户模型配置。

## 技术决策

1. B158 `opencv_visual_heuristic_v1` 继续作为可选低层启发式，不得标注为 semantic recognition。
2. 在没有可验证视觉模型与真实素材评估前，内容感知运镜必须保留 fail-closed：低置信度时不输出主体结论，并回退到确定性运镜。
3. 不因 Gemma 4 的模型名称或 `gemma4` 架构推断其 LM Studio GGUF 构建支持视觉输入；以 API 明示 capability 和实际图像输入验证为准。
4. 不在本轮下载新模型。后续若要引入本地检测器，先比较模型许可证、显存占用、AMD/DirectML 或 ONNX Runtime 可运行性、速度与真实素材精度，再决定是否安装。

## B160 验收计划

### A. 建立人工标注基准

对现有六段素材逐段抽取固定时间点帧，由人工标注：

- 主体是否明确可见（present / absent / ambiguous）。
- 主体归一化边界框 `x_min, y_min, x_max, y_max`（仅 present）。
- 画面主体类型只作粗分类（person / object / landscape / text_or_graphic / other），不推断身份。
- 镜头运动等级（static / low / medium / high），并记录标注者与时间点。
- ambiguous 样本不参与准确率计算，单独统计覆盖率。

### B. 对照指标

- 边界框 IoU 与中心点归一化误差。
- 主体存在性 precision / recall（仅在有足够人工标注后计算）。
- 可用覆盖率与低置信度回退率。
- 不允许只报告成功样本的准确率而隐藏回退样本。
- 每段视频按时间点分层统计，避免单帧结果代表整段。

### C. 运镜启用门槛

- 只有人工标注集上的精度与覆盖率同时达到预先设定门槛，才允许自动内容感知运镜。
- `ambiguous`、无主体、解码失败或模型不可用一律回退，不猜测主体。
- 任何新模型必须先在现有六段素材上做离线对照；不得直接进入默认生产路径。

## 本轮结论

B159 完成了本地视觉模型能力审计，当前未找到可直接验证的语义视觉推理能力。Gemma 4 已登记但未加载，LM Studio 公布的 capability 只有 `tool_use`。本轮没有代码改动、模型安装或素材推理；这是一次基于本机 API 的能力核验与技术决策，避免在证据不足时错误宣称视觉识别已打通。

下一步优先完成六段素材的人工标注基准与评估器，再评估可用的本地视觉模型/检测器。