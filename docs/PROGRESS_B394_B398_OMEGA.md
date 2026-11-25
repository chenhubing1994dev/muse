# Omega B394–B398 收官记录（2026-11-25 Cycle 5）

| 批次 | 新文件 | 关键能力 | 云端沙箱测试 |
|---|---|---|---|
| B394 | `evolution_guard.py` `autonomy_safety.py` `plugin_capability_broker.py`（改 `evolution_executor.py` `worker.py` `autonomous_runtime.py`） | 补丁写入前 Ed25519 独立签名门（生产密钥、禁自签、一次性 nonce、24h 过期、绑定票据+文件哈希）；9 类受保护面硬编码拦截 + 禁止新增越权标识符；版本化记录 + 全有或全无原子回滚（并发编辑/新版本重叠即拒绝）；SafetyController 暂停/恢复/资源背压指数退避/断路器半开单次试探/熔断锁存+KILL_SWITCH 文件，重置需 `RESET-KILL-SWITCH` 且不自动恢复；Capability Broker 默认拒绝（启用+批准+已验签+声明权限+风险上限），短期令牌、预算计量、清单/签名变化即失效；供应链 lock SHA-256 校验 | 16/16 |
| B395 | `consciousness_core.py` `memory_lifecycle.py` | 十维状态（只读 SQLite 任务/记忆/安全遥测 + 资源探针，每维带驱动证据，标注 `functional_telemetry_model_not_sentience`）+ 相位状态机（halted 只能经 dormant 恢复、滞回）；记忆过期提醒、降权、归档（不删除）、修订 diff、可审计遗忘（正文与全部修订清除，仅留 SHA-256 墓碑）、跨项目因果时间线（拒绝成环）、多模态锚点、Obsidian 单向镜像冲突审计（绝不覆盖用户修改）、Agent-Track 哈希链回执 | 10/10 |
| B396 | `research_closed_loop.py` `xuanshu_fusion.py` `feedback_bridge.py` | 支持+反证检索必做、来源独立性聚类（同域/规范 URL/转载署名/近重复文本 → 识别伪装多源）、引用锚点逐字校验、版本化只追加 Claim 档案（结论=证据平衡，非真理）；易经/紫微/奇门/六壬融合，未配置引擎如实 `unavailable`，标签 `symbolic_inspiration/unverified_hypothesis` 全层强制校验；JSON/HTML/ZIP 反馈上传：声明 SHA-256 复核、Zip-Slip/炸弹/未列文件拒绝、幂等、`tested_by` 连回 Claim | 10/10 |
| B397 | `cinematic_factory.py` | 钩子矩阵（6 模式×2）、3 秒节奏密度、Story Bible 锁、镜头导演、7.7GiB(7885MiB) 预检、Wan 2.1 ComfyUI 图（含连线校验）、真 AI 超分识别（模型节点必须在输出路径上）+720P 门禁、粤语优先双语字幕（阅读速度/重叠/音画漂移/粤拼校验）、BS.1770-4 LUFS（系数与 ITU 官方 48k 系数一致，997Hz 参考音 −3.01/−23.01）、VLM 审片回退人工、留存曲线导入 → Agent-Track 候选；`muse-film` CLI | 13/13 |
| B398 | `omega_api.py` `security_guards.py` `island_registry.py` + 6 个页面 | 主控台侧栏 7 个直达按钮；Status API `/api/v1/omega/*`（沿用 loopback+Bearer+Origin+JSON）；SSRF/DNS 重绑定、路径穿越/Zip-Slip、租约 fencing（8 线程争抢单赢家、僵尸写入被拒）、提示词注入测试；13 个孤岛全部有决策（jyutping 已接线，12 个以库形式归档并写明原因）；UI↔API 静态契约 + CSP 测试 | 11/11 |

合计新增 60 例全部通过（云端沙箱；`os.fdopen/os.fstat` 在沙箱被禁，测试时用等价垫片，真机由 `.bat` 复跑）。既有 `test_evolution_executor` 12/13（1 例因沙箱缺 `experiment_bridge` 模块，非回归）。

真机验收报告修复：3 个失败均为 Windows 下 `with sqlite3.connect()` 不关闭连接导致临时目录删除失败 → 改为 `closing()`；全仓测试 0 运行是因为 `discover -t .` 要求 `tests/__init__.py` → 改为 `discover -s tests -p test_*.py`。
