# B160｜六段真实视频人工标注基准与评估器

日期：2026-11-18

## 本轮目标

把 B159 的模型审计结论转化为可重复的真实素材评测流程。先建立独立人工标注基准，再评估 OpenCV 或未来本地视觉模型；不把模型预测写成人工真值。

## 已完成

### 1. 六段素材固定抽帧

来源：`data/video_sources/muse-cosmos-film/shot-01.mp4` 至 `shot-06.mp4`。

- 每段按视频时长的 15%、50%、85% 抽取 3 帧。
- 共生成 18 张 JPEG，存放于 `data/annotation/b160/frames/`。
- `manifest.json` 保存源视频名、帧索引、秒数、分辨率与 SHA-256。
- 抽帧脚本检查确认 6 个来源视频、18 条记录和 18 个实际存在的帧文件一一对应。
- 目录创建前检查不存在同名目录；没有覆盖旧标注资料。

### 2. 独立人工标注模板

新增 `data/annotation/b160/annotations.csv` 与 README：

- 人工标签：presence（present/absent/ambiguous）、subject_type、归一化 bbox、camera_motion、annotator、notes。
- 预测字段独立存放：pred_presence、pred bbox、pred_confidence、pred_source。
- 明确要求先独立观察帧图像，不参考 OpenCV 输出填写真值。
- 当前 18 条人工标签均为空，manifest 的 `ground_truth_status` 为 `not_annotated`。因此目前只是待标注基准，不宣称已获得人工真值。

### 3. 可复用评估器

新增 `tools/evaluate_scene_annotations.py`：

- 统计主体存在性 TP/FP/TN/FN、precision、recall。
- ambiguous 与未标注样本分别统计，不混入正确率。
- 统计预测覆盖率与缺失预测数，避免只报告有输出的样本。
- 对有效匹配框计算 IoU 与归一化中心点误差。
- 对越界、非有限或不完整 bbox 拒绝计算。
- 没有人工标签时返回 `status=not_scored`，不会把空白标签当成通过。

新增 `tests/test_scene_annotation_eval.py` 覆盖空标签、混淆矩阵/歧义排除/覆盖率、bbox 非法输入。

## 验证结果

- `test_scene_annotation_eval.py`：3 项通过。
- 在当前空白人工标签上执行评估器：`not_scored`，18 项 unlabelled，0 项 scored；这是预期结果。
- 数据集完整性脚本：`DATASET_VERIFY_OK records=18 clips=6 frames=18 ground_truth=unannotated`。
- 未安装依赖、未下载模型、未改动原始 MP4。

## 尚未完成 / 不可夸大的部分

- 18 帧尚未人工标注，因此没有主体识别准确率、IoU 或运镜准确率结果。
- B158 OpenCV 分析目前仍是低层视觉启发式，尚未通过真实标注集验收。
- 当前没有已确认可调用的本地语义视觉模型；Gemma 4 在 LM Studio 登记但 not-loaded，模型 API 未公布图像输入能力。
- 没有把任何视觉预测接入默认生产路径。

## 下一步

1. 对 18 帧完成人工标注；复杂/多主体帧可标 ambiguous，并在 notes 解释。
2. 建立预测导入适配器，把候选模型输出写入 pred_* 字段，保留来源与置信度。
3. 用同一批标注分别比较 OpenCV 与候选检测模型，报告覆盖率、precision/recall、IoU、中心误差及按视频分层结果。
4. 只有达到预先约定的精度和覆盖率门槛，才开放内容感知运镜；否则继续确定性回退。

## 结论

B160 已建立真实素材的可重复抽帧基准、独立人工标注模板和可自动执行的评估器。基准尚未人工标注，因此目前完成的是评测基础设施，不是视觉识别精度突破。