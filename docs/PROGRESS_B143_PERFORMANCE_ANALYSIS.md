# B-143｜商业表现分析与创作策略反馈

日期：2026-11-19
项目：Muse

## 本轮目标

把 B142 的 append-only 商业表现快照转化为可复核的描述性信号，为后续选题、脚本与制作改进提供依据。当前阶段不自动改写创作策略，也不把相关性包装成因果结论。

## 已实现

- `InitiativeStore.analyze_performance()` 按平台、来源、币种分别建立 cohort，不跨平台、不混合 `official_analytics` 与 `manual_entry`，也不混算货币。
- 快照是重复测量而非天然可加事件：对每个 `platform + source + currency + source_reference` 只采用最新快照，避免把同一作品多次采集的累计播放、互动和收入重复相加。
- 没有 `source_reference` 的快照不参与作品间比较，另行计数并在 cohort warnings 标明；前端手动录入新增必填作品链接或平台 ID，供后续快照去重。
- 默认最小样本门槛为 3 条可识别作品、累计 1,000 播放；不足时返回 `insufficient_sample`，不生成强结论。
- 互动率、CTA 点击率、合格线索率、订单率均保留 numerator / views 的定义；留存率、完播率和平均观看时长按有该指标的作品播放量加权。缺失值仍为 null，不当作零。
- 报告收入、已结算收入、制作成本独立返回，并在同币种 cohort 内汇总。
- Bearer-auth GET performance API 增加 `analysis` 字段；创作面板展示 cohort 样本门槛、互动率、CTA、完播率和警告信息。

## 验证

- `tests/test_initiative_engine.py`：17/17 passed，覆盖作品最新快照去重、来源/币种分组、小样本状态、缺失率保留、阈值校验。
- `tests/test_status_api.py`：37/37 passed，包含真实 loopback HTTP + Bearer Token 的表现快照 POST、幂等重放、GET 分析回读与无效完播率拒绝。
- UI：`npm test` 46/46 passed；`npm run check` 0 errors / 0 warnings；`npm run build` 成功。

## 边界与下一步

- `ready_for_review` 仅表示达到最低复核门槛，不等于作品成功，也不代表统计显著。
- 目前仍是描述性 cohort 汇总；尚未做显著性检验、按发布时间窗口的时间序列分析、成功/失败作品匹配比较或自动策略建议。
- 手动录入数据仍未经过平台官方凭证核验；OAuth、自动分析导入、自动发布和到账凭证核验尚未实现。
- 下一步应先把内容标识与创作 initiative / 视频产品 ID 关联得更可靠，再建立带样本区间、基准对照和反例检查的策略提示；建议仍须经人工复核与测试，不能直接触发自我进化升级。
