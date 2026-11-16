# B134 — 多平台发布准备包（草稿模式）

日期：2026-11-13

## 本轮交付

新增 src/muse/video_publish_package.py，并注册命令 muse-video-publish-pack。它从已完成的 Muse 视频生产目录读取 generation.json、storyboard.json、source-notes.json、字幕、封面、MP4 与产品记录，先调用 VideoProductBuilder.verify_path() 复核产品及技术 QA，再生成统一的手动发布准备包。

首批元数据配置：YouTube Shorts、TikTok、Instagram Reels、哔哩哔哩。四个平台分别导出标题、caption、description、hashtags/tags、置顶评论建议和来源引用；共用同一份 9:16 母版。

发布准备包包括：video.mp4、cover.jpg、subtitles.srt、storyboard.json、source-notes.json、generation.json、README、qa-report.json、Muse 产品 ZIP、产品 manifest、release-checklist.json、四平台独立草稿、总 manifest 和 publish-package.zip。

## 安全与门禁

- 只有 Muse 产品完整性验证通过、QA 状态 passed、原始 MP4 SHA-256 匹配、母版全片解码证据存在时，才允许生成发布草稿包。
- QA failed/blocked、文件哈希不一致、路径越界、缺失素材或未知平台配置都会拒绝生成。
- 输出目录已存在时拒绝覆盖；输入路径必须在 Muse workspace 内；不接受路径穿越。
- 每个平台草稿均标记 release_state=draft_only、publish_api_called=false；账号授权 not_connected；平台视频 ID 和 URL 保持 null。
- AI 生成画面披露、版权/模型权利、隐私/肖像、事实审阅、商业关系披露、平台当前上传规格均列为人工复核项。
- 技术 profile 只表示现有母版元数据，不代表当前平台上传限制、账号资格或审核结果已经验证。

## 真实样片上的实测

源生产目录：data/prometheus/high-value-short-b133/

发布准备包：data/prometheus/high-value-short-b133/publish-package-v1/publish-package.zip

- 平台草稿：youtube_shorts、tiktok、instagram_reels、bilibili 四份。
- 发布 ZIP：27,287,329 bytes，17 个成员（其中 16 个独立文件由总 manifest 记录哈希，另含 manifest.json）。
- ZIP CRC 检查通过；独立复算 manifest 中所有文件 SHA-256，0 项不匹配。
- 发布状态 draft_only，can_publish=false；官方发布 API 没有调用，账号没有连接。
- SHA-256：cdf3ec0754f288579fb7ef9c5baebf132dadb3f938a9f226920c5773523f4281

## 测试证据

- 新增 tests/test_video_publish_package.py：5/5 通过，覆盖四平台草稿包、QA 失败阻断、母版哈希篡改阻断、未知平台拒绝和禁止覆盖已有输出。
- B133 视频 Builder + QA + B134 发布准备包 + API/Dashboard 集成合计 34/34 通过。
- Python 源码语法编译、pyproject.toml 解析及 CLI entrypoint 声明检查通过。
- 全项目测试仍以 B133 记录为准：1,123 项中 42 项错误、1 项跳过；可见错误包含 Windows Python 环境缺少 tzdata 导致 Asia/Shanghai 时区无法解析。B134 定向回归通过，但不能据此宣称全项目测试全绿。

## 尚未实现

- B134 v1 只接受 9:16 竖屏母版；同一选题的 16:9 横屏重新构图/平台剪辑尚未统一自动生成。
- 目前输出的是多平台发布准备资料，不是 OAuth 授权器或自动发布器；发布队列、官方 API、状态回读和失败重试均未实现。
- 尚无各平台当前上传规格的自动在线核验；每个平台的编码/时长/封面/商业披露要求仍需按当时官方说明和实际账号界面核对。
- 真实播放、留存、互动、转化、结算与到账数据尚未导入；没有收益回流、A/B 实验或自动选题优化。

## 下一阶段

1. 将 B134 竖屏草稿配方扩展成横屏/竖屏双母版和不同平台构图配置，并以真实片段做画面安全区验收。
2. 为各平台建立可版本化的官方规格资料来源和过期检查，避免使用未经验证的固定限制。
3. 在用户逐个平台完成最小权限账号授权后，再接官方发布 API 和平台状态回读。
4. 建立只接收官方分析/结算记录的收益账本，再把留存与真实收益反馈给选题实验。

本轮完成的是可验证的视频发布准备包，不是全自动社交媒体运营系统，也不保证任何收入。