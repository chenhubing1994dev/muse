# Muse Lease Manager — Step 2

## Scope and integration

Step 2 adds src/muse/lease_manager.py and wires fencing epochs into the existing RoutineStore and RuntimeStateStore paths. It does not add a scheduler, queue, or competing supervisor.

The lease manager supports two existing resources:

- routines rows keyed by id, owner column lease_token.
- autonomous_runtime singleton row keyed by singleton=1, owner column owner_id.

## Safety semantics

- acquire requires desired_state='running', a free owner/PID slot, and a verifiable process identity.
- renew and release use compare-and-swap on both owner and fencing_epoch.
- Every successful new acquisition increments fencing_epoch monotonically.
- takeover_if_stale requires heartbeat age greater than TTL and desired_state='running'.
- Missing PID permits orphan takeover. A live PID with expired heartbeat returns suspected_hung; only explicit force_takeover=True can proceed, and only when the recorded and observed process identities match.
- PID reuse (identity mismatch) is reported as orphaned and is not auto-taken over.
- desired_state='stopped' blocks acquire and takeover. RuntimeStateStore.set_control('running') is an explicit user resume action; a legacy runtime row with state stopped is migrated to desired_state stopped.
- RoutineStore's migrated claim path only claims rows with no lease owner, PID, or lease expiry. It never silently steals an expired lease. Stale completion/heartbeat calls include the fencing epoch and are rejected after takeover.
- RuntimeStateStore heartbeat, error, and release writes include the fencing epoch. The Supervisor obtains its lease through LeaseManager before entering its loop.

## Audit and backups

Every LeaseManager mutation creates a unique data/handoff/lease-op-*.sqlite3 snapshot using sqlite3.Connection.backup() before the write transaction. RoutineStore lease operations create routine-op-*.sqlite3; RuntimeStateStore mutations create runtime-state-*.sqlite3. A collision is fatal; no backup is overwritten.

The lease manager uses BEGIN IMMEDIATE and busy_timeout=10000. The routine and runtime stores retain their existing table layout and only use the additive columns from Step 1.

## API

    from muse.lease_manager import LeaseManager

    manager = LeaseManager(
        "data/routines/muse.sqlite3",
        resource_table="routines",
        resource_id="routine-row-id",
    )
    lease = manager.acquire("supervisor-owner", pid=1234, ttl_seconds=60)
    manager.renew("supervisor-owner", lease.epoch, ttl_seconds=60)
    manager.release("supervisor-owner", lease.epoch)

    # Only after doctor reports expired heartbeat and absent PID:
    manager.takeover_if_stale("new-owner", pid=2345, ttl_seconds=60)

    # A live but hung process requires an explicit operator decision:
    manager.takeover_if_stale(
        "new-owner", pid=2345, ttl_seconds=60, force_takeover=True,
    )

The same manager accepts resource_table='autonomous_runtime' and the runtime singleton database path.

## Important operational risk

The strict snapshot-before-every-write policy includes heartbeats. With a 5-second runtime heartbeat, this can create many full SQLite snapshots and consume substantial disk space. Do not start the production Supervisor until the operator chooses a bounded backup policy (for example, per lease epoch plus explicit pre-takeover snapshots) or confirms sufficient storage. The current implementation favors auditability over disk efficiency and does not silently skip backups.

## Tests

New temporary-database coverage is in:

- tests/test_lease_manager.py
- tests/test_routine_fencing.py
- tests/test_runtime_fencing.py

The existing tests/test_routines.py and tests/test_autonomous_runtime.py are regression suites. No test writes to the formal Muse databases.

Run the relevant suites from the project root:

    $env:PYTHONPATH = "$PWD\src"
    py -3.11 -m unittest tests.test_lease_manager tests.test_routine_fencing tests.test_runtime_fencing -v
    py -3.11 -m unittest tests.test_routines tests.test_autonomous_runtime -v

pytest is not installed in the current runner Python environment. The tests use unittest.TestCase, so pytest can discover them when pytest is made available, but this implementation does not install dependencies.
