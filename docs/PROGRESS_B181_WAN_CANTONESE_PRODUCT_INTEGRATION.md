# B181｜Wan 2.1 粤语旁白成片接入 VideoProductBuilder

日期：2026-11-19

## 本轮实现

- 将无 BGM 的旁白封装路径纳入 FFmpeg `loudnorm`：`I=-16 LUFS`、`TP=-1.5 dBTP`、`LRA=11`，随后 `apad` 并按视频时长截取，避免旁白单轨路径绕过响度标准化。
- 产品 storyboard 与 manifest 记录 `loudness_normalization` 元数据；`VideoProductBuilder.BUILDER_VERSION` 升至 12，使新音频处理行为形成独立、可复现的产品版本。
- 扩展旁白封装测试，检查 FFmpeg 参数与 manifest 声明一致；同步更新 builder version 断言。

## 专项验证

- `tests/test_video_product_builder.py`：37/37 passed（代码修改后复跑）。
- 旁白流水线 `tests/test_narration_pipeline.py`：26/26 passed。
- 视频技术 QA `tests/test_video_qa.py`：20/20 passed。
- 后两组为本轮代码修改前专项回归；builder 修改后再次完整通过 37/37。

## 真实 Wan 双镜头产品封装

- 输入：`data/handoff/prosodic-boundary-audit-20261119/wan21_cantonese_narrated_demo_v2.mp4`，既有粤语旁白 `data/narration/cantonese-tts-asr-3.wav` 与 SRT；两个真实 Wan 2.1 镜头的 prompt/seed/workflow 元数据写入 storyboard。
- 输出产品：`data/products/705f8bf125a547ea7716cc2d5c9ec2f4/f75a46e2c0de0bde71f9/`。
- 产物包含 MP4、SRT、旁白 WAV、旁白脚本、storyboard、QA report、manifest 和 ZIP；ZIP CRC 检查通过。
- Storyboard 保留 2 个镜头、各自 prompt、seed 与源文件名；最终视频 832×480、16fps、5.125 秒。
- VideoProductBuilder `verify_path()` 回读验证通过；技术 QA `passed`；产品状态仍为 `testing`，`ready_to_publish=false`。
- MP4 SHA-256：`2b974b46ee900c1abeb58caa88feed412ebc80a35163546a6151eeca28de896f`。
- manifest SHA-256：`c02dc6bb1df95cf9b097019b7c625bf7dca77abe4bf9a3eafa6cfe208a340617`。
- ZIP SHA-256：`11cf7e32b39a474cabe7ec6c55ce61b0686eb9aa78bbb9c7dba9d205019974eb`。

## 未完成 / 验收边界

- 本轮没有生成新 Wan 镜头；使用 B169 已完成并通过实机解码的 staging demo 进行产品化封装。
- 旁白人声仍为既有 neutral default voice，尚无成熟女声控制；尚无 BGM、多比例导出、15–30 秒长片、平台发布/收益复盘与人工听审。
- 本轮全仓 unittest 执行 1,493 项，出现 7 failures、2 errors、1 skipped；失败涉及自主研究选题、改进审核事件顺序、反馈实验与研究来源解析等。未宣称全仓回归通过，须独立定位。
- 因此本次只证明短演示已接入现有 VideoProductBuilder 并通过产品技术 QA，不代表完整视频生产线或发布验收完成。
