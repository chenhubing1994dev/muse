# B-127 桌面客户端任务中心增强

日期：2026-11-14  
项目：D:\\AI AGENT\\Muse

## 本轮交付

- 在 `ui-prototype/src/App.svelte` 的任务中心加入任务名称/编号搜索与状态筛选：全部、排队中、执行中、待审批、失败。
- 新增 `ui-prototype/queue-filter.js`，将过滤逻辑与界面解耦；筛选保持原始顺序，不修改 API 快照。
- 新增 `ui-prototype/queue-filter.test.js`，覆盖中文标题搜索、编号大小写不敏感搜索、状态组合筛选、空查询、未知状态和不修改源数组。
- 显示已载入条数；当 API 标记 `active_truncated` 时明确说明筛选范围只覆盖已载入任务，避免把部分结果误报为全量搜索。
- 增加键盘焦点可见样式，并为窄屏切换为双列筛选布局。
- 本次仅为客户端本地筛选，不增加后端写操作、不改变 Commander 调度权，也不触发任务执行。

## 验证证据

- `npm test`：29/29 passed，0 failed。
- `npm run check`：Svelte 检查 0 errors / 0 warnings。
- `npm run build`：Vite production build 成功，117 modules transformed。
- Tauri Release：使用独立 Cargo target `desktop/src-tauri/target/b127-client-release-check`、已安装 MSVC stable toolchain 与 `--offline` 完成构建，exit code 0。
- 新 EXE：`desktop/src-tauri/target/b127-client-release-check/release/muse-desktop.exe`，8,686,592 bytes，SHA-256 `9527524ec17f170b9cd7263b3883d2514e473b108a74b38de79b44260b91f2a4`。
- 新 EXE 尚未启动；没有替换或终止现有桌面实例。Release build 证明打包成功，不等于窗口交互验收通过。

## 未完成 / 边界

1. 尚未对正在运行的 Tauri 桌面窗口进行截图和真实鼠标/键盘验收。
2. 已生成包含本轮前端产物的隔离 Tauri Release EXE，但尚未启动；现有桌面实例仍未替换。
3. 后端任务快照若已截断，客户端当前没有额外分页 API；搜索不会覆盖未返回的任务。
4. 任务详情、日志展开、受控重试/取消/批准仍需后端现有授权与审计接口，不能由前端直接执行。

## 下一步建议

- 将任务列表分页/完整检索接入后端只读 API，并为任务行增加安全的详情与事件追踪入口。
- 在不覆盖现有实例的前提下，启动隔离 Tauri Release EXE 并完成截图、鼠标/键盘与窗口缩放验收。
- 持续补齐客户端的 Agent 详情、审批证据、知识关联与视频成品工作区，并坚持所有状态使用真实 API 数据。
