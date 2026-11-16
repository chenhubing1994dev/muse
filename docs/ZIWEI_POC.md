# Muse 紫微斗数引擎隔离 POC

日期：2026-11-10  
状态：已完成隔离安装、合成命盘冒烟测试和 Research Wiki 目录同步；尚未作为 Muse 默认运行时依赖。

## 1. 发行版与供应链

- PyPI：ziwei 0.4.1，MIT，Development Status: Alpha，要求 Python >=3.11,<3.15。
- 发布说明列出运行时精确依赖 celestial-calendar==0.8.0、tzdata==2026.5；Windows 目标为 AMD64。
- 已下载 sdist 与 wheel 到 artifacts/ziwei-audit。sdist SHA-256：
  871154701c22586a42ff35782e29a8ed45a706f62b707020041042170424c01e，与 PyPI 页面显示的发布 provenance digest 一致。
- ziwei wheel SHA-256：
  dab8b0d8eb58da8760c520ea81e1f4635ce275bf3197960d065c116fe1a3f492。
- 已下载 celestial-calendar 0.8.0 的 win_amd64 wheel 与 tzdata 2026.5 wheel；使用它们在 artifacts/ziwei-poc-venv 建立独立 Python 3.11 venv。未安装到 Muse 主 Python 环境。
- 独立环境 pip check：No broken requirements found。
- 只做了初步静态检查，不是完整安全审计；未运行未知安装脚本，也未将包源码复制进 Muse。

## 2. 合成命盘冒烟测试

使用包 README 中的合成输入：2024 年农历正月初一、子时、女命；显式指定闰月归属、子时换日、换年口径和四化表。

- create_chart() 返回 ZiweiChart。
- render_text() 成功生成 28 行盘面。
- 首行摘要：火六局 · 大限逆行 · 虚岁首尾包含。
- 这是公开文档中的合成样例，不是用户出生资料，也不构成对命理预测准确性的验证。

## 3. Research Wiki 接线

新增 src/muse/ziwei_adapter.py，并在 InspirationEngine 暴露 sync_ziwei_knowledge_catalog()。上游包采用 lazy import；主环境未安装可选包时不会在导入 Muse 灵感模块时失败。

从实际 ziwei 0.4.1 catalog 同步至隔离 ResearchKnowledgeStore 的结果：

| 上游记录 | 数量 |
|---|---:|
| Sources | 25 |
| Citations | 92 |
| Concepts | 50 |
| Rule variants | 48 |
| Knowledge entries | 57 |
| 合计 Research Wiki nodes | 273 |
| Research Wiki edges | 558 |
| Events | 831 |

数据治理规则：

- 上游 MIT 只说明代码许可证；每条原始来源的文本权利仍按其 source.rights 分别记录，不把 MIT 自动套用到引文内容。
- 不复制 citation.quote 正文；只保存 locator、source ID、quote SHA-256 和长度。
- 来源 edition、lineage、provenance、claims、limits、rights、status、checked_on 均作为 metadata 保存。
- 概念与规则变体进入 idea 节点；目录条目进入 proposed claim 节点，并标记 traditional_framework_statement_not_empirical、requires_independent_review、not_prediction。
- 所有关系使用 relates，不自动建立 supports/refutes，不把传统文本陈述当作现实证据。
- 重复同步使用稳定 external_ref 与指纹，边幂等；已有非 proposed 的 claim 生命周期状态不会被目录刷新覆盖。
- 当前同步是逐节点幂等写入，不是跨所有节点的原子事务；中断后可重试补齐。

## 4. Muse 集成边界

- 默认 Muse 环境不依赖 ziwei；只有在隔离环境/明确安装可选依赖后，才可调用上游计算器。
- Research Wiki 适配器可以接收显式 catalog 对象，因此测试和离线资料导入不必安装第三方包。
- 目录中的 verified 是上游对其所列版本/录文的状态，不表示术数主张经科学验证。
- 未导入出生资料；不自动生成吉凶断语、不自动写 User Track、不自动触发代码改动。
- 该版本当前不提供命盘 JSON 保存/恢复与旧盘重算接口；Muse 若需要长期保存，应另建版本化 snapshot schema。

## 5. 测试与下一步

- tests/test_ziwei_adapter.py：3/3。
- tests/test_inspiration.py：18/18，包含 InspirationEngine 委托接线测试。
- 实际上游目录通过 InspirationEngine 同步：25/92/50/48/57；隔离库 273 nodes、558 edges；未导入 quote body。
- 完整 Muse Python 3.11 unittest：867 项运行，0 failures、0 errors（69.661 秒）；compileall 成功。

下一步：
1. 先对上游 0.4.1 做黄金命盘与历法边界差分，不将 alpha 直接提升为 Muse 必需依赖。
2. 把其 25 个来源的 rights/status/edition 映射到 Muse 的 source-quality 评价，并对 unverified 条目保持低可见度。
3. 设计版本化 Zi Wei chart snapshot schema，记录原始输入、口径、历法版本、规则 variant IDs 和输出 hash；出生资料默认最小化保存。
4. 与第二个独立排盘实现或可靠历史盘例交叉核对，再考虑接入自主研究调度器。

## 6. Muse 真实排盘 Provider 接入（2026-11-10）

- 新增 src/muse/ziwei_provider.py，仍调用独立 artifacts/ziwei-poc-venv，没有把 Alpha 依赖安装到 Muse 主环境。
- InspirationEngine.calculate_ziwei_chart() 已接通实际 create_chart()，并可与 QiMen、大六壬通过 calculate_core_metaphysics_charts() 分别执行。
- 必须显式提供性别（男/女）、闰月归属（current_month/next_month/split_month）和四化表（zhongyi999/wangtingzhi_lecture）；不猜测或自动填充。
- Provider 使用 GregorianBirthRecord + IANA 时区 + CalendarPolicy，选择 ALGO3；核对农历年月日/闰月、时支、立春年干支、真太阳时路径、子时换日和 celestial-calendar 版本。
- 输出结构含十二宫、30 星位、四化、大限和文本盘面；不返回原始出生日期/钟点，不持久化出生记录，不自动生成吉凶断语。
- 2024-06-15 上海合成样例真实调用通过：甲辰年、农历五月初十、午时；十二宫/30 星/十二大限结构完整。此为上游计算冒烟与历法对齐，不是独立历史盘黄金验证。
- tests/test_ziwei_provider.py：6/6，覆盖民用钟和真太阳时路径；tests/test_inspiration.py：31/31；最近一次完整 Muse Python 3.11 回归 916/916 通过（在新增真太阳时和 chart dossier 测试之前）；新增 Ziwei 专项 6/6、Inspiration 专项 31/31 通过，完整 919 项重跑被宿主安全检查拦截。
- 上游仍为 Alpha；完整独立历史盘、闰月、DST、子时边界差分未完成。原始出生资料不进入 User Track 或通用个人记忆。

