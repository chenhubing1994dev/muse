# B150 — Human-approved Cantonese subtitle remux and Creator release gate

Date: 2026-11-17

## Goal

Continue B147-B149 by connecting the cue-level human subtitle review to the verified video product, without allowing ASR confidence or subtitle approval to bypass technical, editorial, rights, or platform release checks.

## Implemented

- Added VideoProductBuilder.package_approved_subtitle_revision().
- The method accepts only a review document with human_approved=true, review_status=approved, reviewer identity, and approval timestamp.
- It binds the review to the exact product narration, raw ASR transcript, pinned ASR model/revision, and narration-audio SHA-256. A review from another audio asset or narration cannot be applied.
- It renders and validates the approved cue-level SRT, then muxes it into a new MP4 as a selectable mov_text subtitle track with language tag yue.
- Video and audio streams are copied (-c:v copy, -c:a copy); only the subtitle stream is encoded. FFprobe checks that video/audio streams remain present and video codec, dimensions, and frame rate are unchanged.
- Technical QA runs against the remuxed MP4. The existing product directory is not overwritten: a new content-addressed version is created.
- The new ZIP includes the approved SRT and subtitle-review.json; the manifest, storyboard, README, hashes, and Creator product event are updated together.
- verify_path() now verifies the review JSON, review/SRT/video hash relationships, and that the approved SRT exactly matches the reviewed cues.
- The revision is attached to the original Creator Initiative by locating its exact product_built ledger event. Creator stays testing; the quality gate's ready_to_publish remains false. Subtitle approval alone cannot ship a product.
- The status API exposes subtitle review status and the explicit ready_to_publish flag.
- scripts/muse-review-subtitles.py adds an opt-in --remux-approved-product flow. It requires --approve, --confirm APPROVE-HUMAN-REVIEW, --product-id, and --version-id. The default review-only CLI path is unchanged.

## CLI

After a human has listened to and reviewed every cue, prepare the answers JSON and run:

~~~powershell
$env:PYTHONPATH='src'
py -3.11 scripts/muse-review-subtitles.py --review "data/prometheus/runs/cantonese_pilot_v2/b0c67132117a4e99a48f0c07302a31b9/subtitle-review.json" --answers "data/prometheus/runs/cantonese_pilot_v2/b0c67132117a4e99a48f0c07302a31b9/subtitle-review-answers.template.json" --reviewer "<reviewer-id>" --approve --confirm APPROVE-HUMAN-REVIEW --remux-approved-product --workspace "D:\AI AGENT\Muse" --product-id "2ff1c0a9339292f7b663a1af1ab0b333" --version-id "1713600cfe751184fc96"
~~~

The answers template starts with empty reviewed indexes and corrections. After listening to each cue, fill every index that has actually been reviewed and add only the necessary corrections; the empty template deliberately cannot pass approval. The CLI writes a new reviewed JSON and approved SRT first, then creates the immutable remuxed product revision. It does not modify the original product version.

## Validation

- tests/test_subtitle_review.py: 9/9 passed.
- tests/test_video_product_builder.py: 28/28 passed, including:
  - pending human review cannot remux;
  - approved SRT creates a new version and preserves the original;
  - MP4 mux uses mov_text, keeps video/audio stream copy, and verifies the yue subtitle tag;
  - approved review JSON and SRT are present in the ZIP;
  - Creator remains testing, and publication readiness remains false;
  - status API exposes the approved subtitle state.
- tests/test_video_dashboard_integration.py: 3/3 passed.
- tests/test_status_api.py: 28/28 passed.
- tests/test_unified_initiative_ledger.py: 1/1 passed.
- tests/test_product_builder.py: 4/4 passed.
- Focused total: 73/73 passed.
- py_compile and the subtitle-review CLI --help check passed.
- Full suite on Python 3.11: 1291 tests ran; 1 error and 1 skipped. The only error is test_local_dashboard.LocalDashboardTests.test_lan_requires_pairing_and_proxies_api_same_origin, which receives HTTP 502 from the LAN proxy path. All other tests, including this batch, passed.
- Full suite on Python 3.14: 1290 tests ran; 54 errors and 2 skipped. The captured log includes missing tzdata (ZoneInfoNotFoundError) and external HTTP 400/502 responses. Prefer Python 3.11 for the current regression baseline; the 3.14 environment still needs dependency/integration triage.
- The new remux method was validated with mocked FFmpeg/FFprobe and QA in unit tests. No real production product has been remuxed in this batch because the current production review remains pending human approval.

## Known release blockers retained

- Four frozen/low-motion intervals from B149 remain mapped to source shots 01, 03, 04, and 05. Do not lower freezedetect=n=-50dB:d=1.0.
- shot-05, shot-04, and shot-01 remain priority candidates for regeneration; shot-03 needs final crop/zoompan comparison first.
- Current CantoTTS voice is still not the requested mature female Cantonese profile.
- No new Wan/ComfyUI inference was run during B150.
- Current production product: data/products/2ff1c0a9339292f7b663a1af1ab0b333/1713600cfe751184fc96. verify_path() passes; Creator status is testing; subtitle review is still pending; QA status is passed but has four frozen_frames warnings and ready_to_publish=false.
- The corresponding review JSON is data/prometheus/runs/cantonese_pilot_v2/b0c67132117a4e99a48f0c07302a31b9/subtitle-review.json. It has not been human-approved; no production remux was attempted.
- Human subtitle approval is separate from technical QA, editorial review, rights/disclosure checks, platform/account approval, and the final publication decision.

## Next

1. Run the approved-remux CLI only after the user has personally reviewed every cue against the original Cantonese audio and explicitly approved it.
2. Verify the new version through the real local FFmpeg/FFprobe toolchain and Creator Dashboard.
3. Diagnose the single Python 3.11 LAN Dashboard HTTP 502 separately; restore tzdata and integration dependencies in Python 3.14 before using it as the regression interpreter.
4. Resume low-motion shot regeneration and the mature female Cantonese TTS feasibility work.
