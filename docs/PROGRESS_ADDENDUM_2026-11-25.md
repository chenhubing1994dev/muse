# Muse 进度补充｜2026-11-25

## Quote Intelligence 产品页预览

- 新建 `outputs/quote_intelligence_launch_preview_20261125/index.html` 与 `README.md`，隔离在新目录，不覆盖既有站点 owner 文件。
- 页面展示可比性分组、税务/币种不确定性、报价来源追溯与人工决策边界。
- 明确声明当前仅支持规范化 JSON；PDF/XLSX/OCR、真实供应商核验、客户需求验证均未完成。
- US$9 是未验证的价格假设；无结账、支付、追踪脚本、表单或线索收集。
- 静态验证：官方 `muse_dev.py run → PSMUX` Job `444b96718bcc401d8b471f143485fc0d`，exit 0；59 个 HTML 标签、2 个本地链接通过。
- HTML SHA-256：`a063791ae312a52e53e1574c3b81d7b551823c1dd1bbe214b51ddbe05f7307f7`。
- README SHA-256：`2f52afe4293a5eff55de218f9d206480665ac56c33cd251849f7f11fd5fa2dc0`。

## 仍需闭环

- VideoStudio 的持久 Commander grant → approval-bound execution ticket → Attempt/lease → VideoTaskWorker → completion evidence 仍未最终验收。
- Site owner 尚未确认 US$7/US$9 页面价格漂移及唯一写入权；未覆盖原页面。
- Product Builder 的内容就绪门槛仍需唯一 writer 修复与负向回归；不以 ZIP 完整性替代内容质量。
- 生产 DB 的 3,954 条临时报告引用风险和 writer-safety 未完成审计；不启动 AutonomousSupervisor、不清理生产数据。
- 公开发布、部署、账号连接、支付启用均未执行；当前订单 0、已结算收入 US$0。

## 2026-11-25｜Campaign v2 当前快照复验

- 重新执行 `py -3.11 scripts/muse_dev.py run --language python --script-path scripts/verify_campaign_v2.py --cwd .`，PSMUX Job `254ec0ec62054354a45dbd2db66b50c5`，exit 0。
- 三条 55 秒 1080×1920 MP4 均通过 SHA-256、字节数和完整解码校验；逐集 ZIP SHA/CRC 通过；总交付 ZIP 14 项、CRC 通过、无重复成员。
- 总包 SHA-256：`ed0b08d308b1bf9787fac48c16b68411a8466b507d782bec6859ac154c0b5f86`。
- 发布闸门仍关闭；US$9 仅为未验证价格假设；订单 0、已结算收入 US$0、未公开部署。
- 本轮没有重新生成视频，复用了既有资产进行完整性复验，避免重复 GPU 成本。
## 2026-11-25｜Site preview v2 当前快照复验

- 官方 `muse-dev run` → PSMUX 执行 `scripts/verify_site_preview.py`，Job `18931c3159104030b095022efc13d2ef`，exit 0。
- 当前 v2 站点 4 个页面、12 项资产 SHA-256 全部通过；ZIP CRC、成员集合、内嵌 manifest 一致性通过。
- ZIP SHA-256：`8958040652eed69a55ee553ce9234ed44471fbf3e90c632f2b861daab3cd14d9`。
- 发布/爬取/支付仍关闭，商业版权复核仍为必需；该验证不等于公网部署或正式商店。
- 新增粤语 YouTube 首发稿仅为录音与字幕候选，尚未录音、TTS、成片或发布；账号未连接。

## 2026-11-25｜Quote Intelligence 与粤语首发稿复验

- 当前 `scripts/validate_quote_intelligence_preview.py` revision `2788764591325371` 的 imports 已分行；历史 Job `97deb...` 的 `import sysimport hashlib` 语法错误属于 stale snapshot，不是当前源码错误。
- 官方 `muse_dev.py run → PSMUX` Job `62b039d979e447e4a77ce8ee9f279965` exit 0：`QUOTE_PREVIEW_VALIDATION=PASS`，59 个 HTML tags、2 个本地链接；checkout、tracking、lead_collection、public_deploy 均为 false。
- 当前粤语 YouTube 首发稿 `outputs/RESEARCH_TO_SCRIPT_SPRINT_CANTONESE_YOUTUBE_DRAFT_20261125.md` revision `2788764591325387`；官方 PSMUX 内容 QA 7/7 通过，覆盖旁白、研究数据与适用范围、局限、YouTube metadata、SRT 候选时间码、研究来源、发布/声线/账号闸门。
- Site preview v2 的四模块回归（site verifier、product download、lead intake、campaign review）24/24 PASS；独立 v2 verifier Job `e1539875c39d492d98fc8ed0125c227e` exit 0。ZIP CRC、资产哈希及 4 页面/12 资产检查通过。
- 历史 `scripts/verify_monetization_delivery_v3.py` 保持不变；按 `docs/COMMERCIAL_PROGRESS_2026-11-25.md`，v2 的 canonical verifier 是 `scripts/verify_site_preview.py`，不得把 v3 历史资产检查结果冒充 v2 结果。
- 本轮重新执行官方 `py -3.11 scripts/muse_dev.py inspect`，`active_job_id=null`；Runner 0.5.0 与 Server build/protocol exact，5/5 registered Projects online，0 active/queued jobs。

## 当前阻塞与商业状态

- Video pipeline P0 仍未闭环：Runtime 已要求 Worker 提供专用 `run_approved_ticket`，并要求成功结果携带与 execution ticket、attempt、initiative、Commander plan/DAG digest、approval event、worker、manifest SHA、独立 reviewer/review event 完全绑定的 completion evidence；Runtime 不能只凭 Worker 自报 `status=succeeded` 结单。已向 Worker owner `wc_sess_3eVxhb11foE0Gxj-` 和 Producer owner `wc_sess_DqCNaDQQhvTO62rD` 发出高优先级接口/验收问题，等待 ACK 与精确 revision/PSMUX 证据。
- YouTube 账号仍未连接；无公开发布/排程、无已验证售价、无订单、已结算收入仍为 US$0。US$9 仅为未验证价格假设；网站仍是本机预览，public deployment/payments/crawl gates 保持关闭。
- 不把内部静音视频、未审核素材或未批准声线包装为可商用成片；下一步以 Worker/Producer 交接、独立 Reviewer 证据、素材/TTS 商业权利核验和人工发布审核为前置。

## 2026-11-25｜Runtime completion evidence gate

- 在唯一 writer 范围内更新 `src/muse/unified_runtime.py`（最终 read-back revision `2788764591325405`）与 `tests/test_unified_runtime.py`（最终 read-back revision `2788764591325406`）；保留初始与中间态字节级备份于 `.muse-backups/video-completion-runtime-20261125/`。
- 新增 `_validate_video_pipeline_completion`：要求 Worker 返回精确 12 字段 `completion_evidence`；与执行 Ticket、attempt、initiative、Commander plan/DAG、approval event、worker 一一绑定；父 Ticket 必须只有一条 `video_pipeline.artifacts_verified` 和一条 `video_pipeline.review_approved`，声明的 reviewer_id/actor 与 Worker 不同，审稿事件晚于素材验证事件并明确 `decision=approved`。这些是结构与事件一致性检查，不等于已认证 Reviewer 身份。
- Runtime 在结束时重新检查原审批 Ticket、审批人及 `approved_at` 与最新 `human.decision` 的时间绑定；独立使用 `LocalArtifactEvidenceVerifier` 对 manifest 的规范相对路径、字节数和 SHA-256 重新读取校验。仅在全部证据匹配后写 `video_pipeline.completed` 并将执行 Ticket 标为 succeeded；缺失、重复、错配、审批撤销或素材被篡改时写 `video_pipeline.completion_rejected`，并写入 `failure.classified`（category=review、retryable=true、恢复指引）。
- **独立审稿信任边界仍未完成**：只读检查 `TaskQueue.record_event()`（`src/muse/task_queue.py:278-296`）确认其 `actor` 是调用方传入的普通字符串；通用事件 API 明确不是身份认证边界。因此 Runtime 当前只能验证不同的 reviewer_id/actor、事件顺序与 manifest 一致性，不能证明事件确由独立 Reviewer 进程/人工身份签发。Worker 不得用自行填写另一个 actor 的方式伪造审稿；需由 Worker/Admission owner 接入可认证的独立 Reviewer 授权/签名或人工复核边界后，才能将其称为真正独立审稿。
- TDD 红灯：旧实现执行 `tests.test_unified_runtime` 的 79 项中 6 项失败，覆盖自报成功、重复素材事件、错误审稿事件 ID、同一 Worker 自审及 manifest 篡改等。
- 最终官方 `muse_dev.py run → PSMUX` Job `312b2db9af714f54914d8832b9d3b300` exit 0：`tests.test_unified_runtime` + `tests.test_artifact_evidence` + `tests.test_task_queue` 合计 **142 tests / 0 failures / 0 errors**；新增审批撤销中途发生时拒绝结单，并验证失败分类/重试元数据真实落入事件 ledger。
- 一次性 patch helper 脚本已删除；只保留上述 byte backups。Runtime 端已完成结构性完成证据闸门，但 Worker owner 尚须实现同一事件 schema 与专用 `run_approved_ticket`；Producer 仍需补齐负向测试，且真实 Reviewer 身份/审稿授权边界尚未认证。在 Worker/Producer 接线与可认证 Reviewer 审核通过前，Video pipeline 仍为 P0 blocked，不得宣称已端到端生成或商用。

## 2026-11-25｜隔离术数 POC 环境复验与全仓回归

- 只读诊断确认 `artifacts/{kinliuren,qimen,ziwei}-poc-venv` 的 `Scripts/python.exe` 入口解析到 D 盘规范环境 `D:\\MuseVenvs\\*-poc-venv`，三个 `pyvenv.cfg` 均存在；在项目根目录、别名目录及规范目录分别启动均可正常报告 Python 3.11.9 的 `sys.prefix/base_prefix`。本轮没有重建、重装或覆盖任何 POC venv。
- 官方 `scripts/muse_dev.py run → PSMUX` 定向回归 Job `770bee29593e4ac4a8624a5be6c770ef` exit 0：`tests.test_kinliuren_provider`、`tests.test_qimen_provider`、`tests.test_ziwei_provider`、`tests.test_inspiration` **50 tests / 0 failures / 0 errors**；包括三套真实隔离引擎 sample chart 与 InspirationEngine 三引擎整合。
- 随后执行官方 `scripts/validate_python_suite.py` 全仓回归：外层 WebCodex Job `wc_job_A_6Hdn7qbdjnJnQT` exit 0，内层 PSMUX Job `7a41c57ef2c84df28c35d57bd054bf65` exit 0；**2,654 tests / 0 failures / 0 errors / 1 skipped**，`src/muse` compileall PASS。先前报告的 2 failures / 7 errors 在当前完整快照中未复现；历史失败保留为历史证据，不再标为当前失败。
- 诊断临时脚本已删除；本轮未修改三个 provider、POC 环境、生产 DB 或 Worker/Producer 文件。PSMUX 最终 inspect：`active_job_id=null`。

## Runtime Reviewer attestation 安全修复（2026-11-25）

- 发现并关闭 Runtime 结单中的身份信任缺口：`TaskQueue.record_event(actor=...)` 的 actor 是调用方提供的普通字符串，`actor != worker_id` 不能证明 Reviewer 独立身份。
- 新增 `src/muse/video_reviewer_attestation.py`：要求 Ed25519 签名、key_id 与固定 reviewer_id 的可信映射、±300 秒时间窗、完整 Ticket／Commander DAG／审批／manifest SHA-256 与字节数绑定，以及数据库全局唯一 nonce 防重放。
- `MuseRuntime` 增加可注入的 `video_reviewer_public_keys`；映射格式为 `key_id -> {reviewer_id, public_key(raw 32 bytes)}`。默认空映射会 fail-closed。**生产环境的可信公钥注册、独立 Reviewer signer/service 和私钥隔离尚未接入**，所以当前不能宣称生产视频流水线已具备独立审核身份。
- 新增 `tests/test_video_reviewer_attestation.py` 与测试签名夹具，覆盖有效 Ed25519 attestation、未配置可信 key、伪造签名、nonce 重放；原 Runtime 测试也改为使用绑定固定 reviewer principal 的临时测试密钥。
- 正式路径回归：Runtime + Reviewer 安全测试 **84/84 PASS**；全仓 `scripts/validate_python_suite.py` **2,738 tests / 0 failures / 0 errors / 1 skipped**，`compileall` PASS。外层 Job `wc_job_BWaCGTY0SrDpXOTr` exit 0；内层 PSMUX Job `40592c3b6f684dd090a5f4f1018d22c0` exit 0。
- 因原 `unified_runtime.py` 与 `test_unified_runtime.py` 混合 CRLF/LF，项目编辑器拒绝局部补丁；通过完整读取、暂存规范化副本、保留原文件备份后替换正式路径。备份为 `src/muse/unified_runtime.py.pre-attestation-20261125.bak` 与 `tests/test_unified_runtime.py.pre-attestation-20261125.bak`。没有使用脚本绕过项目编辑器写源码。
- 视频 P0 仍未端到端完成：Worker 的专属 `run_approved_ticket`、移除 `safe_fallback`、Producer 负向测试和生产 Reviewer signer/key provisioning 仍待各自 owner 交付。没有公开视频、启用支付或对外联系；收入仍为 US$0。





