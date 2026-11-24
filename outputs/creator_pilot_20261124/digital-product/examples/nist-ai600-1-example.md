# 示例：核验一条关于生成式 AI 的主张

## 原子主张
生成式 AI 可能以自信语气输出错误内容，也可能生成看似合理但并不支持结论的引用。

## 原始来源
- 标题：Artificial Intelligence Risk Management Framework: Generative Artificial Intelligence Profile
- 发布机构：National Institute of Standards and Technology (NIST)
- 日期：2024-07-26
- 官方页面：https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence
- PDF：https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf
- 定位：§2.2 Confabulation；MS-2.5-003

## 来源实际支持什么
NIST 对 confabulation 的描述包括生成式 AI 自信地呈现错误或虚假内容；MS-2.5-003 建议在部署前风险测量和持续监测中审查并核实生成式 AI 输出中的来源与引用。

## 不应过度延伸
- NIST 没有把“拆主张→查原文→找反证”命名为官方三步法；这是本工作台的编辑流程。
- 该文件不证明某个具体模型在某次任务中一定出错。
- 不应从该文件推导未提供的错误率、成功率或用户比例。

## 暂时状态
- 主张：partially_verified（需按使用场景补充具体模型/案例证据）
- 来源：verified
- 反证：not_collected
- 发布提示：避免把风险说明包装成所有 AI 输出都错误。
