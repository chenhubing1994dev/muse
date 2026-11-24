# Muse — Windows full-suite triage & fixes (2026-11-24)

## Baseline (observed, Kiro, canonical Windows copy, `py -3.11 -m unittest discover -s tests`)

`Ran 2621 tests in 405s — FAILED (failures=14, errors=7, skipped=1)` — log: `outputs/full-suite-20261124.log`.
Phase 1 (approval → DAG → VideoTaskWorker, see `PROGRESS_2026-11-24_APPROVAL_DAG_VIDEO_HANDOFF.md`) had just passed 144/144 targeted tests.

## Triage of the 21 failing items

| # | Tests | Root cause | Category | Fix |
|---|---|---|---|---|
| 1 | `test_video_worker_reverification` ×4 | Fixture created the approval ticket but not the new `video_pipeline` execution ticket required by Phase 1 | test fixture stale | Kiro updated `_prepare` (observed 2/2 pass in isolation) |
| 2 | `test_product_feedback_import` ×6 | `product_builder.py` BUILDER_VERSION 5 (uncommitted, 17:41) made every build a `draft` and removed the promotion to `testing`, but **no review workflow existed**, while `product_feedback_import` still requires `validated=True, status=testing`. The creator-feedback loop was unreachable for every new product. | real integration gap | Added `LocalProductBuilder.approve_for_testing(store, id, reviewed_by=…, note=…)` + CLI `product_builder approve <id> --reviewed-by NAME`. Writes `review.json` bound to `input_sha256` + `package_sha256`; requires complete `content_readiness`; `publishable` stays false; tampered/mismatched review fails closed; artifact modification invalidates it. Test setUp now builds a draft then approves explicitly. |
| 3 | `test_autonomous_research` ×1 | Asserted autonomous auto-build is `validated=True` — contradicts the BUILDER_VERSION 5 policy | stale expectation | Now asserts `validated=False`, `status=draft`, `package_integrity=passed` |
| 4 | `test_feedback_experiments` ×1 | Its allowlisted nested suite is `test_autonomous_research.py` (nested run: 69 tests, 1 failure = #3) | consequence of #3 | none needed |
| 5 | `test_inspiration` ×5, `test_kinliuren/qimen/ziwei_provider` ×4 | Isolated PoC venvs' `pyvenv.cfg` `home` points at `C:\Users\Administrator\AppData\Local\Programs\Python\Python311`, which no longer exists → interpreter exits non-zero | Windows environment | `scripts/Repair-And-Verify.ps1` re-points `home`/`executable` at the current `py -3.11`; rebuilds the venv with the pinned package (`kinliuren==0.1.2.9`, `qimen-rs==0.2.1`, `ziwei==0.4.1`) only if import still fails. `pyvenv.cfg.bak-20261124` kept. |

No fail-closed, rights, approval or publication gate was weakened.

## Files changed this round

- `src/muse/product_builder.py` — `_read_review`, `approve_for_testing`, `_verify_existing` honours a bound review, CLI `approve` (backup `.bak-20261124-review`)
- `tests/test_product_feedback_import.py` — setUp: build draft → explicit review (backup kept)
- `tests/test_autonomous_research.py` — draft-policy expectation (backup kept)
- `tests/test_product_review.py` — **new**, 7 tests: draft→testing, idempotency, named reviewer required, incomplete content refused, unbuilt refused, tampered review record fails closed, modified artifact invalidates review
- `scripts/Repair-And-Verify.ps1` — **new**, venv repair + targeted + full suite (UTF-8 logs)

## Verification status — NOT YET RUN

The cloud assistant can edit files but cannot execute processes on this workstation, and Kiro's plan quota ran out mid-triage. All edited Python files pass `compile()`. **No post-fix test result exists yet.** Run:

```powershell
powershell -ExecutionPolicy Bypass -File "D:\AI AGENT\Muse\scripts\Repair-And-Verify.ps1"
```

Outputs: `outputs/targeted-tests-20261124.log`, `outputs/full-suite-20261124-after.log`. Expected if the analysis is right: 0 failures / 0 errors (2628 tests incl. 7 new). Report anything else as observed.

## Residual notes

- Phase 1 Worker reads `commander_plan_id/source` from the execution ticket; DAG-digest verification lives in `UnifiedRuntime.dispatch_video_pipeline_once()` (Commander), not the Worker. The Worker still builds a deterministic single-step scene plan from the brief (`source="safe_fallback"`). Acceptable for now; consider having the Worker also re-check `workflow.bound` plan_id/digest for defence in depth.
- Capability smoke checks (status_api, task_cli, lead server POST, node --check, Node tests) from the Phase 2 brief were not reached.
- Nothing published; no production DB written; revenue remains USD 0.
