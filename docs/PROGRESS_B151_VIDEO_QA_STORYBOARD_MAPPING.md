# B151｜视频 QA 警告自动关联分镜源镜头

日期：2026-11-17

## 本轮目标

把 FFmpeg 检测出的黑帧、静帧、静音等带时间码警告，自动关联到分镜中的源镜头，减少人工反复对照时间轴的工作。该映射只用于定位复核对象，不改变 QA 警告本身，也不把启发式信号升级为质量失败或编辑结论。

## 实现

修改：
- src/muse/video_qa.py
- tests/test_video_qa.py

新增纯函数 map_warnings_to_storyboard_scenes(warnings, storyboard, timeline_offset_seconds=0.0)：

1. 按分镜顺序，用每个 scene 的 duration_seconds 或 source_duration_seconds 建立连续时间区间。
2. 时间轴偏移由调用方显式提供；不会猜测片头、片尾或转场时长。
3. 计算每条 QA warning 与各 scene 的真实重叠区间，输出 scene id、source_file/source_asset、镜头起止时间和重叠秒数。
4. 片头/片尾或分镜范围外的警告标记为 outside_storyboard，不强行归入最近镜头。
5. 对独立舍入造成的 10 微秒以内边界噪声不创建伪重叠；场景边界统一到微秒精度。
6. 不含时间区间的音频响度/峰值类 warning 保留为 not_time_based，不因缺少 start/duration 使整个映射失败。

CLI 新增：
- --storyboard <storyboard.json>
- --storyboard-offset-seconds <seconds>（默认 0）

不传 storyboard 时，旧 QA CLI 输出行为保持不变；传入无效 storyboard 或 offset 时返回非零状态，并输出 mapping error，不伪造映射结果。

## TDD 与测试证据

- 先添加测试并观察到预期失败：缺少映射函数时 ImportError；CLI 参数未实现时 argparse 拒绝参数；边界精度测试发现 1 微秒伪重叠。
- 实现后运行 py -3.11 -m unittest discover -s tests -p test_video_qa.py -v：16 项全部通过。
- 覆盖：单镜头映射、跨镜头重叠、片头/片尾未映射、非法时间输入、CLI 输出、微小边界噪声、非时间型音频 warning。

## 真实成片验证

对现有真实 MP4 与其 storyboard 执行新 CLI：

    $env:PYTHONPATH='src'
    py -3.11 -m muse.video_qa data/products/f57e8f7277a85dca44055aa71df1cbb1/1713600cfe751184fc96/video.mp4 --storyboard data/prometheus/runs/cantonese_pilot_v2/fd322c0e6fcb42c2a69251d8584c4c6a/storyboard.json --storyboard-offset-seconds 3

实际输出的四条静帧警告稳定映射为：
- 3.417s，持续 1.667s → shot-01
- 8.167s，持续 1.083s → shot-03
- 9.375s，持续 1.958s → shot-04
- 11.458s，持续 4.958s → shot-05

这与 B149 的源镜头定位一致。shot-02、shot-06 不再因 1 微秒时间舍入误差被误报为重叠镜头。真实视频哈希仍为 07e0d650f885a87b42458fd57d5fe750d4c7c47d6ea45ccd799590523b1cadc4；本轮没有修改视频、字幕、源素材或 QA 阈值，也没有执行新的 Wan 推理。

## 全仓回归结果与未解决项

- 全仓 unittest discover -s tests：运行 1,291 项，1 项 skipped，1 项 error。
- 唯一 error：test_local_dashboard.LocalDashboardTests.test_lan_requires_pairing_and_proxies_api_same_origin，在访问 LAN URL 时收到 HTTP 502。
- 随后单独重跑该测试，仍在同一请求位置收到 HTTP 502（约 3 秒超时窗口）；与本轮 video_qa.py 及测试文件无关。暂不修改 dashboard 或网络代理行为，保留为独立环境/集成问题。
- 本轮 video QA 专项 16/16 通过，真实 MP4 端到端映射通过；但全仓测试尚未全绿。

## 后续

1. 在视频产品构建与 dashboard 中评估是否自动把 storyboard mapping 展示为镜头级 QA 复核清单；需保留显式 offset/timeline 数据，不能把本轮 CLI 映射假定成所有产品都适用。
2. 继续处理 shot-05、shot-04、shot-01 的源素材替换；shot-03 仍先检查合成缩放/裁切。
3. 继续 CosyVoice-Cantonese fork 的只读源码获取与静态审计；当前仍未安装、未下载模型。
4. 继续保留字幕逐段人工听审与批准门槛。

## 证据边界

本轮完成的是 QA 时间警告到 storyboard 镜头的自动定位能力，不是视频素材修复、AI 视频生成、字幕批准或发布验收。