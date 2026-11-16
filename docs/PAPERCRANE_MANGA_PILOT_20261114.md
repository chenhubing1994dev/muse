# 《纸鹤渡命人》第一集试产报告（2026-11-14）

## 成片

- 原创系列：纸鹤渡命人
- 第一集：第九十九只纸鹤
- 核心钩子：第九十九只纸鹤，竟然写着摆渡人沈烬自己的名字
- 核心连载规则：每摆渡一个亡魂，沈烬就会失去一段自己的记忆
- 结尾悬念：下一位渡客是沈烬；门后出现十年前已死去的自己
- 目标受众假设：喜欢东方奇幻、悬疑、命运反转的漫剧观众
- CTA：评论猜测「门后的人是谁？」

## 最终版本

- 推荐成片：`data/prometheus/papercrane-manga-ep01-v2/papercrane-episode01-9x16-v4-grain.mp4`
- 规格：29.5 秒、1080×1920、24 FPS、H.264/AAC
- 文件大小：43,464,925 bytes
- SHA-256：`cead65435409861f5d3ea978a0f88275c1ea756fbfe50f65145fe7620eea7312`
- 技术 QA：passed；1 条视频流、1 条音频流；完整解码通过；黑场、静帧、静音警告均为 0
- 封面：`data/prometheus/papercrane-manga-ep01-v2/cover-v4.jpg`
- 交付包：`data/prometheus/papercrane-manga-ep01-v2/delivery-bundle-v4.zip`，11 项，CRC 通过
- Muse Creator Initiative：`c149070fedde45129be0380d2ab70848`
- Product/version：`8b89b8751af27a27988011d0eced2a93 / a4db3b7a93c5e040990f`
- Evolution Cycle：`cycle_9d8c6bbc8c2d482b9b3142723100fb04`
- Research Wiki：原创 IP idea 节点已与视频 artifact 关联

## 实际制作

- 本轮通过 Muse ComfyUI + Wan 2.1 T2V 1.3B，在 RX 6750 GRE 12GB 上新生成 3 段角色镜头，每段 3 秒、288×512、12 FPS、8 steps。
- 三段新镜头 Seed：2026111401、2026111402、2026111403。后两段保留完整 Prompt ID；第一段 Prompt 与 Seed 保留，但首次编排中断时 Prompt ID 未写入元数据。
- 另外复用 6 段 Muse 既有 Wan 镜头。部分旧镜头原始 Prompt/Seed 不完整，已在 `source-assets.json` 标注，不伪造来源。
- FFmpeg 完成竖屏合成、轻微镜头运动、字幕、AI 虚构内容提示、淡入淡出和原创悬疑氛围音。
- 配音：未生成。本机没有已安装、已授权的粤语 TTS 语音资产；交付包附完整粤语旁白稿，未把音乐冒充旁白。
- 角色视觉一致性：三段新镜头保持相近发型、服装与纸鹤，但仍有一定脸部细节差异，需人工审片。

## Prompt 与资产

- Story Bible：`story-bible.json`
- 新镜头 Prompt Pack：`prompt-pack.json`
- 逐镜来源、SHA-256 与 provenance：`source-assets.json`
- 粤语旁白稿：`voiceover-script-cantonese.md`
- 商业测试卡：`commercial-plan.md`
- Muse 产品 QA：`data/products/8b89b8751af27a27988011d0eced2a93/a4db3b7a93c5e040990f/qa-report.json`

## 版本修正记录

- v1/v2 编辑版出现多路视频流及静帧警告；没有选为最终版本。
- v3 修正为单一视频流后，仍有 1 条静帧警告。
- v4 加入轻微动态胶片颗粒，保留单一视频流，最终 QA warnings=0。颗粒用于极轻微时间变化，避免长静帧被误判；人工仍需确认画面观感。

## 发布与商业状态

- 尚未发布抖音或其他平台，未连接账号、未进行投流。
- 没有播放、完播、互动、订单或收入数据；不宣称爆款，也不宣称已赚钱。
- 发布前仍需人工审片、核对所有素材/模型/字体/音轨的商用授权，确认平台当前 AI 内容标识与漫剧审核要求，并检查封面、标题、字幕及音量。
- 正式发布后按 3 秒/5 秒留存、平均观看时长、完播率、评论猜测率、关注转化、第二集追看率复盘。

## 下一步

1. 安装并验证有明确商用许可的粤语 TTS 声线，实际生成配音并做听感、响度、口型/节奏 QA。
2. 继续强化同一角色的参考图/角色资产锁定与镜头级 continuity；目前三段新镜头是纯 T2V，角色一致性尚非完美。
3. 制作第二集，延续「门后的人是谁」悬念。
4. 发布前完成版权/披露/平台审核清单；只有真实发布和结算数据才能验证商业价值。
