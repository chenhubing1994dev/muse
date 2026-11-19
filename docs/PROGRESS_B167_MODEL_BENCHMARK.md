# B167｜多模型视觉基准与预测输入质量门日期：2026-11-19

## 目标

让 OpenCV、候选本地视觉模型及未来的检测器在同一批人工标注样本上公平比较。模型输出通过独立 CSV 输入，按 sample_id 与人工真值对齐，不回写人工标注文件。

## 输入格式

每个模型单独一个 CSV，至少包含 sample_id、pred_presence；可选 pred_x_min / pred_y_min / pred_x_max / pred_y_max、pred_confidence、pred_source。坐标归一化至 [0,1]。

## 运行

    py -3.11 tools/evaluate_scene_model_benchmark.py --annotations data/annotation/b160/annotations.csv --predictions OpenCV=data/annotation/b160/predictions/opencv.csv --predictions Candidate=data/annotation/b160/predictions/candidate.csv --output data/annotation/b160/model-comparison.json

预测 CSV 可缺少部分样本；缺失项计入 missing_prediction_rows 并降低 coverage。重复 ID、空 ID、未知样本 ID 会拒绝。ambiguous 与未标注样本不进入准确率计算。

## 当前边界

此框架只做公平评估，不执行语义识别，也不伪造模型输出。18 帧人工真值尚未填写，因此正式评估仍为 not_scored。候选模型只有在独立确认安装、推理输入输出契约和可复现结果后才接入。

## 预测模板与输入校验

为模型生成空白 CSV（自动包含全部 sample_id，拒绝覆盖已有文件）：

    py -3.11 tools/evaluate_scene_model_benchmark.py --make-template data/annotation/b160/predictions/my-model.csv

每行填写 pred_presence（present / absent / unknown），可选归一化边界框、0–1 置信度和 pred_source。评估器拒绝重复/未知样本、非法状态、部分或越界边界框、非有限/越界置信度，以及 absent 却带边界框的矛盾记录。模型输出独立于人工标注文件。