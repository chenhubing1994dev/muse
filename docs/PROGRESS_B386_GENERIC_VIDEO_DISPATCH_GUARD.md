# B386 — Generic Dispatcher Video Approval Guard

## Problem

A focused `tests/test_unified_runtime.py` run exposed a regression: an already-approved `creator_initiative_video_approval` or `business_opportunity_video_approval` ticket could enter one of MuseRuntime's generic Ruflo dispatchers. The earlier VideoTaskWorker-only boundary was enforced by Status API and admission paths, but the generic dispatchers did not both reject these ticket kinds before claiming them.

## Change

Updated `src/muse/unified_runtime.py` so both `dispatch_once()` and `dispatch_commander_once()` inspect the explicitly selected queued ticket before `claim_next()`. The two video approval ticket kinds now raise `RuntimeBlocked` with a VideoTaskWorker-only message. This keeps the ticket queued and prevents generic Ruflo agent spawn/execution. Existing plugin capability ticket guards remain unchanged.

## Verification

- `py_compile` for `src/muse/unified_runtime.py` and `tests/test_unified_runtime.py`: PASS.
- `tests/test_unified_runtime.py`: **56/56 PASS** (8.217s), including `test_video_approval_tickets_cannot_enter_generic_dispatchers` and plugin ticket dispatch guards.
- `tests/test_inspiration_productization.py`: **9/9 PASS**; `tests/test_inspiration_evolution.py`: **16/16 PASS** (combined 25 tests, 10.702s).
- Both inspiration suites confirm offline productization stays local, preserves provenance/hypothesis status, and queues optional video work for Commander approval without GPU dispatch.
- These modules use per-test temporary workspaces/databases; no full-suite rerun was started.


## Boundaries / remaining work

- This is a routing guard, not an execution grant. Video work still requires the dedicated VideoTaskWorker, persisted Commander approval, VideoAdmissionRuntime, and valid resource/cost gates.
- No real GPU, ComfyUI, or Wan render was performed.
- No autonomous runtime was started; no public Web Edition deployment or mobile-device release was attempted.
- The latest isolated full-suite baseline remains 2,475 tests / 0 failures / 0 errors from B385; B386 adds 81/81 focused tests across runtime dispatch and inspiration-to-productization, not a new full-suite result.

