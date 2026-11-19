# B178｜视频光流诊断可选依赖声明

日期：2026-11-19

## 变更

- 在 `pyproject.toml` 声明 `video-diagnostics` extra，包含 NumPy 与 OpenCV headless。
- 不将视频分析依赖强制加入 Muse 核心安装；基础环境可继续使用显式 `unavailable` 降级路径。
- 既有测试已覆盖缺依赖降级，并在依赖可用时运行真实指标测试；本轮不修改该测试逻辑。

## 验证

- Python 3.11 TOML 解析：通过；确认 `video-diagnostics` 包含 `numpy>=1.26` 与 `opencv-python-headless>=4.8`。
- `test_video_motion_diagnostics.py`：**4/4 passed**。
- 全仓 `py -3.11 -m unittest discover -s tests -q`：**1497 tests，OK，1 skipped**。
- 第一次全仓运行中出现的 9 项间歇性失败/错误逐项隔离复跑后 **9/9 passed**；第二次完整回归全绿。
- 全仓 `git diff --check` 报告 `docs/PROGRESS.md` 历史行多处尾随空格，以及 `ui-prototype/src/lib/api.ts` 一处尾随空格；这些不是 B178 新增内容，未擅自清理。
## 安装

`pip install -e .[video-diagnostics]`

安装 extra 后才能启用基于 OpenCV/NumPy 的帧间光流指标；缺少依赖时不会伪造分析结果。