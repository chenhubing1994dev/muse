# Muse 进度：本机询价收件与商业转化入口（2026-11-24）

## 本轮目标

修复商业展示页“询价表单只复制到剪贴簿、没有真正收件后端”的断点，建立独立、可测试、默认只在本机使用的线索收集流程。不改动 Commander、VideoTaskWorker、产品化 owner 文件、Research DB、社媒发布器或外部账号。

## 已实现

- 新增 `src/muse/lead_intake.py`：严格 JSON schema、必需明确同意、必填联系方法、文本长度与控制字符检查、隐藏 honeypot、8 KiB body 上限、同源 Origin 检查、禁止 Transfer-Encoding、每 IP 内存限流（5 次/15 分钟）、请求日志不记录 IP/表单数据。
- 询价资料只写入独立 SQLite `data/commerce/leads.sqlite3`；默认保留 90 日。服务启动及每次新提交会清理已过期记录。
- 使用 UUID submission_id 保证重试幂等；数据库提交成功才返回询价编号，响应只包含编号、去重状态及保留期限，不回传联系资料或需求全文。
- 新增 `scripts/muse-lead-server.py`：只允许绑定 `127.0.0.1`，默认端口 8765，不支持 LAN/公网监听；同一服务托管 `ui-prototype` 静态网页与 `POST /api/leads`。
- 更新 `ui-prototype/showcase.html` 与 `commerce.js`：明确同意、隐私提示、反垃圾字段、必填联系信息；只请求同源本机 API，只有收到服务器成功回执才显示提交成功。
- 更新 `commerce.css` 与 `ui-prototype/README.md`：补充同意栏布局、本机启动方式、数据库位置、资料保留及上线前限制。
- 新增 `scripts/muse-leads.py`：`list` 只显示摘要，不显示联系人/需求；`show` 才显示单笔完整资料；`export` 输出新 CSV，禁止覆盖，且对公式开头字符做转义，防止打开试算表时触发公式。
- 更新 `scripts/validate_commercial_assets.py`：验证表单同意字段、honeypot、同源 API、loopback 服务器与本机 SQLite 防护，不再错误地把合法本机 POST 当成外部发布。

## 验证结果

所有命令均经 Muse PSMUX / Python 3.11 运行。

- `tests/test_lead_intake.py`：12/12 PASS。
- `tests/test_site_preview.py`：3/3 PASS。
- 合计收件与网站专项：15/15 PASS。
- `tests/test_social_campaign_tracking.py`：9/9 PASS。
- 商业页本地链接、锚点、响应式 CSS、草稿价格与无背书披露：PASS。
- 表单同意、loopback API、local-only storage guard：PASS。
- Node `--check ui-prototype/commerce.js`：PASS。
- Python 3.11 `py_compile`：lead module、lead server、lead CLI、商业资产 validator 全部通过。
- `muse-lead-server.py --help` 与 `muse-leads.py --help`：均 PASS。

## 本机使用

在 Muse 项目根目录执行：

```powershell
py -3.11 scripts/muse-lead-server.py --host 127.0.0.1 --port 8765
```

然后在同一台电脑打开：

`http://127.0.0.1:8765/showcase.html`

查看线索摘要（不会显示联系人）：

```powershell
py -3.11 scripts/muse-leads.py list
```

按编号查看单笔资料：

```powershell
py -3.11 scripts/muse-leads.py show MUSE-YYYYMMDD-XXXXXXXX
```

导出包含联系资料的 CSV（必须使用新文件路径）：

```powershell
py -3.11 scripts/muse-leads.py export --output outputs/commerce-leads-export.csv
```

## 未完成与真实收入边界

- 本轮没有启动常驻本机服务；上述命令由用户在本机手动启动。
- SQLite 内的联系资料与需求正文以明文保存在本机磁盘；保护 Windows 用户账户及项目目录，勿填写密码、银行卡、身份证件、健康资料等敏感信息。
- 尚未连接 HubSpot / CRM、邮箱通知、网站托管或支付服务；尚未部署公网，也没有社媒账号授权。
- 目前没有客户询价记录、订单、收款或已核验收入。本机表单能收件，不等于已对外获客或已变现。
- 公开部署前必须换用受管理 HTTPS 后端/CRM，增加账号访问控制、加密存储、备份、删除/导出流程、隐私条款、服务条款、价格/交付/退款规则及滥用防护。
- 外部发布、购买广告/素材、签约和收款仍须使用官方最小权限及明确账号/预算策略，并由人工授权。

## 下一步优先级

1. 连接用户选择的 CRM/收件渠道，建立线索状态、跟进责任人与响应 SLA。
2. 在 Webflow / 自有托管环境部署静态展示页，并将表单 API 迁到 HTTPS 托管后端；本机开发服务器不得暴露到互联网。
3. 完成 v6 双比例视频的人声听审、素材商用权利、AI 披露审核，再为获准发布的平台建立排程草稿。
4. 记录曝光、点击、有效询价、报价、成交、退款和净收入，形成真实转化反馈。
5. 在外部 CRM、支付及发布权限接通前，保持所有公网/交易动作关闭。
