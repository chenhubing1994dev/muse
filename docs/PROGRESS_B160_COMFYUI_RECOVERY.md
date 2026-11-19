# B160｜ComfyUI 本机恢复与 Wan 2.1 模型就绪检查

日期：2026-11-19

## 本轮完成

### 1. Runner 与回归
- WebCodex Runner `administrator-72132063e1ed46f0` 恢复在线，协议兼容、build alignment exact，Runner 并发上限 32。
- 恢复原 Muse Workflow Session `wc_sess_zXK5GkUqtugHtnzJ`，保留现有 dirty workspace；没有 reset、clean、stage 或 commit。
- 使用项目 Python 3.11 并显式设置 `PYTHONPATH=src` 执行 `py -3.11 -m unittest discover -s tests`。
- 全仓 Python 回归：1396 tests，158.045s，OK，1 skipped，退出码 0。
- 更新 B159 进度文档，补记全仓回归最终证据。

### 2. ComfyUI 定位与启动
- 找到已安装环境：`D:\AI\ComfyUI-Rinsei\portable\ComfyUI_windows_portable`。
- 使用现有 AMD 启动脚本 `D:\AI\ComfyUI-Rinsei\run_comfyui_amd.bat`，通过 supervisor-owned detached process 启动；没有安装依赖、下载模型或更改驱动。
- ComfyUI API `http://127.0.0.1:8188/system_stats` 返回 HTTP 200，版本 0.23.0。
- 设备报告：AMD Radeon RX 6750 GRE 12GB，启动检查时 VRAM total 12,868,124,672 bytes、free 12,721,029,120 bytes。
- ComfyUI 原生 `UNETLoader`、`VAELoader`、`CLIPLoader`、`KSampler`、`SaveVideo` 均存在；`VHS_VideoCombine` 不存在。Wan 相关原生节点可在 `/object_info` 中发现。

### 3. Wan 2.1 模型文件检查

启动脚本实际使用的模型目录为 `D:\AI\ComfyUI-Rinsei\portable\ComfyUI_windows_portable\ComfyUI\models`：

| 模型角色 | 文件 | 字节数 |
|---|---|---:|
| Diffusion | `diffusion_models\wan2.1_t2v_1.3B_fp16.safetensors` | 2,838,303,560 |
| VAE | `vae\wan_2.1_vae.safetensors` | 253,815,318 |
| Text encoder | `text_encoders\umt5_xxl_fp8_e4m3fn_scaled.safetensors` | 6,735,906,897 |

ComfyUI `/object_info` 的对应 Loader 模型下拉项已识别上述三个文件名。后续实机烟雾测试已进一步确认权重加载和推理成功。

### 4. 首次 Wan 2.1 GPU 推理实测（Smoke Test）
- 通过 ComfyUI `/prompt` 提交原生节点工作流，`node_errors` 为空，服务返回 prompt_id：`857bd11c-6069-47e1-92ee-d2bba1005ec1`。
- 使用 Wan 2.1 T2V 1.3B FP16 + UMT5 XXL FP8 + Wan VAE；输出 256×256、5 帧、8 FPS、4 sampling steps，作为最小烟雾测试，不代表正式画质配置。
- ComfyUI history 状态 `success`，SaveVideo 输出：`ComfyUI/output/muse_b160/wan21_t2v_smoke_00001_.mp4`。
- 文件大小 13,208 bytes；SHA256 `84C69942BE0E65C707F63AA9270B7DC768C15824D9DCCF8C46B981936762DBBB`。
- FFprobe：H.264、256×256、8 FPS、5 帧、0.625 秒；FFmpeg 全文件解码退出码 0；`freezedetect=n=-50dB:d=0.25` 未报告冻结区间。
- 这是首次确认模型加载、AMD GPU 推理、ComfyUI 视频封装和解码完整链路成功；超短低分辨率 smoke test 只证明技术链路，不作为可发布影片，也不证明叙事运动质量/音频/字幕/交付包通过。

## 未完成与边界
- 已完成 1 次 Wan 2.1 T2V GPU smoke test；尚未生成发布级镜头或替换任何生产视频。
- ComfyUI 当前可访问不代表长期服务健康；detached Job 需持续观察，崩溃时先检查同一 Job 和日志，不重复启动第二实例。
- 已额外提交并完成 384×384、17 帧、8 FPS、8 steps 的运动测试 prompt `a916df43-ddb0-43cc-9c4b-dbaf86c76b20`；history `success`，输出 `wan21_t2v_motion_test_00001_.mp4`，41,550 bytes，SHA256 `961E35F883451A2FE2BB47D15F7D8C854E9B84F12C67C70EA59EA2444D372DA2`。FFprobe：H.264、384×384、17 帧、2.125 秒；FFmpeg 全文件解码退出码 0。逐帧 framemd5 为 17/17 unique；相邻帧差异 `signalstats.YAVG` 约 0.397–1.147（8-bit luma level），说明帧并非完全重复，但变化幅度偏弱；`freezedetect=n=-50dB:d=0.25` 报告多个连续 0.25 秒低变化区间。当前判定为低运动/需复核，不是 motion QA 通过，镜头不得进入生产。
- 下一步检查 384×384 测试的逐帧差异/冻结阈值灵敏度，区分缓慢镜头运动与真实静帧；必要时调整 prompt、seed、采样参数或构图后重新 staging。只有更长镜头的运动 QA、音频/字幕与交付检查均通过后，才替换 B149 低运动镜头；绝不修改既有生产 MP4/ZIP，也不降低 QA 阈值。
