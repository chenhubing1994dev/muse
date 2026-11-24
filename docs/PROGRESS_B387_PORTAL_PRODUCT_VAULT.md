# B387 — Web Portal 产品原型库闭环

日期：2026-11-24

## 本轮目标

把已经存在的“灵感 → Commander TaskQueue → 演化 Worker → Initiative → 离线产品原型”能力接到 Web Portal，让创造者能直接查看、预览和下载已验证的产品产物。

## 实现

- `web-portal/index.html`：新增“离线产品原型库”面板，明确产品默认处于 `testing`，需要人工验收，不代表已发布。
- `web-portal/app.js`：读取 `/api/v1/portal/products`，只展示 `offline_product`；对 Initiative ID、版本 ID、上游 preview/download 路径做格式与一致性校验；所有展示文本进行 HTML 转义。创建离线原型后轮询 Commander 任务账本（最长 100 秒），任务成功即自动刷新产品库；超时或待审批时提示手动刷新。
- `src/muse/web_portal.py`：
  - 增加固定 allowlist 的只读产品目录代理，浏览器不能指定任意上游 URL。
  - 增加严格匹配 32 位 Initiative ID、20 位版本 ID、`preview|download` 的文件代理路由。
  - 上游请求使用服务端 Bearer token，禁用代理与重定向；只接受 HTML/ZIP MIME；文件响应上限 20 MiB。
  - 预览必须带有 `default-src 'none'`、SHA-256 固定脚本/样式、`connect-src 'none'` 等严格 CSP；缺失或过宽策略时 fail-closed。
  - 预览响应额外使用 CSP sandbox；允许已固定的产品脚本使用本地存储、导出和确认对话框，但不开放网络连接、弹窗或顶层导航；ZIP 下载使用 attachment、nosniff、no-store。
- `web-portal/styles.css`：增加产品卡片和操作区样式。
- `web-portal/sw.js`：缓存版本提升到 `muse-portal-shell-v6`，确保客户端更新静态资源。
- `tests/test_web_portal.py`：新增产品目录认证、预览/下载代理、路径穿越拒绝、缺失 CSP 拒绝、Commander 任务轮询与自动刷新等覆盖。

## 验证

所有本轮测试均在隔离 Linux 原生副本 `$HOME/work/Muse-linux-native` 执行；未从正式项目根目录运行全量测试，未复制或触碰正式 `data/`。

| 检查 | 结果 |
|---|---:|
| `py_compile`（Web Portal 与测试文件） | 通过 |
| `node --check web-portal/app.js` | 通过 |
| `tests.test_web_portal` | 18/18 通过 |
| tests.test_product_builder、test_inspiration_productization、test_inspiration_evolution、test_status_api | 81/81 通过 |
| tests.test_autonomous_research + tests.test_watchdog | 87/87 通过 |
| tests.test_task_queue + tests.test_watchdog + tests.test_plugin_platform | 105/105 通过 |
| 历史隔离全量基线（早于 B387） | 2,475/2,475；0 failures/0 errors |

其中 Web Portal 集成测试验证了登录态、产品列表读取、HTML 预览与 ZIP 下载、服务端 token 不下发给浏览器、路径穿越拒绝，以及缺少严格 CSP 的预览被拒绝。

## 历史正式数据库风险（未清理）

此前一次并行全量回归曾从正式项目根目录运行，10 个 tests/test_autonomous_research.py 的 run_once() 测试没有显式绑定隔离 workspace，造成正式数据库出现测试写入记录：22 条 Initiative knowledge links、66 条 experiences、204 条 philosophy links、2 条 pending proposals、42 条 website events。**这些历史记录没有被删除或回滚。**

后续已将这 10 个测试绑定到 workspace=root；本轮在隔离 Linux 副本运行 Research/Watchdog 87/87、TaskQueue/Watchdog/Plugin 105/105，未触碰正式 data/。此前另一次 .muse-test-sandbox 隔离全量基线为 2,475/2,475、0 failures/0 errors（早于 B387，不代表本轮所有改动的全量回归）。

正式数据库后续只做只读取证；任何删除、回滚或整理必须另行明确批准，并先制定备份与可逆方案。

## 安全与产品状态

- 产品生成仍由现有 Commander 工作流调度；本轮没有增加绕过 Commander 的执行入口。
- 离线原型仍处于 `testing`，必须人工验收；没有自动发布、没有执行生成代码、没有启动 GPU 视频生成。
- 产品预览的 HTML CSP 来自 `LocalProductBuilder` 已验证的固定脚本/样式哈希；Portal 只在验证策略完整后加 sandbox 响应头。
- 本轮只完成 Web Portal 侧产品可见性与受控交付，不代表公网部署、真实手机 PWA 安装或 Android/iOS 构建已完成。

## 下一轮优先级

1. 把原型内人工反馈回写到 Initiative / Research Wiki，保留支持、反证、实验结果和版本历史。
2. 建立产品验收、版本晋级与回滚工作流；发布必须显式人工批准。
3. 让产品原型库展示关联 Commander 任务、Initiative ID、版本链和失败原因。
4. 继续 Android SDK/NDK 构建准备、iOS/macOS CI、插件执行沙箱和公网部署验证。
5. 继续只在隔离副本运行测试；正式数据库历史测试痕迹仅做只读审计，未经单独批准不清理。
