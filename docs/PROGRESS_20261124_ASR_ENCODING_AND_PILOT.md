# Muse 进度：粤语 ASR 编码完整性与商业样片（2026-11-24）

## 本轮目标

把本地粤语旁白视频从“能生成”推进到“可审查、可追溯、不会误报通过”的商业草稿；同时核实网站、社媒内容包的现存交付物。

## 已修复

- `src/muse/cantonese_asr.py`：CER 比对前增加 Unicode 编码完整性检查。U+FFFD 替换字符或非预期文字系统会将结果标记为 `needs_review`，即使参考稿与识别稿恰好得到 CER=0，也不能假通过。
- `src/muse/monetization_pilot.py`：Python 3.11 → 3.14 的参考稿改用 Base64 UTF-8 ASCII 参数传递，避免 Windows 命令行/文本编码歧义。
- 发布门禁把“编码完整性”与“识别准确度”分开：文字编码完整但 CER 超标时，编码状态仍为 passed，整体 ASR 状态仍为 needs_review。
- 发布门禁要求存在非空 transcript、结构化 comparison、有效 CER；乱码或缺失字段不能解锁发布。
- 对应负向测试覆盖相同乱码文本、替换字符、编码状态与 CER 状态分离。

## 验证

- Python 3.11 `py_compile`：`cantonese_asr.py`、`monetization_pilot.py` 通过。
- PSMUX 定向回归：`test_cantonese_asr.py` + `test_monetization_pilot.py`，17/17 PASS。
- 第二轮真实本地生成验证成功，输出目录：`data/monetization/first-cantonese-pilot-2026-11-24-r3/`。
- 视频：24.512 秒，1080×1920，H.264 + AAC；完整解码通过。
- 旁白：本地 Canto-TTS Nano；Whisper Cantonese 本地识别及时间戳字幕已生成。
- ASR CER：0.214286（门槛 0.10），因此状态为 `needs_review`；编码完整性为 passed。该结果没有降低门槛或伪造通过。
- ZIP 完整性通过；`delivery-package.zip` 为 15,639,955 bytes，SHA-256：`c7da8233b9190ee802451d2a70b89f368d18853bb2c9f9198425c0110451b682`。
- 旁白文本实验 r4（目录 `data/monetization/first-cantonese-pilot-2026-11-24-r4/`）CER 为 0.259259，较 r3 的 0.214286 更差；r3 保留为当前较佳草稿，r4 只作实验记录，两者均未解锁发布。
- 发布状态仍为 `draft_only`；`can_publish=false`、`ready_to_publish=false`。人工听审、字幕校对、画面/模型商用权利、事实与平台披露审查仍待完成。

## 已存在的商业资产

- 网站预览包：`outputs/site_launch_preview_20261124_v2/site-preview.zip`（5,511,523 bytes；13 个归档条目）。页面包括 index、showcase、products、portfolio；本地链接/锚点/响应式 CSS/披露与 no-network 表单验证已通过。尚未部署到公网。
- 五平台社媒内容包：`outputs/creator_pilot_20261124/social-content-kit-v2/social-content-kit.md`（8,036 bytes），含逐平台标题、文案、简介、来源、标签、CTA、排期与审核提醒。
- 这些是本地交付物，不代表已公开发布、已获得询盘或已产生收入。

## 商业与安全状态

- 本轮复用已有 Wan 竖屏镜头，没有重新启动 GPU 视频生成。
- 未写生产数据库；未连接社媒账号；未调用公开发布 API；未购买服务或支付费用。
- 当前没有经核验的平台收入数据，收入仍为 $0 / 未确认。
- 网站公开部署、真实表单/CRM 收件、社媒排程和收款需要用户连接对应服务并完成账号授权。

## 下一步优先级

1. 保持 CER 门槛 0.10，不以放宽标准换取绿色状态；调整粤语旁白文本/发音并做真人听审，再生成新版本。
2. 修订字幕草稿并逐句听审；确认声音、画面、模型和来源素材的商用许可与署名义务。
3. 把网站询价入口接入真实收件/CRM，完成公网部署前的域名、隐私与安全检查。
4. 将五平台内容包接入已授权的社媒排程工具，先人工确认首发内容，再记录曝光、点击、询价、成交与净收入。
5. 继续保持单写者、PSMUX 单飞、发布审批与预算/存储/GPU 配额；不得绕过 Commander 或启动未审批的旧 Worker 路径。