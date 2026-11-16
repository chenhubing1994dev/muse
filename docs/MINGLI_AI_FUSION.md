# Mingli-AI（真一命理）选择性融合记录

更新时间：2026-11-15

## 决策

采用选择性融合，不复制整个上游产品，不替换 Muse 现有 Provider/本地模型，不下载 27B BF16 权重。上游项目：ZHENYI-ORG/Mingli-AI（https://github.com/ZHENYI-ORG/Mingli-AI）。

另发现独立排盘项目 `ZHENYI-ORG/Zhenyi-bazi`（MIT）：公开源码包含四柱、十神、藏干、真太阳时、起运、大运流年与神煞等确定性计算。它不是 27B 模型；其盲派口径与 Muse CalendarPolicy 不天然一致，必须逐项差分验证。

Muse 继续以共享 CalendarContext 为历法事实来源；Zhenyi-Bazi 是可选确定性排盘对照器；Mingli 是可选传统八字解释器。所有传统断语只能作为灵感假设，不能作为事实证据、科学预测或现实决策依据。

## 已实现的代码融合

- `src/muse/zhenyi_bazi_provider.py`：通过 Node 子进程直接调用审计过的 `dist/api.js`，不启动上游 HTTP Server、不开放网络端口。出生年月日时只在本机子进程中使用；返回对象剔除姓名、原始出生日期/时间、时区等字段；对 DST 重复/不存在的当地时间 fail-closed。
- `src/muse/mingli_provider.py`：标准库 HTTP 适配器，只允许 localhost/127.0.0.1/::1 的 loopback HTTP，禁用代理，拒绝远程地址、URL 凭证和重定向；请求/响应有限额、超时（默认 185 秒，匹配上游 180 秒推理超时）。提供 `list_models()` / `model_available()` 探测 `/v1/models`。默认 API 地址 `http://127.0.0.1:8787`，对应 Mingli README 的 Web/API 后端。
- `InspirationEngine.interpret_bazi_with_mingli(..., bazi_provider=..., gender=...)`：可先执行 Zhenyi-Bazi，再与 Muse 四柱比较；只有完全一致才把精简命盘交给 Mingli。发生差异则 fail-closed，不允许任何一方静默覆盖另一方。
- Mingli 可接收精简 Zhenyi chart（十神、藏干、五行、纳音、日主、大运等），但不会接收原始出生日期/钟点/姓名/地理位置。另实现 `follow_up_bazi()`，按同一会话完整重放“命盘+首问→assistant 首答→选项追问”的两轮协议。
- `InspirationEngine.record_mingli_inspiration()` 仅在显式调用时写入 Research Wiki：模型输出登记为 source，解释登记为 idea，并以 inspired_by 关联；不会创建事实 claim、supports/refutes 边或写入 User Track。
- 原始出生记录不进入 Wiki；只保留 CalendarContext 指纹、命盘结构与来源链。

## 上游审计与真实运行

### Mingli-AI 服务端审计

- Mingli-AI 当前仓库 HEAD：`0e0539da34e4d2f82a81b338e7c0ea0cc809b9a5`，package 2.5.6；包含自己的 `computePaipan`、`OpenAIChat` 与 `ZhenyiAnalyzer`。它的 OpenAI 兼容服务会从最近的 user message 解析命盘，因此 Muse 请求同时发送上游文档规定的自然语言格式“`四柱为甲子 甲戌 戊寅 庚申`”和结构化盘面；显式性别也会传入，不猜性别。
- 上游 `start-model-service.sh` 默认模型端口 8000，`start-web.sh` 默认 Web 服务监听 `0.0.0.0:8787`。Muse 若启动它，必须覆盖 `HOST=127.0.0.1`，避免无意开放局域网访问。
- Mingli 自身仓库包含更新到 2.5.6 的排盘实现；Muse 当前固定的独立 Zhenyi-Bazi adapter 是 2.0.2。两者版本不一致，因此现阶段只以四柱一致作为启用 Mingli 的门槛，不声称大运、起运、判盘细节也完全一致；下一步要对比 2.5.6 规则后再决定是否统一版本。

- Zhenyi-Bazi 固定在 package 2.0.2、commit `383a726437a81db2b87eed517ef5e6ccd30b7bd4`；源码 MIT。审计保留 `LICENSE` 与 `THIRD_PARTY_NOTICES.md`。没有执行 npm install/build 或上游脚本。
- 已在真实上游代码上用合成案例完成本地 Node 子进程实测：庚午、壬午、辛亥、甲午四柱与 Muse fixture 一致，盲派判盘状态 completed；返回对象不含出生时间/日期字段。
- 上游回归：基础 10/10、扩展 52/52、判盘 30/30、来源一致性 8/8 均通过。
- 此前 Muse Mingli/Zhenyi/四传统专项回归记录为 68/68 通过；其中包含 60 个跨 2020–2024 年非节气交界日期、8 个午夜/子时边界实机差分，以及 DST 重复/不存在时间拒绝。本轮 Runner 环境缺少 Windows IANA tzdata，无法重跑完整回归，不能把本轮状态写成全量通过。
- Mingli 真实 API 当前不可用（本机 8787 未监听），真实模型尚未加载。上游要求 ms-swift 部署 Qwen3.8-27B 合并 BF16 权重（约 54GB），当前 AMD 12GB VRAM / 32GB RAM 不适合直接部署。公开可检索到的 Qwen3.8 GGUF 是基础模型量化，不是 Mingli 微调权重，不能冒充真一模型；未找到可验证的 Mingli 专属 GGUF，因此暂不下载替代权重。

## 评测原则

上游维护者指出其官方评测流程是先排盘再推演：同一会话先发送核验后的命盘与目标问题，再发送选项追问。直接把出生参数交给模型做单轮选择题，不等价于该流程。具体协议见 `docs/MINGLI_BENCHMARK_PROTOCOL.md`。

## 下一步验收

1. 完成 Zhenyi-Bazi 边界案例集：立春、节令、夜子/早子、时区/DST、真太阳时跨日、未知时间。
2. 逐项比对 Muse CalendarContext 与真一盲派排盘口径，保留差异及规则说明。
3. 确认 Mingli 2.5.6 的模型权重与运行时方案；若启动 Web 服务，强制绑定 127.0.0.1，再验证 `/v1/models` 和 chat/completions 的真实请求响应。
4. 用至少 30 个有授权案例按上游两轮协议进行可复现评测。
5. 将解释拆成带流派、规则来源、替代解释和不确定性的 idea；只有独立证据才可进入事实 claim 审查。

## 2026-11-15 继续开发记录

- 新增真实上游 Node 实测边界向量：00:00、00:59、01:00、02:59、03:00、22:59、23:00、23:59 共 8 个时间点，午夜换日与早/夜子时四柱全部与 Muse 算法一致；新增测试 `test_real_upstream_engine_matches_midnight_and_zi_hour_boundaries`。
- Mingli/Zhenyi/灵感/紫微/奇门/大六壬专项回归更新为 68/68 通过，compileall 通过。
- Release API 清单显示 54 个文件、合计 51.78 GiB；其中模型权重约 51.75 GiB。当前没有下载 BF16 权重，D 盘 253.9 GiB 空间足够保存文件，但 32GB RAM/12GB VRAM 不足以直接按 BF16 部署。
- 本机 llama.cpp 0.9.0-dev（commit `240d68fcf`）Vulkan 可识别 RX 6750 GRE 12GB；Mingli API 8787 仍未监听。基础 Qwen3.8-27B GGUF 与 Mingli 微调权重不是同一模型，不能互相替代。
- GitHub 镜像正式小文件完整性测试：ghfast.top 与 gh-proxy.com 下载 `merges.txt` 后均与上游 manifest 的 3,353,259 bytes / SHA-256 一致；ghproxy.net 90 秒超时。下载脚本已加入按镜像断点续传和 preflight，尚未开始 51.78 GiB 全量权重下载。
- 当前最重要的阻塞仍是真一微调模型在该 AMD/Windows 设备上的可运行量化方案；不得在没有模型专属权重验证的情况下拿基础模型顶替。

## 2026-11-15 真太阳时融合验证补充

- 新增 `test_real_upstream_engine_matches_muse_with_true_solar_time_enabled`：以 Muse `CelestialCalendarProvider` 生成真太阳时 CalendarContext，再调用真实 Zhenyi-Bazi Node 引擎，逐柱比较并确认 `time_basis=true_solar`。向量覆盖上海、成都、乌鲁木齐三个经度。
- 初次运行因 Runner 缺少 IANA tzdata 而 skip；随后将 `tzdata>=2024.1` 与 `celestial-calendar==0.8.0` 安装到项目内隔离目录 `.muse-test-deps`（未修改全局 Python 环境），三地点真实差分测试 3/3 通过。
- 在该隔离依赖路径下，Mingli/Zhenyi/灵感/紫微/奇门/大六壬定向回归 69/69 通过；全仓库 `python -m unittest discover -s tests -v` 共 1212 项，修复 GPU lease 零等待首次探测问题后，1212/1212 通过。
- 真一 `TrueSolarTimeCalculator` 使用 IANA 时区解析民用时间、经度修正与逐日均时差表；Muse 使用 `celestial-calendar.apparent_solar_time`。三地非边界日期结果已一致，但跨节气边界和跨时辰/跨日仍需专门差分，不能据此宣称所有时刻逐秒一致。
