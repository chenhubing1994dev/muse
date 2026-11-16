# Muse × Mingli-AI × Zhenyi-Bazi 实施记录

日期：2026-11-15

## 当前可验证成果

### 上游源码审计

- 项目：ZHENYI-ORG/Zhenyi-bazi
- package version：2.0.2
- commit：383a726437a81db2b87eed517ef5e6ccd30b7bd4
- license：MIT（上游 LICENSE 已保留）
- Node：24.20.0；npm：11.19.0
- runtime npm dependencies：0；仅 TypeScript devDependency
- 没有执行 npm install、npm build 或任何上游脚本；直接审计预构建 dist API 与源代码。
- Mingli-AI 仓库另行审计到 HEAD `0e0539da34e4d2f82a81b338e7c0ea0cc809b9a5`、package 2.5.6；服务端含 computePaipan + OpenAI-compatible chat parser。其启动脚本默认 Web host 为 0.0.0.0，Muse 必须覆盖为 127.0.0.1。

### 实机验证

- 上游 node dist/server.js 的 API 端点已在 loopback 127.0.0.1:8788 运行 smoke test，使用合成命例调用 POST /api/paipan，返回完整四柱、十神、藏干、12 步大运与 blind_judgment。
- Muse Provider 随后改为更安全的 node -e 子进程调用 dist/api.js，不启动上游 Server、不开放端口；本机合成命例得到 庚午、壬午、辛亥、甲午，与 Muse CalendarContext fixture 四柱一致，盲派判盘 completed。
- 返回 chart 对象剔除姓名、出生日期、民用出生时间、真太阳时具体时间、时区与出生年份等字段。
- 四柱差异不隐藏：结果保留 Muse/Zhenyi 两套柱与 differences；Mingli 推演接线遇到不一致会 fail-closed。

### Muse 代码

- src/muse/zhenyi_bazi_provider.py：隔离式本地确定性排盘 Provider。
- src/muse/mingli_provider.py：loopback-only OpenAI-compatible Mingli Provider，支持精简 Zhenyi chart、/v1/models 探测与两轮完整会话重放；默认超时 185 秒。
- src/muse/inspiration.py：interpret_bazi_with_mingli(..., bazi_provider=..., gender=...) 先差分校验再解释；record_mingli_inspiration 只保存 source/idea。
- Tests：tests/test_zhenyi_bazi_provider.py、tests/test_mingli_provider.py、tests/test_mingli_integration.py。
- Docs：docs/MINGLI_AI_FUSION.md、docs/MINGLI_BENCHMARK_PROTOCOL.md。

## 测试结果

- Zhenyi-Bazi upstream regression：10/10。
- Zhenyi-Bazi extended regression：52/52。
- Zhenyi-Bazi blind-judgment regression：30/30。
- Zhenyi-Bazi source consistency benchmark：8/8。
- Muse Mingli + Zhenyi + Inspiration + Ziwei + Qimen + Kinliuren regression：67/67；包含 60 个 2020–2024 年月中非边界日真实 Node 差分，60/60 四柱一致；DST 重复/不存在当地时间拒绝测试通过。
- Python compileall：通过。

## 未完成，不可标记最终完工

- Mingli Web/API 服务 127.0.0.1:8787 当前未运行；已探测确认不可用。
- Mingli 27B 模型未下载、未加载；真实模型回复、推理延迟、质量和两轮评测尚未实测。
- Zhenyi 与 Muse 的全量历法边界差分案例尚未建立；当前真实交叉验证只覆盖合成基准例。
- 主 docs/PROGRESS.md 混用 LF/CRLF，安全编辑器拒绝追加；本次变更完整记录在本文件与 docs/MINGLI_AI_FUSION.md。

## 下一步

1. 把上游 Zhenyi-Bazi 边界规则逐项映射到 CalendarPolicy，先做 30+ 固定可重放案例。
2. 为立春、十二节令、夜子/早子、真太阳时跨日、DST 重复/不存在当地时间建立差分。
3. 确认 Mingli 真实模型运行时与 API 启动方式，保持 loopback-only；先用小型/可运行量化方案评估，不下载 27B BF16。
4. 真实 API 连接后验证 /v1/models、单轮命盘分析、同一会话第二轮选项追问。
5. 将测试结果与失败案例回写 Research Wiki 和进度文档，再考虑 Commander 自主调度。

## 2026-11-15 后续实测补充

- 增加 8 个真实 Node 边界差分：00:00、00:59、01:00、02:59、03:00、22:59、23:00、23:59；8/8 与 Muse 四柱结果一致，23:00–23:59 的夜子时与 00:00–00:59 的早子时标签符合上游实现。
- Muse 专项回归 68/68 通过，Python compileall 通过。
- GitHub 镜像完整文件测试：ghfast.top、gh-proxy.com 完整下载 `merges.txt`，3,353,259 bytes 与上游 SHA-256 一致；ghproxy.net 在 90 秒内超时。64 KiB 三路线探测仍为 7/9。
- 下载器现在支持按镜像分开 partial、同镜像续传、缓存 API manifest、空间预检；54 个 Release 资产合计 51.78 GiB，preflight 成功。尚未开始全量权重下载。
- 实机硬件：RX 6750 GRE 12GB（Vulkan 可识别）、RAM 32GB、D 盘空闲 253.9 GiB；llama.cpp 0.9.0-dev commit `240d68fcf`。Mingli API 8787 未监听。

## 真太阳时差分验证（本轮）

- 新增真实引擎差分测试 `test_real_upstream_engine_matches_muse_with_true_solar_time_enabled`，用 Muse `CelestialCalendarProvider` 的 CalendarContext 对照真实 Zhenyi-Bazi Node 子进程，覆盖上海、成都、乌鲁木齐经度，检查四柱与 `time_basis=true_solar`。
- 初次执行因 Runner 缺少 IANA tzdata 而 skip；随后将 `tzdata>=2024.1` 与 `celestial-calendar==0.8.0` 安装到项目隔离目录 `.muse-test-deps`，未修改全局 Python 环境。
- 三地点真实引擎差分（上海、成都、乌鲁木齐）3/3 通过，验证 Muse CalendarContext 与真实 Zhenyi-Bazi Node 引擎四柱一致且 `time_basis=true_solar`。
- Mingli/Zhenyi/灵感/紫微/奇门/大六壬定向回归 69/69 通过；全仓库 `python -m unittest discover -s tests -v` 1212/1212 通过（修复 GPU lease 零等待首次探测问题后复跑）。compileall 通过。
- 两侧算法仍需边界验证：真一 `TrueSolarTimeCalculator` 使用 IANA 时区解析、经度修正与逐日均时差表；Muse 调用 `celestial_calendar.apparent_solar_time`。本轮三地向量均为非边界日期，不能推断跨节气、跨时辰、跨日时刻也完全一致。
- 重要判断：Release 中是 51.75 GiB 的 Mingli merged-BF16 权重。公开基础 Qwen3.8-27B GGUF 并非 Mingli 微调版；不允许以基础模型替代。下一阶段先验证模型专属量化路径和推理兼容性，再决定是否下载全量 BF16。
