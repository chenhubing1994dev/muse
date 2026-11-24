# AI 内容核验工作台 v0.1.1

**产品定位**：给短视频创作者、独立研究者和小团队使用的 AI 内容核验模板包。  
**核心流程**：拆分主张 → 核对原始来源 → 寻找反证 → 记录不确定性 → 再决定是否发布。

## 这不是“保证 AI 永远正确”的工具

生成式 AI 可能自信地给出错误内容，甚至给出看似合理但不存在或不支持结论的引用。NIST AI 600-1 将这类风险称为 confabulation，并在 MS-2.5-003 建议审查、核实 AI 输出中的来源与引用。本文档中的三步工作流是面向创作者的实用整理，不是 NIST 官方认证或逐字规定。

官方参考：
- NIST AI 600-1（2024-07-26）：https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence
- PDF：https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf

## 包含内容

- `templates/claim-register.csv`：主张登记表
- `templates/source-card.md`：来源卡片
- `templates/counter-evidence.md`：反证与替代解释工作表
- `templates/publishing-checklist.md`：视频发布前检查
- `templates/rights-manifest.template.json`：素材授权清单
- `templates/cost-revenue-ledger.csv`：成本、订单与结算收入账本
- `examples/nist-ai600-1-example.md`：已填示例

## 5 分钟快速使用

1. 把 AI 输出拆成一条条可以独立核实的主张。
2. 每条主张至少记录一个原始来源；没有来源就写 `not_collected`，不要编 URL。
3. 搜索反面证据、边界条件和替代解释。
4. 给每条主张标记 `verified`、`partially_verified`、`unverified` 或 `contradicted`。
5. 发布前检查素材授权、AI 内容披露、字幕、音频、平台规则和实际成本。

## 使用原则

- 不把流畅、权威语气、神秘感或热门程度当作证据。
- 不使用没有来源的比例、覆盖率、排名或“成功率”。
- 不能核实就明确写“未证实”，不要用猜测补空白。
- 商业素材默认不可商用，直到许可证或授权证据被核实。
- 收入只记真实订单和已结算金额；播放量不是收入。

## 当前版本状态

`v0.1` 是产品候选稿。价格、需求、授权文本和销售渠道尚未验证；尚无订单或结算收入。发布前需要人工审校与市场测试。
