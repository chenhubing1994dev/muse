# B-128 Muse 视频生产线：真实 Wan 生成与 AMD VAE 解码稳定性

日期：2026-11-14  
项目：`D:\AI AGENT\Muse`

## 本轮目标

验证从 Muse 提示词入口，经 ComfyUI + Wan 2.1 实际 GPU 推理、Remotion 动效、FFmpeg、技术质检，到 MP4 与工程 ZIP 的真实交付链路。测试素材与 Mock 不能作为实机完成证据。

## 环境实测

- ComfyUI Portable：`D:\AI\ComfyUI-Rinsei\portable\ComfyUI_windows_portable`
- ComfyUI 版本：0.23.0；PyTorch 2.10.0+rocm7.14.0a20260603；ROCm 7.14；AMD arch gfx1031。
- GPU：AMD Radeon RX 6750 GRE 12GB；ComfyUI 报告 VRAM 总量 12,868,124,672 bytes。
- Wan 2.1 模型文件均已找到：
  - `wan2.1_t2v_1.3B_fp16.safetensors`（2,838,303,560 bytes）
  - `umt5_xxl_fp8_e4m3fn_scaled.safetensors`（6,735,906,897 bytes）
  - `wan_2.1_vae.safetensors`（253,815,318 bytes）
- ComfyUI API 实测 ready；Wan 工作流需要的 10 种节点均存在；FFmpeg / FFprobe 已检测到，AMD AMF 编码器可用。
- ComfyUI 只绑定 `127.0.0.1:8188`。Muse 没有终止 LM Studio 等外部程序。

## 真实推理结果

- 首次实机任务：竖屏 9:16、3 秒、8 steps、Wan 2.1 T2V 1.3B。
- ComfyUI 队列确实进入 running；GPU 可用显存从约 12.72GB 降至约 5.40GB，证明任务已实际占用 GPU。
- ComfyUI history 显示文本编码、KSampler 等节点已执行；失败发生在 `VAEDecode`，报错为 `torch.AcceleratorError: HIP error: unspecified launch failure`。
- 该次没有产生合格 MP4；不能标记 Wan 实机生成完成。
- 错误后 ComfyUI 的 `/system_stats` 返回 HTTP 500。确认队列 running=0、pending=0 后，只停止了本轮由 Muse 启动的 ComfyUI PID，并重新启动服务。

## 本轮代码修复

1. `src/muse/wan_video.py`
   - 修复 GPU 租约失败时遗漏导入 `GpuResourceBusy`、导致原始资源错误被 NameError 覆盖的问题。
   - 增加真实显存预检：必须有有效 VRAM 统计，且系统与可用的 PyTorch 显存读数均达到 70% 空闲阈值，否则不提交工作流。
   - 将 Wan 工作流解码节点改为 ComfyUI 内置 `VAEDecodeTiled`：tile_size=256、overlap=64、temporal_size=8、temporal_overlap=4。
2. `src/muse/gpu_resources.py`
   - 当监督器刚刚报告 `external_model_active`、但 Muse 配置的模型端点已经 unavailable 时，允许等待监督器针对当前视频租约作出确认；仍不停止外部进程，且仍要求租约确认。
3. `src/muse/video_product_builder.py`
   - 将 `vae_decode_mode=tiled_temporal_8` 纳入产品设置指纹，避免不同解码策略共用同一产品版本。
4. 专项测试增加了租约错误映射、旧监督器状态确认、显存不足拦截和 tiled VAE 工作流断言。

## 验证

- Wan 视频、GPU 资源协调、视频产品封装专项测试：**41/41 passed**。
- ComfyUI 重启时采用 `--enable-dynamic-vram --cpu-vae`，将 VAE 解码切换到 CPU，Wan 采样仍使用 AMD GPU；服务 API 已再次 ready。
- ComfyUI 的 `VAEDecodeTiled` 节点已通过 API 实际发现。
- **尚未完成第二次真实 Wan 生成**：CPU VAE + tiled decode 是针对已观察到的 HIP VAE 解码故障所做的修复方向，必须再次真实生成并检查 MP4 后才能判定有效。
- 先前 Remotion 集成烟测确实使用真实 Remotion 与 FFmpeg，但其中 Wan 调用是 Mock；这不等于本轮真实 Wan 成片已经通过。

## 当前状态与下一步

**状态：实机生成未验收**

1. 在当前 ComfyUI（CPU VAE + tiled decode）实例上重新提交同级别短片，确认 KSampler 与 VAE 解码都成功。
2. 对真实 MP4 执行 FFprobe、全片解码、黑帧/冻结/静音等技术质检。
3. 通过后再执行 Remotion 品牌片头片尾、FFmpeg 最终编码，并验证 MP4、`product.zip`、manifest、QA 报告和 SHA-256。
4. 单镜头链路稳定后，扩展为多镜头高价值视频：选题研究、受众与平台定位、脚本、分镜、镜头生成、旁白/字幕/音乐、节奏与转场、平台画幅版本、发布复盘。
5. 不虚报播放量、收益、平台通过率或商业价值；技术质检与内容/版权/平台审核分开记录。
