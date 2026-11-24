# Muse 奇门遁甲引擎隔离 POC

更新时间：2026-11-10  
状态：qimen-rs 0.2.1 已在独立 venv 运行，并通过 Muse CalendarContext 交叉校验；源码级供应链审计和多流派覆盖尚未完成。

## 1. 候选与供应链

- 包：PyPI qimen-rs==0.2.1，PyPI metadata 标示 MIT，无运行时依赖。
- 发行 wheel：qimen_rs-0.2.1-cp310-abi3-win_amd64.whl，SHA-256：
  1a95ba7e086e46c7b8fb6c4f0726f390547853cc409ad900bef0489d443bf920，与 PyPI 发布页显示值相符。
- 规范安装位置：`D:\MuseVenvs\qimen-poc-venv`；`artifacts/qimen-poc-venv` 为兼容 junction，未安装到 Muse 主 Python 环境；pip check：No broken requirements found。
- PyPI 页面未提供 build provenance。源代码包下载时，pip 构建隔离阶段因宿主代理端口解析错误失败；未因此运行构建脚本。当前只验证了固定 wheel 的哈希、metadata 和运行行为，不宣称完成源码审计。

## 2. 实际计算样例

输入：2024-06-15 12:30，Asia/Shanghai，UTC+08:00，民用钟、午夜换日。

引擎输出：
- 四柱：甲辰、庚午、庚戌、壬午。
- 农历：甲辰年五月初十。
- 节气：芒种。
- 阳遁六局。
- 方法：shi_jia_chai_bu_zhuan_pan。
- 返回九宫结构、值符/值使、八门、九星、八神、天马与空亡等字段。

四柱、农历日期和节气与 Muse CalendarContext/历算结果完全一致。这个向量验证接口及历法对齐，不是独立历史奇门黄金盘，也不证明断局准确性。

## 3. Muse Adapter

- src/muse/qimen_provider.py：使用固定 Python bridge、JSON argv、shell=False、15 秒超时。
- 仅接受 Lichun 年界、Jie 月界、civil_local 农历基准和民用钟；true solar time / apparent_solar、微秒精度及其他年/月界直接 unsupported。
- 重新核对引擎返回的年/月/日/时干支、农历年月日/闰月、节气与九宫数量；任何差异均拒绝返回结果。
- InspirationEngine.calculate_qimen_chart() 提供单独调用；calculate_core_metaphysics_charts() 与紫微、大六壬独立执行。
- 输出标记为传统计算、非证据、未生成解释；不会自动保存用户资料。

## 4. 明确限制

- 目前只接入时家拆补转盘，不覆盖置闰、飞盘、日家、月家、年家及其他门派规则。
- 不支持真太阳时；不能静默把真太阳时输入转换为民用时间。
- 只有 wheel hash 和行为校验，没有完整源码审计和独立历史盘例对照。
- 不得把 qimen-rs 结果代表所有奇门遁甲流派，也不得把盘面当成现实因果或预测证据。

## 5. 测试

- tests/test_qimen_provider.py：7/7，包含隔离环境真实样例、输入口径、结果历法差异、未知版本、畸形九宫、超时/退出码和不支持精度。
- tests/test_inspiration.py：31/31，含 QiMen 单引擎及三引擎统一调度端到端测试。
- 最近一次完整 Muse Python 3.11 回归：916/916 通过（在新增真太阳时和 chart dossier 测试之前）；最新 Inspiration 专项 31/31、compileall 通过；完整 919 项重跑被宿主安全检查拦截。
