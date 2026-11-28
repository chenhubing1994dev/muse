# 02 · 八大版图子系统四态矩阵

> 🟢 VERIFIED_LIVE（latest.json 有通过证据）· 🟡 WIRED_PENDING_RUN · 🔴 BROKEN_OR_MISSING · ⚪ HORIZON_RESERVED
> 依据 2026-11-27 22:22 `latest.json`：53/53 AC 全部 🟢；本轮新增项为 🟡。

| # | 版图 | 子模块 | 状态 | AC | 主干接线 |
|---|---|---|---|---|---|
| 1 | 统帅/适配器/舰队 | muse_commander（DAG/风险门/审批/Kill/超时/审查/凭证） | 🟢 | 020–025,031,033 | B：CLI + 诊断；Portal 未接 |
| 1 | | adapter_hub（9 适配器四态 + 授权门） | 🟢 | 028,091 | B |
| 1 | | expert_fleet（15 角色、权限、执行器绑定） | 🟢 | 029 | 注册入 SpecialistRegistry/AgentRegistry |
| 1 | | resource_governor 并发上限 | 🟢 | 027,090 | Commander.capacity |
| 1 | | Ruflo 适配器 | 🟡 CONTRACT_ONLY | 091 | ruflo_adapter 存在，未经 Commander 授权派发 |
| 1 | | dsh / Super Agent Party 适配器 | ⚪ 休眠/未运行（见 036） | 091 | external_agents |
| 1 | | MuseRuntime/自治运行时 服从全局 Kill（commander_gate） | 🟢 入口；内部 🔴 | — | 两套派发路径并存 |
| 2 | 双轨记忆 | dual_track_memory（越权拦截/人工确认/标签侧表） | 🟢 | 010–012 | A |
| 2 | | Obsidian 幂等同步 | 🟢 |
| 2 | | 对话抽取→双轨候选（agent_inference 印章） | 🟢 | 014 | A | 013 | A |
| 2 | | MemPalace 语义检索 | 🟡 CONTRACT_ONLY | 091 | 需配置 MUSE_MEMPALACE_COMMAND |
| 3 | 研究/知识 | epistemics 八类标签 | 🟢 | 040 | 诊断；其它模块未全面改用 |
| 3 | | research_wiki 七类对象/正反证/同源合并 | 🟢 |
| 3 | | ResearchPipeline 生产写入 Wiki 契约 | 🟢 | 046 | A |
| 3 | | 认知标签治理映射（52 私有标签） | 🟢 | 047 | A |
| A | 主干 | Workflow A 经 Commander 票据端到端（workflow_a.py） | 🟢 | 048 | A |
| A | 主干 | status_api 灵感/研究写接口走统帅票据 | 🟢 | 049 | A |
| C | 主干 | Workflow C 统帅主干（workflow_c.py） | 🟢 | 076 | C |
| C | 主干 | 视频完成→交付包钩子 | 🟢 | 077 | C |
| 8 | Portal | 全部 JSON 响应带认知标签 | 🟢 | 084 | — |
| 1 | 统帅 | Ruflo 执行收归统帅票据 | 🟢 | 035 | B |
| 2 | 记忆 | 旧记忆迁移 legacy_unverified | 🟢 | 015 | — |
| 1 | 适配器 | dsh / Super Agent Party 统一适配器 | ⚪ 契约测试通过；dsh 服务未运行、SAP 无地址 | 036 | B |
| 实连 | 模型 | 本地模型最小推理 | ⚪ SKIP（无 nvidia-smi） | 093 | — | 041–043 | A |
| 3 | | 象征实验室（xuanshu_fusion + metaphysics_label） | 🟢 | 044–045 | A |
| 4 | 守护/进化 | reminders 到期巡检 | 🟢 | 050 | 诊断 |
| 4 | | evolution_guard 版本+回滚 + Commander 高风险门 | 🟢 | 051–052 | A |
| 5 | 插件治理 | plugin_platform manifest 校验 | 🟢 | 060 | — |
| 5 | | handler 崩溃隔离 | 🟢 | 061 | B |
| 6 | 多媒体工厂 | publish_gate 产物校验（WAV/MP4/SRT/MD） | 🟢 | 070 | C |
| 6 | | FFmpeg 实合成 | 🟢 LIVE | 071 | C |
| 6 | | 视频包→发布准备包桥接 | 🟢 | 075 | C |
| 6 | | 脚本→分镜→TTS→合成自动流水线经 Commander | 🔴 | — | 视频线仍走 video_task_worker |
| 7 | 商业/成本主权 | provider_policy 付费阻断 | 🟢 | 026 | B |
| 7 | | revenue_ledger 曝光≠收入 | 🟢 | 073 | C |
| 7 | | 发布准备包人工门 | 🟢 | 072 | C |
| 7 | | unit_economics 反证据要求 | 🟢 | 074 | C |
| 8 | Portal/桌面/教学 | status_api 鉴权健康 | 🟡 | 080 | — |
| 8 | | web-portal 静态资源 | 🟢 | 081 | — |
| 8 | | Commander HTTP 接口（审批/取消/Kill） | 🟢 |
| 8 | | ui-prototype CommanderPanel（审批/拒绝/Kill/适配器四态） | 🟢 | 083 | B | — | 未接线 |
| 8 | | start_muse.bat + USER_GUIDE_LIVE.md | 🟢 | 092 | — |
| — | 远期 | Phase 5–6 文明级扩展（多节点意识体/星际同步等） | ⚪ | — | 待你拍板，不写桩 |
