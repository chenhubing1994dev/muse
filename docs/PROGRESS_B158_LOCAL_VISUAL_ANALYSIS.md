# B158｜本地视觉特征分析与 Dashboard 类型检查

日期：2026-11-18

## 本轮目标

在 B157 的构图元数据运镜策略上，接入可选的本地视觉特征估计；同时复核 Dashboard 字幕审校类型错误。

## 完成内容

### 1. Dashboard 检查恢复全绿

本轮未改动 Dashboard 类型定义或 Svelte 分支，重新执行后确认当前工作区：

- npm run check：Svelte 0 errors、0 warnings。
- npm test：37 项通过。
- npm run build：Vite 119 modules transformed，构建成功。

因此此前记录的两个 subtitle_review.status 类型错误当前不再复现；本轮没有为它们做多余改动。

### 2. 新增可选本地视觉特征分析器

新增：

- src/muse/scene_analysis.py
- tests/test_scene_analysis.py

使用当前环境已有的 OpenCV 4.13 与 NumPy，不安装依赖、不下载模型。图像/视频资产可尝试抽取：

- 局部对比度、饱和度与边缘组成的视觉显著性启发式图。
- 显著区域的加权中心位置与包围区域尺度。
- 上下左右边界留白估计。
- 视频抽样帧之间的 Farneback 光流运动强度估计。

对静态图像，motion_level 为 0。视频采样首帧、中间帧和末帧；分析或解码失败时明确返回 unavailable，不伪造分析值。显著区域包围范围覆盖画面 75% 或以上时也视为低置信度并拒绝输出，避免把纹理/背景误当主体。
### 3. 与运镜生产线集成

- build_from_source 在 brand_scene_transitions 模式且未显式传入 scene_analysis_metadata 时，尝试调用本地分析器。
- 成功时把 8 个归一化构图字段传入 B157 决策器，并将 metadata_source 标记为 opencv_visual_heuristic_v1。
- 失败或依赖不可用时回退到原有确定性运镜循环，不阻断视频生成。
- 调用方显式传入数据时仍标记 caller_supplied。
- 视觉估计结果进入 shot_directives、storyboard 和产品 fingerprint。
- BUILDER_VERSION 从 9 升至 10，避免新分析策略与旧产品版本发生指纹混淆。

## 重要能力边界

本轮不是语义视觉理解模型。OpenCV 启发式只估计画面局部显著性、几何区域和帧间运动：

- 不识别人、脸、具体物体或场景语义。
- 不理解故事内容、镜头意图、人物身份或动作含义。
- 高对比度区域可能不是主体；字幕、灯光、纹理和背景也可能影响估计。
- 这些数值只能作为剪辑启发式，不能替代人工构图判断。
- 对真实素材进行六段短片抽样试跑时，显著区域范围过宽，分析器按低置信度规则返回 unavailable；该批素材会安全回退到默认运镜，而不会输出看似精确但不可信的主体框。
- OpenCV 与 NumPy 以可选运行时方式导入；环境缺少它们或分析置信度不足时返回 unavailable 并安全回退。
## 验证证据

- Python 编译检查：scene_analysis.py、video_product_builder.py 及相关测试通过。
- scene_analysis + VideoProductBuilder + Remotion：46 项通过。- Dashboard Svelte 检查：0 errors、0 warnings。
- Dashboard Node：37 项通过。
- Vite build：119 modules transformed，构建成功。

全仓测试另行运行：

全仓测试最终复跑：

- Ran 1320 tests in 158.435s。
- OK，1 项跳过。
- 首轮全仓运行曾出现 3 个研究知识/实验测试失败；同一工作区未改动这些研究模块，随后完整复跑全部通过。将首轮波动保留为调查线索，但当前最终回归结果为全绿。
- 未安装 Python/Node 依赖，未下载模型。
- 未运行新的 Wan 推理或发布视频。
- 未执行 git reset、git clean、commit 或批量删除。
- 保留既有未提交和未跟踪改动。

## 下一步

1. 在真实素材上比较 OpenCV 显著性估计与人工标注，量化主体中心、留白和运动强度误差。
2. 如需语义识别，再单独评估本地可用视觉模型与 GPU/VRAM 成本；没有明确模型能力与实测前，不启用人物/物体语义结论。
1. 优化显著区域估计并建立人工标注对照集；只有通过质量门槛的素材才启用内容感知运镜。
2. 如需语义识别，再单独评估本地可用视觉模型与 GPU/VRAM 成本；没有明确模型能力与实测前，不启用人物/物体语义结论。
3. 独立修复全仓研究知识/实验测试的 3 个失败，再重新跑完整回归。
## 结论

B158 已把 B157 从“只接受构图元数据”推进到“可选地从本地图片/视频提取低层视觉特征，再驱动运镜策略”。它具备本地视觉启发式分析，不等于语义级视觉智能；全仓回归目前有 3 个与研究知识/实验路径相关的失败待处理。
