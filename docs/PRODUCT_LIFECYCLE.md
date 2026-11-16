# Muse 产品生成与交付流水线

更新：2026-11-10（B-085）

## 当前闭环

1. 自主研究完成后，生成或复用一个 Initiative，保留研究问题、灵感假设、反证、未确定项和来源引用。
2. 本地 AutonomousRuntime 显式启用 `auto_build_products=True`，调用 `LocalProductBuilder` 将 Initiative 转为离线交互式产品。
3. 产品先进入 `testing`，生成 `index.html`、`README.md`、`manifest.json` 和 `product.zip`。
4. Builder 验证固定模板、CSP、文件 SHA-256、ZIP 成员和内容一致性；同一输入重复构建是幂等的，遇到已修改或不安全的产物会拒绝覆盖。
5. 本地状态 API 提供产品列表、预览和 ZIP 下载；控制台的“产品库”展示版本、状态、大小和时间。
6. 用户可离线打开产品、编辑数据、导入/导出 JSON。后续反馈仍需进入 Initiative/改进提案流程，不能把产品测试状态自动升级为发布状态。

## 命令

在 Muse 项目目录运行：

```powershell
$env:PYTHONPATH = 'src'
py -3.11 -m muse.product_builder build <initiative_id>
py -3.11 -m muse.product_builder verify <initiative_id>
```

安装项目包后也提供 `muse-product build <initiative_id>` / `muse-product verify <initiative_id>` 入口。

## 本地目录与 API

- 产品目录：`data/products/<initiative_id>/<input_hash_prefix>/`
- 可运行页面：`index.html`
- 交付包：`product.zip`（含 index.html 与 README.md）
- 校验清单：`manifest.json`
- 列表：`GET /api/v1/products`
- 预览：`GET /api/v1/products/<initiative_id>/<version_id>/preview`
- 下载：`GET /api/v1/products/<initiative_id>/<version_id>/download`

产品目录只在本机保存；控制台 API 仍受 loopback / 配对代理、Bearer 凭据、路径 allowlist、SHA-256 校验和大小上限保护。

## 安全与发布边界

- 只使用 Muse 内置、经过审查的 HTML/CSS/JavaScript 模板；Initiative 内容作为数据转义，不作为可执行代码。
- CSP 只允许固定脚本与样式哈希；禁止外部网络、远程资源、第三方依赖和任意代码执行。
- 浏览器数据使用本地存储；建议定期导出 JSON。不同浏览器或 file/blob origin 的本地存储隔离行为可能不同。
- 产品生成失败会记录失败类型，不会伪装成功，也不应抹掉研究报告。
- `testing` 表示待人工验收，不是 shipped / 正式发布。
- 此阶段只提供通用“灵感实验台”模板，不等于 Muse 已能把任意灵感自动写成任意复杂应用。
- 不自动修改 Muse 自身源代码；代码升级仍需审查、隔离测试和回滚证据。

## B-085 验收记录

- 产品生成专项 4/4；自主研究专项 44/44；状态 API 专项 15/15。
- Python 全量 unittest：932/932，0 failures、0 errors；compileall 通过。
- UI API 测试 14/14；Svelte-check 0 errors / 0 warnings；Vite production build 成功。
- 实际产品 V2：Initiative `d086fea4f2de4cd4850c81045aabbcbf`，输入哈希前缀 `2d48d7f9028aec47d44d`，ZIP SHA-256 `2789fa940097436cd21cf5b8eba9c10283987cf1d551bc1d6498a92509178683`。
- Runner 恢复后的实时验收：Dashboard HTTP 200；产品库前端 JS 资产 HTTP 200 且包含列表/预览/下载逻辑；产品 API 列出 5 个产品版本，5/5 预览成功、5/5 ZIP 下载有效。

## 后续产品模板：一键视频创作（规划中）
- 产品工坊可扩展为多模板工厂；视频创作是独立 `video_creator` 模板，不改变现有灵感实验台模板。
- 复用 B-031 已有 `muse-video make`（把已有图片/视频素材合成为 MP4）、ComfyUI adapter、Velorn MCP adapter、分镜 DAG 与 FFmpeg/FFprobe，不另造视频编辑/调度核心。
- 目标流程：创意/灵感 → 故事与分镜 → 本地 AI 镜头生成 → 素材登记 → 时间线剪辑/字幕/旁白/音乐 → MP4 与可编辑工程、素材、提示词、版本和生产报告一并交付。
- 当前边界：已有素材合成能力不代表 prompt-to-video 已完成；GPU 推理、AI 镜头生成、角色一致性、Velorn 时间线写入/导出及完整一键流程仍需实机验收。视频线仍遵循主路线优先级，不自动下载模型、安装节点或调用云端付费服务。
