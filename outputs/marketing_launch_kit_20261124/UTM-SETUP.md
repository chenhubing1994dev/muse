# UTM setup

The site currently has no confirmed public domain. Do not substitute an example domain or publish local file paths as tracking destinations.

When a production HTTPS URL is confirmed, generate one distinct tracking URL per channel using `muse.social_campaign_tracking.build_social_tracking_manifest()`:
- `destination_url`: the confirmed production page URL.
- `campaign`: `ai-verification-three-step-2026-11`.
- `platforms`: `youtube_shorts`, `tiktok`, `instagram_reels`, `bilibili`, `linkedin`.
- `content_by_platform`: stable variant labels such as `hook-a` or `carousel-a`.

The helper is deterministic and offline; it does not publish or send requests. Existing UTM parameters are rejected rather than overwritten. A tagged URL is not evidence of a click or sale.

Import actual click and conversion data only from authorized analytics sources. Reconcile orders, refunds, platform fees and payment fees before calculating net settled revenue.
