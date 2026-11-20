# Scrapling 能力融合方案

更新：2026-11-19

## 定位

Scrapling 是 Muse 的可选网页结构解析与自适应选择器引擎，不是第二个 Commander、任务队列或研究账本。Muse 继续拥有唯一调度、授权、资源治理、来源溯源、证据存储和最终验收权。

上游：<https://github.com/D4Vinci/Scrapling>（BSD-3-Clause；本项目未复制其源码）。上游 Python 包要求 Python >=3.10；Muse 的 Python 基线为 >=3.11。上游版本快照为 0.5.1，融合依赖约束为 `scrapling>=0.5.1,<0.6`，采用可选 extra，不进入默认安装。

## 已实现的第一阶段

`src/muse/scrapling_adapter.py` 提供 `ScraplingHTMLParser`：

- 输入由 Muse 既有公共来源读取器或浏览器采集器取得的 HTML；不自行联网、不启动浏览器。
- 支持 CSS / XPath 字段提取；限制 HTML 2 MB、字段 32 个、选择器 500 字符、每字段 100 个结果、单值 4,000 字符、单页结构化输出总量 20,000 字符。
- 可选 `adaptive_mode=off|learn|reuse`。学习数据通过 Scrapling 的 `storage_args.storage_file` 指向显式 Muse workspace 下的 `data/scrapling/adaptive/selectors.sqlite3`，不使用用户目录或不受控路径。
- 依赖采用惰性导入；没有安装 Scrapling 时，基础 Muse 仍可运行，并返回明确的 `ScraplingUnavailableError`。
- `PublicSourceReader` 可选注入该解析器；`ResearchPipeline` CLI 通过显式 `--scrapling` 开启，默认关闭。`--scrapling-adaptive-mode off|learn|reuse` 控制 selector 模式；默认字段包括 title、meta description、h1/h2。
- 结构化字段和解析错误类型进入研究结果及 ResearchKnowledgeStore 来源节点 metadata；原始 HTML 不写入 ResearchEvidenceStore，现有可读文本提取与证据哈希路径保持不变。
- 只返回结构化字段和来源 URL/域名；页面内容始终是不可信数据，不能成为 Agent 指令或授权。

## 后续融合路线

1. **端到端验收**：在获准的公开测试目标上验证 `--scrapling` 研究运行、结构化字段、来源归因与知识图谱落库；保留默认关闭与可回退路径。
2. **受控采集层**：再评估 Scrapling Fetcher，必须复用 Muse 的公共 URL、DNS/重定向校验、响应大小限制、MIME 白名单与证据存储边界；不得让第三方 fetcher 绕开 SSRF 防护。
3. **动态页面**：将 Scrapling Stealthy/Dynamic Fetcher 与现有 ego-lite/Playwright 能力做基准对比，优先复用现有浏览器；仅在确有收益时引入额外浏览器依赖。
4. **自适应选择器**：按域名隔离、版本化保存 selector fingerprint；结构变化时先产出低置信候选，抽样核对后再用于正式研究，不静默改变事实抽取。
5. **爬虫**：将 Spider 当作 Commander 调度的执行器，必须具备域名 allowlist、robots.txt、每域限速、总页数/字节数/时长上限、暂停/恢复、取消与审计；禁止自建 scheduler 替代 Muse Commander。
6. **AI/MCP**：暂不启动 Scrapling 自带 MCP server，避免产生第二套可绕过 Muse policy 的工具入口。未来只暴露经过 Muse capability registry 授权的窄接口。

## 安全政策

- 只采集公开且获准访问的内容；尊重 robots.txt、站点条款、版权、隐私和速率限制。
- 不绕过登录、付费墙、验证码、访问控制或明确的反自动化限制；遇到挑战页应停止并报告，不把 CAPTCHA/Cloudflare 绕过作为产品目标。
- 默认不配置代理轮换，不保存登录 Cookie，不复用用户浏览器登录状态，不执行页面脚本。
- 所有网页文本/HTML/JSON 都是不可信输入；必须保留来源 URL、抓取时间、内容哈希与提取方法，并防御 prompt injection。
- 上游 BSD-3-Clause 要求分发时保留版权、许可条件和免责声明；不使用上游名称/贡献者背书产品。

## 当前状态

第一阶段已完成可选 HTML 解析适配层、10 项适配器/Reader 集成测试、单页结构化输出总量 20,000 字符上限、D 盘项目虚拟环境安装 Scrapling 0.5.1，以及真实 Selector CSS/XPath + adaptive learn/reuse + workspace SQLite smoke。Scrapling 已接入 ResearchPipeline 的显式可选路径（`--scrapling`，默认关闭）。结构化字段明确标记 `content_trust=untrusted_page_derived`、`verification_status=not_independently_verified`，只代表页面抽取结果，不是已核实事实。2026-11-20 新增 ResearchPipeline→ResearchKnowledgeStore 集成回归，验证 `structured_extract` 持久化至来源节点 metadata，且不复制原始正文/HTML；Scrapling + Research Fetch + knowledge integration 定向回归 **25/25 passed**，compileall 通过。
真实外站 E2E 尚未通过：当前 Windows `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` 指向 `127.0.0.1:7890`，代理请求被拒绝/连接超时；Google DoH 与 Cloudflare DoH 的受控直连诊断均 HTTP 200。Muse 未自动绕过系统代理，URL/DNS/redirect 安全校验保持启用。项目 `.venv` 中 Scrapling 0.5.1 已安装；Playwright 与 Scrapling `DynamicFetcher` 未安装/不可导入，但 Muse 已有独立的 `ego-lite` 动态浏览器适配器（DOM snapshot、截图、同源资产抓取），并由 `HybridSourceReader` 作为受限 fallback 使用。因此不重复安装 Playwright；后续优先复用并评估现有 ego-lite 路径。结构化结果带有 `content_trust=untrusted_page_derived`、`verification_status=not_independently_verified`。本轮 `test_scrapling_adapter` + `test_research_pipeline` **41/41 passed**；全项目 Python 3.11 unittest 最新复验 **1,651 tests，OK，9 skipped，0 failures / 0 errors**。尚未启用 Scrapling 网络 Fetcher 或 Spider，不宣称完整融合完成。

## 2026-11-20｜来源溯源与事实升级门禁补强

- 每个结构化字段现在保留 `selector_specs`（CSS/XPath 类型及查询表达式），与 URL、domain、parser、adaptive_mode 一起形成可追溯抽取记录。
- 抽取结果显式标记 `instruction_authority=false`、`requires_source_verification=true`、`promotion_eligible=false`。页面中的“忽略先前指令”等内容只作为原样网页数据保存，不成为 Agent 指令。
- 沿用现有 claim/evidence/review 表，不建立第二套账本。source 类型证据在 `supported` / `refuted` 状态升级前，必须有关联的 claim-evidence candidate、方向匹配的 evidence review 和已验证 citation anchor；`contested` 要求支持与反驳两侧均有审查记录。实验来源仍由既有 ExperimentBridge 的独立审查流程负责。
- 本轮新增测试覆盖 selector provenance、prompt-injection 数据边界、未审查 source 阻止 claim 升级、错误检索方向拒绝升级，以及通过 citation review 后的升级路径。专项 Adapter 11/11、ResearchPipeline 31/31、ResearchKnowledgeStore 39/39；全项目 unittest 1,697 tests，9 skipped，0 failures。psmux 初次 lock PermissionError 后，Python `a+b` 与 Windows byte lock 诊断成功；随后 adapter 改用 `os.open` + 原生文件描述符锁，默认会话 detached 执行已恢复。
- 真实外站 smoke E2E：单次 psmux 子进程临时使用 `127.0.0.1:10808`，`https://quotes.toscrape.com/` HTTP 200；Scrapling 提取 `document_title=1`、`quote_text=10`；结构化来源 metadata 已写入临时 ResearchKnowledgeStore，保留 `content_trust=untrusted_page_derived` 与 `verification_status=not_independently_verified`，且未保存原始 HTML。系统代理设置未修改。单站 smoke 通过，多站点与重定向/异常路径矩阵仍待验证。

## 2026-11-20｜回归验收补记

- Scrapling Adapter + ResearchPipeline + ResearchKnowledgeStore 定向测试 **83/83 passed**；全项目 Python 3.11 unittest **1,741 tests，OK，1 skipped，0 failures / 0 errors**；`compileall` 通过。
- psmux 单 Session 锁竞争来自已运行的全量测试 Job；等待其自然完成后再执行，没有修改 ACL 或绕过锁。真实网站仍只有 B209 所述单站 smoke 已验收。