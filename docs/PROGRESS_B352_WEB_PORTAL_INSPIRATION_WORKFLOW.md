# B352 — Muse 跨设备灵感工作流与 PWA 加固

日期：2026-11-22

## 本轮完成

### 1. 服务器网页 / PWA

- 保留独立 HTTPS Server Web Portal；TLS 1.2–1.3 由 Portal 进程终止。
- 默认监听仍为 `127.0.0.1`；公网监听必须显式指定 `--bind`，没有自动打开公网端口。
- CLI 按真实 Muse `data/` 布局解析可选数据库与目录；`--task-db` 必须指向已存在的 TaskQueue 文件。缺失的可选知识库/Agent/改进库保持未配置，不会为了让界面显示而创建空数据库。
- PWA 安装资源已覆盖通用 SVG、iOS 180px touch icon、Android/桌面 192px 与 512px PNG。
- Service Worker 升至缓存版本 `muse-portal-shell-v5`：
  - 安装时预缓存完整静态应用壳。
  - 激活时清理旧 Muse shell 缓存，不触碰其他应用缓存。
  - 对应用壳使用网络优先、离线回退。
  - API、跨域请求、非 GET 请求不进入离线缓存。
  - 使用 fetch event 的 `waitUntil` 保证缓存写入生命周期。
- 页面继续使用同源 Cookie Session；浏览器端不接触桌面 Bearer。

### 2. 灵感捕获与演化闭环落地到网页

- 新增灵感捕获表单：标题、内容、研究权限。
- 外部联网研究默认关闭；只有用户对单条灵感明确选择允许后，演化流程才会使用该条目的联网授权。
- 新增精确的内部写路由白名单：
  - `POST /api/v1/portal/inspirations` → 现有 Status API `POST /api/v1/inspirations`
  - `POST /api/v1/portal/inspirations/evolve` → 现有 Status API `POST /api/v1/inspirations/evolve`
- 两条写路由都要求有效 Session、精确 Origin/Host 与 CSRF；请求体大小受限、拒绝重复 JSON key / 非有限数 / Transfer-Encoding。
- 演化请求只允许 `inspiration_id` 一个字段；网页不能传入 `enqueue_video`，因此不会由此启动视频任务。
- Portal 对演化请求实行单飞门禁；上游调用有 180 秒超时。演化沿用既有 `evolve_inspiration_closed_loop`，生成本地灵感简报、象征假设候选、研究/反证整理、改进提案与视频蓝图。
- 该闭环不会自动批准或执行代码修改；改进提案仍需独立人工审核。
- 易经、紫微、奇门、大六壬只作为假设灵感源，不作为事实证据。
- 内部上游请求由服务器端添加 loopback Bearer；浏览器看不到该 Token。

### 3. 插件超融合边界保持不变

- Manifest 只登记为待审核候选。
- 本机已批准并启用的插件，网页可提交 capability request；请求进入现有 Commander 的 `waiting_approval` 队列。
- 网页不能批准、启用、停用插件，也不执行第三方插件代码。
- 没有创建第二个 Commander 或第二套插件调度器。

### 4. Commander 任务账本

- 新增精确路由 `GET /api/v1/portal/tasks`，要求已登录 Session；只读取最近 40 项现有 TaskQueue Ticket。
- 响应字段仅包含 task id、标题、状态、时间、优先级、尝试次数与 workload class；不返回 payload、内部 lease、worker 身份或执行参数。
- 网页新增任务账本面板；异常相似路径不会命中该接口，任务队列仍由唯一 Commander 管理。账本接口故障时仅该面板局部降级，不阻断其他仪表板数据。
## 本轮验证
- Python 全仓最新复测：**2,404 tests / 0 failures / 0 errors / 0 skipped**（`scripts/validate_full_suite.py`，Job `wc_job_jYcIORrUNPTKV8qR` exit 0）。此前 1 项 skipped 的真太阳时上游比对测试，确认隔离环境中 `celestial-calendar==0.8.0` 可正常导入，单测 1/1 PASS；不需安装到 Muse 主环境。
- Web Portal + Plugin + Portal DB maintenance 专项：**40/40 passed**（76.309 秒）；另对曾发生导入错误的 8 个测试模块执行针对性复测：**74/74 passed**。
- 前端 Node：**66/66 passed**（`node --test`，ui-prototype）；`web-portal/app.js` 与 `sw.js` Node syntax check 通过。
- `node --check web-portal/app.js`：通过。
- `node --check web-portal/sw.js`：通过。
- `compileall src/muse tests`：通过。
- `muse.web_portal --help`：通过。
- 全仓回归脚本现明确将项目根目录加入 `sys.path`，修复了 `scripts`、`tools`、`tests` 命名空间包在部分 Runner 启动方式下不可导入的问题；此前一次复测出现 8 个导入错误（Runner 包装器错误地报告 exit 0），修复后 8 个受影响模块 74/74 PASS，最终全仓 2,404/0/0/0。

## 仍未完成，不能宣称已完成

1. 尚未把 Portal TLS Listener 生命周期接入 Muse Supervisor；当前仍是独立 CLI 服务。
2. 尚未配置用户真实域名、DNS、证书、主机防火墙，也没有公网部署或跨公网端到端验收。
3. PWA 的 iOS Safari、Android Chrome、PC 浏览器仍需真实设备验收；本轮自动化测试不等于实机安装认证。
4. 离线仅覆盖静态应用壳；登录、知识、研究与任务数据仍需联网。没有后台同步或原生推送。
5. 尚未生成 Android APK/AAB、iOS 原生包；Android Keystore / Apple Keychain 未接入。
6. 当前仍是单一服务器访问密码，没有多用户、RBAC、SSO、MFA。
7. Ed25519 发布者信任根、签名 bundle（manifest + artifact digest）验证、密钥撤销和版本不可变性检查已实现；安装包供应链扫描、WASM/受限子进程沙箱、运行时 capability broker、健康探测与回滚尚未实现。
8. 灵感演化目前是受保护的同步请求，单次最长等待 180 秒；下一阶段应接入 Commander 任务状态与事件续传，不能另建调度中心。
9. Linux/macOS 服务端运行和证书部署尚未验证；本轮自动化回归在 Windows / Python 3.11 环境执行。

## 下一轮优先级

1. Supervisor 显式启停 Portal / TLS Listener、健康探测与证书到期提醒。
2. 将灵感演化变成 Commander 可追踪任务，提供进度、取消/恢复和幂等请求；保持唯一调度中心。
3. 网页端补齐研究证据、反证、知识关系、改进提案审核与测试结果回看。
4. 插件隔离执行：完成 WASM/受限子进程沙箱评估、资源配额、运行时健康探测、回滚和 Commander Worker 适配；签名验证本身不授予执行权限。
5. Android / iOS 原生密钥库与真实设备验收。
6. 用户提供服务器域名与部署环境后，完成公网 HTTPS 端到端验收。

## 2026-11-23 follow-up — Web Portal 持久登录审计与健康探针

- Portal session SQLite 新增 `web_login_events` 审计表及索引；只记录来源地址、事件类型与时间，不记录密码、Session Token 或 Bearer。
- 登录失败限速从进程内存迁移到 SQLite：同一来源地址 15 分钟内连续 5 次失败后返回 429；成功登录会重置该地址的失败计数。事件保留 7 天并限制最多 10,000 条，Portal 重启后限速状态仍然有效。
- 新增精确 `GET /healthz` HTTPS liveness 与 `GET /readyz` readiness endpoint。Readiness 只读检查 Session/Plugin/Task/Capability SQLite 表结构可访问性及本机 Status API overview；缺失/不可用时返回 503，响应不暴露数据库路径或内部错误。
- GET 与 POST 均拒绝重复 Host / Origin 标头；Host 必须恰好一个且与配置主机完全相符，POST 必须恰好一个 Origin，避免代理与应用层对重复权威标头产生不同解读。
- 管理員密碼門檻嚴格檢查 32–1024 個有效 UTF-8 字節（不是字符數）；拒絕無效 Unicode。首次啟動將獨立隨機鹽與 scrypt 摘要持久化至 Session SQLite，不保存明文；後續啟動要求 `MUSE_WEB_PASSWORD` 與持久化憑證匹配，否則拒絕啟動。新增 `py -3.11 -m muse.web_portal_admin --session-db <session-db>` 無回顯密碼輪換工具，輪換需先停 Portal，成功後撤銷所有瀏覽器 Session。
- 部署指南補充高熵隨機管理員密碼注入建議，以及 WAL 數據庫應使用 SQLite Online Backup API / `Connection.backup()` 或停機一致性備份的說明。
- 历史 Web Portal + Plugin Platform 定向回归：**36/36 passed**（70.122 秒）；现已由后续 40/40 Portal + Plugin + DB maintenance 回归取代。
- 历史全仓 Python 回归 2,399 项已由最新 **2,404 tests / 0 failures / 0 errors / 0 skipped**（339.125 秒）取代。全仓导入路径修复与真太阳时可选依赖补齐均已复测通过；Portal 仍未纳入 Supervisor，也未部署公网。

## 2026-11-23 follow-up — SQLite Portal backup / restore

- 新增 `muse-portal-db backup`：使用 SQLite Online Backup API 获取 WAL 一致快照，执行 `PRAGMA quick_check`，为每个数据库生成 SHA-256 与 manifest；支持最多 16 个来源数据库。
- 新增 `muse-portal-db restore`：要求调用方提供 manifest 中的 SHA-256，先做常量时间摘要比对，再恢复到新路径、拒绝覆盖已有文件，并再次执行完整性检查。
- 新增 CLI / WAL snapshot / tampered digest / restore no-overwrite 测试；Portal + Plugin + maintenance 专项 40/40 PASS。备份目录须位于静态网页目录外并按主机权限保护；恢复时先写入新路径验证，不自动替换生产数据库。

## Linux systemd deployment template

- 新增 `deploy/systemd/muse-web-portal.service` 与无真实密钥的 `muse-web-portal.env.example`；服务使用专用 `muse` 用户、systemd StateDirectory、NoNewPrivileges、ProtectSystem=strict、PrivateTmp、严格 UMask 与 loopback-only 默认监听。
- systemd `ExecStartPre` 会检查 TaskQueue、Progress、Capability、Plugin registry 数据库及 TLS 证书/私钥可读；Session DB 由 StateDirectory 放在 `/var/lib/muse-web-portal/`。
- `docs/WEB_PORTAL_SERVER_DEPLOYMENT_V1.md` 新增 Linux 部署步骤、密钥文件权限、真实数据路径和公网监听防火墙注意事项。
- 新增 Linux systemd 部署静态契约测试：**3/3 passed**。这只验证模板的安全配置与路径约束；当前 Runner 是 Windows，尚未执行 `systemd-analyze verify` 或 Linux 实机启动。
- 最新全仓回归已包含上述 3 项部署模板测试；这不等于在 Linux 主机完成 systemd 验收。

## 2026-11-23 follow-up — Inspiration / Harness test integrity

- 全仓 AST 审计发现两个测试类存在同名 `test_*` 方法定义，Python 后定义覆盖前定义，导致重复代码/空壳测试不可见；已删除被覆盖的重复片段，保留实际断言版本。
- 定向灵感演化 + Harness Evolution 回归：**27/27 passed**（7.486 秒）。
- 再次扫描 `tests/` 全部 `test_*.py` 的类级测试方法名：**0 个重复定义**。
- 最新全仓 Python 回归：**2,406 tests / 0 failures / 0 errors / 0 skipped**（365.160 秒），Job `wc_job_0kuK9CqmQVlQdY9F` exit 0。
- 本次仅清理测试代码；未改变插件执行边界、Commander 调度权或生产运行时行为。

## 2026-11-23 follow-up — Inspiration to Creator Initiative

- `evolve_inspiration_closed_loop` 新增严格布尔 opt-in `create_business_initiative`，默认关闭；仅当调用方提供完整商业简报/评分/数据库并显式开启时，才把研究候选提升为现有 Creator Initiative 的 `proposed` / `draft` 草案。
- 立项复用 `BusinessOpportunityStore.promote_to_initiative`，在草案中保留业务候选 ID、证据引用、客户验证步骤和创作 brief；Research Wiki 节点同步记录 initiative ID 与草案状态。
- 增加 initiative DB 固定路径、符号链接和数据库路径碰撞防护。失败时返回部分成功状态，不谎报已完成立项。
- 该衔接不创建 Commander 派工，不启动 GPU、不发布内容；视频执行仍须走既有 Commander 审批票据。
- 灵感演化 + Business Opportunity + Research Business Bridge 专项：**33/33 passed**（9.999 秒）；全仓重复测试方法扫描仍为 **0**。
- 本轮全仓 Python 回归：**2,408 tests / 0 failures / 0 errors / 0 skipped**（349.598 秒），Job `wc_job_xSlQtF4z7tGP7gux` exit 0。
- 全仓完成后补强 Wiki→Initiative 元数据关联断言；最终灵感演化模块复测 **13/13 passed**（10.929 秒），两个改动文件 `py_compile` 通过，测试类重复方法 AST 扫描仍为 **0**。

## 2026-11-23 follow-up — Inspiration to Commander video approval

- `evolve_inspiration_closed_loop` 新增严格布尔 opt-in `request_business_video_approval`；必须同时显式开启 `create_business_initiative=True`，否则拒绝，避免绕过产品立项。
- 开启后复用 `BusinessOpportunityStore.request_video_approval`，在唯一 Commander TaskQueue 创建 `business_opportunity_video_approval` 审批票据；票据 ID / 状态回写 Research Wiki idea metadata。
- 该路径只创建 `waiting_approval` 票据；视频 attempt、GPU 执行、发布均未启动。后续执行仍须验证同一票据持久化的人工 `human.decision=approved` 事件。
- 新增集成测试覆盖严格 opt-in、Commander 票据、Wiki 关联、draft autonomy 与不派工断言；灵感演化模块 **15/15 passed**（11.053 秒），两个改动文件 `py_compile` 通过。
- 初次全量测试发现新增模块的导入语法错误；已修复并重新执行专项 **15/15 passed**（9.244 秒）。随后全量 `unittest discover`：**2,415 tests / 0 failures / 0 errors / 1 skipped**（341.146 秒），Job `wc_job_l0Na2SNhJChJX9Pl` exit 0。此项是完整 unittest 发现回归，不代表 Linux systemd 实机或公网部署验收。
- 对 `tests/test_*.py` 重新执行 AST 类级测试方法唯一性扫描：**0 个重复 test_* 定义**。