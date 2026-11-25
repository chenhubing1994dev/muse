# Muse Video Pipeline Security Audit — 2026-11-25

## Scope
Read-only audit of the current shared Runtime/TaskQueue/Worker/Builder snapshot. No production source files were modified in this audit.

## Verified Runtime contract
- `src/muse/unified_runtime.py` revision `2788764591325405` requires `video_worker.run_approved_ticket(...)`; legacy `run_once()` is not accepted by the dedicated video dispatcher.
- Worker success requires exactly 12 `completion_evidence` fields: execution task, Attempt, Initiative, Commander Plan, DAG digest, approval Ticket/event/actor, Worker, manifest digest, Reviewer and review event.
- Runtime checks the approval Ticket remains approved, requires exactly one artifact-verification event and one review event, checks ordering and identity-string equality, and independently verifies the manifest path, SHA-256 and byte count.
- Existing evidence: Runtime PSMUX `312b2db9af714f54914d8832b9d3b300` — 142/142 PASS. Full Python suite PSMUX `7a41c57ef2c84df28c35d57bd054bf65` — 2,654 tests, 0 failures, 0 errors, 1 skipped; compileall PASS. These tests do not establish real video execution or reviewer authentication.

## Critical finding: reviewer identity is not authenticated
`src/muse/task_queue.py` revision `2788764591325249`, lines 278–296, exposes `TaskQueue.record_event(task_id, event_type, *, actor, payload)` and accepts an arbitrary non-empty actor string. The Runtime completion validator currently checks that the review event actor equals the `reviewer_id` declared in Worker completion evidence. That is an equality check, not proof of identity.

A Worker able to call this generic event API can potentially append `video_pipeline.review_approved` while claiming a different actor/reviewer ID. The Runtime's distinct-string check (`reviewer_id != worker_id`) does not prevent that spoofing. Therefore the current completion gate must not be described as proving an independent human/agent reviewer.

### Required remediation
1. TaskQueue/security owner must provide a trusted reviewer identity mechanism: preferably a dedicated review transition API that authenticates the reviewer principal and enforces authorization, or a verifiable signed review attestation from a separately controlled key.
2. Generic `record_event()` must not be sufficient to create trusted `video_pipeline.review_approved` transitions.
3. Runtime must validate the trusted principal/attestation and bind it to the exact manifest digest, relative path, byte count, execution Ticket, Attempt, Initiative and Commander DAG.
4. Add adversarial regression: Worker writes a forged review event with a different actor string; Runtime must reject completion. Add positive test using a separately authenticated Reviewer.
5. Until this is implemented and independently tested, keep video completion fail-closed; do not accept Worker-generated review events as independent approval.

## Remaining integration blockers
- `src/muse/video_task_worker.py` remains owned by the Worker session. Latest shared snapshot still contains `CommanderPlan(source="safe_fallback")` and does not expose the required `run_approved_ticket()` entry point.
- `src/muse/video_product_builder.py` has no confirmed `build_creator_media_plan()` integration.
- No real GPU/ComfyUI/Wan end-to-end run has been proven by the Runtime unit tests.
- Existing creator/video assets remain internal-review only where rights, voice approval or visual review are pending. No public posting, deployment, paid spend or revenue is claimed.

## Ownership boundary
This audit does not edit Worker/Builder/TaskQueue-owned production code and does not bypass PSMUX, Commander, approval, Grant, lease or review gates.
