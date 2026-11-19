# B201｜VideoProductBuilder Manifest → Commander 交付证据

日期：2026-11-19

## 实现内容

- 在 `WorkflowController` 增加 `record_video_product_evidence()`，复用现有 `VideoProductBuilder.verify_path()`，不另写第二套视频产品验证器。
- 必须提供当前 Ticket、worker lease、成功的 Commander Run、产品 initiative ID 和 version ID。
- 先让 VideoProductBuilder 验证 MP4 容器、manifest 文件清单、各文件 SHA-256、ZIP CRC、ZIP 成员与 ZIP 内外内容一致性。
- 再由 B200 `LocalArtifactEvidenceVerifier` 对 manifest 本身及其声明的 README、Storyboard、MP4、ZIP，以及 manifest 存在时的 QA 报告、字幕和字幕审核文件逐一重新计算 SHA-256。
- 每个文件生成独立的 `workflow.artifact_verified` 事件，并绑定当前 Plan ID / Run ID；返回完整 evidence_refs 和更新后的 workflow revision。
- Windows 下 VideoProductBuilder 返回的本机相对路径会规范化为 POSIX 相对证据路径；拒绝绝对路径、越界路径和非预期产品目录。
- `complete()` 沿用 B200 的完成时二次核验：产品登记后任何文件被修改，均不得完成工作流。

## 验证

- 新增 `tests/test_workflow_video_product_evidence.py`：构造一个符合 VideoProductBuilder legacy manifest/ZIP 合同的本地视频产品 fixture，验证 manifest + 4 个声明文件进入当前 Ticket/Run；篡改 MP4 后完成操作被拒绝。
- B201 定向回归（Video product evidence + B200 artifact evidence + Workflow profiles）：**35/35 passed**。
- 全项目 Python 3.11 unittest：**1,628 tests，OK，1 skipped**。
- `compileall`：`src/muse` 与 `tests` 通过。
- 未启动真实 Wan GPU 生成；本轮产品集成使用本地构造的有效视频产品 fixture，不冒充真实 AI 成片实测。

## 当前边界 / 下一步

- 现有产品构建器已有 `verify_path()` 的完整产品包验证；B201 把该结果登记到 Commander，但尚未自动从所有 Creator Initiative / Video Worker 任务中发现并调用此方法。
- 下一步在明确的 Video Worker 成功路径中接入 `record_video_product_evidence()`：只处理当前 Ticket 绑定的 initiative/version，注册成功后再构造包含全部 artifact refs 的 acceptance evidence；不能通过全盘扫描猜测产品归属。
- 产品技术质检通过不等于内容事实、版权/肖像、平台政策或正式发布获批；发布仍须独立审批。
