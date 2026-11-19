# B168｜视频光流诊断工具

日期：2026-11-19

## 本轮实现

- 新增 `src/muse/video_motion_diagnostics.py`，提供 `inspect_video_motion()` 与 CLI：`py -3.11 -m muse.video_motion_diagnostics <clip.mp4> --max-frames 180`。
- 使用可选 OpenCV + NumPy，按最大采样帧数均匀抽帧；输出每对相邻采样帧的平均光流、95 分位光流、位移大于 0.5 px 的像素比例、平均绝对亮度差，以及汇总中位数/最大值。
- 对无 OpenCV 环境返回 unavailable；无可解码帧返回 failed；拒绝非 MP4、缺失/符号链接文件和无效采样上限。
- 不应用未经人工标注校准的统一运动 pass/fail 阈值；报告明确标记为 advisory only，不替代视觉审片和现有 FFmpeg QA。

## 验证

- 新增 `tests/test_video_motion_diagnostics.py`，专项测试 **3/3 通过**。
- Python 3.11 compileall 通过。
- 对 B160 staging 文件实跑 CLI：17 帧、16 对；平均光流中位数 **0.276740 px/frame**，最大 **0.413654 px/frame**；首对 95 分位 **1.009059 px**、大于 0.5 px 像素比例 **10.85%**。与 B163 独立诊断一致。
- 全仓 Python 3.11 unittest：**1439 tests，157.001s，OK，1 skipped，退出码 0**。FFmpeg fixture 测试 stderr 有一条预期无效 fixture 的 `moov atom not found` 日志；未导致测试失败。

## 边界与下一步

- 本工具目前是独立诊断工具，尚未接入 `video_qa.inspect_video_file` 自动发布门；避免把未校准的运动指标误作硬门槛。
- 下一步先用人工标注的主体/镜头运动数据校准指标解释，再考虑在 Dashboard 展示运动诊断；真实 Wan 生成仍需等 ComfyUI 资源可用并逐组受控测试。
- 未修改生产 MP4/ZIP，未改变 QA 门槛，未安装依赖或下载模型。
