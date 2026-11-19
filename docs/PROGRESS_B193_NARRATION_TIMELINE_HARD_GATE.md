# B193｜粤语旁白时长对齐硬门槛

日期：2026-11-19  
项目：Muse

## 问题证据

- 复核 B192 产品 `data/products/2552004058c5385868866d80883e8b31/4c8a850feaa986bc7163`。
- FFprobe 实测最终 MP4：5.083008 秒，512×288，12 FPS，包含音轨。
- 从产品 ZIP 独立提取旁白 WAV，FFprobe 实测：6.160 秒。
- 旁白比视频长约 1.077 秒。原有 FFmpeg `-shortest` 会以较短的视频流结束封装，因此 ZIP 内保留完整旁白源文件，但 MP4 内嵌音轨被截断。原技术 QA 检查了容器、音轨存在、完整解码和哈希，却没有检查旁白覆盖时长。

## 已实现

- `VideoProductBuilder.BUILDER_VERSION` 升至 13，避免旧版本产品缓存绕过新门槛。
- 新增 FFprobe 单流时长读取与 narration timeline 校验；同时探测原始 MP4 的 video stream duration 和旁白 audio stream duration。
- 旁白时长只要超过视频时长即在 FFmpeg mux 前失败关闭（默认容差 **0.00 秒**），要求先生成更多镜头或延长视频，避免任何旁白尾部被静默截断。
- 通过的时间线事实写入 QA report、storyboard 与 manifest，包含视频/旁白时长、容差和策略标识。
- 该门槛只阻止不完整交付，不会自动把 5 秒 Wan 镜头扩展成 6 秒以上；后续仍需分镜拆段、多镜头续生成或经过审核的动态补镜方案。

## 验证

- 修改文件 Python 3.11 `py_compile` 通过。
- 新增的时长不匹配拒绝、较短旁白允许、既有 TTS wrapper 三项定向测试：**3/3 passed**。
- 使用 B192 真实产品及其 ZIP 内旁白做端到端探测，实际返回：`narration (6.160s) exceeds generated video (5.083s); refusing to truncate voiceover`，符合预期；没有创建新产品。
- 首轮视频模块回归发现两个 OpenFilm 测试夹具使用虚拟 MP4、未提供 FFprobe 时长；为夹具补上明确的 5.0 秒视频/4.0 秒旁白测量后，视频产品 + 视频 QA + OpenFilm 专项 **68/68 passed**。
- B193 修改后全仓 `compileall -q src/muse tools tests` 通过；全仓 Python 3.11 unittest **1,565 tests in 156.992s，OK，9 skipped**。
## 下一步

1. 将 Wan 单镜头生成改成按旁白时长规划多镜头任务；逐镜头记录 prompt、时长、seed、生成状态和失败重试。
2. 将 Wan 单镜头生成改成按旁白时长规划多镜头任务；逐镜头记录 prompt、时长、seed、生成状态和失败重试。
3. 只有当最终 MP4 的视频时长覆盖完整旁白，且字幕时间轴/人工听审通过，才允许进入成片审核。
4. 保持产品 `testing` 与 `ready_to_publish=false`，直到版权、披露、编辑和平台/账号审核全部通过。
