# B196｜Video Builder Live Research Revalidation

日期：2026-11-19

## 实现

- VideoProductBuilder 在 Wan prompt-build 入口及 MP4 package 入口，对每个 claim-grounded narration contract 重新读取当前 workspace 的 data/research/knowledge.sqlite3。
- 从 contract 的逐段 text、Claim IDs、factual 标记和 timecode 重建输入，重新读取对应 Claim dossier（每个 Claim 最多 50 条证据），用 ResearchKnowledgeStore 的最新 append-only review/citation 记录重算 contract。
- 只有重算结果与调用方提交的完整 contract 深度相等才继续；数据库缺失、符号链接、Claim 无效、dossier 缺失、review/citation 变化或 contract 被伪造/过期都会阻断。
- 已有产品版本复用检查之前也会执行 live revalidation，避免通过命中旧产品目录绕过新鲜审核状态。
- 该验证不把 source fetched_at 当作网页仍然有效的证明；source_current 未被可信流程确认时仍保持 None，故不能放行事实段。

## 验证

- 四项针对性测试通过：伪造 ready contract 被真实空 ResearchKnowledgeStore 拒绝、blocked contract 拒绝、脚本不匹配拒绝、Worker 在 GPU 前阻断。
- 完整回归：tests.test_narration_evidence_contract、tests.test_narration_evidence_pipeline、tests.test_narration_pipeline、tests.test_video_product_builder、tests.test_video_task_worker：**98/98 passed**。
- py_compile 六个旁白/视频模块通过。

## 仍待完成

- 目前知识库 adapter 对来源时效仍返回 unknown；下一阶段需要可信的 live fetch、规范化内容哈希/片段锚点重算、重定向和内容变化检测，并在 append-only review/audit 记录中保存 freshness 证据。
- 事实旁白在 freshness 生命周期完成前继续 fail-closed。氛围性/创作性段落仍可标记 factual=false，但不能把具体事实包装成氛围段绕过审核。
- 尚未执行真实 Wan GPU 生成或生产 MP4/ZIP 的 claim-grounded 端到端验收。
