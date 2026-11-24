# Muse Social Content Kit

Status: offline draft adapter. It composes existing Muse platform publish drafts and the existing UTM helper; it is not a second publisher, scheduler, analytics store or revenue ledger.

## What it adds

- Platform-specific CTA and copy handoff for YouTube Shorts, TikTok, Instagram Reels, Bilibili and Xiaohongshu; Xiaohongshu receives a simplified-Chinese editorial adaptation.
- Xiaohongshu copy is explicitly derived from an existing platform draft; it does not claim an official platform profile exists in the source package.
- Caption-safe-area guidance is a conservative editorial heuristic, not a platform-certified specification.
- Master duration is copied from generation metadata. Official duration limits remain unknown until checked in the target account/app.
- Optional timezone-aware schedule timestamps remain drafts and are never submitted.
- Optional UTM links reuse `muse.social_campaign_tracking`; destination URL ownership must be explicitly confirmed.
- Source publish-pack file hashes and source MP4 SHA-256 are rechecked before the kit is created.
- A human-readable `social-content-kit.md` is included beside the JSON manifest and ZIP. Voiceover status is copied from generation metadata. Existing release blockers are carried forward. The output always remains `draft_only`, `can_publish=false`, and `publish_api_called=false`.

## Usage

```python
from pathlib import Path
from muse.social_content_kit import build_social_content_kit

result = build_social_content_kit(
    Path("."),
    "outputs/<verified-publish-package-directory>",
    campaign="muse-launch-2026-11",
)
```

Use a real destination URL only after confirming that it is controlled by the project owner:

```python
result = build_social_content_kit(
    Path("."),
    "outputs/<verified-publish-package-directory>",
    campaign="muse-launch-2026-11",
    destination_url="https://your-verified-domain.example/services",
    destination_url_owner_confirmed=True,
)
```

The example domain is a placeholder and must not be used as a real link. No platform login, publishing, customer contact, advertising, analytics import, order creation or payment action occurs. Views and clicks are not revenue; only verified orders and settlements count as realized income.

## Verified pilot — 2026-11-24

- Source publish package: `.muse-video-pilot-workspace-20261124/data/products/5d8e4a058ea37814c61fa25b138a4e62/a448d971e85f97611878/publish-package-v1` (ZIP SHA-256 `f6097596092827c6bd87fce45aaa46368210d47229c32732b796003f3a247627`); source video SHA-256 `47acafe30d77b1e16cef09fa4f23d21fc8942d54d5eb2e15a3b3409000ed7229`.
- Human-readable deliverable: `outputs/creator_pilot_20261124/social-content-kit-v2/social-content-kit.md`.
- Complete draft bundle: `outputs/creator_pilot_20261124/social-content-kit-v2/social-content-kit.zip` (SHA-256 `749b96a5ecd584fce8b9751e0cb28832bb93b78fb4a8d437421ff767bdee1c1e`).
- Five platform drafts: YouTube Shorts, TikTok, Instagram Reels, Bilibili and Xiaohongshu. Xiaohongshu copy is derived from the source draft and separately adapted into simplified Chinese.
- Voiceover status: `not_used`. The source package carries 11 release blockers; output remains `draft_only`, `can_publish=false`, and `publish_api_called=false`.
- Official PSMUX/Python 3.11 regression: **39/39 passed** (social content kit 7, social tracking 9, creator media plan 8, video publish package 15); compile check passed.
- No account authorization, owner-confirmed destination URL, analytics import, orders or settlements were available. Tracking is awaiting an owner-controlled destination URL; realized revenue remains `not_observed`.

## Narrated video asset v6 — verified 2026-11-24

- Review package: `data/monetization/first-cantonese-pilot-2026-11-24-v6/`.
- Vertical master: `video-9x16.mp4`, 1080×1920, 24.592 s, SHA-256 `9509b563db438c036171d2fdb4b33ba66c25d2584af2241bd87bc8840068f827`.
- Horizontal derivative: `video-16x9.mp4`, 1920×1080, 24.592 s, AMD AMF H.264, SHA-256 `24e098cca65e8e4e156f63671dc953c0a217274cfac2c46754c52f753336cbe5`.
- Delivery ZIP: 47,951,406 bytes, SHA-256 `553bb7378ca53d706d12a3333a80da2f8435b4f63fa27cd9bc46ce239b0f3154`.
- Independent official PSMUX integrity check: Job `80eabe4e6ab041d89af9daf13a44a03a`, exit 0. Verified 19 manifest member hashes, ZIP CRC/member set/embedded manifest and all member digests; both MP4s passed FFprobe dimension/audio/`mov_text` subtitle checks and full decode.
- Standalone Cantonese voiceover ASR CER: 0.0625 (6.25%), below the 10% gate. The final mixed audio was not ASR-checked. Voice profile is neutral local Hong Kong Cantonese, not approved as a mature female voice.
- Human listening, subtitle, visual, factual and rights reviews remain pending. Source Wan shots, background audio, TTS terms/voice and ASR model terms are not fully cleared for commercial use. `commercial_use_allowed=false`, `publishable=false`, `release_state=draft_only`.
- No platform account authorization, publishing, paid API/asset spend, orders or settlements. Paid API/asset cost is $0; local energy/labor is not metered; settled revenue is $0.
