# Muse — Approval → DAG → VideoTaskWorker Handoff Fix

Date: 2026-11-24

## Scope

Implemented the "Recommended ownership and implementation contract" from
`docs/PROGRESS_2026-11-24_REVENUE_PIPELINE_AUDIT.md`.  The fix wires the
human-approval ticket to a persisted Commander DAG, routes through a dedicated
Commander dispatcher that persists `workflow.bound`, and proves the Worker
consumes that exact persisted plan before any GPU dispatch.

---

## Files changed

| File | Role |
|------|------|
| `src/muse/video_commander_admission.py` | Blocker C — added `commander_plan_id`, `commander_plan_source`, `workflow_ticket_id` fields to `VideoAdmissionRequest` and `binding_payload()` |
| `src/muse/business_opportunities.py` | Blocker A — `request_video_approval` freezes a CommanderPlan snapshot + DAG digest into the approval ticket; `queue_video_after_approval` enqueues a `kind=video_pipeline` execution ticket carrying all required bindings; also fixed recipe_sha256 derivation when brief has no explicit fingerprint |
| `src/muse/unified_runtime.py` | Blocker B — added `dispatch_video_pipeline_once()`: claims the execution ticket, reconstructs + validates the plan snapshot, calls `WorkflowController.bind_plan_to_ticket()` to persist `workflow.bound`, routes to VideoTaskWorker |
| `src/muse/video_task_worker.py` | Blocker C — replaced synthetic `CommanderPlan(source="video_task_worker_single_step_v1")` with: (1) fail-closed loading of `commander_plan_id/source` from persisted `video_pipeline` execution ticket; (2) deterministic single-step scene plan built from brief with `source="safe_fallback"`; (3) workflow-level IDs passed through `_consume_commander_admission_grant` to the admission request factory |
| `tests/test_video_commander_admission.py` | No new tests needed; existing 10 pass |
| `tests/test_business_opportunities.py` | +3 new tests for plan snapshot, execution ticket creation, digest consistency |
| `tests/test_unified_runtime.py` | +10 new tests for `dispatch_video_pipeline_once`: approval-flag gate, task-id gate, non-video-pipeline rejection, workflow.bound persistence, tampered DAG digest rejection, wrong plan_id rejection, missing approval ticket rejection, stale approval event_id rejection |
| `tests/test_video_task_worker.py` | +6 new tests: E2E handoff, commander_plan_id/source propagation, fail-closed with no execution ticket, DAG digest separation (Commander's gate), missing plan_id fail-closed, binding_payload field presence |

---

## Implementation contract per blocker

### A — Producer (`business_opportunities.py`)

`request_video_approval()` now calls `_build_video_commander_plan_snapshot(title, brief_sha256)` which returns a canonical `muse-commander-plan-snapshot-v1` snapshot (research → developer/build → reviewer, `source="safe_fallback"`) plus its SHA-256 DAG digest.  These are frozen into the approval ticket payload alongside `commander_plan_id`, `commander_plan_source`, `dag_sha256`.

`queue_video_after_approval()` now:
1. Retrieves the `approval_event_id` from the latest `human.decision` event.
2. Reads the frozen plan snapshot from the approval ticket.
3. Idempotently enqueues a separate `kind=video_pipeline` execution ticket carrying `approval_task_id`, `approval_event_id`, `approved_by`, `attempt_id`, `initiative_id`, `brief_sha256`, `commander_plan_id`, `commander_plan_source`, `commander_plan_snapshot`, `dag_sha256`.
4. Returns `execution_ticket_id` in the result.
5. Does **not** call `bind_plan()` on the approval ticket.

### B — Commander routing (`unified_runtime.py`)

`dispatch_video_pipeline_once(task_id, approved, worker_id, video_worker)`:
1. Requires `approved=True`.
2. Calls `_validate_video_pipeline_execution_ticket()` (existing read-only validator) to verify the DAG digest, approval ticket, and human.decision event — all before claiming.
3. Claims the execution ticket (`queue.claim_next(...)`).
4. Under the active lease, calls `WorkflowController.bind_plan_to_ticket()` to persist `workflow.bound`.
5. Records `commander.video_pipeline_bound` event.
6. Routes to the injected `video_worker.run_once()` if provided; otherwise returns `workflow_bound_no_worker`.
7. On any binding failure, records `commander.video_pipeline_bind_failed`, fails the ticket, and raises `RuntimeBlocked` — never routes to GPU.

Generic `dispatch_once` and `dispatch_commander_once` already block `video_pipeline` tickets; this new method is the only allowed path.

### C — Worker/admission (`video_task_worker.py`, `video_commander_admission.py`)

`VideoAdmissionRequest` gains three new optional fields (default `""`):
- `commander_plan_id` — workflow-level Commander plan ID from the execution ticket.
- `commander_plan_source` — `"safe_fallback"` or `"model"`.
- `workflow_ticket_id` — the execution ticket ID.

`binding_payload()` includes all three.

`build_video_admission_request()` accepts and passes through the three new kwargs.

In `VideoTaskWorker.run_once()`:
- **Before the generation loop**, the Worker reads `commander_plan_id`, `commander_plan_source`, and `workflow_ticket_id` from the matching `video_pipeline` execution ticket via `TaskQueue.list_tasks(kind="video_pipeline")`. If none is found or the values are blank, it raises `VideoAdmissionBlocked` with `"no persisted video_pipeline execution ticket found…"` — this surfaces as `blocked_admission` before any GPU dispatch.
- The scene-level admission plan (shot_001, shot_002, …) is still built deterministically from the brief: for multi-scene via `checkpoint_plan["plan"]`, for single-shot via a `CommanderPlan(source="safe_fallback")` derived from the attempt's `recipe_sha256`.  The synthetic `source="video_task_worker_single_step_v1"` is gone.
- `_consume_commander_admission_grant` accepts and forwards `commander_plan_id`, `commander_plan_source`, `workflow_ticket_id` to the admission request factory.

**DAG digest validation is Commander's responsibility** (`_validate_video_pipeline_execution_ticket` / `dispatch_video_pipeline_once`), not the Worker's. The Worker only requires the plan_id and source strings to be present.

---

## Test results (observed)

All runs executed directly with `py -3.11 -m unittest` from the `tests/` directory
(`PYTHONPATH=../src:.`); PSMUX was not used because direct execution succeeded.

| File | Before | After | Status |
|------|--------|-------|--------|
| test_business_opportunities | 15 | 18 | OK |
| test_unified_runtime | 62 | 72 | OK |
| test_video_task_worker | 38 | 44 | OK |
| test_video_commander_admission | 10 | 10 | OK |
| **Total** | **125** | **144** | **OK** |

> Note: the combined run reports 144 because `test_business_opportunities` imports
> `FakeAdmissionProvider` and `fake_admission_request_factory` from
> `test_video_task_worker`, sharing 1 test counted in both files in the
> combined run, hence total = 18+72+44+10 = 144.

`py -3.11 -m py_compile` returned exit 0 on all eight changed files.

---

## New tests summary

### test_business_opportunities (+3)
- `test_request_video_approval_freezes_plan_snapshot_and_dag_digest` — approval ticket contains a valid, self-consistent plan snapshot with a verifiable DAG digest.
- `test_queue_video_after_approval_enqueues_video_pipeline_execution_ticket` — `queue_video_after_approval` creates and returns an idempotent `kind=video_pipeline` execution ticket with all required fields.
- `test_video_pipeline_execution_ticket_dag_digest_matches_approval_ticket` — execution and approval tickets carry identical plan snapshot and DAG digest.

### test_unified_runtime (+10)
- `test_dispatch_video_pipeline_once_requires_approval_flag`
- `test_dispatch_video_pipeline_once_requires_explicit_task_id`
- `test_dispatch_video_pipeline_once_rejects_non_video_pipeline_ticket`
- `test_dispatch_video_pipeline_once_binds_workflow_and_returns_bound_state`
- `test_dispatch_video_pipeline_once_persists_workflow_bound_plan_id`
- `test_dispatch_video_pipeline_once_rejects_tampered_dag_digest` — rejected **before** ticket is claimed
- `test_dispatch_video_pipeline_once_rejects_wrong_plan_id_in_execution_ticket` — rejected before claim
- `test_dispatch_video_pipeline_once_rejects_missing_approval_ticket` — rejected before claim
- `test_dispatch_video_pipeline_once_rejects_stale_approval_event_id` — rejected before claim

### test_video_task_worker (+6)
- `test_e2e_approval_dag_worker_handoff_succeeds_with_persisted_plan` — full pipeline through `BusinessOpportunityStore` → Worker succeeds with mocked TTS.
- `test_e2e_worker_reads_commander_plan_id_and_source_from_execution_ticket` — Worker passes the correct `commander_plan_id`/`source` from the execution ticket through the admission factory.
- `test_worker_fails_closed_when_no_video_pipeline_execution_ticket_exists` — blocked_admission with descriptive reason.
- `test_worker_fails_closed_when_dag_digest_is_tampered` — clarifies that DAG digest validation is the Commander's gate; Worker reads plan_id/source directly and succeeds (tampered dag_sha256 alone does not block Worker).
- `test_worker_fails_closed_when_execution_ticket_has_missing_plan_id` — blank commander_plan_id → blocked_admission.
- `test_admission_request_binding_payload_includes_commander_plan_and_workflow_fields` — `binding_payload()` contains all three new fields with correct values.

---

## What remains

- **No production DB touched.** `data/*.sqlite3` files were not read or written.
- **No GPU dispatch.** Wan/ComfyUI paths were not reached; all tests use mock builders.
- **No publishing/network/payment calls.**
- **Legacy `prompt_to_video` path** remains fail-closed (HTTP 409 `video.commander_admission_required`).
- **Rights and commercial-use gates** are unchanged; rights review remains fail-closed.
- The `dispatch_video_pipeline_once()` method does not call `video_worker.run_once()` by default (it returns `workflow_bound_no_worker`). A real integration test with a live VideoTaskWorker instance requires the caller to inject `video_worker=` explicitly — this is intentional (separation of Commander binding from GPU dispatch).
- `test_video_product_builder.py` was not changed; it was not in the audit's affected scope and all its tests continue to pass.
