# B159｜低置信度镜头的独立运动诊断与安全回退

日期：2026-11-18

## 本轮目标

解决 B158 本地视觉分析的一个信息损失问题：当画面显著区域过宽、无法可靠估计主体构图时，分析器会整体返回 unavailable，连同已经可以测量的帧间运动数据一并丢弃。保留独立运动诊断，但不允许低置信度构图进入内容感知运镜决策。

## 实现

修改文件：

- `src/muse/scene_analysis.py`
- `src/muse/video_product_builder.py`
- `tests/test_scene_analysis.py`
- `tests/test_video_product_builder.py`

### 1. 分离构图置信度与运动测量

- 每个源素材仍抽样首帧、中间帧、末帧。
- 无论构图显著区域能否通过置信度门槛，只要视频帧成功解码且存在帧对，就独立计算 Farneback 光流运动强度。
- 逐镜记录 scene_index、asset_name、sampled_frame_count、motion_sample_pairs、motion_level、motion_status、composition_status 和失败原因。
- 当构图分析不完整时返回 status=partial，不输出 scenes 构图列表；不会将不完整数据传入 B157 的整批运镜验证器。
- 任一素材解码或分析失败会逐镜留下明确 unavailable 原因；可测量的其他镜头诊断仍保留。
- 低置信度情况下仍使用原有确定性运镜默认值，不放宽构图置信度门槛，不推断人物/物体语义。

### 2. 产品追溯

- VideoProductBuilder 将 scene_analysis 报告写入 storyboard.settings，并纳入产品 fingerprint。
- 诊断包含来源、总体状态、非语义识别标记、失败原因和逐镜运动记录。
- BUILDER_VERSION 从 10 升至 11，避免旧产品指纹复用新诊断逻辑。

## 六段真实源素材复测

素材目录：`data/video_sources/muse-cosmos-film`

所有素材均为 H.264、288×512、12 FPS；前三秒/片长不代表电影级质量。以下 motion_level 是当前三帧抽样的相对光流指标，不是经人工标定的质量分数，不单独作为发布判定阈值。

| 镜头 | 时长 | motion_level | FFmpeg freezedetect（n=-50dB,d=1.0s） | SHA-256 |
|---|---:|---:|---|---|
| shot-01 | 2.083s | 0.000326 | freeze_start=0.5s | 9d118a5e19ad9e3de5b135ccfdc622441d9fdf4aec89c9f79d43f669ac06122d |
| shot-02 | 2.083s | 0.006929 | 未触发 | 00c08f6e4b414ddbc9f486ed2dee969ac326b0e83e4fdb11d54644d5c58a06c8 |
| shot-03 | 2.083s | 0.012228 | 未触发 | 78afe81ea152ba6b04b667a6b6655d16449fe42228e01079658dcf211a9efcc6 |
| shot-04 | 2.083s | 0.003066 | freeze_start=0s | 2c3ed1c365c1de627990e987e8edd8870f7b252ce6d203e50b73b57673a6b572 |
| shot-05 | 5.083s | 0.002711 | freeze_start=0s；freeze_duration=1.5s；随后再次进入冻结 | 2f7fc7227e84b896be7aaa1e7060b51d76175a81e36ee43cf32732d5850aa05d |
| shot-06 | 5.083s | 1.000000 | 未触发 | 92fcfa3b3fa1ac9fceb49e65b4e563deecb67cd8260a61808813e84eb0f6d24e |

这次实测证明：即使六段素材的构图启发式全部因低置信度被拒绝，运动诊断仍可完整返回六条数据；shot-01/04/05 的低运动问题得到独立量化线索，shot-06 的高运动量也被保留。shot-03 源片未触发同阈值冻结，但最终成片仍有冻结警告，需继续检查合成时间轴/缩放路径。

## 验证

- `py -3.11 -m py_compile`：四个修改模块/测试文件通过。
- `tests.test_scene_analysis` + `tests.test_video_product_builder`：40/40 通过。
- 新增测试覆盖：全画面纹理构图低置信度时仍保留帧间运动诊断；partial 分析结果进入产品 storyboard；不完整构图仍使用确定性默认运镜。
- 全仓 Python 回归：`$env:PYTHONPATH='src'; py -3.11 -m unittest discover -s tests`，Ran 1396 tests in 158.045s，OK（skipped=1），退出码 0；本轮由 Runner 实机复跑确认。

## 真实推理状态与边界

- 当前 `http://127.0.0.1:8188/system_stats` 无法连接；未发现 ComfyUI 主进程。
- RX 6750 GRE 12GB 仍被系统识别，但当前不能据此声称 Wan/ComfyUI 在线。
- 本轮没有启动未知安装目录中的程序、没有下载模型、没有新跑 Wan GPU 推理，也没有修改任何既有生产 MP4/ZIP。
- B149 的四段冻结区间仍是发布阻塞项；QA 阈值保持不变。
- 粤语字幕仍需真实人工逐条听审；技术验证不能替代创作者审片。

## 下一步

1. 恢复并实测 ComfyUI/Wan 本地运行环境后，优先重生成 shot-05、shot-04、shot-01；每条新素材先做同阈值 freezedetect 预检并记录 prompt、seed、workflow hash、模型版本和源哈希。
2. 对 shot-03 做原始源帧与最终合成对应时间码的对照，检查裁切、缩放、时间重映射和转场，不以运动分数替代画面检查。
3. 全仓回归通过后，继续真实 Wan 生成、旁白混音、MP4/ZIP/Dashboard 交付闭环；发布状态仍须等待内容、权利、披露和人工审核。
