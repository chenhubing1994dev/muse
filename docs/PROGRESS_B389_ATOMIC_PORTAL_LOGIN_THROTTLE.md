# B389 — Atomic Web Portal Login Throttling

## Finding

The HTTPS portal previously split login throttling into three independent operations:
1. Check the count of recent failed attempts.
2. Verify the supplied password.
3. Persist the success/failure event.

Because the HTTP server handles requests concurrently, multiple requests from one client could all pass the first check before any failure event was committed. This created a time-of-check/time-of-use race in the five-failure limit.

## Change

- Added `PortalSessionStore.authenticate()`, which serializes the per-process rate-limit read, scrypt password verification, and audit-event write under the session-store lock.
- The atomic operation records `login_failure`, `login_success`, or `login_rate_limited` before releasing the lock.
- Preserved the existing 15-minute failure window, five-failure threshold, successful-login reset behavior, seven-day audit retention, and 10,000-event cap.
- Updated the HTTPS login handler to use the atomic operation; malformed login payloads remain rejected without being treated as password failures.
- Added a 12-thread regression test proving exactly five attempts are evaluated and seven are rate-limited when all twelve invalid requests race simultaneously.

## Verification

Command: `py -3.11 -m unittest discover -s tests -p test_web_portal.py -v`

- Web Portal suite: **19/19 passed**.
- New concurrent throttling regression: passed.
- Existing HTTPS login/session/logout, credential persistence/rotation, restart invalidation, proxy trust, CSRF, and origin policy tests: passed.
- `py_compile src/muse/web_portal.py`: passed in the same validation command.
- Runner Job: `wc_job_eFstqjs0ERLSRUb_`, terminal completed, exit code 0.

## Boundaries

This serializes requests handled by one Portal process. A future multi-process deployment sharing the same session database would need a database-level reservation/transaction design or a single-writer service; this change does not claim cross-process locking.

No production credentials, public deployment settings, or live databases were modified. Plugin execution remains fail-closed pending a verified sandbox runtime and Commander Worker integration.
