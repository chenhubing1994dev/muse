# B355｜旁白分镜关键词素材匹配

日期：2026-11-24

## 本轮实现

- `src/muse/media_compositor.py` 新增 `match_assets_to_segments()`：按旁白文本、visual、keywords、topic 与素材 title/snippet/description/tags 的词项重叠，为每个分镜挑选素材。
- 支持英文词项与中文连续字串的 2/3 字符片段，并加入少量显式跨语言主题别名（太空/宇宙、城市/街道、餐饮、美食、科技等）。
- 在可用素材数量允许时优先一镜一素材；没有命中时标记 `fallback_unmatched`，素材耗尽后标记 `reused_fallback`，避免静默伪装成语义匹配。
- `MuseSceneTimeline` 每个 scene 新增 `asset_match`（状态、重叠分数、命中词项、素材索引），保留原有 source URL、license 与 `rights_review_required=true`。
- 明确这是可解释的关键词匹配，不是 embedding/视觉理解；素材许可未知仍不可视为商业可用，必须经过独立版权审核。

## 验证

- Python 3.11，经 `scripts/muse_dev.py → psmux` 执行 `tests/test_media_studio.py`：**16/16 passed**，exit 0。
- 新增测试覆盖中文/英文主题命中、不同分镜分配、无匹配回退、素材不足时复用；原有下载安全、尺寸比例、裁切、时间轴与 SQLite manifest 测试通过。
- 未执行真实图片搜索/下载、未启动 Wan/ComfyUI/GPU、未生成新视频、未发布或联系客户。
- 项目仍非 Git 工作区；本轮按读取版本、逐文件编辑与定向测试验证。

## 下一步

1. 将镜头级视觉需求从 Storyboard/VideoPromptSpec 显式传入匹配器，并接通真实检索候选集。
2. 增加素材类型/画幅/分辨率/人物肖像/商标等过滤与去重，再生成整套可追溯素材包。
3. 将许可证据细化为 commercial_allowed / attribution_required / unknown / prohibited；unknown 一律阻断商业发布。
4. 继续处理 VideoStudio DAG → Commander admission grant → Worker 的授权接线；不能绕过当前 fail-closed。
5. 旁白结构化 prosody runtime 消费与真实 TTS/ASR/最终成片审片仍需独立验收。
