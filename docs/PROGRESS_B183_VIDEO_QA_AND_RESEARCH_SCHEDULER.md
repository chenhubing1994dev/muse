# B183｜视频质检工具、动态研究优先级与全仓回归修复

日期：2026-11-19

## 本轮目标

收敛此前 B167 视频技术质检工具的验证工作，同时修复完整回归中实际暴露的自主研究调度、反馈实验预算和网络依赖测试稳定性问题。

## 视频 MP4 自动技术质检

- 保留 `tools/inspect_video_motion.py`：通过 ffprobe 检查容器/编码/分辨率/帧率/帧数，通过 FFmpeg 全片解码、相邻帧差异统计与 freezedetect 检查。
- 报告明确区分 `technical_pass` 与 `technical_review`；帧差不是感知运动质量、镜头连贯性或发布许可评分。
- `tests/test_video_motion_qa.py` 4 项测试通过；视频运动诊断测试在 NumPy 不存在时正确报告 optional dependency unavailable，并对仅依赖 NumPy 的指标测试执行显式 skip。
- 对 B166 真实 Wan 2.1 staging MP4 再次实跑，报告写入 `artifacts/wan_staging/wan21_smoke_qa_b167_recheck.json`：
  - H.264，256×256，17 帧，16 FPS，1.0625 秒；
  - 全片解码成功，16 对相邻帧差异，mean YAVG 7.017675；
  - freeze events 为 0；
  - 状态为 `technical_pass`，同时保留 `very_short_clip_not_suitable_for_motion_quality_judgement` 警告。
- 该样片仅是 GPU→MP4 staging smoke test，不代表生产级成片、动态连贯性或可发布质量。

## 自主研究调度修复

- 修复 `AutonomousResearchEngine._next_topic()` 的优先级：普通 `curated_seed` 不再与动态研究主题同级竞争。
- 被拒绝审查的跟进、正在研究的反馈提案、普通反馈提案和创作者收件箱继续保留原有高优先级；其他动态主题（研究失败、Claim 证据缺口、象征性假设、哲学实验后续等）排在精选种子之前。
- 象征性假设仍只作为待验证候选；研究结果不会自动升级为事实或批准状态。
- 反馈研究生命周期固定白名单套件预算从 120 秒提高到 300 秒，避免随研究测试模块扩张而被旧预算误判失败。

## 测试稳定性修复

- 视频光流指标测试在 NumPy 未安装时显式 skip；缺少可选依赖的能力状态测试仍验证 `unavailable`，没有把可选依赖改成 Muse 核心强制安装项。
- 研究来源审查测试将 DNS 地址解析替换为明确的公共 IP fixture；测试继续验证 URL 公网地址安全约束和证据快照引用，但不再依赖外部 DNS-over-HTTPS 在测试期间可用。
- 修复场景标注 HTTP 测试文件中两条语句意外粘连导致的 Python SyntaxError。

## 验证结果

- 定向自主研究与反馈实验回归：7/7 passed。
- 视频运动 QA + 可选依赖诊断：7 passed，1 skipped（本机未安装 NumPy）。
- 研究 Wiki 审查写入器：4/4 passed。
- Python 3.11 `compileall -q src/muse tools tests`：通过。
- 全仓 Python 3.11 unittest：**1498 tests，174.156s，OK，9 skipped**。
- 全仓结果是修复网络 fixture 与语法问题后的最终回归；此前 1497 项回归因 DNS-over-HTTPS 握手超时失败，不作为最终结果。

## 边界与未完成项

- 未安装 NumPy/OpenCV，不做真实光流质量评分；可选诊断依赖仍保持可选。
- 未重新执行 Wan GPU 推理；本轮只对已有 MP4 重新运行技术质检。
- 未把 1 秒低分辨率样片标记为生产级成片；尚需更长镜头、多种运动场景、视觉人工审片和音画审听。
- 未自动发布、投放或执行任何外部商业动作。
