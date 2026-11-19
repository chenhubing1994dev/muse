# B191｜已验证视频产品导出 OpenFilm 可编辑多轨工程

日期：2026-11-19

## 本轮交付

- 扩展 `src/muse/openfilm_adapter.py`：
  - 视频镜头支持独立音量，允许将成片内嵌音轨静音，避免与拆分旁白/BGM 重复播放。
  - 新增最多 500 条独立定时字幕/标题覆盖层；每条字幕使用独立 HTML 页面，按 SRT 起止时间进入 OpenFilm 标题轨。
  - 保留既有镜头标题、素材哈希清单、轨道清单与禁止覆盖既有工程的行为。
- 新增 `src/muse/video_editable_export.py`：
  - 仅接受 `VideoProductBuilder.verify_path()` 已验证的 Muse 视频产品。
  - 对 manifest、storyboard、MP4、产品 ZIP 与 SRT 再做 SHA-256 快照核对，降低验证后文件被替换的风险。
  - 从产品 ZIP 安全读取旁白与背景音乐，使用流式复制，不把大音频文件整体载入内存。
  - 旁白与 BGM 存在时，将最终 MP4 的内嵌混音静音，并还原为独立 narration/music 轨；无拆分音轨时保留原片音频。
  - 将 SRT cue 转成独立时间覆盖层；校验字幕不超出成片时长与 OpenFilm 单条标题长度限制。
  - 导出包含素材、film.html、字幕页面和清单的 OpenFilm 工程目录及 ZIP；ZIP CRC、SHA-256 回读验证。
  - 清单记录源产品 ID、版本、MP4/manifest 哈希、音轨策略及混音限制；不提升产品发布状态。
- 新增 `tests/test_video_editable_export.py`，覆盖真实产品封装→完整性验证→OpenFilm 多轨导出、旁白/BGM 分轨、字幕 cue、原片音频保留、未通过验证拒绝及验证后篡改拒绝。

## 验证

- Python 3.11 `py_compile`：本轮新增/修改的 OpenFilm adapter、editable exporter 与测试文件通过。
- `tests.test_openfilm_adapter` + `tests.test_video_editable_export`：**9/9 passed**。
- 较大视频专项回归：OpenFilm adapter + editable exporter + VideoProductBuilder **48/48 passed**（该轮后续临时碰到的 Status API 导入错误在并行文件落盘后消失）。
- 最终 Python 3.11 `compileall -q src/muse tools tests` 通过；全仓 `unittest discover -s tests -q`：**1558 tests in 210.197s，OK，9 skipped**。该结果覆盖本轮新增导出测试，并取代此前并行写入期间观察到的临时语法错误。
- ComfyUI 0.23.0 API HTTP 200；`WanImageToVideo`、`WanFirstLastFrameToVideo`、`WanAnimateToVideo` 节点及 Wan 2.1 T2V 1.3B FP16、UMT5 XXL FP8、Wan VAE 均可枚举；AMD RX 6750 GRE 12GB，设备可用显存约 9.20 GB（71.49%）。
- 检查时已有一个有效 `video_generation` GPU lease，ComfyUI queue 正运行 1 项；没有再提交第二个推理任务。本轮未产生新 MP4，也未改变既有 Worker/Commander 审批门禁。

## 使用

在 Muse 项目根目录执行：

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m muse.video_editable_export --products-root data/products --initiative-id <32位产品ID> --version <20位版本ID> --output-dir data/exports/<工程目录名>
```

输出为：
- `data/exports/<工程目录名>/`：OpenFilm 可编辑工程目录
- `data/exports/<工程目录名>.zip`：工程压缩包

## 验收边界

- 这是既有成片的编辑工程导出，不是重新生成 Wan 视频，也不等于 OpenFilm 桌面应用已完成导入实机验收。
- BGM 以原始音轨与名义增益拆分；不会伪称已重建 FFmpeg sidechain ducking 自动化，清单要求编辑阶段重新审听混音。
- 不自动发布、不批准版权/肖像/事实内容，也不改变 Creator Initiative 的 testing 状态。
- 本轮未运行 GPU 推理；ComfyUI 8188 当前离线。
