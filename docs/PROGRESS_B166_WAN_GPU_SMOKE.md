# B166｜Wan 2.1 GPU smoke test repeat
日期：2026-11-19

## 目的

在 B160/B162 已有实机验证基础上，独立验证已归档的原生 ComfyUI Wan 2.1 T2V workflow 能否从 Muse 项目文件复现，并把输出复制到项目 staging 区做完整解码检查。本轮不接触任何正式 MP4/ZIP，不替换 B149/B160 生产候选。

## 工作流与执行

- Workflow：`workflows/wan21_t2v_smoke.json`，11 个原生节点：UNETLoader、CLIPLoader、VAELoader、ModelSamplingSD3、CLIPTextEncode ×2、WanImageToVideo、KSampler、VAEDecode、CreateVideo、SaveVideo。
- 模型：Wan 2.1 T2V 1.3B FP16、Wan 2.1 VAE、UMT5 XXL FP8；ComfyUI 0.23.0，设备 AMD Radeon RX 6750 GRE 12GB。
- 正式测试配置：256×256、17 帧、16 FPS、6 steps、CFG 5、Euler/simple、seed 20261119；输出前缀 `muse_staging/wan21_smoke_20261119_gpu`。
- 初版将 CLIPLoader 强制设为 CPU，文本编码耗时过长（约 505.85 秒）后中断；没有把该中断任务当作成功。改为 ComfyUI 默认设备/offload 策略后，CLIP 文本编码、Wan 采样、VAE 解码与 SaveVideo 全部完成，ComfyUI 报告 Prompt executed in 22.97 seconds，history 状态 success。

## 输出与验证

- MP4：`artifacts/wan_staging/wan21_smoke_20261119_gpu_00001_.mp4`
- SHA-256：`91004ac8c592210e9ee9647497274bd6d4766bdcbf1fa8d00dfcc3a9b4c94caa`
- 文件大小：29,557 bytes；H.264；256×256；17 帧；16 FPS；时长 1.0625 秒。
- FFmpeg 完整解码退出码 0；`freezedetect=n=-60dB:d=0.25` 无 freeze_start/freeze_duration 事件。
- 相邻帧差分统计：16 对帧，亮度差 YAVG 均值 7.0177，最小 0.23143，最大 34.8397。联系表显示主体构图变化有限；差分存在不代表运动质量达标。
- 联系表：`artifacts/wan_staging/wan21_smoke_contact.png`。该短片只证明 GPU 推理与 MP4 交付链路可运行，不证明生产级动态质量；不通过生产素材验收。

## 结论与下一步

- <b>通过</b>：Muse 项目归档 workflow → ComfyUI GPU/offload 推理 → Wan VAE 解码 → H.264 MP4 保存 → staging 取回 → FFmpeg 完整解码。
- <b>未通过/未验证</b>：生产分辨率、镜头运动强度、主体运动连贯性、长片稳定性、粤语旁白/字幕混音与完整交付 ZIP。
- 下一步应在资源允许时做受控 prompt/seed/steps/分辨率对照，配合人工标注与光流指标；在运动质量达到既有 QA 门槛前，不接入宣传页作为正式成片，不替换现有生产镜头。
