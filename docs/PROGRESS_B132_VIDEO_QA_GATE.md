# B132 — 视频技术质量门

日期：2026-11-13

## 本轮交付

- 新增 `src/muse/video_qa.py`：可复用 MP4 技术质量检查模块 `inspect_video_file()`。
- 新增 CLI 入口 `muse-video-qa`（已登记在 `pyproject.toml`）。
- 新增 `tests/test_video_qa.py`，覆盖通过、尺寸不符、完整解码失败、缺失必需音轨、工具缺失、错误扩展名和文件不存在。

## 检查范围

1. FFprobe JSON 探测与 MP4/MOV 容器识别。
2. 视频流存在、时长范围、宽高、帧率。
3. 可选强制要求音轨。
4. 文件大小与 SHA-256。
5. FFmpeg 全片解码（非仅读取文件头）。
6. FFmpeg/FFprobe 不存在时返回 `blocked`，绝不将缺工具视为通过。

调用示例：

```powershell
muse-video-qa .\video.mp4 --expect-width 1080 --expect-height 1920 --expect-fps 24 --require-audio
```

返回 JSON 中 `status` 只有 `passed`、`failed`、`blocked` 三种。退出码分别为 0、1、2。

## 验证状态

- 代码与测试文件已写入项目并通过读取复核。
- WebCodex Runner 的 `run_script` 对 `sh` 和 `python` 均在启动前返回 `interpreter_unavailable`，没有执行命令。
- 随后结构化 Python pytest 验证命令被 Runner 接受并显示 `command_started=true`，但进程结果 `exit_code=null`、标准输出/错误均为空、未检测到测试摘要；因此不能判定测试通过或失败，也不能把测试数记为 0。
- 当前状态：实现已落盘；自动化测试尚未获得可采信的执行证据。必须在 Python Runner 恢复后运行 `python -m pytest -q tests/test_video_qa.py`，再运行完整 Python 测试集。

## 重要边界

这是技术 QA，不等于内容质量、事实准确性、版权/肖像授权、平台规则或商业发布审批。黑帧/冻结帧、响度/静音、字幕同步、封面与平台规格等尚未加入本轮 gate。当前模块尚未接入 `VideoProductBuilder` 的正式产品发布状态转换；不得据此把任何视频标记为 `ready_to_publish` 或 `published`。

## 下一步

1. 在 Python 执行环境恢复后跑通新增测试并修复问题。
2. 将技术 QA 结果写入 `VideoProductBuilder` manifest，并让产品状态在 QA 失败时保持 `testing`。
3. 补充黑帧/冻结帧、音频响度/静音、字幕时间轴和封面校验。
4. 建立可版本化的平台输出 profile 与发布包生成器；先支持导出，不伪造平台 API 发布成功。
5. 最后才接官方 OAuth/API 与真实分析/收益回流。
