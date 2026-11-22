# Muse 共享历法上下文与传统排盘 Provider

更新时间：2026-11-10  
状态：共享历法与四柱底座已实现；Ziwei、QiMen、LiuRen 三个独立排盘引擎已通过隔离环境端到端样例；尚未完成所有流派、历史边界和独立黄金盘验证。

## 目标

易经、紫微、奇门、大六壬不能各自猜测时区、节气、闰月和换日规则。Muse 使用显式 CalendarContext，再由每个体系声明需要哪些字段。缺字段返回 incomplete；目标引擎无法表达某个历法差异时返回 unsupported；不同引擎算出的历法字段不一致时拒绝输出盘局。

## CalendarPolicy

- timezone：必需 IANA 时区。
- year_boundary：lichun 或 lunar_new_year。
- month_boundary：jieqi 或 lunar_month。
- day_rollover：midnight 或 zi_hour_23。
- true_solar_time：是否要求真太阳时；启用时，时支/日柱使用真太阳时钟。
- lunar_date_basis：civil_local 或 apparent_solar，独立控制农历日期采用的日期基准。

## CalendarContext

- aware instant、地理经纬度、当前节气。
- 农历年/月/日/闰月标记（必须完整成组）。
- 年/月/日/时干支与时支。
- 真太阳时当地表观时间。
- Provider 名称/版本、来源引用。
- 稳定 SHA-256 fingerprint，覆盖输入、口径、来源与 Provider 版本。
- 对象只作为一次计算上下文；不自动保存出生资料。

## 历算 Provider

CelestialCalendarProvider 是可选依赖适配器，不安装到 Muse 默认 Python 环境：

- celestial-calendar 0.8.0 提供 ALGO3 公农历转换、24 节气 UT1 时刻、apparent_solar_time。
- 使用其 chart_snapshot 的 UTC→UT1 时间桥；天文快照中的星体/宫位结果全部丢弃。
- UTC 时间范围限制为 1972–2099；不在支持范围内直接返回 unsupported。
- Muse sexagenary v1 计算年/月/日/时柱：年界和月界由 CalendarPolicy 控制；日柱根据 day_rollover；时柱根据日干和时支。
- 2024-06-15 参考向量：甲辰年、庚午月、庚戌日；11:00–12:59 为壬午时。

## 已接入的独立排盘引擎

| 体系/Provider | 适配输入与实际能力 | 限制与 fail-closed 条件 |
|---|---|---|
| 紫微斗数 ziwei 0.4.1 | 公历当地钟点、IANA 时区、可选真太阳时经度、农历、时支、年界；生成十二宫、30 星、四化、十二大限 | Alpha；必须显式给出性别、闰月归属、四化表；与 Muse 农历/时支/年干支不一致即拒绝；原始出生记录不返回、不持久化 |
| 奇门遁甲 qimen-rs 0.2.1 | 当地民用日期时间、UTC offset、换日口径；生成时家拆补转盘九宫、阴阳遁/局数、值符值使、八门九星八神及空亡 | 仅时家拆补转盘；不支持真太阳时、置闰法、飞盘及日/月/年家；必须匹配 Muse 四柱、农历日期和节气；wheel 尚未源码审计 |
| 大六壬 kinliuren 0.1.2.9 | 节气、农历月标签、日干支、时干支四参数；实际返回四课、三传、天地盘、天将与课体 | 无测试目录；README Quickstart 参数过时；不支持闰月输入；尚缺独立黄金盘例 |

### 运行边界

- 三个引擎均位于独立 D 盘 venv，Muse 默认 Python 环境不安装第三方依赖。
- 调用使用固定桥接代码、参数列表、shell=False、超时限制和版本固定；结果经过 JSON 及结构校验。
- QiMen 和 Ziwei 会重新计算日历并与 CalendarContext 对照；任何差异均拒绝返回盘局。
- Ziwei 的性别、闰月归属、四化表必须由用户显式提供；不推断、不保存原始出生记录。
- 三套输出均标记 traditional_calculation、is_evidence=false、interpretation_status=not_generated。计算结构不是现实证据，也不自动形成吉凶断语。

## InspirationEngine 接线

- prepare_metaphysics_calendar_context()：只做字段准备与 ready/incomplete/unsupported 路由。
- calculate_ziwei_chart()、calculate_qimen_chart()、calculate_kinliuren_chart()：实际调用各自隔离引擎。
- calculate_core_metaphysics_charts()：逐个执行三套引擎；某套不可用/口径不兼容时保留其状态，不阻止其他引擎完成。
- build_metaphysics_chart_dossier()：校验同一历法 fingerprint 下的 2–3 套实际排盘，可附加独立 Yi Jing cast；提取结构特征、生成可证伪研究问题和 chart hashes。Yi Jing 与出生历法无关，单独保留其 input provenance；不作体系共识、不生成断语、不自动持久化。
- 易经三钱起卦与六十四卦结构分析独立运行，不依赖出生资料。

## 测试

- tests/test_calendar_context.py：6/6。
- tests/test_celestial_calendar_provider.py：7/7。
- tests/test_sexagenary.py：7/7。
- tests/test_kinliuren_provider.py：6/6（含隔离环境真实样例）。
- tests/test_qimen_provider.py：7/7（含隔离环境真实样例及历法差异拒绝）。
- tests/test_ziwei_provider.py：6/6（含民用钟与真太阳时隔离环境真实样例）。
- tests/test_inspiration.py：31/31（含三引擎真实调度 + Yi Jing 的四体系 dossier 端到端校验）。
- 早期历史验证：Python 3.11 unittest 916/916 通过（71.371 秒；当时尚未加入 1 个真太阳时 Ziwei 测试和 2 个 dossier 测试）。新增 Ziwei 专项 6/6、Inspiration 专项 31/31、compileall 和三个隔离 venv 的 pip check 均通过；完整 919 项重跑当时曾被宿主安全检查拦截。

- 2026-11-23 回归更新：`scripts/validate_full_suite.py` 会在测试期间复用现有 `artifacts/ziwei-poc-venv` 隔离环境中的 site-packages（Windows/Linux venv 路径均兼容），不向 Muse 默认 Python 环境安装可选依赖。此前因缺少 `celestial-calendar==0.8.0` 被跳过的真太阳时真实引擎差分测试现已执行并通过；全仓 Python 3.11 回归 **2,390 tests / 0 failures / 0 errors / 0 skipped**（331.349s）。

## 尚未完成

- 1972/2099 边界、DST gap/fold、跨日真太阳时、闰月、多个历史盘例的独立差分。
- Kinliuren 独立黄金盘与规则出处交叉校验。
- QiMen wheel 对应源码的完整审计、不同置闰/拆补/飞盘流派实现及独立历史盘对照。
- Ziwei 0.4.1 Alpha 的独立历史盘差分、更多闰月/子时/流派边界测试；版本化 chart snapshot schema 尚未建立。
- 四体系解释层、证据/反证与 Research Wiki 的端到端闭环；当前三个排盘器只返回结构，不生成断语。

因此当前已打通三套可运行的独立排盘引擎与共享历法路由，但不能称为四套术数全部完工，也不能把单一流派实现等同整个体系。
