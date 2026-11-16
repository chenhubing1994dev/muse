# B-119：自我进化状态接入 Muse Dashboard

日期：2026-11-13

## 本轮完成

- `src/muse/status_api.py` 新增只读 `GET /api/v1/self-improvement`，聚合提案状态数量、待研究/待人工审查/已批准待执行阻塞，以及最近 Evolution Cycle 的状态、可恢复性和是否已保存主题检查点。
- 状态接口只返回聚合计数与安全状态，不返回提案自由文本、feedback 正文、review findings、cycle ID 或 topic key。
- `ui-prototype/api.js` 与 `src/lib/api.ts` 增加类型安全的 self-improvement 客户端调用。
- `ui-prototype/src/App.svelte` 的「反馈 · 自我改进」区域新增进化状态面板：显示待研究、待人工审查、已批准待执行、拒绝/回滚数量，最近周期状态、下一步阻塞、检查点与审批安全门。
- 轮询刷新每 5 秒同步状态；界面明确显示人工批准仍为必需，自动代码修改关闭。

## 验证

- `tests.test_status_api`：20/20 通过，包括聚合状态隐私隔离和真实 HTTP API endpoint 测试。
- UI API TAP：17/17 通过。
- `svelte-check`：0 errors / 0 warnings。
- Vite production build：成功。

## 已知边界

- Dashboard 显示最近 200 条 evolution 事件范围内的最新周期状态；它不等同于长期无人值守运行的健康证明。
- 仍需在实际桌面/局域网浏览器中人工确认状态面板显示与刷新体验。
- 当前只读显示状态；提案批准、代码执行与能力升级继续受原有用户批准、独立 Reviewer、测试和哈希保护约束。
