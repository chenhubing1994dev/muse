# B120 — Autonomous research multi-cycle and crash-recovery validation

Date: 2026-11-13

## Scope

B120 continues from B118/B119. It does not repeat the rejected-review feedback topic selection or the self-improvement Dashboard work. The focus is proving that the existing autonomous research cycle can complete consecutive runs, resume a cycle after interruption, and avoid duplicate Research Wiki idea nodes when durable writes happened before a process interruption.

## Runtime architecture findings

- The supported background entrypoint is `AutonomousSupervisor.run_forever()`.
- `RuntimeStateStore.acquire()` enforces one active supervisor owner with a transactional SQLite write lock and PID/heartbeat freshness check.
- The supervisor's `run_tick()` invokes `run_due_once()` for the named routine set. `RoutineStore.claim_due()` atomically claims due routines with a lease token; `run_due_once()` renews that lease in a heartbeat thread and refuses stale lease completion.
- The autonomous research routine is registered once as `idle-autonomous-research` and runs at a bounded interval. The normal service path therefore serializes the research routine through the existing Commander-owned supervisor and routine lease rather than introducing a second scheduler.
- Direct calls to `AutonomousResearchEngine.run_once()` are still not a cross-process public scheduler API. The production serialization guarantee comes from the supervisor owner + routine lease.

## Tests added

`tests/test_autonomous_research.py` now includes:

1. `test_two_completed_research_cycles_start_fresh_topics_and_persist_terminal_events`
   - Executes two real research-engine cycles using deterministic fake search/reader adapters and isolated SQLite databases.
   - Confirms both complete, have distinct cycle IDs and topics, each has a durable topic checkpoint and terminal `completed` event, and at least two distinct autonomous topic keys exist in the Research Wiki.
2. `test_crash_after_research_persistence_resumes_same_topic_without_duplicate_idea`
   - Executes the research body directly to simulate process death after research writes/checkpoints but before the wrapper writes a terminal event.
   - Re-enters through `run_once()`, verifies it resumes the same cycle ID and topic key, then confirms exactly one idea node exists for that topic and the cycle becomes terminal `completed`.

The existing interrupted-cycle unit test remains in place for the checkpoint-selection contract.

## Live local-model validation

Ran `scripts/muse-live-evolution-smoke.py` through the project's `.venv` Python against the configured loopback LM Studio endpoint. All artifacts were created inside a `TemporaryDirectory`:

- Baseline: 1 test, 1 failure.
- Candidate: 1 test, 0 failures.
- A distinct local-model Reviewer returned a valid decision; `IndependentReviewGate` persisted the review.
- The approved-code-change executor completed the proposal lifecycle through `implemented` and upgraded only the temporary capability from `unverified` to `ready`.
- One Creator Initiative knowledge link was recorded.
- The temporary source changed from `return 1` to `return 2`; no Muse production source or production database was targeted.

This validates one live local-model review/execution transaction. It is not evidence that the full autonomous research service has run unattended for days or that all proposals should be auto-approved.

## Validation

- New B120 tests: 2/2 passed.
- Complete `tests.test_autonomous_research` module: 51/51 passed.
- `python -m compileall -q src tests`: passed.
- Full backend regression: **1,086/1,086 tests passed**, exit code 0, 113.326 seconds.

## Existing Windows runtime observation

A read-only inspection of the user's current Windows installation found:

- Task Scheduler task `MuseAutonomousRuntime` is Running. Its action targets `<MUSE_ROOT>\\.venv\\Scripts\\pythonw.exe` and `scripts/muse_runtime_entry.py`.
- The task process tree is one launcher (`.venv\\Scripts\\pythonw.exe`, PID 18048) with its Python base-interpreter child (PID 17612), not two independent supervisor instances. The runtime SQLite owner PID is 17612; its heartbeat was 13.7 seconds old at inspection time.
- The five configured routines are enabled and had `last_status=succeeded`; no routine lease was held at inspection. The autonomous research routine last ran at 00:53 UTC and the latest report was written at 00:51 UTC on 2026-11-13.
- Important deployment caveat: the running process tree started on 2026-11-11, while `autonomous_research.py`, `autonomous_runtime.py`, `status_api.py` and the Dashboard source were modified later (through 2026-11-13). The process therefore predates the latest source and cannot be assumed to have loaded B118/B119/B120 code. The existing service must be gracefully restarted to load current code, but this B120 turn did not restart or mutate the running task.
- Watchdog progress summaries currently report `model_service=not_configured`; that warning should be reconciled with the observed loopback LM Studio live smoke before claiming the always-on service can use the local model. The live smoke itself proved the endpoint only during the test.

## Local-model timeout diagnosis and hardening

- The active non-secret model route is configured as `llama_cpp` / `DeepSeek-V4-Pro-Qwen3.5-9B` on a loopback endpoint. A read-only health probe reported `healthy`, one model available, 16 ms probe latency; a short JSON generation completed in 6.66 seconds.
- The newest production research report (00:51 UTC) recorded `fallback_after_failed:ModelError` with `Local model request failed: timed out`.
- A controlled, non-persistent synthesis probe using four bounded source excerpts plus inspiration-only context validated successfully after the existing one-time schema retry, taking 155.05 seconds total. This demonstrates why a fixed 120-second ceiling can be too tight for long research prompts even when the model endpoint is healthy.
- Increased the local model request timeout in autonomous research synthesis and claim review from 120 to 240 seconds. The regression test now asserts both client paths use the extended timeout; observed RED at 120 seconds, then GREEN after the implementation change.
- Post-change verification: autonomous research suite 51/51 passed; full backend suite 1,086/1,086 passed; compileall passed.
- The running Windows supervisor predates this change and has not yet been restarted, so its in-memory research engine still uses the previous 120-second timeout until the task is gracefully restarted.

## Remaining acceptance work

- Run the actual local autonomous supervisor in a disposable data root for multiple scheduled ticks and verify routine lease renewal, pause/resume, and process restart.
- Exercise an actual process kill (not only a deterministic interruption simulation) and confirm recovery from persisted progress without duplicate side effects.
- Confirm the running production configuration's database paths, local model/reviewer availability, and user-visible report delivery without mutating existing production ledgers.
- Perform real browser acceptance of the self-improvement Dashboard; current evidence is API tests, Svelte check, and production build, not manual browser interaction.
- Keep human approval, independent review, candidate/baseline tests, and SHA-256/hash guards intact. No automatic production code changes are enabled by B120.
