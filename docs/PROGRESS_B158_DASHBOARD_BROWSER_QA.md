# B158｜Dashboard 浏览器诊断收尾与默认图标请求处理

日期：2026-11-18

## 本轮工作

### 浏览器实机观察
- 使用新的临时 WebCodex Browser 实例打开本机 Muse Dashboard。
- 页面主导航、创作台、灵感工坊、产品库、反馈、事件、灵感输入表单、创作表单和产品操作按钮均能被真实浏览器识别。
- 在产品审校入口打开「逐条字幕审校」。
- 页面展示 6 条字幕 cue，每条均有时间范围、可编辑最终字幕文本及独立「已听审」复选框。
- 审核人、批准口令输入框正常呈现。
- 未勾选任何 cue；「确认人工审校并生成新版本」按钮仍为 disabled。
- 浏览器请求 `/api/v1/products/2ff1c0a9339292f7b663a1af1ab0b333/1713600cfe751184fc96/subtitle-review` 返回 HTTP 200。
- 页面其他主要数据 API、JS、CSS 和动态连接配置均返回 HTTP 200。

### 浏览器诊断
- 285 条网络请求中，唯一明确的 4xx 为浏览器自动探测 `/favicon.ico` 的 404。
- 新增 `/favicon.ico` 空响应处理，返回 HTTP 204 和零长度 body，不创建伪造图标文件。
- 新增 `test_browser_favicon_probe_returns_empty_success`，覆盖该行为。
- 浏览器在随后截图阶段出现实例失效；因此本轮不声称已完成全流程点击或视觉截图验收。字幕审校页面的 DOM/交互控件检查已完成。

### Dashboard 客户端断连处理（承接 B157）
- 保留对 `BrokenPipeError`、`ConnectionAbortedError`、`ConnectionResetError` 的安静处理。
- 非预期异常仍调用父类 `handle_error`，不吞掉一般错误。

### 工作区并行写入一致性
- 一次完整回归运行期间，另一条研究知识文件更新正处于写入中间态，出现过单行粘连的临时 SyntaxError。
- 按当前文件内容做最小换行修复，没有回滚、重写或覆盖其他研究功能逻辑。
- 相关研究审校模块 py_compile 和专项测试均通过；完整回归在文件稳定后重新运行通过。

## 修改文件
- `src/muse/local_dashboard.py`
- `tests/test_local_dashboard.py`
- `src/muse/research_knowledge.py`（仅修复一处相邻语句间缺失换行）
- B156/B157 的 UI 类型和进度文档保持不变。

## 验证结果
- Dashboard 专项：11/11 通过。
- Research Wiki review writer 专项：4/4 通过。
- 相关 Python 模块 `py_compile`：通过。
- 完整 Python unittest：1320 tests，全部通过，1 skipped。
- `git diff --check`：通过（Git 仅提示工作区 LF/CRLF 转换警告）。
- B156 UI：Svelte check 0 errors / 0 warnings，Vite build 成功（先前已验证）。

## 产品状态与安全边界
- 真实字幕审校尚未由人类完成；本轮没有勾选听审项、没有填写真实审核人、没有提交批准口令。
- 没有调用生产字幕批准 API，没有生成生产字幕修订版本。
- 测试用临时 remux 结果仍然只是 B156 的隔离夹具，不进入生产产品库。
- 视频产品保持 `testing`，`ready_to_publish=false`。
- 未执行 Git reset、clean、stage 或 commit；原有未提交和未跟踪内容保留。

## 下一步
1. 对 Dashboard 的实际视觉截图与逐项导航交互做完整验收；确认 favicon 204 在重新启动的新服务实例中无 console error。
2. 由真实听审完成字幕修改与审核，再执行新版本 remux 和全量 QA。
3. 按 B149 的源片段映射重新生成低运动镜头，不能放宽 freeze 检测阈值。
4. 评估成熟粤语女声 TTS 与旁白/音乐混音，区分 music-only 与 narration-led 的响度验收策略。
5. 继续 Wan/ComfyUI 实际推理与 MP4/ZIP/Dashboard 交付闭环验证。
