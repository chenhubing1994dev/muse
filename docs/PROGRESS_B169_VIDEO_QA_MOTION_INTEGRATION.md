# B169｜视频 QA 可选光流报告接入

日期：2026-11-19

## 变更

- `muse-video-qa` CLI 新增 `--motion-diagnostics` 与 `--motion-max-frames`。
- 显式开启时，调用 `inspect_video_motion()` 并在 JSON 报告中追加 `motion_diagnostics`；默认不开启，未安装 OpenCV 时返回 unavailable。
- 光流诊断异常只记录在附加报告，不改变原技术 QA 的 status / exit code；不把运动指标作为发布硬门槛。
- 新增 CLI 集成测试，验证运动报告可附加且原 QA verdict 保持不变。

## 验证

- 视频 QA + 光流专项测试 **23/23 passed**。
- Python 3.11 compileall 通过；本轮涉及文件 `git diff --check` 无错误（仅 Git 提示工作副本 LF/CRLF 自动转换）。
- 对 B160 Wan staging MP4 实跑：
  - 原技术 QA：`passed`（仅技术解码/容器检测，不代表编辑质量或发布许可）。
  - 光流：`observed`，17 帧；平均光流中位数 0.276740 px/frame。
  - QA JSON 同时保留原 status 和独立 `motion_diagnostics` 字段。
- 全仓 Python 3.11 unittest：**1458 tests，173.073s，OK，1 skipped，退出码 0**。

## 运行示例

```powershell
$env:PYTHONPATH = "src"
py -3.11 -m muse.video_qa "D:\\path\\to\\clip.mp4" --motion-diagnostics --motion-max-frames 180
```

## 边界

- 运动分析仍为 advisory；需要人工标注的主体运动/镜头运动基准校准后，才考虑更深入的 Dashboard 展示或发布策略。
- 本轮 ComfyUI API 离线，未执行新 Wan GPU 推理；没有修改或覆盖任何视频、ZIP 或用户素材。
