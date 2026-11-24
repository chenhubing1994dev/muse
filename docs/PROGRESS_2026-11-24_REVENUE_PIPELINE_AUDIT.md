# Muse Revenue Pipeline — Read-only Commander Binding Audit

Date: 2026-11-24

## Scope and safety

This audit inspected the current shared source and the existing local SQLite files using a dedicated read-only script:

- `scripts/audit_commander_workflow_binding_readonly.py`
- SQLite URI `mode=ro` and `PRAGMA query_only=ON`
- No schema creation, migration, insert, update, delete, GPU generation, publish API, or payment operation was performed.

## Observed state

- `data/tasks/muse.sqlite3` exists. The read-only sample found 47 tasks and 28 `workflow.bound` events.
- The six sampled legacy `video_pipeline` tickets had no `workflow.bound` event.
- No `business_opportunity_video_approval` ticket was present in the sampled task records.
- `data/initiatives/muse.sqlite3` exists and contains 3,153 rows in each of `initiative_video_attempts` and `initiative_video_attempt_events`.
- `data/business/opportunities.sqlite3` does not exist. The business-opportunity ledger is therefore not yet instantiated in the current workspace.

## Source-level blocker

- `BusinessOpportunityStore.request_video_approval()` creates a human-approval TaskQueue ticket, but does not bind a persisted Commander DAG to it.
- `queue_video_after_approval()` verifies the persisted approval and creates an Initiative video attempt, but does not create a `workflow.bound` event or attach a Commander plan to the attempt.
- `VideoTaskWorker.admission_context_for_step()` still constructs a local synthetic `CommanderPlan` with source `video_task_worker_single_step_v1`.
- `VideoAdmissionRequest.binding_payload()` includes `dag_sha256`, batch and approval fields, but omits explicit `commander_plan_id` and `commander_plan_source`.

This means the current video generation admission path is not yet proven to consume a persisted Commander DAG. The old `video_pipeline` TaskQueue path remains fail-closed; do not re-enable it as a shortcut.

## Recommended ownership and implementation contract

1. **Producer — Dq sole writer:** `src/muse/business_opportunities.py` and `tests/test_business_opportunities.py`. Freeze the exact validated storyboard/CommanderPlan snapshot and brief digest in the human approval ticket. After a matching approval event, idempotently enqueue a separate `kind=video_pipeline` execution ticket carrying approval_task_id/event_id/approved_by, attempt_id, initiative_id, brief_sha256, plan_id/source, serialized plan and DAG digest. Do not call `bind_plan()` on a `waiting_approval` ticket; that API expects queued child tickets.
2. **Commander routing — 42Ft sole writer:** `src/muse/unified_runtime.py` and `tests/test_unified_runtime.py`. Special-case `video_pipeline` execution tickets. Claim the parent ticket, reconstruct and validate the plan snapshot, call `WorkflowController.bind_plan_to_ticket()` under the active lease to persist `workflow.bound`, then route to the dedicated VideoTaskWorker handler. Do not send video tasks through generic Ruflo agent dispatch.
3. **Worker/admission — 3e sole writer:** `src/muse/video_task_worker.py`, `src/muse/video_commander_admission.py`, `src/muse/video_product_builder.py`, and their direct tests. Consume the exact persisted plan and verify workflow.bound plan_id/source/DAG digest, scene step IDs, brief hash, approval event, current lease and one-shot grant. `VideoAdmissionRequest` and grant binding must explicitly include commander_plan_id/source and workflow_ticket_id. Missing or mismatched binding must fail closed before GPU dispatch.
4. **Coordinator:** this audit/doc session owns only the read-only audit script and progress documentation; it does not write the producer, runtime, worker, admission, builder or PipelineController files.
5. Do not construct a synthetic fallback plan in the Worker. Do not create workflow events from inside the Worker unless the existing lease/Commander contract explicitly permits it.
6. Keep scene asset provenance, rights review and commercial-use gates fail-closed. Validate with temporary databases and mock generation first; do not touch production DBs or real GPU until the complete integration tests pass. A completed local render is not permission to publish.

## Commercial status

Local video, site-preview and social-copy assets exist, but the business ledger and live storefront/payment connection do not. No public publication, customer lead, sale, or settled revenue is claimed. The immediate revenue-enabling blocker is the persisted approval-to-Commander-DAG-to-VideoTaskWorker handoff, followed by owner-controlled contact/payment setup and human release review.
