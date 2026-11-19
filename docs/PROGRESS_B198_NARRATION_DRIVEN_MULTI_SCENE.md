# B198｜粤语旁白驱动的 Wan 多镜头时长规划

日期：2026-11-19  
项目：Muse  
VideoProductBuilder：v15

## 本轮实现

- 将多镜头 Creator 流程中的本地粤语 TTS 合成前移到 Wan GPU 生成之前。
- 对 TTS 产出的 WAV 使用 FFprobe 测量真实音频时长；不依赖脚本文字数或 TTS 返回的估算时长。
- 新增 _allocate_wan_scene_durations：按 Wan 2.1 12 FPS、4n+1 帧约束规划镜头长度。先保留用户分镜的时长比例，再将额外帧分配给相对较短的镜头。
- 规划时至少为旁白预留一帧余量；单镜不超过 61 帧（5 秒请求上限），整段预览计划仍不超过 60 秒。
- 如果现有镜头数量无法覆盖旁白，或扩展后会超过 60 秒，立即失败关闭；在任何 generate_wan21_t2v 调用之前结束，不浪费 GPU。
- 可选 ASR/字幕识别也在 Wan 生成前执行；CER 门禁不是 passed 时不开始 GPU 生成。
- TTS 只合成一次。生成完成后将已合成 WAV、脚本、可选字幕/ASR 结果直接传给 package_generated_clip，避免再次合成。
- 成片后仍以 FFprobe 实测合并视频和音频时长，再执行零容差旁白对齐检查；不允许 -shortest 静默截断。
- 无旁白多镜头调用保留原来的生成与封装路径。
- VideoProductBuilder.BUILDER_VERSION 升至 15，使新规划逻辑进入产品版本与指纹体系。

## 测试与验证

- 视频产品构建器专项：**51/51 passed**。
- 全仓 Python 3.11 unittest：**1,607 tests，OK，1 skipped**。
- compileall：src/muse、tools、tests 全部通过。
- 新增测试覆盖：Wan 4n+1 帧量化扩展、TTS 仅合成一次、旁白时长驱动镜头扩展、旁白超过可用镜头容量时在 Wan 调用前拒绝。
- 全部测试均为本地/mock/fixture 验证；没有提交真实 GPU 生成任务。

## 边界与未完成

- 本轮未证明真实 ComfyUI/Wan 实机成片成功。B194 最近一次探测时 127.0.0.1:8188 不可连接；需在 ComfyUI 在线且 GPU 显存通过既有 70% 安全门槛后，做两镜头真实验收。
- TTS/ASR 模型仍必须已在本机按既有固定版本部署；本轮没有下载模型、联网调用云端 TTS 或引入付费 API。
- 旁白时长规划只保证时间轴覆盖，不保证口播节奏、停顿、情绪或镜头语义匹配；后续需加入人工试听与镜头内容 QA。
- ASR 的 CER 通过仍不替代人工听审。
- 产品状态仍是 testing，ready_to_publish=false；版权、事实、肖像、披露和平台审核仍需独立通过。

## 下一步

1. ComfyUI 在线后执行 2 镜头真实生成，核对 Wan 返回帧数与 FFprobe 实测时长。
2. 增加镜头连续性 QA：主体、场景、色彩、运动与旁白语义；低置信度转人工复核。
3. 增加有限重试、幂等 attempt ID、每镜资源消耗和失败原因账本。
4. 在真实语音试听通过后，再将粤语旁白模板用于正式 Creator 自动生产线。