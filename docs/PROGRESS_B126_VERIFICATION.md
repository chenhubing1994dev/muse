# B-126 验证复核与桌面交付记录

日期：2026-11-14
项目：D:\AI AGENT\Muse

## 运行状态

- WebCodex Server / Runner 0.5.0 在线，Runner 与 Server build alignment 为 exact，source alignment 为 aligned。
- Muse Runtime Project 已准确定位，项目为非 Git 工作区。
- Runner Job 并发上限 32；本轮检查时活动 Job 为 0。该数字是执行槽位上限，不代表 32 个 Agent 或模型推理已经启动。
- ACP Coding Agent Provider inventory 仍为空。

## 验证证据

- ui-prototype：npm run check 通过，Svelte 0 errors / 0 warnings。
- ui-prototype：npm test 通过，Node TAP 25/25。
- ui-prototype：npm run build 通过，Vite transformed 116 modules。
- Python 3.11：项目 .venv 执行 python -m unittest discover -s tests -q，1,164 tests passed，127.230 秒，退出码 0。
- 首次用系统 py -3.11 运行全量测试时没有项目 PYTHONPATH，出现 ModuleNotFoundError: muse；改用项目 .venv 后完整套件通过。该首次环境导入错误不计入最终测试失败数。

## Tauri Release 构建

- 标准 target/release 构建无法替换正在运行的 muse-desktop.exe，Windows 返回 os error 5。没有结束现有桌面进程，也没有覆盖现有 EXE。
- Runner 的 rustup 未设置 default toolchain；通过明确设置已安装的 stable-x86_64-pc-windows-msvc，并使用 cargo build --offline --release --features custom-protocol，在独立 Cargo target directory 成功完成构建。
- 新 Release EXE：desktop/src-tauri/target/b125-release-check/release/muse-desktop.exe
- 文件大小：8,686,080 bytes
- SHA-256：1d56b720f97bccaf37e532c2daaf4a3b2676b6dd9f33be63a952bf180b94b6fb
- 构建退出码：0；Cargo 显示 release profile optimized build finished。
- 该 EXE 尚未启动；现有桌面实例未被替换。构建成功不等于桌面交互或截图验收通过。

## Remotion + FFmpeg 集成实测

- 运行 scripts/verify_remotion_motion.py，使用项目 .venv Python 3.11；真实 Remotion 4.0.543 与 FFmpeg 执行，Job exit code 0，报告 status=passed。
- Remotion TransitionSeries：3 场景、512x288、12 FPS；交替 crossfade/wipe 转场，音轨保留，Muse Video QA passed，完整解码通过。
- MuseBumper：music 与 narration 两种模式均完成真实渲染、完整解码与 QA。Narration 测试夹具出现 sustained_silence 警告（夹具音频本身不包含旁白内容），该警告被如实保留，不作为测试失败。
- VideoProductBuilder.build_from_source、build_from_prompt、package_generated_clip 三个入口均生成并验证 Remotion 增强产品，product_validated=true；MP4 与 ZIP SHA-256 均已输出。
- Wan 生成在该集成测试中被 fixture mock；未调用 GPU，故此结果只证明 Remotion 后期链路，不证明真实 Wan 生成。
- 测试素材及输出位于 TemporaryDirectory，执行结束后自动清理。

## Remotion 工程静态检查

- video-motion 的 npm run check（TypeScript tsc --noEmit）通过，退出码 0。

## ComfyUI 运行诊断

- 只读健康探测：127.0.0.1:8188 返回 unavailable；Windows TCP 8188 没有监听；进程命令行中没有 ComfyUI/python_embeded 进程。
- 已确认便携安装目录存在，当前配置路径为 D:\\AI\\ComfyUI-Rinsei\\portable\\ComfyUI_windows_portable。
- 2026-11-13 旧 stderr 日志记录 ComfyUI 0.23.0、Python 3.12.10、PyTorch 2.10.0+rocm7.14.0a20260603、ROCm 7.14、AMD gfx1031、RX 6750 GRE 12GB，并到达 Starting server；可用日志中没有 CLIP 重载崩溃 traceback。
- 2026-11-14 logs 子目录中的 muse-comfyui stdout/stderr 日志为空。现有日志不足以确定此前 CLIP 二次加载崩溃根因。
- 当前 muse-comfyui-start.cmd / muse-comfyui-foreground.ps1 只是前台启动器；src/muse/model_supervisor.py 管理的是 llama.cpp，不是 ComfyUI。尚无专用 ComfyUI 长驻监督器与有界崩溃恢复策略。
- 本轮没有启动 ComfyUI、没有加载模型、没有占用 GPU；保留现有 GPU/模型状态。

## 当前未完成

1. 对独立构建的 Release EXE 做真实启动与桌面交互验收，确认新 UI 确实嵌入并显示。
2. 完成截图/窗口尺寸/键盘焦点/减少动态效果等 UI 验收。
3. 将视频 Attempt Ledger、Creator Feedback、Research Wiki 与 Evolution Cycle 的信息在桌面 UI 中进行真实端到端检查。
4. 继续修复 ComfyUI 长驻 supervisor 与 CLIP 重载崩溃，验证真实 Wan 2.1 MP4/ZIP 端到端产出。

## 文档维护限制

docs/PROGRESS.md 当前存在混合 LF/CRLF 行结束符；WebCodex revision-fenced structured editor 因混合换行拒绝修改，且未写入任何内容。为避免通过脚本绕过版本保护，本轮将完整验证记录单独保存于本文件。主进度文件需在后续先通过安全、可审查的换行规范化方案处理后再追加索引。
