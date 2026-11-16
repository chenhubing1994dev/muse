# B130 — Remotion 视频后期超融合

## 目标

把 Remotion 从独立动效模板升级为 Muse 视频生产线的统一后期渲染层，覆盖本地素材剪辑、Wan/ComfyUI 文生视频和已生成 MP4 的补封装流程。

## 实现

- 新增 src/muse/remotion_motion.py：
  - RemotionMotionAdapter 绑定 Muse 工作区、Node/Remotion、FFmpeg 与 FFprobe。
  - 通过固定 composition ID、受限 JSON props 和参数数组调用本地 Remotion CLI，不使用 shell 拼接。
  - 生成动态片头和片尾，支持源视频画幅、分辨率与帧率；覆盖 512×288 等 Wan 预览尺寸及 1920×1080。
  - 使用 FFmpeg xfade 将片头、主片、片尾做淡入淡出交叠，保留主片音轨。
  - 音乐模式从片头开始并延续至片尾淡出；旁白模式延迟至正片起点，避免提前播放旁白。
  - 对 Remotion 输出及最终 MP4 执行 FFprobe 元数据校验；任何渲染或合成失败均阻止发布。
- VideoProductBuilder 三条入口均支持 motion_profile：
  - build_from_source
  - build_from_prompt
  - package_generated_clip
- 默认底层 API 保留 none 向后兼容；Dashboard 默认提交 brand_intro_outro，用户可关闭。
- CLI 增加 --motion-profile none|brand_intro_outro。
- motion profile、Remotion 版本、模板版本、时长、转场、音频模式写入 storyboard / manifest，fingerprint 包含 motion profile。
- 新增可重复运行的真实集成脚本 scripts/verify_remotion_motion.py，覆盖两种音轨模式及三个产品入口。

## 实机验证

- Remotion 4.0.543 + 本机 Chrome 实际渲染通过。
- 512×288 / 12 FPS 音频测试：
  - music：5.0 秒，AAC 音轨保留，Muse QA passed，0 条警告。
  - narration：5.0 秒，AAC 音轨保留，Muse QA passed；片头/片尾静音被保留为人工复核警告（预期行为）。
- VideoProductBuilder 本地素材入口：实际输出 1920×1080 / 30 FPS / 4.9 秒 MP4，music 模式，Remotion 完成，产品目录校验、ZIP CRC 和 MP4/ZIP SHA-256 通过。
- VideoProductBuilder Wan 文生视频入口：Wan 生成函数用测试夹具模拟（未执行真实 GPU 生成）；其后真实 Remotion 渲染、FFmpeg 合成、Muse QA、产品 ZIP 和 SHA-256 验证通过。
- VideoProductBuilder package_generated_clip 补封装入口：真实 Remotion 渲染、Muse QA、产品 ZIP 与 SHA-256 验证通过。
- Python 全项目 unittest：1,136/1,136 通过。
- Python compileall：通过。
- Dashboard：Svelte 0 errors / 0 warnings；前端测试 25/25；Vite production build 通过；产品库已展示 Remotion 版本、转场类型、音轨模式和片头/片尾时长。
- Status API 对 Remotion metadata 使用 allowlist 输出；测试确认不会泄露 manifest 中的私有字段。
- Remotion TypeScript check 通过；composition discovery 成功（MuseBrandMotion 与 MuseBumper）。
## 重要边界

- 本轮未启动 ComfyUI，也未真实运行 Wan GPU 推理；只验证 Wan 返回 MP4 之后的后期路径。
- 旁白模式的片头和片尾保留静音，Muse QA 会提示持续静音供人工复核；这不是技术解码失败。
- 多镜头素材目前仍由原有 FFmpeg 素材装配流程处理，Remotion 负责片头/片尾和整体淡入淡出；尚未把每个场景剪辑轨道完整迁入 Remotion 的 TransitionSeries。
- 主进度文档 docs/PROGRESS.md 有混合 LF/CRLF，未冒险重写；本轮记录在此文件。

## 下一步

1. 为 Dashboard 成品详情增加可视化时间轴和 QA 告警复核状态。
2. 增加可选主题模板（科幻、纪录片、知识科普、神秘东方、产品广告），但必须有真实渲染回归。
3. 研究把多镜头 timeline 直接送入 Remotion TransitionSeries，实现场景级擦除、推拉、匹配切换；保留 FFmpeg 作为可靠编码与 QA 后端。
4. 在真实 ComfyUI/Wan 完成一次带动效的端到端 GPU 成片验收，再将该链路标记为实机完成。