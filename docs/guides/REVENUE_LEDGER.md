# 操作指南：已结算收入与净贡献账本

**它做什么**：只承认“钱真的到账”的收入。浏览、点赞、线索、咨询、加购、未结算订单只计数量，永远不计收入。

**公式**：`净贡献 = 已结算收入 − 退款 − 拒付 − 平台与支付手续费 − 实测算力/工具成本`

**真实入口**：`muse-revenue-ledger summary | import-statement | ingest-webhook`（或 `python -m muse.revenue_ledger`）；代码 `src/muse/revenue_ledger.py`；测试 `tests/test_revenue_ledger.py`（9 例）。默认库：`data\commerce\revenue-ledger.sqlite3`。

**使用步骤**
1. 查看：`muse-revenue-ledger summary` → 未导入任何回执时显示 `US$0.00`。
2. 导入平台结算单：先自己核对文件 SHA-256（`certutil -hashfile 结算单.csv SHA256`），再 `muse-revenue-ledger import-statement --file 结算单.csv --sha256 <哈希> --provider gumroad --external-id <单号> --amount-minor 900 --currency USD`
3. Webhook（可选）：密钥只放环境变量 `MUSE_PAYMENT_WEBHOOK_SECRET`，`muse-revenue-ledger ingest-webhook --provider lemonsqueezy --body-file body.json --signature "t=…,v1=…"`

**防护**：签名错误、时间戳超出 ±5 分钟、畸形 JSON、未知事件类型全部拒绝；同一事件重复投递 = 幂等；同 ID 不同金额 = 冲突拒绝；账本只追加且哈希链可校验（`chain_intact`）。

**诚实边界**：只用伪造的本地 webhook 测试过（`fake_external_boundary: payment_provider_webhook`）；没有接入任何真实支付平台，也没有任何真实收入。对外发布、收款均属 G5，必须由你本人操作。
