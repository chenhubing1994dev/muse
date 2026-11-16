# B-139｜旁白感知背景音乐 Ducking

日期：2026-11-16

## 目标

在旁白存在时自动压低背景音乐，旁白间隙恢复音乐；保留可追溯的原始音乐素材、混音参数与哈希，避免“把两条音轨直接叠加”造成旁白被淹没。

## 实施

- `VideoProductBuilder.package_generated_clip(..., background_music_path=...)` 接入可选 BGM 输入；必须同时提供 narration_path，禁止把纯音乐误当作旁白 sidechain。
- FFmpeg filter_complex 使用 `sidechaincompress`：旁白音轨先 `asplit` 为 sidechain 检测与最终人声混音两路；背景音乐作为被压缩主输入，人声作为 sidechain 控制输入。
- 初始混音参数：BGM gain 0.22、threshold 0.025、ratio 8、attack 20 ms、release 450 ms；最终使用 AAC 192 kbps，视频流 copy，输出时长由视频 `-shortest` 约束。
- BGM 原始文件以 `music/background.<ext>` 放入 product.zip；manifest/storyboard 记录文件大小、SHA-256、archive path 与完整混音参数；`verify_path` 校验 ZIP 成员、字节数与哈希。
- 输入 BGM 做普通文件、扩展名、symlink parent、大小校验；封装期间复核源文件哈希。背景音乐资产哈希进入产品 fingerprint。Builder version 升至 7。

## TDD 与实机验证

- 新增 `test_voice_aware_background_music_is_sidechain_ducked_and_hash_verified`：先以旧签名 RED，再验证 sidechain 参数、manifest、ZIP 与 `verify_path`。
- 视频/粤语旁白定向回归：42/42 通过；`compileall -q src tests` 通过。
- 全项目回归：**1,229 项通过，1 项跳过，0 项失败**。
- 真实 FFmpeg 8.1.1 sidechain filter 独立合成测试：输出 MP4 4.0 秒，完整解码成功，音频指标可读取。
- 真实 VideoProductBuilder 端到端合成测试：生成源视频 + 合成 voice WAV + 循环 BGM WAV，经正式 package_generated_clip 流程完成 sidechain 混音；产品 ZIP 同时包含旁白与原始 BGM，BGM SHA-256 与 manifest 一致，`verify_path` validated=true。产品仍为 testing，ready_to_publish=false。
- 以上是合成测试素材，不代表真实粤语 TTS、真实 Wan 影片或人工听感已验收。

## 尚待完善

- 当前阈值与增益是保守默认值，后续应根据旁白响度、BGM LUFS、场景类型做自动增益建议，并提供可审计的参数配置。
- 削波检测仍是 sample-peak 风险启发式，不输出逐样本精确 clipped-sample 数。
- Whisper 模型下载超时；真实 ASR/CER/SRT、人工试听、真实 Wan 成片与发布审核仍未完成。
