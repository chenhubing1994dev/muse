# B154｜真实 Remotion 运镜与产品构建 E2E

日期：2026-11-17

## 本轮目标

在独立 staging 中验证真实 Remotion scene timeline 渲染，并把确定性的轻运镜默认策略接入正式 VideoProductBuilder；确认分镜、QA、产品 ZIP 与 Dashboard 产品列表可以追踪同一份产物。

## 环境发现与纠正

- Remotion 并非未安装，而是独立位于 `video-motion/` 子项目。
- 锁定版本：Remotion 4.0.543；CLI、Renderer 与 transitions 依赖均从该子项目解析。
- B153 文档中“当前无法解析 Remotion 包”的结论是从 Muse 根目录检查得到的误判。后续应以 `video-motion/` 子项目为准，不再从根目录判断依赖是否存在。

## 实现

修改：
- `src/muse/video_product_builder.py`
- `src/muse/remotion_motion.py`
- `tests/test_video_product_builder.py`

### 1. 静态镜头默认运镜

- 新增确定性 `_default_scene_motion_directives(scene_count)`。
- 镜头顺序循环采用 push_in、drift_left、pull_out、rise、drift_right；不默认叠加 VFX。
- 仅 `brand_scene_transitions` profile 自动应用，不影响 motion_profile=none 的旧流程。
- shot directives 写入产品 settings、storyboard scenes 和 Remotion scene timeline 元数据。
- Builder 版本升至 8，避免旧指纹复用没有新运镜设置的历史产品。

### 2. 真实产品构建验证

使用现有六段源素材与环境音乐，在独立临时 workspace/staging 执行 VideoProductBuilder.build_from_source：
- Remotion 4.0.543 实际渲染 scene timeline 和转场。
- 1080×1920、24fps、H.264/AAC。
- 输出时长 19.125 秒，MP4 约 7.15 MB。
- 六个场景均有 scene timing 与镜头运镜记录。
- `verify_path` 验证通过；ZIP 成员与产品目录文件逐项字节一致，ZIP CRC 检查通过。
- FFmpeg 全片解码通过；freezedetect 未发现冻结区间。
- QA 检查通过，但仍保留一条 `audio_loudness_low` 复核警告（-24.3 LUFS，阈值下限 -24.0 LUFS）。它是整段音频指标，不映射到某个镜头。
- 产品状态保持 testing，`ready_to_publish=false`。

产物目录：
`data/prometheus/staging/b154-motion-e2e-zth_2vcq/verified-product/`

包含：
- `video.mp4`
- `product.zip`
- `manifest.json`
- `storyboard.json`
- `qa-report.json`
- `README.md`

### 3. 独立运镜对照

在正式产品集成前，使用独立输出 `scene-dynamic.mp4` 对同一组镜头做了运镜对照。FFmpeg freezedetect 不再报告持续冻结区间。该对照只验证运动与冻结检测，不代表 Wan 新推理。

### 4. Dashboard API

- 将 staging 产品复制到临时产品根目录调用 `status_api.build_products`。
- API 返回一条产品记录，包含 `audio_loudness_low`、-24.3 LUFS 和 `not_time_based` 映射状态。
- 不泄露临时目录绝对路径。
- Dashboard UI 的 Svelte 检查、Node 测试与生产构建均通过。

## 验证证据

- VideoProductBuilder：30 项测试全部通过。
- Remotion motion：7 项测试全部通过。
- Status API：30 项测试全部通过。
- Svelte check：0 errors、0 warnings。
- Dashboard Node tests：37 项全部通过。
- Vite production build：119 modules transformed，构建成功。
- Python 全仓测试：`Ran 1301 tests`，`OK (skipped=1)`。
- 实机 MP4：1080×1920、24fps、19.125 秒，H.264/AAC 双流；全片 decode 与 ZIP 完整性验证通过。

## 未完成与边界

- 本轮使用既有 Wan 素材做 Remotion 运镜与剪辑，没有启动新的 Wan 模型推理；不能把它描述成新生成的 AI 镜头。
- 音频响度 -24.3 LUFS 略低于当前 QA 下限，保留人工听审/混音复核，不自动篡改原始音频。
- 未进行粤语旁白人工听审、字幕人工批准、版权/事实审查或正式平台发布。
- 产品质量门仍为 testing，禁止标记 ready_to_publish。
- 尚未在真实浏览器中完成 Dashboard 视觉验收。

## 工作区保护

- 所有新渲染均位于独立 staging，不覆盖历史 MP4、源素材或产品目录。
- 保留既有脏改动与未跟踪文件；未执行 git clean、reset、commit 或批量清理。

## 下一步

1. Dashboard 产品详情增加更直观的镜头运镜与 scene timeline 展示，并在真实浏览器人工验收。
2. 处理音频响度警告：先试听并判断素材是否为刻意低音量氛围音乐，再决定混音增益；不要只为消除 QA warning 而盲目拉高。
3. 将运镜参数从固定轮换升级为依据镜头内容/构图的策略选择，并通过对照实验评估运动幅度与画面安全边界。
4. 回到真实 Wan 推理链：核实 ComfyUI/模型/GPU 状态后，独立生成新镜头，再走同一 Remotion、QA、ZIP、Dashboard 闭环。
5. 继续粤语旁白、字幕和发布人工审核门槛。

## 结论

B154 已验证 Muse 的正式产品构建器可以调用真实 Remotion 4.0.543 完成六镜头动态运镜、转场、QA、分镜时间映射与产品 ZIP 打包。相较之前静止镜头版本，冻结检测从多段告警降为零；当前仍有轻微低响度告警，且没有进行新 Wan 推理或正式发布。因此这是视频后期生产链的一次真实集成验收，不等于完整 AI 视频创作与商业发布链已全部完工。
