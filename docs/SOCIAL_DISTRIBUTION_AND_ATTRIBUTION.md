# Muse 社交媒体分发与转化归因草案

状态：本地草稿能力；不代表任何平台账号已连接或自动发布已启用。

## 复用现有生产链

- 使用 `muse.video_publish_package.build_publish_package()` 从完整、技术验收通过的 MP4 产品生成平台草稿包。
- 当前平台草稿配置位于 `src/muse/video_publish_package.py`，覆盖 YouTube Shorts、TikTok、Instagram Reels、Bilibili，以及可选 YouTube、抖音、微信视频号。
- 发布包要求 generation、MP4、封面、字幕、来源说明、产品 manifest 与 QA 证据齐全；包状态为 `draft_only`，不执行登录或发布。
- 规格配置是元数据交接，不是平台当前上传规则/账号资格认证；发布前须按目标账号实时核对。

## UTM 归因

`muse.social_campaign_tracking.build_social_tracking_manifest()` 为每个平台生成独立 UTM 链接，保留原始非 UTM 查询参数，拒绝覆盖已有 UTM、非 HTTP(S) URL、凭据和片段。输出固定为 `draft_only`、`can_publish=false`、`auto_publish=false`。

示例：

```python
from muse.social_campaign_tracking import build_social_tracking_manifest

manifest = build_social_tracking_manifest(
    "https://your-domain.example/services",
    campaign="muse-launch-01",
    platforms=["youtube_shorts", "tiktok", "instagram_reels"],
    content_by_platform={
        "youtube_shorts": "hook-a",
        "tiktok": "hook-b",
        "instagram_reels": "hook-a",
    },
)
```

把 `your-domain.example` 换成已验证并由你控制的真实域名后再使用。示例域名不是真实 Muse 网站。

## 运营和收益回流

1. 先人工检查事实、来源、素材许可、肖像/音乐权利、AI 合成披露和平台规则。
2. 用户在官方平台自行发布；账号连接、OAuth、自动发布需另行明确授权。
3. 分开记录平台分析报表、UTM 点击、有效询盘、订单和实际结算。播放量或点击量不等于收入。
4. 只有平台/支付/订单来源可核验的实际结算，才计入已实现收益；估算和待付款单独标记。
5. 将每条内容的 campaign、platform、content variant、观察窗口和证据来源用于下一轮实验。

## 当前限制

- 没有连接平台账号、发布权限或已验证的公开域名。
- 没有在本地草稿模块中收集平台密码、支付凭据或 OAuth token。
- UTM 链接只生成归因标签；点击、转化与收入须由真实 analytics/订单/结算数据证明。
