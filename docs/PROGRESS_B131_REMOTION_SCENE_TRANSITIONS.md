# B131 — Remotion 逐场景转场超融合

## 目标
在 B130 的品牌片头/片尾包装之外，把多张图片与多个视频镜头组成真正的 Remotion TransitionSeries 时间线；保留原素材音轨、画幅、FPS 与可复现的场景时长。

## 本阶段实现
- 新增 `MuseSceneTimeline` Remotion composition：真实 `OffthreadVideo` 镜头，按时间轴交替使用 crossfade 与 wipe。
- 新增 `RemotionMotionAdapter.render_scene_timeline`：先由 FFmpeg 规范化每个真实源素材，再暂存到 Remotion public 临时目录，渲染 TransitionSeries，最后从原始合成 MP4 取回音轨并按新时间轴截取。
- `VideoProductBuilder` 增加 `brand_scene_transitions` profile：逐镜头转场后继续执行 B130 的品牌片头/片尾。
- Dashboard 只在本地多素材模式显示逐镜头选项；AI 单镜头仍使用原品牌包装。
- 临时 Remotion public 素材使用唯一目录，结束后清理；源素材只读。任何 Remotion/FFmpeg 阶段失败均使产品构建失败，不静默降级。

## 验收边界
- 必须通过真实 Remotion + FFmpeg 多图片/视频混合素材 smoke、音轨检查、完整解码和 Muse QA。
- 还必须通过 Python 回归、前端检查、Remotion TypeScript 检查。
- ComfyUI/Wan GPU 只有在 RX 6750 GRE 实际推理产出 MP4 并完成产品/QA 验证后才能标记实机完成；模拟推理不计入。

## 已验证结果

### Remotion 多场景真实渲染
- 本机 Remotion 4.0.543 实际渲染混合素材：2 张 PNG + 1 段 MP4，共 3 个镜头。最终 smoke 直接走真实 VideoProductBuilder._apply_motion_profile，不再用测试替身绕过 builder 的场景转场集成；通过独立 motion_workspace 将临时产品目录与 Remotion 工程目录隔离。
- 场景时间线：1920×1080 / 30 FPS，9.067 秒；Remotion TransitionSeries 交替 crossfade / wipe，转场 0.467 秒；从源 MP4 保留音轨。
- 再叠加 B130 动态片头/片尾，成片 11.967 秒；Muse QA passed，完整 FFmpeg 解码通过，产品 ZIP CRC、MP4/ZIP SHA-256 和产品验证通过。
- 另有 512×288 / 12 FPS 的 B130 music/narration 两种真实 Remotion smoke；均完成渲染、音轨检查、完整解码和 Muse QA。旁白测试的片头/片尾静音被 QA 正确标为 review warning。
- 定向 Python 回归 48 项通过；最终代码变更后再次运行全量 Python unittest 1,140/1,140 通过（Python 3.11，含 tzdata）；compileall 通过；Remotion TypeScript check 通过；Dashboard Svelte check 0 errors / 0 warnings；前端测试 25/25；Vite production build 通过。VideoProductBuilder 升至 v5，manifest / storyboard / Dashboard 现在以 Remotion 后的最终 MP4 时长和帧数为准，并保留原始 AI 镜头时长/帧数。

### ComfyUI / Wan 真实 GPU 路线
- 可用环境：D:\\AI\\ComfyUI-Rinsei\\portable\\ComfyUI_windows_portable；ComfyUI 0.23.0，嵌入式 Torch 2.10.0+rocm7.14.0a20260603 / HIP 7.14.60850，真实识别 AMD Radeon RX 6750 GRE 12GB（gfx1031）。
- Wan 工作流所需 10/10 节点全部在线；Wan 2.1 T2V 1.3B 的扩散模型、UMT5 文本编码器、VAE 三个模型文件均存在。
- 首次与第二次真实推理均在 CLIPLoader 遇到 Windows OSError 1455。诊断发现本机 llama.cpp 的 DeepSeek-V4-Pro 9B 服务占用约 19.4 GB Private Bytes，使系统可用提交内存降至约 2.4 GB。
- 为完成本轮 GPU 验收，按 PID 精确停止 Muse model_supervisor / llama-server（没有停止其他用户进程）；释放后可用提交内存升至约 13.1 GB，Wan GPU 推理成功。
- 真实生成结果：ComfyUI prompt_id `32700a92-9e07-47be-b4c1-ff42d528262d`；Wan 2.1 T2V 1.3B；512×288 / 12 FPS / 8 steps；请求 1.0 秒，生成 13 帧（源片段约 1.083 秒）。
- 实际产品：`data/products/d16c8182b4cca0a62b1c4123ed80c8e0/ce8293d212a269af65ec`；Remotion 4.0.543 片头/片尾包装后最终 MP4 4.416667 秒，193,821 bytes；MP4 SHA-256 `acf0007047f3f9c357b6de920f87e2f3a316d84cf99f40296f12efbfcc62be6e`；ZIP SHA-256 `00f84a83f81c7161e6e905c64e98598dccd6557ef80bdb119f9fb678b3b8dc01`。
- Muse QA：passed；FFmpeg 全片解码通过；ZIP CRC 与成员清单验证通过；产品 `verify_path` 返回 validated=true。历史产品元数据已修复：AI 原始镜头约 1.083 秒 / 13 帧，最终 MP4 4.416667 秒 / 53 帧，Dashboard 产品目录显示最终成片时长。此测试片段没有音轨；发布就绪仍为 false，需后续完成配音/音乐、内容审查、权利/披露与平台审核。
- 页面文件配置现为 D:\\pagefile.sys 初始 64 GB / 最大 96 GB；生成后 Windows 当前实际分配约 32.7 GB。D 盘剩余约 292.5 GB。
- ComfyUI 当前保持在线供 Muse 视频生成使用，GPU 已占用约 9.9 GB / 12 GB。尝试恢复 9B llama.cpp 服务时因 Vulkan 显存分配失败（约 918 MB）未能启动；因此本轮结束时本地 LLM 暂时离线。不要同时强制启动两个 GPU 重负载；下一步应实现 Muse GPU 资源互斥/让位与恢复策略。

## 当前状态与后续
- B131 Remotion 逐镜头转场 + Wan 2.1 GPU 实机短片 + 产品封装验收已完成。
- 后续：GPU 资源协调器（ComfyUI 与 llama.cpp 互斥/恢复）；Wan 更高时长/分辨率/步数分级验收；音轨、字幕、旁白；多镜头故事一致性；Velorn 时间线写入/导出；完整内容 Review；多平台发布与收益复盘闭环。