# Muse 大六壬引擎隔离 POC

更新时间：2026-11-10  
状态：kinliuren 0.1.2.9 已在独立 venv 实际运行，并通过 InspirationEngine 调用；独立黄金盘与闰月规则尚未解决。

## 1. 候选与源码审查

- 包：kinliuren==0.1.2.9，MIT，无运行时依赖。
- 固定 wheel 与 sdist 哈希已记录在 docs/METAPHYSICS_ENGINE_RESEARCH.md。
- 实际源码入口：Liuren(jieqi, cmonth, daygangzhi, hourgangzhi)，必须传四个参数。PyPI README Quickstart 仍显示过时的三参数示例。
- 上游没有自动化 tests 目录或独立黄金盘；包不负责公历/农历、时区和节气计算。
- 闰月没有参数；Muse 明确拒绝闰月上下文，不会擅自折算。

## 2. Muse Adapter

- src/muse/kinliuren_provider.py：固定 bridge、参数列表、shell=False、15 秒超时、版本固定。
- 输入来自共享 CalendarContext 的节气、农历月、日干支、时干支。
- 结果校验农历月、节气、日时干支、四课、三传、天地盘和天将结构。
- InspirationEngine.calculate_kinliuren_chart() 可单独调用；统一调度器可以与 Ziwei/QiMen 独立执行。
- 返回结果标记为传统计算、非证据、未生成解释；不会自动写入 User Track。

## 3. 实际运行样例

2024-06-15 上海参考历法向量：芒种、农历五月、庚戌日、壬午时。隔离引擎成功返回课体、四课、三传、天地盘和天将。该样例是实际运行冒烟，不是独立人工校验的黄金课例；不能据此宣称断课规则已被外部验证。

## 4. 测试

- tests/test_kinliuren_provider.py：6/6，含真实隔离环境调用、四参数检查、闰月拒绝、版本/结构校验、超时和退出码。
- tests/test_inspiration.py：31/31，含大六壬单引擎及三引擎统一调度端到端测试。
- 最近一次完整 Muse Python 3.11 回归：916/916 通过（在新增真太阳时和 chart dossier 测试之前）；最新 Inspiration 专项 31/31、compileall 通过；完整 919 项重跑被宿主安全检查拦截。
