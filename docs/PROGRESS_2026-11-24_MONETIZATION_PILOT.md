# Muse Monetization Pilot｜2026-11-24

## Objective

Start producing real, low-cost content assets while continuing Muse engineering. Keep the canonical production route as Creator Initiative → persisted Commander DAG → VideoTaskWorker/VideoProductBuilder. This pilot is an isolated local media experiment; it does not replace Commander or write to production ResearchKnowledge/Initiative databases.

## Verified existing video deliverable

- Existing output: `outputs/creator_pilot_20261124/`.
- Video: 24.5 seconds, 1080×1920, 24 FPS, H.264 + AAC, full decode passed.
- Production: six previously generated local Wan 2.1 T2V shots plus FFmpeg editing; this run did not launch new Wan/ComfyUI GPU generation.
- Existing manual publishing package contains YouTube Shorts, TikTok, Instagram Reels and Bilibili drafts.
- Voiceover: not used in the final output. Local Cantonese TTS was attempted, but Whisper Cantonese ASR returned CER 0.344828 and the product builder correctly rejected the narration package.
- QA warnings include low audio loudness and frozen frames. Technical QA passed, but release quality remains blocked pending human review.
- Cost ledger: paid API, stock asset and cloud compute spend were each recorded as USD 0 for this run. Local electricity and labour were not metered. Settled revenue is USD 0; no sales are recorded.
- Rights manifest: all six Wan shots and the original ambient audio remain `commercial_use_allowed=false` pending model/output rights review. This asset is not cleared for commercial publication.

## New isolated Cantonese voiceover pilot

Added:

- `src/muse/monetization_pilot.py`
- `scripts/muse-monetization-pilot.py`
- `tests/test_monetization_pilot.py`

The pilot reuses the verified B133 video and existing local components:

1. Pinned Canto-TTS Nano for Cantonese voice generation (Python 3.14 isolated runtime).
2. Pinned Whisper Cantonese for timestamped subtitle generation and CER measurement.
3. FFmpeg for voice/background mix while copying the already-encoded H.264 video stream.
4. Full decode, stream/duration verification, SHA-256 manifest, ASR transcript, subtitle-review handoff, four platform copy drafts and a ZIP delivery package.

The script writes only to a new `data/monetization/...` output directory. It does not write production DBs, call external TTS services, start Wan GPU generation, publish to social accounts, or claim revenue.

### ASR review policy

A CER miss must not be silently treated as a pass. The pilot retains a usable ASR transcript and timestamped SRT as a human-review draft, records the CER and reference/recognized text, and sets the release gate to `needs_review`. Publication remains blocked regardless of ASR status until human listening, subtitle, source, rights, privacy, disclosure and account checks are complete.

## Validation status

- TDD RED was observed before the module existed (`ModuleNotFoundError`).
- Unit tests were added for platform drafts, release blocking, path safety and the ASR-needs-review gate.
- The targeted monetization/landing-page/Cantonese-ASR suite now passes **21/21** through the approved `muse-dev` → PSMUX route, including syntax compilation of the changed Python files. This is a focused suite, not a claim that the entire Muse repository regression suite is green.
- A new Cantonese-voiced MP4 was generated locally; see the v3 output and release blockers below. No public upload or revenue is claimed.

## Immediate next steps

1. Human-listen to the v3 voiceover and review the two-character ASR difference (`揾反正` versus the reference `搵反證`); correct subtitles only after confirming the audio.
2. Review pacing, audio loudness, frozen frames, subtitle safe areas and the final rendered video on a phone-sized screen.
3. Complete the Wan/model/output and ambient-audio commercial-rights review. The source product remains `testing`, with `ready_to_publish=false` and editorial/rights/disclosure/account checks outstanding.
4. Complete the digital kit's human editorial, final licence and refund-policy review; then configure a storefront and payment processor only with the account owner's authorization.
5. Keep the landing page `noindex,nofollow` until the public domain, HTTPS deployment, final copy and release checks are deliberately approved.
6. When shared mutation fences are fresh and relevant owners hand off the core vertical slice, continue the broader Commander/Creator regression work; do not bypass PSMUX or single-writer rules.
7. Publish only after account authorization and platform-specific AI disclosure checks. Import analytics and payments through authorized integrations and record settled revenue separately from views, estimates and affiliate clicks.

## Current business truth

The pilot now proves that Muse can produce a technically decoded local Cantonese-voiced video, timestamped ASR/subtitle metadata and a hash-verified delivery ZIP using local TTS/ASR/FFmpeg without paid API spend. It does **not** yet prove human subtitle accuracy, commercial rights clearance, publication readiness, audience demand or income.

## First digital product draft

Created outputs/digital_products/ai-fact-check-starter-kit-v1/ with ten files:

- README.md, FREE_CHECKLIST.md (0+ lead magnet), CHECKLIST.md, CLAIM_LEDGER_TEMPLATE.md
- COUNTEREVIDENCE_WORKSHEET.md, PROMPT_PACK.md
- SAMPLE_CASE.md, SOURCE_NOTES.md, LICENSE_DRAFT.md
- product-manifest.json

The draft is original Muse-authored text with no third-party media assets. NIST AI 600-1 is cited as a source, with no claim of NIST endorsement. The official NIST page and DOI were checked; the page lists publication date 2024-07-26. Suggested test price is US$7 (not market-validated); the free 0+ lead magnet now exists as a local Markdown download and landing-page asset but remains unpublished. A 10-entry internal-review ZIP exists at `outputs/digital_products/ai-fact-check-starter-kit-v1/ai-fact-check-starter-kit-v1-internal-review.zip` (10,984 bytes; SHA-256 `0d769f4d7966c9d12ee1957452397590b93e7d76125d3e2990d5caf91a3a3771`); `PACKAGE-VERIFY.json` records ZIP CRC passed. The product manifest remains internal_review_draft, can_publish=false, payment_processor=not_connected, sales=0 and settled revenue=US$0. No storefront listing, payment connection or sales have occurred.

## Distribution and monetization research (checked 2026-11-24)

- Payhip Free Forever: US$0/month and 5% platform transaction fee; Stripe/PayPal processing fees are additional. Payhip supports 0+ pricing and digital downloads. A payment processor must be connected before selling; even free products require a processor connection for Payhip's free-product flow.
- Payhip states it handles US sales tax from 2026-07-01; a seller with physical presence in a state may still have separate filing obligations. This is not tax advice.
- Gumroad direct sales: 10% + US$0.50 per transaction, plus payment processing; marketplace discovery sales are 30%. Its merchant-of-record/tax handling may justify the higher fee for some sellers.
- TikTok Creator Rewards: eligible videos must be original and at least one minute; published program criteria also include account eligibility such as 10,000 followers and 100,000 video views in the last 30 days. The current 24.5-second pilot is not a Creator Rewards candidate.
- YouTube full Shorts ad-revenue eligibility generally requires 1,000 subscribers and 10 million valid public Shorts views in 90 days. The expanded YPP's lower threshold (500 subscribers, 3 public uploads and 3 million Shorts views) unlocks selected fan-funding/shopping features, not full Shorts ad share.
- A Stripe connector suggestion was surfaced to support future payment/revenue tracking; it is not connected or authorized yet.

Official references:
- NIST AI 600-1: https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence
- Payhip pricing: https://payhip.com/pricing
- Payhip US sales tax: https://help.payhip.com/article/392-us-sales-tax
- Payhip payment processing: https://help.payhip.com/article/65-how-do-i-get-paid
- Gumroad fees: https://gumroad.com/help/article/66-gumroads-fees
- TikTok Creator Rewards: https://newsroom.tiktok.com/introducing-the-new-creator-rewards-program/?lang=en
- YouTube YPP eligibility: https://support.google.com/youtube/answer/72851

## Next content variant

Created outputs/creator_pilot_20261124/script-70s-cantonese-v2.md: a 65–75 second Cantonese explainer with a hook, three evidence-checking steps, a precise NIST attribution caveat, and a CTA to the free checklist. It is intended as a longer candidate for TikTok Creator Rewards length requirements, but account eligibility still applies. The script has not yet been rendered, voiced, or published.

## Website and social distribution assets

- Added `web-portal/ai-fact-check.html` and `web-portal/ai-fact-check.css` as a standalone responsive product/lead-magnet page at the static route `/ai-fact-check.html`; the page uses local assets only, has no form or payment integration, and is marked `noindex,nofollow` until reviewed and deliberately released.
- Added `web-portal/ai-fact-check-free.md` as the direct free-checklist download. The landing page has two download CTAs, both pointing to that local file.
- Added `outputs/digital_products/ai-fact-check-starter-kit-v1/SOCIAL_CONTENT_PLAN.md` with a 7-day YouTube Shorts, TikTok, Instagram Reels and Bilibili validation plan. It records no invented performance data and requires rights, editorial, disclosure and account checks before posting.
- The static page is created in the project but has not been deployed, linked from the main portal navigation, or verified through a live HTTPS request. No public URL is claimed.

## Latest validation attempt

- Ran `tests/test_monetization_*.py` through the approved `muse_dev.py` → PSMUX route. The first run executed 9 tests: 8 passed and 1 failed because the landing-page test expected exactly one download link while the page intentionally has two CTAs. The test assertion was corrected to accept one or more download links and verify that each carries the download attribute.
- One immediate retry was rejected with `another_psmux_command_is_in_flight`; no duplicate process was started. A later controlled run on the pre-existing idle `muse-review-validation` PSMUX session passed 21/21 targeted tests; see the v3 revalidation section below.
- The shared default `muse-dev` PSMUX session and its other Session's Job were left untouched. No lock was cleared, stolen or bypassed.

## Current release and revenue gate

The internal ZIP and static landing page are review assets, not a live offer. Product licence/refund review, storefront/payment setup, audience validation and commercial rights review remain outstanding. No social posts were published, no storefront was opened, no payment processor was connected, and settled revenue remains US$0.

## Revalidation and Cantonese video variants (2026-11-24)

- Fixed the Python 3.11 → Python 3.14 ASR handoff: reference narration is written to a temporary UTF-8 file instead of passed as a command-line Unicode argument; the child process emits ASCII-safe JSON (`ensure_ascii=true`); `PYTHONIOENCODING=utf-8` and the intended `PYTHONPATH` are passed into the subprocess. The temporary reference file is removed after the call.
- Fixed two release-metadata defects: a machine-passing CER no longer clears the human subtitle-review requirement, and `asr-result.json` now records `subtitles.path` as package-relative `subtitles.srt` rather than a deleted staging path.
- **v3 short-voice candidate:** `data/monetization/first-cantonese-pilot-2026-11-24-v3/`. Video 24.512 seconds, 1080×1920, 24 FPS, H.264 + AAC; full decode passed. Voiceover 12.32 seconds; Unicode-correct ASR CER 0.0625 against a 0.10 machine threshold. ASR text differs at `再揾反正` versus reference `再搵反證`; human listening/subtitle review is still required. `human_subtitle_review_required=true`, `ready_to_publish=false`, `release_state=draft_only`.
- **v4 longer-voice experiment:** `data/monetization/first-cantonese-pilot-2026-11-24-v4/`. Voiceover 22.56 seconds; CER 0.321429, so ASR gate is `needs_review`. Full video decode passed; keep as an internal experiment only.
- **v5 simpler-Cantonese experiment:** `data/monetization/first-cantonese-pilot-2026-11-24-v5/`. Voiceover 20.96 seconds; CER 0.214286, so ASR gate is `needs_review`. Full video decode passed; keep as an internal experiment only. Current `NARRATION` constant uses this longer script, but no variant is approved for publication.
- v3 delivery package: 14,867,218 bytes; SHA-256 `721fe2267f8af719f660b3e5bc6bc0447e1f6219f6726deebe94fbf6b0603398`. A separate integrity run passed ZIP CRC, manifest member-set comparison and every member SHA-256; archive contains 14 members. v4/v5 builder CRC checks passed, but their full member-hash verification was not run.
- Focused regression: **21/21 tests passed** (`test_monetization_*.py` plus `test_cantonese_asr.py`) after the latest narration/code change in PSMUX job `80d55be1a88943bd8746fa3fc8d0f126`; syntax compilation also passed. v3 package integrity was checked in PSMUX job `f515ea37fdaa47929dfca1379b5575fb`.
- Earlier v1 output is superseded because its ASR JSON contained Unicode mojibake. v2 fixed Unicode but retained a temporary staging path in ASR metadata. v3 has the best machine CER but shorter narration; v4/v5 cover more of the video but fail the ASR gate. All variants remain draft-only and require human listening/subtitle review.
- Source video `data/prometheus/high-value-short-b133/` remains a testing product; its generation manifest says `ready_to_publish=false` and `blocking_reason=editorial_rights_disclosure_platform_and_account_review_required`. No commercial rights clearance is claimed.
- No public upload, storefront sale, payment connection, audience metric or settled revenue occurred; revenue remains US$0.

