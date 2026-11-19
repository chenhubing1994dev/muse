# B157｜Dashboard 客户端断连稳定性修复

日期：2026-11-18

## 背景

B156 本机 Dashboard 浏览器验收期间，服务端出现 Windows `ConnectionAbortedError [WinError 10053]`。堆栈显示请求已进入 `local_dashboard.py` 的 API 代理，服务端向客户端写回响应时，对端已经关闭连接。这类情况可能由浏览器导航、超时、页面关闭或自动化客户端取消请求触发；它不等于上游 API 或产品数据损坏，但默认 ThreadingHTTPServer 错误处理会打印整段 traceback，影响运行日志可读性。

## 修改

- `src/muse/local_dashboard.py`
  - 在 `LocalDashboardServer.handle_error` 中识别正常客户端断连类异常：`BrokenPipeError`、`ConnectionAbortedError`、`ConnectionResetError`。
  - 这些异常不再输出误导性 traceback；其他未预期异常仍交给父类处理，保留诊断能力。
  - 没有吞掉代理层其他 OSError，也没有改变 HTTP 状态码、鉴权、Origin、CSP 或 LAN 配对策略。

- `tests/test_local_dashboard.py`
  - 新增回归测试，覆盖上述三种断连异常。
  - 验证预期断连不会向 stderr 输出 traceback。

## 验证

- Dashboard 专项：10/10 通过。
- 完整 Python unittest：1313 tests，全部通过，1 skipped。
- `git diff --check`：通过（仅 Git 提示工作区 LF/CRLF 自动转换警告）。
- WebCodex Runner：在线且与服务端版本/协议完全匹配；运行并发上限 32，本轮任务由 Runner 实际执行。
- B156 的 UI 类型修复仍在工作区：`ProductRecord.subtitle_review.status` 覆盖 `unavailable` 与 `ambiguous`，Svelte check 0 errors / 0 warnings，Vite build 成功。

## 浏览器验收边界

- B156 的 HTTP 健康、首页、动态配置和 JS/CSS 静态资源均返回 HTTP 200。
- 浏览器交互树已识别 Dashboard 导航、灵感输入、产品列表及操作按钮。
- 自动化浏览器实例在截图阶段失效，随后重新查询时没有活动浏览器实例；因此未完成逐项点击、字幕审校表单交互和真实音频播放验证。
- 本轮断连修复是基于真实服务端堆栈证据，并不声称浏览器 UI 完整验收已经通过。

## 保护与未完成事项

- 未执行 Git reset/clean/stage/commit；保留原工作区既有改动。
- 未批准真实生产字幕、未调用生产 remux、未修改任何真实产品 MP4/ZIP。
- 产品仍为 testing，`ready_to_publish=false`。
- 后续继续：恢复浏览器会话并完成 Dashboard 实机交互验收；人工逐条听审字幕后才可触发正式字幕版本；按 B149 映射重做低运动镜头；解决成熟粤语女声 TTS 与旁白混音响度问题；最后重新执行全链路产品 QA。
