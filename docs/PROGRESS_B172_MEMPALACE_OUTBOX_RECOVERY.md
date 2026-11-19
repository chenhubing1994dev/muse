

# B172 MemPalace projection outbox restart recovery

Date: 2026-11-19

## Scope

This slice verifies the existing MP-M4 projection outbox's durable behavior across process boundaries. It does not enable production projection, change the MemPalace palace, or transfer User Track data.

## Change

- Added a restart-recovery test for a staged Agent Track projection.
- The test closes the outbox after queueing, reopens it, verifies the pending payload survives, and explicitly delivers it with the second per-call approval.
- It closes and reopens the outbox again, verifies the delivered state persists, and proves a replay does not invoke the remote checkpoint twice.

## Safety boundaries

- No production memory was projected.
- No personal conversation, User Track record, or private file was transferred.
- No automatic sync, background retry, service startup, or remote hub was enabled.
- Existing MemPalace integration remains opt-in and disabled by default.

## Validation

- `py -3.11 -m unittest discover -s tests -p 'test_mempalace_projection.py' -q` → **9 tests passed**, exit code 0.
- The new restart test verifies pending-state recovery, second-approval delivery, delivered-state persistence and replay suppression across two outbox reopen cycles.



- Focused diff reviewed; changes are limited to the new synthetic restart test and this report.

## Remaining MP-M4 / MP-M5 work

- Replace caller-supplied approval labels with verifiable Commander/UI approval receipts before production writes. Completed in B173 for the MuseRuntime delivery path; see `docs/PROGRESS_B173_MEMPALACE_COMMANDER_APPROVAL.md`.
- Add backup/restore and index-rebuild verification for the separate MemPalace palace.
- Add health/freshness visibility and controlled pause/resume UX.
- Keep User Track isolated until a separate palace and retrieval scope are implemented.
