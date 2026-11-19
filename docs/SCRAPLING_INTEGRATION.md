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

第一阶段已完成可选 HTML 解析适配层、10 项适配器/Reader 集成测试、单页结构化输出总量 20,000 字符上限、D 盘项目虚拟环境安装 Scrapling 0.5.1，以及真实 Selector CSS/XPath + adaptive learn/reuse + workspace SQLite smoke。Scrapling 已接入 ResearchPipeline 的显式可选路径（`--scrapling`，默认关闭），尚未启用网络 Fetcher、动态浏览器或 Spider，也尚未在真实网站上做端到端验收。不能将完整 Scrapling 能力宣称为已接入。
