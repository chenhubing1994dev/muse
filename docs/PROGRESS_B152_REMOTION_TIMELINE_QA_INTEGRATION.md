# B152｜Remotion 逐镜头时间轴与 QA 自动定位闭环

日期：2026-11-17

## 本轮目标

在 B151 的 QA warning → storyboard 映射基础上，把 Remotion 实际使用的逐镜头帧计划、转场重叠和产品 QA 报告接通；同时修复 LAN Dashboard 反向代理在系统 HTTP 代理环境下的本机 API 请求问题。

## 实现

修改：
- src/muse/remotion_motion.py
- src/muse/video_qa.py
- src/muse/video_product_builder.py
- src/muse/local_dashboard.py
- tests/test_remotion_motion.py
- tests/test_video_qa.py
- tests/test_video_product_builder.py
- tests/test_local_dashboard.py
- tests/test_status_api.py

### 1. Remotion 输出权威镜头时间

新增 _scene_timing_records(clips, transition_frames, fps)：
- 以 Remotion 已确定的 durationInFrames 为准，不用素材时长或平均时长推算。
- 每个镜头输出 start_frame/end_frame、duration_frames、timeline_start_seconds/timeline_end_seconds。
- 明确输出 transition_in_seconds / transition_out_seconds。
- 后一镜头的起点扣除前一镜头转场帧数，因此相邻镜头的区间可以真实重叠。
- 对非法帧数、FPS、转场帧数进行 fail-closed 校验。

render_scene_timeline 的结果现在包含 scene_timings；这些记录是在最终视频通过 Remotion/FFprobe 元数据校验后随结果返回。

### 2. QA 映射支持显式重叠时间区间

map_warnings_to_storyboard_scenes 现在支持两种时间模式：
- 旧模式：使用 duration_seconds/source_duration_seconds 顺序累加，并由调用方显式给出片头等 offset。
- 新模式：每个 scene 提供 timeline_start_seconds 和 timeline_end_seconds；时间区间已包含转场重叠及时间偏移，不能再叠加 timeline_offset_seconds。

部分镜头缺少显式起止时间、时间区间非法，或显式时间轴同时又传入 offset 时，都会明确报错，不会静默猜测。

非时间型音频 warning 继续标记为 not_time_based；亚 10 微秒的边界舍入噪声不会产生伪镜头匹配。

### 3. 视频产品构建自动保存镜头级 QA 结果

build_from_source 在 brand_scene_transitions 模式下：
- 将 Remotion scene_timings 与按同一顺序传入的源素材关联。
- 为每个导入镜头生成稳定 id（shot-01、shot-02 等）。
- 将 duration、起止时间、转场入/出时长写入 storyboard.json。
- 使用真实时间区间把 qa-report warnings 映射到源镜头。
- 将 storyboard_mapping 写入 qa-report.json，并确保 product.zip 中的报告也包含映射。

如果 Remotion 已返回 scene_timeline，却缺少数量匹配或字段有效的逐镜头时间记录，构建会失败，不会发布一个看似成功但无法追溯的产品。

普通素材合成、没有权威逐镜头时间数据的产品不会伪造映射；B151 CLI 的显式 storyboard/offset 模式仍可供人工审计使用。

### 4. Dashboard 本机 API 代理修复

LAN Dashboard 的 upstream API 固定为本机 127.0.0.1 status API。此前 urllib.urlopen 会继承系统 HTTP_PROXY 设置，可能把本机请求送到外部代理并返回 502。

现在使用 ProxyHandler({}) 创建直连 opener，明确绕过外部代理。测试客户端也使用显式直连代理配置，保证 LAN 私有地址测试不依赖宿主机代理环境。

同时更新大文件流式代理测试，使其 mock 新的 build_opener 接口。

### 5. 测试夹具校准

status API 的字幕审核查找测试夹具补齐 subtitle_cue_count，和当前审核文档 schema 的实际必填校验一致；不降低产品侧校验要求。

## 验证证据

### 专项测试

- Remotion + Video QA + VideoProductBuilder：55 项全部通过。
- Local Dashboard：9 项全部通过。
- Status API：30 项全部通过。

### 全仓测试

使用 Python 3.11、NO_PROXY=localhost,127.0.0.1,::1，并将 Python bytecode cache 指向系统临时目录（不清理、不覆盖项目内现有缓存）运行：

    py -3.11 -X "pycache_prefix=<TEMP>/muse-pycache-b152-final" -m unittest discover -s tests -q

结果：

    Ran 1298 tests in 134.409s
    OK (skipped=1)

### 验证边界

- 全仓 Python unittest 通过，1 项按测试设计跳过。
- 本轮没有执行真实 Remotion/FFmpeg 生产渲染；逐镜头帧计算和 builder→QA→ZIP 数据链通过单元/集成测试。
- 没有启动新的 Wan 推理，没有更换原始 MP4、源素材或字幕。
- 没有批准字幕、没有发布产品；产品仍需遵守人工字幕听审、内容/版权检查和发布门槛。
- 没有安装 CosyVoice、没有下载模型；AMD GPU TTS 加速仍未获验证。

## 工作区保护

本轮只编辑上述源代码和测试文件，并新增本进度文档。保留既有工作区变更；未执行 git clean/reset/commit，也未清理其他未跟踪文件。

## 下一步

1. 在具备完整本机 Remotion/FFmpeg 环境的真实测试素材上跑一次 brand_scene_transitions 成片，核对 MP4 时长、逐镜头区间和 QA warning 定位。
2. 将 storyboard_mapping 呈现到 Dashboard 的镜头级复核 UI，点击 warning 可定位镜头和源文件。
3. 处理 shot-05、shot-04、shot-01 的静帧素材质量问题；shot-03 先检查缩放/裁切。
4. 继续 CosyVoice-Cantonese 源码只读获取与安装脚本静态审计。
5. 保留粤语旁白人工听审、字幕人工批准和正式发布独立门槛。

## 结论

B152 完成的是“真实渲染时间轴 → 产品分镜 → QA warning → 源镜头”的数据闭环，以及 LAN Dashboard 的本机代理修复。它不是新视频实机渲染验收，也不是 AI 视频生成或发布完成。
