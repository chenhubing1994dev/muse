# B156｜Dashboard HTTP 验收与真实字幕封装隔离验证

日期：2026-11-18

## 本轮目标

继续 B155/B150：验证本地 Dashboard 服务链路；用真实 FFmpeg/FFprobe 对已批准字幕版本构建器进行隔离实机验证；修复回归检查发现的 Dashboard TypeScript 类型漂移。

## Dashboard 验收

- 本机以 Python 3.11 启动 `muse.local_dashboard`，使用随机端口和 TEMP 状态文件；测试结束后停止临时服务。
- `/__muse__/health` 返回 HTTP 200，状态 `ready`。
- 首页返回 HTTP 200。
- `/__muse__/config.js` 返回 HTTP 200。
- Vite 生产 JS（约 155,692 bytes）、CSS（约 41,396 bytes）及配置脚本均返回 HTTP 200。
- 这证明 Dashboard HTTP 服务和前端静态资源链路可用。
- Chrome headless `--dump-dom` 实机渲染本次超时；因此本轮**不宣称已完成真实浏览器视觉/交互验收**。下一步仍需通过连接的浏览器检查产品卡、字幕逐条审校、音频播放与提交反馈。

## B150 字幕 remux 隔离实机验证

- 取现有视频产品 `2ff1c0a9339292f7b663a1af1ab0b333/1713600cfe751184fc96` 作为输入，只复制到系统 TEMP 下的独立工作区。
- 在临时工作区创建独立 Creator Initiative Ledger 测试记录；测试批准身份明确标记为 `B156-TEST-FIXTURE-ONLY`。这只是构建器测试夹具，不代表任何真实人工听审或生产字幕审批。
- 使用真实本机 FFmpeg 执行字幕 mux，真实 FFprobe 验证输出流，再调用产品 QA 与 `verify_path()`。
- 输出临时版本：`45ee9b0ce521d66225a9`。
- 输出 MP4：H.264 1080×1920、24 fps；AAC 音频；第三条流为 `mov_text`，语言标签 `yue`。
- FFmpeg 全片视频/音频解码通过。
- ZIP CRC 通过，共 9 个成员；含 MP4、SRT、subtitle-review.json、qa-report.json、README 和 storyboard。
- `verify_path()` 通过；ZIP 中 MP4 哈希与产品验证哈希一致。
- 原始输入 MP4 SHA-256 保持不变：`07e0d650f885a87b42458fd57d5fe750d4c7c47d6ea45ccd799590523b1cadc4`。
- 临时输出 Creator 状态仍为 `testing`，`ready_to_publish=false`；QA 仍保留源产品的 `frozen_frames` 警告。
- 临时工作区：`%TEMP%\muse-b156-remux-fixture-t71dbc2l`。
- **没有批准真实生产字幕，没有调用生产审批 API，没有修改或覆盖生产 MP4/ZIP。**

## TypeScript 修复与回归

回归检查发现 `ProductRecord.subtitle_review.status` 类型只允许 `approved | pending_human_review`，但 Status API 合法返回 `unavailable` 与 `ambiguous`，导致 Svelte 检查报 2 个类型错误。

修改：
- `ui-prototype/src/lib/api.ts`：补齐 `unavailable`、`ambiguous` 状态，并补充可选的 `review_available` 字段。

验证结果：
- `tests/test_video_product_builder.py`：35/35 通过。
- `tests/test_subtitle_review.py`：9/9 通过。
- `tests/test_status_api.py`：31/31 通过。
- Dashboard Node 测试：37/37 通过。
- `npm run check`：0 errors、0 warnings。
- `npm run build`：成功，119 modules transformed。

## 音频响度观察

- B154 music-only Remotion 成片 QA 记录：约 -24.3 LUFS，true peak 约 -14.8 dBFS，sample peak 约 -14.75 dBFS；没有削波迹象。
- 该产物的音频模式是 `music`，不是旁白主导混音。仅因略低于当前 -24 LUFS 下限就盲目加增益，缺乏充分依据。
- 本轮没有更改增益、没有放宽 QA 阈值；保留响度警告，等待针对音乐/旁白模式的素材分轨与听感评估，再决定是否引入按音频模式区分的审查目标。

## 未完成与边界

1. 真实浏览器视觉/交互验收未完成；Chrome headless 本轮超时。
2. 生产字幕仍待用户逐条对照原始粤语音频听审；不运行生产 remux。
3. 生产视频仍有 B149 冻结/低运动区间，需按 shot-01/03/04/05 映射处理；不降低 freezedetect 阈值。
4. 当前粤语 TTS 仍未达到用户要求的成熟女声风格。
5. 本轮未启动新的 Wan/ComfyUI 推理，也未发布到任何平台。
6. 工作区已有的未提交改动和未跟踪文件全部保留；未执行 reset、clean、stage 或 commit。

## 结论

B156 证明本地 Dashboard HTTP 与资源加载正常，B150 字幕 remux 构建器在真实 FFmpeg/FFprobe、独立 Creator 数据库与临时产品副本上成功产出新的、可验证的带粤语可切换字幕 MP4/ZIP。Dashboard 的 TypeScript 状态类型漂移已修复，相关 Python/Node/Svelte/构建检查通过。浏览器视觉验收、真实人工字幕审批、低运动镜头再生成、成熟粤语女声和正式发布仍未完成；产品保持 testing，不得标记为发布就绪。
