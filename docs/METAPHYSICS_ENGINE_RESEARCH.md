# 玄学引擎开源组件初步尽调

更新时间：2026-11-10  
状态：三个传统排盘引擎已在隔离环境实际运行并接入 Muse Provider；仍有 Alpha、无 build provenance、缺独立黄金盘等限制，不代表四套体系或所有流派全部完成。

## 结论摘要

优先采用“多个小型、可复算、来源清楚的引擎 + Muse 统一解释/研究层”，不要找一个号称全能的玄学项目直接替换 Muse。

| 体系 | 候选 | 初步判断 | 处理建议 |
|---|---|---|---|
| 易经/周易文本研究 | ProfesseurHaipeng/iching-research-skill | MIT；可见 seed 的三钱起卦、六十四卦目录、来源注册表和研究模板；明确区分输入、计算、版本文本、条件式反思 | 优先吸收其来源治理和可复算流程；逐文件核查后再移植 |
| 六爻子引擎 | yaomancy/liuyao-engine | Apache-2.0；Python >=3.11；纯规则、无网络，宣称与 najia/yigram-najia-rules 交叉核对，历法对照 sxtwl/lunar-python | 可作为“六爻/纳甲”独立子模块候选；不能代表全部易经体系 |
| 紫微斗数 | PyPI ziwei 0.4.1 | Python 3.11–3.14；Windows amd64；具名历法、闰月、子时、换年口径、十二宫、30星、四化和大限 | 已接入可选 ZiweiProvider，隔离 venv 实际排盘；上游 Alpha，须继续独立历史盘差分 |
| 紫微斗数参考实现 | JUTZUGC/ziwei-doushu | 代码 MIT；样例数据 CC BY 4.0；古籍公版；部分现代讲座引文 CC BY-NC-SA；解释判断库仍闭源；样例数据解压约 56.6GB | 仅参考算法/测试思路；不要搬运大数据集或 NC-SA 引文 |
| 大六壬 | kentang2017/kinliuren 0.1.2.9 | MIT；无运行时依赖；四参数入口；无测试目录；不支持闰月 | 已接入可选 KinliurenProvider，隔离 venv 实际运行；仍缺独立黄金课例 |
| 奇门遁甲 | SpenserCai/qimen-rs 0.2.1 | MIT metadata；Rust wheel；无运行时依赖；PyPI 无 build provenance，源码包下载受宿主代理错误阻断 | 已接入可选 QimenRsProvider，隔离 venv 实际运行；仅时家拆补转盘，wheel 尚未源码审计 |
| 奇门遁甲（备选） | dglijin-oss/qimen-dunjia | MIT；实际脚本 scripts/qimen_pan_v2.py 约 1214 行；无测试目录；README 样例节气与日期不符 | 暂不接入；样例错误和历法表缺乏验证 |

## 具体审查发现

### 1. 易经/周易

- `iching-research-skill` 将用户问题与起卦输入、可复算六爻计算、收到的文本/版本/注释传统、条件式反思分开。
- 起卦工具使用可见 seed 和明确方法，输出爻值、变爻、上下卦、本卦/之卦；技能本身强调不把《周易》包装成经验预测模型。
- 适合优先移植：来源登记、版本标注、透明起卦参数、分析模板、解释边界。
- `liuyao-engine` 是六爻专门计算器，不应把它误称为所有易经方法的完整实现；它可提供纳甲、六亲、六神、世应等可复算结构。

### 2. 紫微斗数

- PyPI `ziwei` 页面明确列出 Python 3.11–3.14 和 Windows amd64 支持；运行时依赖精确固定的 `celestial-calendar==0.8.0` 与 `tzdata==2026.5`，安装前必须在独立 venv 检查 wheel、依赖冲突和 `pip check`。
- 其配置显式处理闰月归属、子时换日、换年边界；还记录时间解析、历法版本和派生结果，符合 Muse 的规则溯源目标。
- 页面说明目前不提供命盘 JSON 保存/恢复和旧盘重算接口；若 Muse 需要长期保存命盘，必须自行设计稳定的版本化 chart snapshot schema。
- Muse 已接入可选 ZiweiProvider：性别、闰月归属、四化表必须显式提供；Provider 重新核对农历、时支、年干支和历法版本；返回结构不含原始出生记录，不自动持久化。实际样例为上游计算冒烟，不是独立历史盘黄金验证。
- JUTZUGC fork 的代码与数据许可证不同：代码 MIT、数据 CC BY 4.0、部分讲座引文 CC BY-NC-SA。其 56.6GB 解压数据集和闭源判断库不适合作为 Muse 的直接依赖。


### 3. 大六壬：已接入隔离 Provider，仍缺独立黄金课例

- PyPI 版本为 kinliuren 0.1.2.9，MIT，setup.py 无运行时依赖；sdist SHA-256 55924cc035c891bbd1d1ef457829f77c0993a188f66d1d2c175387732aad7ea2 与 PyPI 页面一致，wheel SHA-256 6b099e0e8cc20a2e0081e69e2f5556aa6c54ecb7936e9fc27d7ae8526c2fa210 与 PyPI 页面一致。
- sdist 只包含 __init__.py、kinliuren.py、setup.py 等少量文件，没有测试目录或黄金盘例。核心模块只依赖 Python 标准库。
- 源码签名是 Liuren(jieqi, cmonth, daygangzhi, hourgangzhi)：必须由外部提供节气、农历月、日干支、时干支；它不是从完整公历时间自动排盘的历法引擎。
- PyPI README 的 Quickstart 仍展示三个参数，但源码实际要求四个参数；GitHub 主分支 README 已显示四参数形式。以固定版本源码签名为准，不照抄过期 README 示例。
- GitHub 仓库还含 kinqimen 与 cerebras_client.py 等额外目录/文件；Muse 只考虑纯规则核心，绝不接入外部 Cerebras API。
- 已新增 KinliurenProvider，在独立 venv 实际运行固定样例；Muse 输入四参数并校验节气、农历月、日/时干支和四课三传结构。上游无测试目录、无独立黄金课例且不支持闰月；因此只证明运行链路打通，不证明断课规则正确。

### 4. 奇门遁甲备选 dglijin：样例存在重大矛盾，未接入

- dglijin-oss/qimen-dunjia 标示 MIT，仓库 9 次提交；实际排盘脚本位于 scripts/qimen_pan_v2.py（约 1214 行），另有 scripts/geju_enhancer.py、根目录 index.js 和 references。README 的运行命令没有明确写出 scripts/ 子目录路径。
- README 宣称 Python 3.8+、无外部依赖、覆盖 1900–2100、农历/节气/真太阳时和九宫排盘；但未发现自动化 tests 目录或基准盘例。
- README 样例把 2026-03-20 10:00 标为“小满”，同页又将其标为农历正月初一；公开历法资料显示该时刻在春分日附近，小满在 5 月下旬。至少 README 示例不可信，不能据此接受其精度声明。
- 脚本包含内置 1900–2100 农历数据表，尚未逐年/闰月/节气边界验证。当前不安装、不运行、不接入；先静态审查算法，再建立 1900/2100 边界、闰月、春分/小满交界和真太阳时测试。

### 5. 奇门遁甲：qimen-rs 0.2.1 已隔离运行，源码审计未完成

- PyPI qimen-rs 0.2.1 wheel 为 Windows abi3，pip metadata 标示 MIT、无运行时依赖；wheel SHA-256 1a95ba7e086e46c7b8fb6c4f0726f390547853cc409ad900bef0489d443bf920 与 PyPI 发布页相符，独立 venv pip check 通过。
- PyPI 页面未提供 build provenance。源代码包下载在 pip 构建隔离阶段因宿主代理端口解析错误失败；未运行构建脚本，也未宣称源码审计完成。
- qimen-rs 的 Python calculate() 返回时家拆补转盘九宫、四柱、农历、节气、阴阳遁/局数、值符值使等结构。Muse 已用 2024-06-15 12:30 上海样例运行，并将四柱/农历/节气与 CalendarContext 交叉核对一致。
- 当前只支持时家拆补转盘；不支持置闰、飞盘、日/月/年家或真太阳时。任何四柱、农历或节气不一致均拒绝输出。详见 docs/QIMEN_POC.md。


### 易经经传文本来源候选（尚未导入正文）

- Project Gutenberg eBook #25501《易經》提供中文文本，页面标示 public domain in the USA；纯文本头部明确提醒美国以外地区需自行核对当地法律。可用于来源审查与分层解析候选，不因页面标示而跳过当地权利核验。
- James Legge《The Yî King》，Sacred Books of the East vol. XVI，1882；第二版 1899。英文译文应作为独立 translation layer，不与中文原文混为一份规则文本。
- Wikisource《周易》页面提供六十四卦速查表和单卦分篇页面，可用于结构交叉核对；录文修订、贡献者许可和文本底本仍需逐页记录。
- 当前只记录来源候选，不复制卦辞、爻辞、彖象或译文。未来按卦辞/爻辞/彖/象/文言等层级保存 edition、locator、text hash、rights、校勘差异和替代版本。

## 建议的集成架构

1. `TraditionProfile`：流派、规则版本、来源、输入口径和适用边界。
2. `CalendarAdapter`：公历/农历/节气/时区/真太阳时/干支统一转换；所有边界都必须显式记录。
3. 四个独立确定性计算器：YiJing/LiuYao、ZiWei、QiMen、LiuRen；每个引擎只负责“可复算结构事实”，不负责自由断语。
4. `InterpretationLayer`：引用具体规则和原文，展示多流派差异、替代解释和不确定性。
5. `CrossSystemInspiration`：只做结构对照与假设生成，不强行等同术语或制造共识。
6. `ResearchWikiAdapter`：保存原始输入、算法版本、规则引用、计算中间值、输出哈希、反证与复核记录。

## Muse 当前实现

- src/muse/yijing.py 已实现 SHA-256 可见 seed 的三钱结构模拟、六爻值解析、收到本序六十四卦编号/中文卦名/Unicode 符号查询，以及本卦/之卦与互卦/错卦/综卦结构关系。
- 六十四卦结构表由 BaZi Atlas、YiAtlas、TOEA 三份二级资料交叉核对；标记为 secondary_structural_reference_not_primary_textual_citation，不等同于已校勘经传版本。
- 尚未导入卦辞、爻辞、十翼或现代注释；解释状态仍为 not_generated。seeded simulation 不是实体掷币，所有结构都不是证据或预测。
- 紫微斗数 0.4.1 的隔离 POC 与 Research Wiki 适配见 `docs/ZIWEI_POC.md`：已验证 Windows AMD64 wheels、pip check、合成命盘与 25/92/50/48/57 目录同步；主环境仍不依赖该 Alpha 包。
- 共享历法与四柱 Provider 见 docs/CALENDAR_CONTEXT.md：celestial-calendar 0.8.0 + Muse sexagenary v1 已计算农历、节气、真太阳时与四柱；Ziwei/QiMen/LiuRen 三个可选 Provider 已在隔离 venv 实际运行并接入 InspirationEngine 统一调度。历史边界、独立黄金盘与 wheel 源码审计仍待完成。
- 2026-11-10 已新增 ZiweiProvider、QimenRsProvider、KinliurenProvider，并在 InspirationEngine 暴露独立调用及三引擎统一调度；build_metaphysics_chart_dossier() 可把三套真实盘面与可选 Yi Jing cast 组织为四体系结构档案，生成框架层研究问题而非直接断语，不持久化。详见 docs/ZIWEI_POC.md、docs/QIMEN_POC.md、docs/KINLIUREN_POC.md 和 docs/CALENDAR_CONTEXT.md。

## 接入前强制门槛

- 许可证：代码、规则表、数据集、古籍文本、现代注释分别登记，不因仓库根许可证而默认所有内容同许可。
- 供应链：锁定 commit/tag、SHA-256、依赖版本；在隔离目录审查，不运行未知安装脚本。
- 规则：至少有来源章节/页码、流派、版本、口径和维护者；无出处数据标记 unverified。
- 测试：黄金盘例、随机属性测试、时间边界、闰月、节气交界、子时换日、时区/DST、跨实现差分。
- 输出：必须提供结构化 JSON、可复算中间步骤、source_refs、rule_version 和不确定性。
- 安全：紫微出生资料仅用户主动提供；最小化存储、本地优先；不得自动写入通用个人记忆。
- 预测边界：任何玄学结果均标为传统体系内解释/文化灵感，不是科学证据或确定性现实预测。

## 原始项目与资料

- I Ching Research Skill: https://github.com/ProfesseurHaipeng/iching-research-skill
- Deterministic Liu Yao engine: https://github.com/yaomancy/liuyao-engine
- Zi Wei Python package: https://pypi.org/project/ziwei/
- Zi Wei reference engine: https://github.com/JUTZUGC/ziwei-doushu
- Da Liu Ren Python library: https://github.com/kentang2017/kinliuren
- Qi Men candidate (not approved for integration): https://github.com/dglijin-oss/qimen-dunjia
- 2026 solar-term reference used to catch the Qi Men README inconsistency: https://kuangchujia.com/solar-terms/
