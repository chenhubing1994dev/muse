# B133 — 视频产品技术 QA 正式接入

日期：2026-11-13

## 本轮目标

把 B132 已存在的 MP4 技术质检从独立 CLI/函数，接入 Muse 正式视频产品构建链；每个产品都必须随包保存 QA 报告，并且无论技术 QA 通过、失败还是阻塞，都不能绕过内容、版权、披露和账号审核直接发布。

## 已实现

1. 三条产品入口统一接入 QA：
   - 本地素材合成：VideoProductBuilder.build_from_source()。
   - 本地 Wan 2.1 文生视频：VideoProductBuilder.build_from_prompt()。
   - 已生成 MP4 的恢复/补封装：VideoProductBuilder.package_generated_clip()。
2. QA 报告进入产品账本：
   - 每个新产品输出 qa-report.json，报告内使用相对路径 video.mp4，并记录最终 MP4 SHA-256。
   - manifest.json 增加 quality_gate：状态为 passed、failed 或 blocked，并指向 QA 报告。
   - product.zip 收录 QA 报告；manifest 同时记录报告文件与 ZIP 的哈希。
3. 发布状态保持 fail-closed：
   - 产品状态保持 testing。
   - quality_gate.ready_to_publish 固定为 false。
   - 技术 QA 通过只表示可通过当前技术检查；仍需内容、事实、版权/肖像、隐私、广告/AI 披露、平台规则与账号授权审核。
   - 技术 QA 失败或缺少工具时分别落为 failed / blocked，不会伪装成通过。
4. 校验与知识关联：
   - verify_path() 校验 QA 报告结构、报告与 MP4 的 SHA-256 对应关系、manifest gate 状态和 ZIP 成员/内容。
   - 保留旧版没有 qa-report.json 的历史产品读取兼容。
   - Creator Initiative 产品事件与 Research Wiki 视频资产节点都记录 quality_gate。
   - Builder 版本升至 2，并将版本写入输入指纹，避免复用升级前没有 QA 报告的旧版产品目录。

## 测试与验证证据

- Python 语法编译检查：通过。
- tests/test_video_product_builder.py + tests/test_video_qa.py：最终复跑 22/22 通过，包含新产品 QA 通过/失败、QA 报告哈希防篡改、旧版无 QA 报告产品兼容。
- tests/test_video_product_api.py + tests/test_video_dashboard_integration.py：7 项通过，覆盖 API、产品列表、MP4/ZIP 下载与 Dashboard 关联。
- 全项目 `unittest discover`：发现并运行 1,123 项，42 项错误、1 项跳过，退出码 1；因此全项目测试**未通过**。当前保留的错误日志中可确认至少部分时区用例因环境缺少 `tzdata`、无法解析 `Asia/Shanghai` 而失败；由于 Runner 返回的是截断后的日志，不能断言全部 42 项均由此单一原因导致。
- 本轮 Runner 的 Python adapter 未提供可靠 pytest 汇总，且当前 Python 环境未安装 pytest；使用 Windows `py.exe -3` 直接执行标准库 unittest，取得逐条测试名称与退出结果。没有安装依赖，也没有把结构化 pytest 的空输出记为通过。全套测试的 42 项错误仍需在补齐 Python 时区数据后分类复核。

## 真实集成生产验证（2026-11-13）

使用现有六段已验收 Wan 2.1 镜头执行 scripts/muse-high-value-short.py 的生产函数，输出到：

- 竖屏 MP4：data/prometheus/high-value-short-b133/ai-verification-three-steps-9x16.mp4
- 完整交付包：data/prometheus/high-value-short-b133/delivery-bundle.zip
- 独立产品目录：data/products/556e9781babaf2fe314e5ee46d41c5df/43e84ef072e3cfc5430f/

真实结果：

- 24.5 秒，1080×1920，24 FPS，H.264 + AAC stereo / 48 kHz；文件 13,739,995 bytes。
- FFmpeg 全片解码通过；VideoProductBuilder 新 QA 报告状态 passed。
- 产品 ZIP 包含 README、storyboard、qa-report.json、video.mp4；ZIP CRC 与各文件哈希复核通过。
- 完整 delivery-bundle.zip 含 9 项：竖屏视频、SRT、封面、storyboard、source-notes、原创氛围 WAV、README、产品 ZIP、产品 manifest；CRC 复核通过，大小 30,052,253 bytes。
- Creator Initiative 记录了产品事件；Research Wiki 新建视频资产节点并关联同一 evolution cycle 的 3 个研究节点。
- 产品仍为 testing，quality_gate.ready_to_publish=false。这是技术检测通过，不是版权/事实/披露/平台审核或平台发布通过。
- 本次复用现有镜头，没有重新执行 Wan 文生视频；FFmpeg 使用的编码器为 AMD AMF。

交付完整性复核：

- MP4 SHA-256：a9e30f4ef5225ceab8781c3bb8ab548ae880da8c30f8632525c434c46659e69b
- 产品 ZIP SHA-256：7f4899c10c8a0bb966d83bf07062b85f3ff354db443157b159608288788dfe42
- delivery-bundle.zip SHA-256：019f0b37db835b41746450fd0802e1b5cef48a566f887e3006c482014190536a
- evolution cycle：cycle_34d2b8116fac4b7da6900c3a04a31b34

## 尚未完成 / 不能宣称

- 本轮没有重新运行 GPU Wan 实机生成，也没有把预览级视频标记为商业级成片。
- 当前 QA 以 MP4 探测、流/尺寸/帧率/时长、可选音轨、完整解码和哈希为主；黑帧/冻结帧、响度/静音、字幕同步/错字、封面校验和平台规格 profile 仍需补齐。
- 尚未交付可配置通用生产 Recipe、研究选题自动评分、多平台横竖屏导出/发布包、官方 OAuth/API 发布适配器、真实分析/收益数据回流和 A/B 复盘。
- 不存在平台账号真实授权或官方 API 发布回执，因此当前不是自动发布系统，也不能声称已经变现。

## 下一阶段顺序

1. 将研究来源、受众假设、脚本、分镜、镜头素材和 QA 报告纳入一个可复现的 Pipeline Recipe。
2. 补齐黑帧/静帧、音频响度/静音、字幕时间轴和封面检查。
3. 建立版本化平台 profile 与发布包，至少导出对应成片、字幕、封面、标题/简介、来源/授权清单和披露草稿。
4. 再逐平台核验官方 API 与账号资格，按最小权限授权后适配发布及状态回读。
5. 最后导入真实分析、转化与结算数据，驱动选题和内容实验。

本文件记录的是代码和测试证据，不代表全部商业视频生产线已完工。
