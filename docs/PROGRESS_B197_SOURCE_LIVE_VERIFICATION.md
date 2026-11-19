# B197｜来源实时核验与引用锚点生命周期

日期：2026-11-19

## 实现

- 复用现有 PublicSourceReader，不新增第二套 HTTP 栈；保留其 HTTP(S)、公共地址解析、重定向目标复核、响应体大小与可读内容类型限制。
- 对已审核 citation 做限量实时重抓取（每次旁白 run 全局最多 24 个 citation；单页最多 2 MiB、规范化文本最多 20,000 字符、8 秒读取超时）。
- 只有最终 URL 与审核时 URL 相同、页面未截断、规范化全文 SHA-256 不变、引用字符区间与 excerpt SHA-256 同时匹配，才产生 source_current=true。
- 内容哈希变化、重定向变化、锚点不匹配记录为 stale；网络失败或截断页面记录为 unknown。抓取时间本身不构成 freshness。
- 将核验事件追加到既有 knowledge_events（entity_type=task、event_type=citation_freshness_checked），以 evidence ID 和 citation ID 绑定；记录 fetched_at、最终 URL、HTTP 状态、ETag、Last-Modified、全文/片段哈希与结果；原始 review/verdict/citation 记录不更新。
- claim_dossier() 返回每个 citation 的最新 freshness event；旁白 adapter 只消费这个持久化事件。VideoProductBuilder 的 B196 live revalidation 仍从知识库重新构建合同，不信任调用方注入的 freshness 字段。

## 验证边界

- B197 的自动核验仅判断“当前可访问文本是否与审核时完整快照及引用锚点一致”，不重新判断论点是否真实，也不取代人工 review。
- URL 重定向、页面变更、无法完整抓取、超出读取上限或抓取失败均不能放行事实旁白。
- 未审核来源候选不会被自动抓取后升级成支持证据。
- 当前仍未执行真实 Wan GPU 视频生成；需在事实旁白通过来源核验后再做 B200 实机验收。

## 测试

- tests.test_source_freshness 覆盖 unchanged/current、content_changed/stale、fetch_failed/unknown、truncated/unknown、append-only audit。
- py_compile：6 个源模块 + B197 测试文件通过。
- 定向回归：114/114 通过（来源核验、research fetch/store、旁白合同/编排/同步、Worker）。
- B196 构建器关键回归：3/3 通过（阻断合同、伪造 ready 合同、脚本不匹配）。
- 完整 video_product_builder 测试模块曾有 1 项既有测试错误：测试 patch 模块级 _apply_motion_profile，但当前实现是 VideoProductBuilder 实例方法；本轮未修改该构建器文件，也未把该项误报为通过。
