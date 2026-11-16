# B126 — Video QA perceptual signals

Date: 2026-11-13

## Delivery

Extended `muse.video_qa.inspect_video_file()` with FFmpeg heuristic analysis for:

- Sustained near-black intervals (`blackdetect`).
- Potential frozen-frame intervals (`freezedetect`).
- Sustained audio silence (`silencedetect`, only when an audio stream exists).

The report now includes `quality_signals` and structured `warnings`. Default review thresholds are 0.5 seconds for black intervals and 1.0 second for frozen or silent intervals. CLI thresholds are configurable with `--black-warning-seconds`, `--freeze-warning-seconds`, and `--silence-warning-seconds`.

These are editorial review signals, not automatic failure verdicts. Intentional fades, dark title cards, static shots, and pauses can trigger them. Existing hard technical checks (container, streams, dimensions, frame rate, duration, required audio and full decode) remain authoritative. `ready_to_publish` remains false pending editorial, rights, disclosure and platform review.

## Verification

- Baseline before change: 27 targeted tests passed.
- After implementation: 31 targeted tests passed across `test_video_qa`, `test_video_product_builder`, and `test_video_publish_package`.
- Final full Python unittest suite: 1,132/1,132 passed; `compileall -q src/muse tests` passed.
- Product `quality_gate` summaries now carry review warning count/codes; multi-platform publish checklists include the warning details and keep visual review required.
- Added tests cover multiline FFmpeg log parsing, black/freeze/silence warnings, and skipping silence analysis when no audio stream exists.
- Real B122 master `data/prometheus/high-value-short-v2/ai-verification-three-steps-9x16.mp4` was analyzed with the installed FFmpeg/FFprobe: 24.5 seconds, 1080x1920, 24 FPS, H.264/AAC, full decode passed, SHA-256 matched the recorded master.
- The real sample produced 2 near-black intervals (0.0s/3.0s and 21.5s/2.958333s) and 6 potential frozen-frame intervals (longest 4.958333s); no sustained silence was detected. These are heuristic review warnings, not proof of defective footage, and do not fail the technical decode gate.

## Boundaries and next steps

- This does not establish factual accuracy, subtitle/voice synchronization, loudness compliance, clipping absence, rights clearance, or platform eligibility.
- Existing products are not silently rewritten; new QA fields are generated for newly built products.
- Appending to the legacy `docs/PROGRESS.md` was blocked by mixed LF/CRLF line endings; this focused B126 progress record is preserved separately rather than rewriting the 1,778-line history file.
- Next: render the warning summary in Dashboard product cards; add measured loudness/peak checks and subtitle timing/overflow checks; compare detector findings against human-reviewed footage before tightening any gate.
