# B157｜内容感知镜头运动策略

日期：2026-11-18

## 本轮目标

在 B154 的 Remotion 场景时间轴和 B156 的 Dashboard 分镜可追溯性之上，为 brand_scene_transitions 增加可复现、保守的内容感知运镜选择；没有可靠构图数据时，保持原有确定性运镜循环。

## 实现

修改文件：

- src/muse/video_product_builder.py
- tests/test_video_product_builder.py

### 1. 显式构图元数据与安全验证

新增 _content_aware_scene_motion_directives。每个场景必须提供以下 8 个归一化字段，数值范围均为 0..1：

- subject_center_x、subject_center_y
- subject_scale
- negative_space_left、negative_space_right
- negative_space_top、negative_space_bottom
- motion_level

拒绝布尔值、缺失字段、非数值、NaN/Infinity、越界数值，以及场景数量不匹配。验证采用整批策略：任一场景不合格，整条序列回退至 B154 的默认确定性镜头循环，不混用部分推断与默认值。

### 2. 运镜决策规则

仅对 brand_scene_transitions 生效：

1. motion_level >= 0.7：不增加镜头运动（none）。
2. subject_scale <= 0.3：轻推近（push_in）。
3. subject_scale >= 0.7：拉远（pull_out）。
4. 其余情况依据主体横向位置与左右留白差，选择 drift_right 或 drift_left；没有明确方向优势则 none。
5. 所有分支 vfx=none，不凭空添加视觉特效。

构图元数据是调用方显式提供的输入；本轮没有加入视觉模型，也没有声称系统已自动识别画面主体、留白或运动强度。

### 3. 产品可追溯性

- build_from_source 新增可选 scene_analysis_metadata 参数。
- 经验证的归一化数值、运镜策略与来源标签写入 settings.shot_directives 和每个 storyboard scene。
- 这些设置参与产品 fingerprint；同一素材但构图元数据不同会产生不同版本。
- BUILDER_VERSION 升至 9。
- 无效/缺失元数据仍沿用既有默认镜头循环，旧调用方式保持兼容。

## 验证证据

- py -3.11 -m py_compile src/muse/video_product_builder.py tests/test_video_product_builder.py：通过。
- py -3.11 -m unittest tests.test_video_product_builder：35 项通过。
- py -3.11 -m unittest tests.test_remotion_motion：7 项通过。
- py -3.11 -m unittest discover -s tests：1309 项通过，1 项跳过。
- Dashboard Node 测试：37 项通过。
- npm run build：Vite 119 modules transformed，构建成功。
- npm run check：未通过，Svelte 检查报告 2 个 TypeScript 错误、0 个 warning，位置为 ui-prototype/src/App.svelte:1492 和 :1494；subtitle_review.status 的类型联合不包含 "unavailable" / "ambiguous"。这两处是本轮未修改的现有 UI 类型问题，故不能记录为 Dashboard 全部检查通过。

## 未完成与边界

- 当前策略消费调用方给出的构图数值，不会自动从图片/视频抽取这些数值；自动视觉分析仍是后续独立工作。
- 尚未对真实镜头进行视觉质量盲评或浏览器画面验收；本轮测试以可控 fixture 验证规则和产品持久化。
- 未启动新的 Wan 推理，未生成新的 AI 镜头；Remotion 仍是已有素材的后期合成路径。
- 未完成粤语旁白听审、字幕人工批准、版权/事实审查或平台发布。
- Dashboard Node 测试和 Vite 构建通过，但 Svelte 类型检查的 2 个错误仍待单独修复。

## 工作区保护

- 仅修改本轮实现、测试与新增进度文档。
- 保留既有工作区改动及未跟踪文件；没有执行 reset、clean、commit 或批量清理。
- 未安装依赖、下载模型、启动 Wan 推理或发布作品。

## 下一步

1. 修复 Dashboard subtitle_review.status 类型声明与 UI 分支的联合类型不一致，再跑 Svelte 检查。
2. 设计可选的本地视觉特征提取器，为显式构图 schema 提供可验证输入；没有模型或分析失败时继续默认策略。
3. 对真实素材进行镜头方向与主体安全区的人工对照验收。
4. 继续音轨响度听审与真实 Wan 镜头生成验证。

## 结论

B157 已完成显式构图元数据驱动的运镜决策、整批安全校验、默认回退、产品版本指纹与 storyboard 追溯。核心 Python 测试和全仓测试通过；Dashboard Node 测试与 Vite 构建通过，但 Svelte 检查仍有 2 个未解决的既有类型错误。它是“有结构化构图数据时的内容感知运镜策略”，不是已经具备自动视觉理解能力的完整视觉智能系统。
