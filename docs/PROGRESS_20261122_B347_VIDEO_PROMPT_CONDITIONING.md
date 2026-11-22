# B347 — 逐镜头负面提示词贯通

日期：2026-11-22

## 目标

把 VideoPromptComposer / VideoPromptStudio 产生的逐镜头 `negative_prompt` 真正传到每一次本地 Wan 生成调用，同时保证断点续作不会复用与当前生成约束不匹配的旧镜头。

## 实施

- `src/muse/video_prompt_studio.py`
  - 多镜头 Worker handoff 保留每个场景的 `negative_prompt`，不再因存在逐镜头约束而一律阻断。
  - 将全局与单镜头负面提示词合并后限制在 1000 字符；超限仍在审核候选阶段 fail-closed。
  - handoff 仍为 `execution=not_started`、`commander_submission=not_submitted`，不会自行派发任务。
- `src/muse/video_task_worker.py`
  - 接受并验证逐镜头负面提示词，保留全局和镜头两层输入。
  - Checkpoint Adapter v1 的 scene schema 只接受 `prompt/duration_seconds/seed`。为兼容旧接口，含逐镜头负面提示词时，Worker 将正向 prompt 与镜头负面提示词组合成 domain-separated SHA-256 conditioning fingerprint，仅放入 checkpoint input digest 的 prompt 槽位；原始 prompt 和 negative prompt 仍原样交给 Builder。
  - 没有逐镜头负面提示词的旧输入继续使用原 prompt fingerprint，避免无谓失效。
- `src/muse/video_product_builder.py`
  - 每镜头合并全局和场景负面提示词后传给 Wan。
  - 在 GPU 调用前拒绝合并后超过 1000 字符的约束。
  - 将实际生效的负面提示词记录在 scene records，便于产物审计。

## 验证

- Python 3.11 专项回归：VideoTaskWorker、VideoProductBuilder、VideoPromptStudio、VideoPromptComposer、VideoCheckpointAdapter **131/131 passed**。
- 修改文件 compileall 通过。
- 全仓 Python 3.11 compileall：`src/muse`、`tests`、`scripts` 通过。
- 全仓 unittest：**2,313 tests，0 failures，0 errors，1 skipped**，217.799 秒。
- psmux job：`2fcc78fad7a54d0980b7060443ae896a`，完成、exit code 0。

## 边界与后续

- 本阶段没有启动真实 GPU/Wan 生成；验证证明字段、checkpoint 输入指纹和测试链路贯通，不代表本机模型已完成真实成片验收。
- Commander one-shot admission grant 尚未由生产 runtime 调用。当前 `AutonomousSupervisor._video_worker()` 仍直接创建 Worker；需要由该文件的单写者 owner 注入 provider/registries，并由 Worker 在具体 handler/GPU dispatch 前创建及消费 grant。
- `video_checkpoint_adapter.py` 的公开恢复 artifact API 所有者尚未确认；Worker 仍读取 adapter resume 内部结构，后续应协调公开 `resume_scene_artifacts` 接口，不得跨 owner 修改。
- 主 `docs/PROGRESS.md` 当前存在混合 LF/CRLF 行结束符，结构化编辑器拒绝安全追加。本阶段未强制重写历史进度文件，使用本独立附录保留完整记录。
