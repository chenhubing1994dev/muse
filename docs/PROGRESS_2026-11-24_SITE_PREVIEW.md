# Muse Monetization Track — Static Site Preview v2

Date: 2026-11-24

## Deliverable

- Output: outputs/site_launch_preview_20261124_v2/
- ZIP: 5,511,523 bytes
- SHA-256: c2eefc0650fb708690a633b3b44b9fc4bfb370b66f1bfb27a92bf2b3ee389443
- Pages: index.html, showcase.html, products.html, portfolio.html.
- Assets: four existing CSS/JS files, 64-second Muse kinetic video, five-platform social copy, README, robots.txt and site manifest.
- The portfolio page includes a manual-play video preview and explicitly labels the sample as internal-only pending font/commercial-rights review.
- Navigation connects home, products and portfolio. Builder checks all local HTML links, ZIP CRC, duplicate ZIP member names and each packaged asset SHA-256.
- Targeted site preview tests: 3/3 passed.
- Combined kinetic-video + site-preview + monetization-pilot + Cantonese-ASR regression: 23/23 passed.

## Safety and release state

- robots.txt blocks crawling and the portfolio page includes noindex/nofollow.
- site-manifest.json: public_deployment_allowed=false, crawl_allowed=false, contact_channel_configured=false, payments_enabled=false.
- The inquiry form remains client-side only: it drafts/copies text; it does not submit, upload, store or notify.
- No payment processor, order pipeline, analytics, customer database or live contact destination is connected.
- The sample's Microsoft YaHei commercial-use terms and final editorial review remain unresolved. No commercial clearance is claimed.
- No public deployment, social publication, leads, orders or revenue occurred.

## Build

Code:
- src/muse/site_preview.py
- scripts/muse-site-preview.py
- tests/test_site_preview.py

Build through the approved Runner/PSMUX route:

```powershell
py -3.11 scripts/muse_dev.py run --session muse-review-validation --language python --script "from pathlib import Path; from muse.site_preview import build_preview; import json; print(json.dumps(build_preview(Path('outputs/site_launch_preview_20261124_v2'), Path.cwd()), ensure_ascii=False, indent=2))" --cwd . --timeout 600
```

## Superseded iteration

The first preview ZIP (site_launch_preview_20261124_v1) contained a duplicate site-manifest member because the builder archived it twice. It is retained for audit only. v2 fixes the duplicate-member bug and validates local links before packaging.

## Next release steps

1. Human-review product wording, legal/privacy text, prices and the free checklist.
2. Confirm a licensed CJK font or replace Microsoft YaHei with an appropriately licensed font; rebuild the video and site bundle.
3. Configure a real contact destination and privacy notice.
4. Select an owner-controlled static host/domain and HTTPS.
5. Only after explicit release approval, remove noindex/robots blocking, configure payment and order/refund callbacks, and publish manually.
