# Muse Lease Recovery — Step 1

## Scope

Step 1 introduces an additive lease-schema migration planner and a read-only lease doctor. It does not acquire or release leases, dispatch TaskQueue work, or start/stop the Supervisor. It does not touch `knowledge_nodes` or TaskQueue.

## Actual integration points

- Routine lease table: `data/routines/muse.sqlite3`, table `routines`.
- Runtime singleton: `data/autonomy/runtime.sqlite3`, table `autonomous_runtime`.
- Research candidate selection remains owned by `AutonomousResearchEngine._next_topic()` in `src/muse/autonomous_research.py`; the verified signature is `_next_topic(self, *, preferred_key: str | None = None) -> dict[str, Any]`. Creator-inbox candidates are selected there using `epistemic_status`, `review_state`, and `allow_external_research`; returned topic entries contain `support_query` and `counter_query`.

## Additive migration

The migration adds only lease metadata columns:

- `routines`: `heartbeat_at`, `ttl_seconds`, `pid`, `fencing_epoch`, `desired_state`, `process_identity`.
- `autonomous_runtime`: `ttl_seconds`, `fencing_epoch`, `desired_state`, `process_identity` (the table already has `heartbeat_at` and `pid`).

`lease_token` is preserved. Existing routine rows with `enabled=0` receive `desired_state='stopped'`; enabled rows default to `running`. The legacy runtime's `desired_state` defaults conservatively to `stopped`, because `state='running'` is not sufficient proof of user intent. A legacy runtime row with `state='stopped'`, `owner_id IS NULL`, and `pid IS NULL` is reported as `stopped_by_intent` with an explicit inference annotation, not as an orphan.

No `knowledge_nodes` columns are added. `research_status` remains metadata-only by design for later steps.

## Commands

From the Muse project root:

```powershell
$env:PYTHONPATH = "$PWD\src"

# Strictly read-only lease diagnosis
py -3.11 -m muse.lease_recovery doctor

# Read-only migration preview; no backup and no writes
py -3.11 -m muse.lease_recovery migrate

# Actual additive migration; explicit opt-in required
py -3.11 -m muse.lease_recovery migrate --apply
```
Apply checks both source databases with `PRAGMA integrity_check`, verifies that both daily backup targets are absent, then creates consistent snapshots with `sqlite3.Connection.backup()`. It uses `BEGIN IMMEDIATE`, `busy_timeout=10000`, and `PRAGMA table_info` checks to make column addition repeat-safe. A second invocation after successful migration returns `already_current` without creating another snapshot or writing rows. A backup-name collision aborts before any database is changed.

Snapshot names are `data/handoff/lease-migration-YYYYMMDD-routines.sqlite3` and `data/handoff/lease-migration-YYYYMMDD-autonomy.sqlite3`. Existing snapshots are never overwritten.

## Doctor interpretation

- `healthy`: a fresh heartbeat, live PID, and verifiable matching process identity are available.
- `suspected_hung`: PID is alive but heartbeat exceeds TTL; takeover is deliberately not implemented in Step 1.
- `orphaned`: desired state is `running` and the lease has expired with no live PID, or the live PID's observed process identity differs from the recorded identity.
- `identity_unverified`: PID is alive and heartbeat is fresh, but the recorded/observed process identity is unavailable; this is not considered healthy.
- `stopped_by_intent`: explicit `desired_state='stopped'`, or the conservative legacy inference `state='stopped'` with no owner/PID.
- `legacy_unknown`: old schema lacks enough heartbeat/TTL/process identity data. `last_status` and `lease_token` are never treated as proof of a live process.
Windows process identity is derived from process creation FILETIME plus executable image path. Linux identity uses `/proc/<pid>/stat` start ticks plus command line. If identity cannot be read, the doctor does not claim a verified match.

## Downgrade / rollback

The migration is additive. The safest logical downgrade is to leave the extra columns in place; old code ignores them and `lease_token` remains intact. Do not manually drop columns while any Muse process is active.

For exact physical rollback: stop all Muse processes and confirm no process has either database open; copy the matching two pre-migration snapshots back to their original paths; then run `PRAGMA integrity_check` on both restored databases and compare the preserved row counts/schema with the snapshots. Do not restore only one database. Keep the snapshots until all later lease-manager and runtime integration steps have been validated.

## Safety status

The implementation and tests use temporary SQLite files. The formal databases were opened read-only for `integrity_check` and `doctor` only. No migration was applied to formal databases, no Supervisor was started, and no TaskQueue/knowledge data was changed. Because the current formal runtime is `stopped` with no owner/PID, automatic restart remains prohibited until a human explicitly changes desired state to `running` in a later authorized operation.
