# Muse Campaign Video Builder v1 — Progress

Date: 2026-11-24

## Delivered in source
- Added `scripts/build_campaign_videos_v1.py` as an isolated renderer entry point; existing kinetic renderer and other sessions' owned files were not modified.
- Three independent story concepts are defined: source-trace (citation verification), balanced-evidence (counter-evidence), and uncertainty-map (fact/inference/prediction/unknown).
- Each episode is designed as five 11-second scenes for a 55-second 1080×1920, 24 FPS silent H.264 MP4. Original programmatic typography/diagrams only; no third-party footage, photos, or music.
- Output target is a new directory under `outputs/`; the script refuses to overwrite an existing target. It writes rights and QA manifests, verifies ffprobe metadata, full-decodes each MP4, and checks ZIP CRC and SHA-256.

## Validation
- Python AST syntax check: passed.
- Existing creator/monetization regression suite via unittest: 26 tests passed.
- A first pytest attempt failed because the Runner Python environment has no pytest module; no dependency was installed.
- The initial PSMUX build invocation was blocked by the host safety check. After `muse_dev.py inspect` confirmed `active_job_id=null`, the builder was run through the official Runner → PSMUX route. v1 rendered and fully decoded; visual QA caught a label/card collision in episode 03 scene 01, so v1 is retained as an audit iteration and not the release candidate.

## Release and revenue boundary
- This builder is internal tooling only. Its output manifest keeps `ready_to_publish=false`.
- No public posting, public deployment, payment processing, orders, or revenue occurred. Sales remain 0; settled revenue remains USD 0.00.
- Before release: render and full-decode all three MP4s; manually review text, pacing, source attribution, rights and platform disclosures; wire the landing page to a real free download and checkout only after the account owner authorizes those account-impacting steps.

## Rendered outputs — v2 (2026-11-24)

- Output root: `outputs/campaign_videos_20261124_v2/`; v1 retained unchanged for audit.
- Visual QA found episode 03 scene 01's `MATCH CONFIDENCE TO EVIDENCE` label overlapped the lower callout card in v1. The builder now places that label at y=1400 (above the card), writes to a fresh v2 directory, and refuses overwrite. The three v2 opening scenes were visually inspected; the collision is resolved.
- Official Runner → PSMUX build Job `wc_job_0yhEolSjj784zSAB` completed exit 0 (inner PSMUX job `adade1b4608146309a5db46c26fbd6d4`). Each MP4 is 55.0s, 1080×1920, 24 FPS, H.264, silent, five scenes; FFprobe metadata and full FFmpeg decode passed.
- Episode 01 `引用不是证据`: MP4 3,536,892 bytes, SHA-256 `7d302dbe9b846e9b9bc1d5e97a5d7eccd3d1124a184e901df295568f2065214c`; ZIP 4,208,510 bytes, SHA-256 `15cd08a8733f51446fc0fca06c1c97af5d33eaa84aac5b74e8249a202ae18c52`.
- Episode 02 `主动寻找反面证据`: MP4 3,632,790 bytes, SHA-256 `251a5d0e528f2f11e032b27f9d69a6da5a719068c365c1ab6482994966d137e5`; ZIP 4,298,422 bytes, SHA-256 `026b92f45ee7d16ef0ea54fdfb89bbc669e32f1d5a7178a7ee119c438791081b`.
- Episode 03 `不知道也是有效结论`: MP4 3,566,089 bytes, SHA-256 `77199e3f6f2946d5afe61e57a269362ad234fa8c31d21ed41ee48a0bf0fde4ee`; ZIP 4,224,308 bytes, SHA-256 `135ee0fdee593561e64b5893d73cd081effc25d8fc5d77a7d4b2135de532ac98`.
- Each ZIP contains 9 entries and passed ZIP CRC validation. `qa-report.json` is `technical_pass_editorial_review_required`; `rights-manifest.json` records original programmatic visuals, no third-party video/photo/music, no voiceover, Noto Sans CJK HK / SIL OFL 1.1, and `commercial_release_authorized=false`. All manifests keep `ready_to_publish=false`.
- Visual review in this pass sampled each episode's opening scene, not all 15 scenes. Human review of every scene, NIST/source attribution, pacing, phone safe areas, platform AI disclosure and final release approval remain required. No upload, deployment, sale or revenue occurred.
