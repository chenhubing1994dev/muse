# Lightpanda 能力融合记录

更新：2026-11-20

## 定位与边界

上游：<https://github.com/lightpanda-io/browser>。Lightpanda 是独立的无头浏览器运行时，使用 JavaScript/V8 与 CDP 接口，不是 Chromium/Blink/WebKit 的替代发行版，也不负责 Muse 的研究调度。

Muse 的唯一调度权仍属于 Muse Commander。Lightpanda 只作为可选网页执行器；研究授权、公共 URL/DNS 校验、证据目录、哈希、来源归因和知识入库均由 Muse 管理。不会启用 Lightpanda 自带 Agent/MCP 或另建任务队列。

## 第一阶段交付范围

`src/muse/lightpanda_adapter.py` 提供独立的 `LightpandaBrowserAdapter`：

- 只连接已经启动的本地 CDP 服务；通过 `MUSE_LIGHTPANDA_CDP_URL` 显式配置，默认禁用。
- CDP 地址只允许 loopback IP 或 `localhost`，拒绝公网/局域网地址、凭据和非 HTTP/WebSocket scheme。
- Playwright Python 使用惰性导入；基础 Muse 不依赖 Playwright。可选依赖 extra 为 `lightpanda`。
- 采集前必须同时提供 `authorization_confirmed=True` 与 `approved=True`；只支持 public-only。
- 导航前、每个被拦截的 HTTP(S) 页面请求、最终 URL，以及写入证据的每个链接都调用 Muse 现有 `_validate_public_url`；非 HTTP(S)、超长或解析到私网的链接不会进入证据清单。
- 将页面标题、正文、有限链接、采集时间、最终 URL 与正文 SHA-256 写入新建的 `capture.json` / `capture.md`；拒绝覆盖已有目录，并限制输出必须位于 Muse workspace。
- 不截图、不下载媒体、不提交表单、不执行自动化点击；页面自身 JavaScript 由 Lightpanda 执行，以便取得动态 DOM。
- 不启动/安装 Lightpanda，不自动安装 WSL2，不启动或开放 CDP 监听端口。

## 与现有浏览器分工

| 能力 | Lightpanda | ego-lite |
|---|---|---|
| 动态 JavaScript / DOM | 目标能力 | 已有 |
| CDP / Playwright 连接 | 支持 | 不走此接口 |
| 视觉截图 | 不支持 | 已支持 |
| 媒体资产采集 | 本阶段不支持 | 已有受限实现 |
| Muse 默认研究链 | 未接入 | 保持现状 |

因此，本阶段不会替换 `EgoBrowserAdapter`，也不会改动 `HybridSourceReader` 或 `AutonomousResearchEngine`。只有后续完成真实 CDP 兼容性、网络隔离与回退测试后，才评估以显式配置方式接入 Reader；失败必须回退到现有 ego-lite / HTTP 路径。

## 安全限制

- CDP endpoint 必须保持 loopback；不要将 CDP 端口暴露到局域网或公网。
- Playwright route handler 对每个 HTTP(S) 请求执行 Muse 公共 URL 校验并拒绝私网/本机目标。但浏览器进程的 DNS 解析、重绑定和非 HTTP 通道不等同于 OS 级网络沙箱；此 adapter **不能单独视为完整 SSRF 隔离边界**。生产使用还需由运行环境限制网络出口，真实环境验收前不得自动接入自主研究。
- 页面内容均为不可信数据，不能成为 Agent 指令、工具授权或事实结论；入库仍须经过现有 Research Wiki 的证据审查与 Claim Promotion Gate。
- Lightpanda 上游默认许可证为 AGPL-3.0-only。Muse 仅通过独立进程和公开 CDP 协议进行可选集成，不复制其源码。若未来分发 Lightpanda 二进制、修改版本或形成组合分发，须先审查适用的 AGPL 义务。
- 上游文档给出的 Windows 安装路线为 WSL2；本阶段不自动安装或配置 WSL2，也不假设原生 Windows 安装路线。

## 当前状态与下一步

当前完成的是适配器代码骨架和测试优先的首轮实现；尚未验证真实 Lightpanda CDP 服务，尚未在 Muse 研究主链启用，也没有安装 Playwright/Lightpanda。下一步先通过定向单测、现有研究回归和静态检查；之后再在隔离环境验证 CDP、重定向/子资源拦截与运行时兼容性。验证未完成前保持默认禁用。
