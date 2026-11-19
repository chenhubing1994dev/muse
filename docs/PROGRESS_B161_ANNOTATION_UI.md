# B161｜本地人工视觉标注台

日期：2026-11-19

## 实现

- 新增 tools/scene_annotation_server.py：Python 标准库 HTTP 服务，无新增依赖。
- 仅允许绑定 127.0.0.1 / localhost，默认端口 8765，不向局域网暴露。
- 浏览器逐帧查看 B160 图像，支持 present / absent / ambiguous、主体类型、镜头运动、标注者、备注和拖框归一化坐标。
- 保存时校验状态、运动等级、标注者、bbox 范围与矩形有效性；使用同目录临时文件和 os.replace 原子更新 CSV。- HTTP POST 写入使用进程内锁串行化读-改-原子替换，避免并发标注请求互相覆盖。
- 只更新人工标注字段，保留 pred_* 预测列；拒绝未知 sample_id、路径穿越和缺失帧。
- 新增测试覆盖输入校验、预测列保留、未知样本和路径穿越。
- HTTP 集成测试实际启动 loopback 测试服务，验证页面 GET、样本 API GET 与标注 POST。
## 验证结果

- Python 3.11 py_compile：通过。
- 标注 UI + B160 评估器专项：**8/8 通过**，包括并发双帧保存测试、真实 HTTP GET/POST 集成。
- 在正式 18 帧数据上运行 B160 评估器：仍为 `not_scored`，18 unlabelled、0 scored；没有伪造精度。- HTTP 测试使用临时目录，不改动正式人工标注 CSV；正式数据仍为 18 帧未标注。
- 全仓库回归：1403 tests，1 skipped，出现 1 项 `test_concurrent_outbox_dispatchers_share_one_idempotent_task` 并发断言失败；该单测随即单独重跑 1/1 通过。当前将其记录为全量并发运行下的间歇性失败，不把全仓库回归标记为全绿。

## 启动

在 Muse 项目根目录运行：

    py -3.11 tools/scene_annotation_server.py

浏览器打开 http://127.0.0.1:8765/，按 Ctrl+C 停止。服务只绑定本机回环地址。

## 验收边界

本工具只提供人工标注界面，不执行主体识别，也不会自动生成真值。18 帧仍须由人独立观察标注；完成标注后，B160 评估器才能给出有效的视觉准确率结果。
