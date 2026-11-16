# B127 Dashboard QA 与 Remotion 动画子系统

## 目标

- 将 B126 的视频质量门禁和时间点告警呈现在 Dashboard 成品库。
- 建立可复用 Remotion 动画子项目，验证片头包装、标题动效和转场。
- ComfyUI/Wan 负责 AI 镜头；Remotion 负责程序化动效；FFmpeg 负责最终合成、编码和技术 QA。

## 本轮实现

- `status_api.build_products()` 在先通过 `VideoProductBuilder.verify_path()` 校验文件哈希与 ZIP 内容后，才读取 QA 报告；API 只暴露 allowlist 字段、最多 50 条时间点告警。
- Dashboard 产品库展示 QA 状态、待人工复核数量、告警类型和起始时间；“定位复核”通过 MP4 时间片段锚点打开预览。
- 告警仍是启发式人工复核提示，不自动改变技术 QA 结果，也不自动批准发布。
- 新增 `video-motion/` Remotion 子项目，Remotion / CLI / Transitions 固定为 4.0.543；React 18.3.1、TypeScript 5.9.3，并生成 package-lock。
- `MuseBrandMotion` 包含动态背景、标题入场、淡入和擦除转场；支持 9:16、16:9、1:1 的动态 composition metadata。

## 实机验证

- Dashboard：Svelte 检查 0 errors / 0 warnings；前端 Node 测试 25/25；Vite production build 通过。
- 后端：Status API 24/24；VideoProductBuilder 18/18；全项目 Python unittest 1,133/1,133。
- Remotion：`npm run check` 通过；composition discovery 成功；Node 24.20.0 + 系统 Chrome 渲染成功。
- 9:16 MP4：1080×1920、30 FPS、6.8 秒、H.264、1,405,253 bytes；Muse `video_qa` 完整解码通过、无黑帧/冻结/静音告警。SHA-256：`ff94082e8c08361203e8ad34842972962674bbabccd095fc06b2860d49960e13`。
- 16:9 MP4：1920×1080、30 FPS、6.8 秒、H.264、1,639,076 bytes；完整解码通过。QA 检出 2 个疑似冻结区间（约 1.13s 与 3.60s 起），属于需要人工检查的启发式信号；无黑帧/静音告警。
- 输出位于 `video-motion/out/muse-brand-motion-9x16.mp4` 与 `video-motion/out/muse-brand-motion-16x9.mp4`。

## 重要边界

- Remotion 已完成真实 MP4 渲染和 QA 验证，但仍是独立动画模板子项目，尚未接入 `VideoProductBuilder` 自动合成路径；现有 ComfyUI/Wan 成片链路未被修改。
- 1:1 已有动态尺寸计算，尚未进行单独 MP4 实机渲染。
- QA 人工复核目前支持查看与时间点定位；持久化“已复核/忽略（有意暗场等）”账本尚未实现。
- Remotion 是 source-available 专有许可软件，不属于 OSI 定义的开源软件；官方说明个人及不超过 3 人团队可在符合条款时使用 Free License，商业用途亦可。使用前仍须按实际主体资格核对许可。
- `docs/PROGRESS.md` 混合 LF/CRLF，未冒险重写；本轮进度保存在此文件。

## 下一步

1. 新增受限 Python `RemotionMotionAdapter`，通过结构化 props 文件调用本地 CLI，并支持可选片头/片尾/转场。
2. 将 Remotion 输出与主片按音轨、帧率、画幅安全合成进 VideoProductBuilder；合成结果再次经过 FFprobe、完整解码、SHA-256 和产品 ZIP 校验。
3. 将动画模板版本、props、渲染日志、输出哈希写入 storyboard/manifest，失败时不得静默降级或标记完成。
4. 增加持久化 QA 复核账本，记录 reviewer、warning fingerprint、decision、note、timestamp，并绑定产品版本哈希。
