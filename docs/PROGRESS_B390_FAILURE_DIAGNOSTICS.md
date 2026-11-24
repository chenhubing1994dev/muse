# B390 — Worker 失败诊断的隐私安全增强

日期：2026-11-24

## 改动

- Worker 处理器失败时，在任务结果中增加受限 exception_type（例如 RuntimeError）。
- TaskQueue.finish() 只接受长度不超过 80、符合 ASCII 类标识符规则的 exception_type，并将其写入 failure.classified 事件。
- 不记录异常 message、traceback、用户 payload、路径参数或 token；failure_taxonomy 的“永不持久化异常消息”原则保持不变。
- 这能让未来 handler.unexpected 失败至少暴露异常类别，而不会把敏感异常文本写进持久日志。

## 回归

- 新增/更新 test_worker_persists_stable_failure_code_without_exception_message：验证 RuntimeError 类型可见，但 “private exception and token” 与 private payload 均不进入结果或事件。
- TaskQueue + Worker + FailureTaxonomy + Watchdog + PluginPlatform：149/149 通过。
- Python py_compile：通过。

## 历史故障边界

- 正式 TaskQueue 中有一条旧 video_pipeline 任务被分类为 handler.unexpected；该历史事件没有保存异常类型或消息，因此不能从现有记录还原根因。
- 未重放该旧任务，未触发 GPU/FFmpeg 生成，也未修改正式任务记录。
- 新的 exception_type 只对后续失败生效；不会伪造或补写历史诊断。

正式数据库仍只读；历史污染记录及处理边界见 B388/B389 进度文档。
