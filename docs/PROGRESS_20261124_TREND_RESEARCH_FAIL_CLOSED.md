# 2026-11-24｜热点研究 fail-closed 与创作链专项回归

## 本轮目标

继续打通 Muse 的灵感变现通道，先处理热点选题入口的真实性风险，并对已有素材、商业机会、旁白和发布准备模块做专项回归。此轮不启动自治运行、不触发 GPU、不发布内容、不写生产业务数据库。

## 代码变更

### `pipeline/muse_pipeline.py`

- 删除使用内置虚构热点样本的 fallback 路径；Bilibili 请求失败、响应异常或没有有效榜单数据时，研究流程明确停止。
- 只接受具备真实 Bilibili 视频 URL、标题、播放/投币/收藏非负整数指标和 UTC 抓取时间的榜单记录。
- 生成脚本时将平台播放/互动数据标注为“热度讯号”，不再声称“几天冲破百万”“千万人共鸣”等没有来源支持的结论。
- 脚本增加 `research_state=trend_signal_only`、`fact_check_required=true`、`publish_state=draft_only`，明确热度不等于事实核实，也不等于付费需求。

### `src/muse/media_search.py`

- 新增 `classify_commercial_license()`，保守识别 CC0 / Public Domain、CC BY、CC BY-SA、NC / ND、All Rights Reserved 与未知许可标签。
- CC BY-ND 不被笼统描述为“禁止商业使用”，而是标记 `blocked_for_derivative_video_use`；NC / ARR 等明确限制仍标记 `prohibited`。
- 下载素材 manifest 增加 `commercial_use_status`、署名要求、ShareAlike 要求和证据复核标记。
- 即使标签识别为 commercial_allowed / attribution_required，`license_status` 仍保持 `unreviewed` 且 `reuse_allowed=false`；声明标签不是版权证明，需核实原始许可证据。

### `tests/test_media_rights_policy.py`

- 最终 7 项测试已在最终 ND 细分版本上重跑，7/7 PASS（Job `2a5a892356c145b486ef00ed9cc61fbd`）。

### `tests/test_muse_pipeline_research.py`

新增 6 项测试，覆盖：
1. Bilibili API 成功响应解析真实榜单指标、视频链接与抓取时间；
2. 拒绝 API 错误响应；
3. 文案使用抓取到的实际指标并保留热度讯号状态；
4. 拒绝 fallback / 缺来源 / 缺抓取时间的记录；
5. 榜单为空时 fail-closed，且不写出伪热点文案；
6. 网络/提供方失败时 fail-closed，且不使用虚构样本。
## 实测结果

通过 Windows Runner → `scripts/muse_dev.py` → psmux → Python 3.11：

| 测试文件 | 结果 | Job |
|---|---:|---|
| `tests/test_muse_pipeline_research.py` | 6/6 PASS | `ee4420a32fc541f098bdb0de2ba26af7` |
| `tests/test_media_studio.py` | 16/16 PASS | `b919caf63f6f49c791bdfdf52c1f0987` |
| `tests/test_business_opportunities.py` | 15/15 PASS | `49f5ccb955034ea9881bcb96be3b4f21` |
| `tests/test_video_publish_package.py` | 15/15 PASS | `627cae6dd5ba4b66a3f195c9b9ec1c5b` |
| `tests/test_narration_pipeline.py` | 34/34 PASS | `ab0248c7d26a448c8284b95da6d24790` |
| **核心专项合计** | **86/86 PASS** | |

- 以上 86 项专项回归均返回 exit code 0。
- 素材工作室与最初版本的版权策略曾合并回归 26/26 PASS（Job `524853fe33de4cd6b6ea7155e6c60471`）；最终 ND 细分版本已由上述 7/7 回归覆盖。
- 最终版本 `src/muse/media_search.py` 与 `tests/test_media_rights_policy.py` 的 Python 3.11 `py_compile` 通过（Job `f541c91312f4489896fdfc50b6c038a0`）。
- 最终版权分类 smoke assertions（CC BY / CC BY-NC / CC BY-ND / CC BY URL 归一化）通过（Job `94b62e64952e44f39ef1a41b89b63746`）。
- 最近一次 `muse_dev.py inspect`：`active_job_id=null`；Runner 在线、版本/协议对齐、active_jobs=0。- `tests/test_video_task_worker.py` 本轮未执行：宿主安全检查拦截了该次调用；这不是测试失败。 

## 已验证的现有能力与边界

- 媒体素材搜索无已配置搜索后端时 fail-closed；下载资产的许可默认保持 unreviewed，不能直接视为商用授权。
- 镜头素材匹配是可解释关键词匹配；无匹配/素材不足会显式标记 fallback，不伪装成语义命中。
- 商业机会可形成 Creator Initiative 草案，并通过 Commander 人工批准门禁申请视频任务；排队本身不启动 GPU。
- 粤语 TTS/旁白、字幕与音频混合有本地测试覆盖，但正式声线听审和真实 Wan 影片验收仍是独立门槛。
- 多平台发布包仍为 draft-only；没有 OAuth/API 真实发布响应，不代表已发布。
- 没有平台分析/真实结算数据时，收入状态必须保持 not_observed，不得由播放量推算。

## 下一步

1. 补齐通用趋势来源适配器与抓取快照去重/时效策略，并把来源、反证与受众假设接入 Research Wiki。
2. 完成镜头级素材包：检索候选、视觉需求匹配、分辨率/画幅过滤、来源与许可分类；unknown / prohibited 阻断商业发布。
3. 将热点研究 → 选题评分 → 原创脚本/分镜 → 图片/视频素材 → 粤语旁白 → QA → 多平台发布草稿接入同一 Creator Initiative 与 Commander 任务链。
4. 产品内容 readiness P0 当前已在共享 `src/muse/product_builder.py` 中体现：研究目标、可测试假设/设计目标、实验/验收条件缺一时只产出 draft；ZIP 完整性与内容就绪分开验证，draft 不写 validated artifact、不把 Initiative 推进到 testing。`tests/test_product_builder.py` 含不完整内容负向测试，本轮 7/7 PASS。Builder/灵感演化 owner 交接仍需正式收敛，主协调会话没有覆盖其源码。
5. 在生产 DB 写入隔离、恢复机制和 Worker 运行状态得到确认前，不启动离线自主生产循环。
## 2026-11-24 第二轮：Creator Initiative / Commander / 自主运行回归

通过 Windows Runner → `scripts/muse_dev.py` → psmux → Python 3.11：

| 测试文件 | 结果 | Job |
|---|---:|---|
| `tests/test_business_opportunities.py` | 15/15 PASS | `85ed95a674984ababb6cdc4cf81faf4e` |
| `tests/test_pipeline_control.py` | 13/13 PASS | `822d775f3d8e4f59b225d15bd54f6f50` |
| `tests/test_video_recipe_trace.py` | 8/8 PASS | `32cdb55f78374eb6a74e307eaf5c926e` |
| `tests/test_autonomous_runtime.py` | 32/32 PASS | `c18a8822c29d4fde9148cdf995d44714` |
| `tests/test_product_builder.py` | 7/7 PASS | `bc7b7c4d34ef40f9b73e2aa7fbeae785` |
| `tests/test_inspiration_productization.py` | 12/12 PASS | `d666efb156b1420bae4ec2593e5ce341` |
| `tests/test_inspiration_evolution.py` | 17/17 PASS | `f4be7e19adb4443abc609e5ae76fbd08` |
| `tests/test_product_feedback_import.py` | 6/6 PASS | `bd9b5ba7bba34cacb552c3294f611c31` |
| **本轮新增专项合计** | **110/110 PASS** | |

- 最终版权策略回归：`tests/test_media_rights_policy.py` 7/7 PASS（Job `2a5a892356c145b486ef00ed9cc61fbd`）。

- 自主运行回归首次出现 1 项 `UnboundLocalError`；对 `autonomous_research.py` 执行 Python 3.11 编译后，单独重跑原用例 1/1 PASS，随后整文件 32/32 PASS。没有据此推断根因已经完全解释。
- 反馈导入测试夹具现已提供真实研究目标、假设与实验条件，并断言 Builder 返回 validated/testing；这修复的是旧夹具与 readiness gate 不匹配，不放宽产品门槛。
- `tests/test_product_builder.py` 的负向测试确认缺少实验的产品保持 draft、Initiative 保持 proposed，且不记录 validated artifact。
- 以上测试使用隔离临时目录；没有写生产业务数据库，也没有启动自治 Worker 或触发 GPU。

本轮没有执行真实联网热点抓取、图片下载、ComfyUI/Wan 推理、付费投放、平台发布或收款；前一轮核心专项 86/86，本轮新增专项 110/110，最终版权策略 7/7 PASS。`tests/test_video_task_worker.py` 本轮仍因宿主安全检查拦截而未执行；以上不代表整仓全绿或已实现自主盈利。

## 2026-11-24 第三轮：旧版视频 Worker 旁路 fail-closed

### 已确认的问题

- 隔离临时数据库复现：旧 `PipelineController.submit()` 创建的 `video_pipeline` ticket 为 `queued`，没有 `_approved` 标记，也没有 `approval.requested` 事件。
- 标准 `muse-worker` CLI 曾无条件注册 `PipelineTaskHandler`，可直接 subprocess 调用渲染脚本；默认审批类型此前未包含 `video_pipeline`。
- 正式 TaskQueue 以只读模式核对：`video_pipeline` 当前状态计数为 `succeeded=5`、`failed=1`，没有 queued/running/waiting_approval 遗留任务；未写入数据库。

### 本轮修改

- `src/muse/pipeline_control.py`：旧 `submit()` 对有效旧 recipe 明确拒绝，不再创建可执行 ticket；保留查询历史能力；`PipelineTaskHandler` 改为 fail-closed tombstone，不再调用 subprocess。
- `src/muse/worker.py`：`video_pipeline` 加入审批必需类型；标准 CLI 不再注册旧 handler。
- `tests/test_pipeline_control.py`：改为覆盖旧 submit 拒绝、无新 ticket、历史查询、旧 handler 拒绝执行。
- `tests/test_worker.py`：新增默认 Worker 不注册旧 handler、遗留未审批任务进入 waiting_approval 的测试。
- 四个修改文件通过 Python 3.11 `py_compile`（Job `36cda2b624b04af38974ddd18cef61a0`）。
- 修改后的定向 unittest 执行调用被宿主安全检查拦截，未启动测试 Job；这不是测试失败，也不能算测试通过。

### 尚待闭环

- `inspiration_evolution.py` 仍有调用旧 `PipelineController.submit()` 的路径；其 owner Dq 已收到迁移请求，需改为真实 Creator Initiative / Commander VideoTaskWorker handoff，缺少持久化 plan 时必须 blocked。
- `video_task_worker.py` 当前仍有本地合成 `CommanderPlan(source='video_task_worker_single_step_v1')` 的 fallback；已向其唯一 owner 3eVx 提交 P0，要求只接受真实持久化 Commander DAG，缺失时 zero grant / zero GPU dispatch。
- 本轮未启动 Worker、GPU、ComfyUI/Wan、发布或收款。旧旁路关闭不等于新版生产链已完成。
