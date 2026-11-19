# B191｜Narration Generator Claim-ID Mapping

日期：2026-11-19

## 实现

- NarrationGenerator.generate() 新增可选 claim_ids_by_segment：调用方按输出段落顺序提供 Claim ID 列表。
- 新增可选 factual_by_segment：明确区分事实旁白与氛围/转场语句；没有显式分类时，映射启用后默认按 factual=True 处理。
- Claim ID 严格校验为 32 位小写十六进制；映射长度必须与生成段数一致；同段重复 ID 会去重。
- 未启用新参数时，旧输出字段结构保持不变，原有生成逻辑与三种粤语风格不变。
- 不自动猜测段落对应哪个 Claim；避免语义相似度或模型自报映射被当成证据关联。

## 验证

- py_compile src/muse/narration_generator.py src/muse/narration_evidence_contract.py：通过。
- python -m unittest tests.test_narration_evidence_contract tests.test_narration_pipeline -v：**40/40 passed**。
- 新增测试覆盖逐段 ID 保留、重复去重、事实/非事实标记、映射长度错误、无效 ID 与非布尔 factual 标记。
- 旧调用不传新参数的输出结构回归测试通过。

## 边界与下一步

- Claim ID 仍由上游选题/研究/脚本流程提供；生成器不会擅自把整段研究文本当作已证实主张。
- NarrationSyncStore 尚未持久化 evidence contract；VideoProductBuilder 尚未执行证据门禁。
- 下一步 B192：把 evidence contract 与旁白时间轴一并持久化，并在读取/导出时完整回读；再推进 B193 产品构建门禁。
