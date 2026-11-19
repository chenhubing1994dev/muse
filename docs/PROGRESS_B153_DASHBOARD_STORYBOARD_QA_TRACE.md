# B153｜Dashboard 镜头级 QA 追踪

日期：2026-11-17

## 本轮目标

把 B152 已写入 `qa-report.json` 的 `storyboard_mapping` 接入 Muse 产品库，让用户能从质量告警追到实际分镜、源素材和原片时间点；不依赖新渲染或模型推理。

## 实现

修改：
- `src/muse/status_api.py`
- `tests/test_status_api.py`
- `ui-prototype/src/lib/api.ts`
- `ui-prototype/src/App.svelte`
- `ui-prototype/src/workflow.css`

### 1. Status API 安全输出镜头映射

- 从已验证产品的 `qa-report.json` 读取 `storyboard_mapping`。
- 仅将与原始 warning 的 code、start_seconds、duration_seconds 一致的映射附加到该 warning，防止数组错位或数据不匹配。
- 只输出有限、合法的镜头时间区间；重叠区间必须同时落在镜头区间和 warning 区间内。
- 限制每条 warning 最多输出 12 个镜头匹配。
- scene id 与源文件名使用严格 allowlist；只展示源文件 basename，不泄露本地绝对路径或任意 QA 私有字段。
- 无有效映射的旧产品继续兼容原有质量告警结构；映射不完整时不猜测。

### 2. Dashboard 镜头级复核

- 每条质量告警下面展示匹配的 shot id、源素材文件名、镜头起止时间及告警重叠时长。
- “定位此镜头”直接调用现有 MP4 预览能力，并跳转到 warning 与该镜头的重叠起点。
- 告警不落在分镜时间范围时，明确提示并保留原片时间定位。
- 原有“定位复核”、产品下载、字幕人工审核和发布门槛保持不变。

## 验证证据

- Status API：30 项测试全部通过。
- Svelte 检查：0 errors、0 warnings。
- UI Node 测试：37 项全部通过。
- Vite production build：119 modules transformed，构建成功。
- Python 全仓测试：`Ran 1298 tests`，`OK (skipped=1)`。
- 新增安全测试：验证映射能展示有效镜头，同时拒绝带本机路径的源文件和任意私有字段。

## 实机边界

- 本轮没有执行真实 Remotion 生产渲染。环境检查显示 Node.js 24、npm 11、FFmpeg 8.1.1 可用，但项目当前无法解析 `remotion`、`@remotion/cli`、`@remotion/renderer` npm 包。
- 检查时未发现运行中的 ComfyUI 进程；没有启动新的 Wan 推理或 AI 镜头生成。
- 没有覆盖既有 MP4、素材、字幕或产品包。
- 没有进行粤语旁白人工听审、字幕批准或正式发布。
- Dashboard UI 已构建验证，但本轮没有在真实浏览器中做人工视觉验收。

## 工作区保护

- 保留原有工作区脏改动和未跟踪文件。
- 未执行 git clean、reset、commit 或清理其他产物。
- UI build 输出留在项目既有构建目录；没有触碰产品成片目录。

## 下一步

1. 找到并确认项目预期的 Remotion runtime 安装/调用路径，检查依赖版本与许可；不盲目升级依赖。
2. 在独立 staging 目录选用现有素材跑一次真实 `brand_scene_transitions` 渲染，核对 MP4、scene_timeline、QA mapping、ZIP 与 Dashboard 下载。
3. 复查历史成片中 shot-05、shot-04、shot-01 的静帧告警，优先从源素材与剪辑节奏修复；shot-03 检查缩放/裁切。
4. 继续 CosyVoice-Cantonese 源码静态审计，保持不安装、不下载大模型，直到 AMD/CPU 兼容与脚本安全性得到证实。
5. 粤语旁白、字幕和发布继续保留人工审核门槛。

## 结论

B153 完成了“QA warning → 分镜匹配 → 源素材 → 原片时间点”的 Dashboard 复核入口，并通过全仓与 UI 验证。它改善了现有成片的可追溯性，但不代表本轮生成了新影片，也不代表 AI 视频生产线已经通过实机验收。
