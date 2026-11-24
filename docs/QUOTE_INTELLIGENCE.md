# Muse Quote Intelligence｜报价情报与采购审计

## 目标

把多家供应商的报价整理成可追溯、可复核的比较报告，帮助采购人员识别价格差异、遗漏项目、额外收费、税务口径和合同条件风险。

本能力先建立确定性审计核心，再逐步接入 PDF、Excel 与 OCR 文件解析。AI 只负责未来的文件理解与候选字段提取；金额计算、比较规则和证据引用由可测试的本地代码负责。

## 首阶段范围

- 输入：UTF-8 JSON，顶层包含 `quotes` 数组；每次最多 20 份报价。
- 每份报价必须明确供应商、币种、税务状态、总价完整性，以及至少一项明细。
- 每条价格明细和每项额外收费都必须带原始文件名、页码/行号/单元格等定位信息，以及原文摘录。
- 支持 SGD、USD、EUR、GBP、HKD、AUD、CAD、NZD、CHF、CNY、INR、MYR、THB、TWD、JPY、KRW、BHD、KWD。
- 金额采用十进制字符串输入及 Decimal 运算；不接受浮点数、NaN、Infinity 或超出币种精度的金额。
- 额外收费、税费及折扣必须显式列出；折扣以正数输入，由系统按折扣类型扣减。
- 输出 JSON 审计结果或 Markdown 报告，保留每项价格的来源定位。

## 比较规则

1. 不同币种分组比较；不自动换汇、不抓取汇率。
2. 不同税务状态分组比较；税务状态未知时禁止排名。
3. 报价总价必须由输入明确标记为完整；未确认完整的报价不得参与排名。
4. 使用调用方提供的精确 `item_key` 对齐服务范围；不做模糊匹配。
5. 某供应商缺少另一份报价中的项目时，将缺项标为 unknown，不把缺项当作零元，并阻止该组排名。
6. 任何排名都只表示在当前已提供范围内的价格顺序，不代表最佳供应商或采购建议。
7. 保修、交付时间、付款条件、排除项目和税务状态作为风险信息展示，最终由人核对原始文件。

## 非目标与安全边界

- 第一阶段不解析 PDF、XLSX、扫描件或图片；不安装 OCR/Office 依赖。
- 不联网搜索供应商、不核验供应商资质、不发送询价、不联系客户、不创建外部账户。
- 不自动选供应商、不发采购单、不付款、不审批合同、不发布报告到外部服务。
- 不把 AI 推测补成缺失金额、税率、交付承诺或保修条件。
- 所有采购决定必须由人查看原始文件并批准。

## 输入结构

顶层格式为 `{"quotes": [...]}`。每份报价包含：

- `quote_id`、`supplier`、`currency`
- `tax_status`: `included` / `excluded` / `not_applicable` / `unknown`
- `total_complete`: 必须显式提供布尔值
- `line_items`: `item_key`、`description`、`quantity`、`unit`、`unit_price`、`source`
- `adjustments`: `kind`（fee/tax/discount）、`name`、`amount`、`source`
- `terms`: `warranty_months`、`delivery_days`、`payment_terms`、`exclusions`

每个 `source` 必须包含 `file`、`locator` 和 `excerpt`。未来 PDF/XLSX/OCR Adapter 必须将页码、表格行、单元格或 OCR 区域转换成同一来源结构，并保留不确定性，不得只输出无出处的字段值。

## Muse 集成路线

- 当前核心模块：`src/muse/quote_intelligence.py`。
- 当前回归：`tests/test_quote_intelligence.py`。
- 报价审计先作为独立、无网络、无第三方依赖的本地能力。
- 后续经验证后，再通过 Muse CapabilityRegistry / Commander 的既有受控入口登记；Commander 仍是唯一调度和审批权威。
- 若要把该服务作为商业机会试点，复用现有 BusinessOpportunityStore 与 Creator Initiative，不另建机会账本或任务队列。

## 验收标准

- 相同输入重复执行产生相同 totals、分组与排名。
- 所有金额可追溯至原始文件定位；来源缺失即拒绝该条价格。
- 缺项、未知税务、未完整总价和跨币种比较均 fail-closed。
- 无网络请求、无付费 API、无自动供应商选择或采购执行。
- 单元测试通过，并在冻结源快照上完成适用的全仓验证后，才可宣称首阶段完成。

## 后续阶段（未实现）

1. CSV/Excel 结构化导入。
2. PDF 文本层提取与逐页 provenance。
3. 扫描件 OCR，输出字段置信度与原图区域坐标；低置信度字段必须人工确认。
4. 供应商资质与公开来源核验（仅在明确批准联网研究后）。
5. 与 Muse 研究知识库、商业机会账本、离线产品生成及 Commander 审批流程连接。
