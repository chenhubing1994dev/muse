# B173 MemPalace Commander approval receipts

Date: 2026-11-19

## Scope

Bind the final external MemPalace checkpoint to a durable, human-approved Muse Commander Ticket. This adds a safe runtime path without enabling automatic sync or production projection.

## Implementation

- Added `MuseRuntime.request_mempalace_projection_approval(outbox_id)`, which creates an approval-required Commander Ticket containing the exact outbox ID, idempotency key, source memory ID/revision and content hash.
- Added `MemPalaceProjectionOutbox.deliver_with_approval_ticket(...)`. Before any external checkpoint, it loads the persisted Ticket and event history from the task database.
- The verifier requires the Ticket kind to be `mempalace_projection_approval`, exact outbox/idempotency binding, an approved persisted payload, an eligible Ticket state, and a matching `human.decision=approved` event by the recorded approver.
- The runtime delivery method now requires `approval_task_id`; the old caller-supplied `approved=True, approved_by=...` route is no longer exposed through MuseRuntime.
- Added tests for missing approval, successful Commander approval, and cross-projection Ticket substitution. Updated the fake checkpoint acknowledgement to echo the exact content expected by the existing response validator.

## Validation

- `tests/test_mempalace_projection.py`: **11/11 passed**.
- `tests/test_unified_runtime.py`: **46/46 passed**.
- Combined focused tests: **57 passed**, exit code 0.

## Safety boundaries

- Tests use temporary SQLite databases and synthetic Agent Track records.
- No production MemPalace palace was initialized or written.
- No User Track, private conversation, or personal files were projected.
- No background sync, retry loop, HTTP daemon, or remote hub was enabled.
- The low-level outbox primitive remains a local internal interface; production callers should use the Commander-gated MuseRuntime path.

## Remaining work

- Add a durable health/freshness view and pause/resume control.
- Verify backup/restore and index rebuild against a separate synthetic palace.
- Review the full workspace diff and run broader regression tests before considering production enablement.
