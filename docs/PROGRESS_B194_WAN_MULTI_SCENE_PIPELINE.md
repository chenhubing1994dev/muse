# B194｜Wan 多镜头顺序生成、拼接与源镜头归档

日期：2026-11-19  
项目：Muse

## 已实现

- `VideoProductBuilder.BUILDER_VERSION` 升至 **14**。
- 新增 `build_from_scenes(title, scenes, ...)`：支持 2–20 个独立镜头，每镜 1–5 秒，预览总计划时长不超过 60 秒；每镜可指定 Seed，缺省时生成独立随机 Seed。
- 镜头逐个调用本机 ComfyUI + Wan 2.1 T2V 1.3B，复用既有 GPU Resource Lease / 显存安全门槛，不并发争抢 12GB 显存。
- 每镜必须有 completed 状态、MP4 文件、FFprobe 实测时长及完整尺寸/FPS/帧数元数据；镜头尺寸或 FPS 不一致时拒绝 stream-copy 拼接。
- 使用 FFmpeg concat demuxer 顺序拼接，最终再次 FFprobe；成片时长必须与各镜头实测时长总和吻合（最大容差为 0.25 秒或一帧）。
- 任一镜头失败、缺少媒体工具、元数据不完整或拼接失败，整个任务失败关闭，不发布部分成片。
- 最终 ZIP 同时保存 `video.mp4` 和原始 `scenes/shot_XX.mp4`；manifest/storyboard 记录每镜 prompt、prompt_id、seed、实测时长、帧数、尺寸、FPS、SHA-256。verify_path 会验证源镜头文件、ZIP 成员和内容哈希。
- 可选 zh-HK 粤语旁白沿用现有本地 TTS/ASR 包装及 B193 时长硬门槛；旁白长于最终视频时仍拒绝封装。
- Creator Worker 已将 creator brief 的 scenes 分支路由到新方法；单 prompt 任务仍使用原单镜头入口。

## 验证

- 新增多镜头顺序调用/拼接、单镜失败不交付、60 秒上限、源镜头 ZIP 归档与哈希验证测试：**4/4 passed**。
- 视频产品（排除 4 个当时依赖损坏 ResearchKnowledge import 的测试）+ 视频 QA + OpenFilm 专项：**68/68 passed**。
- 全仓 `compileall -q src/muse tools tests`：通过。
- 最终全仓 Python 3.11 unittest：**1,578 tests in 160.182s，OK，9 skipped**。
- 本机 FFmpeg/FFprobe 已发现，路径位于 D 盘。
- 实机 ComfyUI `http://127.0.0.1:8188` 的 `/system_stats` 与 `/queue` 均无法连接；本轮没有启动未知服务，也没有声称完成真实 GPU 多镜头渲染。多镜头生成目前通过单元/集成测试，尚待 ComfyUI 在线后做实机验收。

## 下一步

1. ComfyUI 恢复在线后，检查 Wan 模型、GPU 空闲显存及队列状态，再做 2 镜头真实生成；不得绕过 70% 显存安全门槛。
2. 对镜头间主体、场景、色彩与运动连续性增加可解释 QA；低置信度只标记人工复核，不自动宣称连续性通过。
3. 按旁白时长自动拆分镜头并规划覆盖余量，必要时追加镜头；不能通过静帧拉长冒充运动画面。
4. 为每镜失败增加有限次数、幂等的重试策略；保留失败原因和资源消耗记录，防止无限 GPU 循环。
5. 最终产品仍保持 `testing` 与 `ready_to_publish=false`，直至内容、版权、披露、编辑及平台审核通过。
