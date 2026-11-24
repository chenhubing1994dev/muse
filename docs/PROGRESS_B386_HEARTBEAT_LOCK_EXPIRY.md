# B386 — SQLite lease heartbeat lock-expiry regression

Date: 2026-11-24

## Scope

Add a deterministic regression test for the lease-clock race in `TaskQueue.heartbeat()`: a Worker begins a heartbeat while another SQLite connection holds the writer lock, and the lease expires before the lock is released.

## Change

- Added `test_heartbeat_waiting_on_writer_lock_cannot_renew_lease_after_expiry` to `tests/test_task_queue.py`.
- The test uses two independent `TaskQueue` connections to the same temporary SQLite database.
- The lock-holder acquires `BEGIN IMMEDIATE`; a Worker thread attempts heartbeat; the one-second lease is allowed to expire before the writer lock is released.
- Assertions prove the heartbeat is rejected, the original heartbeat/expiry fields remain unchanged, and no extra audit event is written.
- No production source or canonical project database was modified in this slice.

## Validation

Command:

```powershell
$env:PYTHONPATH = 'src'
py -3.11 -m unittest tests.test_task_queue tests.test_plugin_platform tests.test_watchdog
```

Result: **103 tests passed, 0 failures, 0 errors** (8.110 seconds).

The lock-contention regression was additionally run five consecutive times; all five runs passed (each approximately 1.2–1.4 seconds).

Product/integration regression command:

```powershell
$env:PYTHONPATH = 'src'
py -3.11 -m unittest tests.test_inspiration_evolution tests.test_inspiration_productization tests.test_video_admission_runtime tests.test_video_product_api tests.test_status_api tests.test_web_portal
```

Result: **110 tests passed, 0 failures, 0 errors** (134.729 seconds). This validates the current inspiration-to-offline-product workflow, video admission boundaries, Status API and Portal regression surface; it does not represent a real GPU render or mobile-device acceptance.



The isolated full-suite evidence from the immediately preceding shared validation remains **2,475 tests passed**. This change is test-only; the full suite was not repeated to avoid redundant long-running runs.

## Remaining gates

- Validate the plugin sandbox execution adapter only after a real isolated runtime, explicit permission/resource budgets, signed artifact verification, Commander approval revalidation, audit, and rollback are connected. Plugin execution remains disabled and fail-closed.
- Complete server deployment checks with a real public origin, TLS/reverse proxy, firewall and recovery exercise.
- Perform Android, iOS, Windows and Linux device acceptance. No claim of cross-platform production readiness is made.
