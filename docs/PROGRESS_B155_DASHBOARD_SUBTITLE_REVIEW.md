# B155｜Dashboard 粤语字幕逐条人工审校

日期：2026-11-17

## 本轮目标

把 B148 的逐条字幕审核数据、B150 的不可覆盖 MP4 字幕 remux 与 Creator 发布门槛接入 Muse Dashboard。用户可以在作品卡片中逐条听审、修改字幕，并明确批准生成新版本；任何机器识别指标或字幕批准都不能绕过视频技术 QA 与正式发布检查。

## 实现

修改：
- `src/muse/status_api.py`
- `tests/test_status_api.py`
- `ui-prototype/api.js`
- `ui-prototype/api.test.js`
- `ui-prototype/src/lib/api.ts`
- `ui-prototype/src/App.svelte`

### 1. 产品与审核资料绑定

- 产品列表仅显示安全摘要：pending/approved、人审者与批准时间等。
- 新增只读 GET `/api/v1/products/{initiative}/{version}/subtitle-review`。
- 审核资料必须同时匹配产品旁白音频 SHA-256、ASR 模型仓库与 revision、参考脚本、原始 ASR transcript、SRT SHA-256。
- 限制扫描范围、文档大小、SRT 大小与 cue 数；发现多个匹配审核文件时 fail closed，不猜测。
- 仅从已验证的 video_creator 产品开放审核接口。

### 2. Dashboard 逐条听审界面

- 视频作品卡片在字幕待审核时出现「逐条字幕审校」入口。
- 展示每条字幕的时间区间与不可变 ASR 原文；提供最终字幕编辑框和逐条「已听审」确认。
- 提供「打开原片听审」入口，提示用户逐条对照原始粤语旁白。
- 必须全部 cue 都已确认，并填写审核人及精确批准口令 `APPROVE-HUMAN-REVIEW`，才允许提交。
- 前端状态只用于交互；后端仍重新校验完整 cue 列表、修订内容和明确批准口令。

### 3. 生成新版本而非覆盖原片

- 新增经过 bearer token、loopback、Origin 与 JSON Content-Type 校验的 POST `/api/v1/products/{initiative}/{version}/subtitle-review/approve`。
- 服务端调用既有 `apply_human_subtitle_review`，并复用 B150 的 `VideoProductBuilder.package_approved_subtitle_revision`。
- FFmpeg 以 stream copy 保留原视频与音频，添加可切换的粤语 `mov_text` 字幕轨；FFprobe 与完整 QA 重新验证。
- 新 MP4、SRT、审核 JSON、QA 报告、manifest 与 ZIP 形成新的 content-addressed version；旧版本不覆盖。
- Creator 仍保持 testing；`ready_to_publish` 继续由完整 QA/其他发布门槛决定，不会因字幕批准自动发布。

## 验证证据

- Python 3.11 全仓：`Ran 1301 tests`，`OK (skipped=1)`。
- Status API 专项：30 项通过，覆盖资料读取、匹配约束、显式批准、未批准请求拒绝与新版本回传。
- Dashboard Node API 测试：37/37 通过。
- Vite production build：119 modules transformed，构建成功。
- `py_compile` 与目标文件 `git diff --check` 通过。
- 同一轮全仓验证也包含 B152 LAN 代理修复、B153 镜头级 QA 追踪及 B154 Remotion 运镜集成的当前工作区代码。

## 尚未执行／发布边界

- 没有替用户听审或批准任何生产字幕；当前生产作品仍是 pending human review。
- 没有对真实生产 MP4 执行字幕 remux；本轮只验证接口与构建流程，B150 的 remux 单元/集成测试继续负责其代码路径验证。
- Dashboard 尚未完成真实浏览器人工视觉验收。
- 字幕批准不是事实审查、版权/肖像审查、平台规则检查或发布批准。
- 不代表新 Wan/ComfyUI 推理已运行，也不代表完整 AI 视频生产与商业发布闭环完工。

## 下一步

1. 在本地 Dashboard 打开待审核成片，逐条播放并听审粤语旁白；只有真实听审后才勾选和批准。
2. 对用户批准的审核记录执行新版本 remux，核验 MP4 字幕轨、音视频完整性、ZIP CRC、manifest hashes、QA 和 Creator 状态。
3. 处理 B154 的 `audio_loudness_low` 复核警告，并对新版本做浏览器端视觉验收。
4. 继续验证成熟女声粤语 TTS 与真实 Wan 推理，不把已有素材的 Remotion 后期渲染误报成新 AI 镜头生成。
