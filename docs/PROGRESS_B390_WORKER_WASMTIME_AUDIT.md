# B390 — Worker Failure-Taxonomy Recheck and Wasmtime Staging Audit

## Worker regression report reconciliation

A collaborator reported an earlier Job, `wc_job_GZVE29DV4nGjCQCZ`, where
`test_worker_persists_stable_failure_code_without_exception_message` failed
with a missing `exception_type` in the `failure.classified` event payload.
The collaborator clarified that this Job ran against the separate
`/home/muse/work/Muse-linux-native` copy, not the canonical Windows workspace.

The canonical Windows workspace was checked directly:
- The reported single test passed.
- The complete current `tests/test_worker.py` module passed: **40/40 tests**.
- Job: `wc_job_jcf11xVowMMWhd0V`, terminal completed, exit code 0.
- The canonical `worker.py` lines 536–544 explicitly add a bounded exception type
  to the result without storing the exception message.

Therefore this is a cross-copy synchronization/revision discrepancy, not a
reproduced failure in the canonical Windows workspace. The Linux-native copy
must be synchronized and independently revalidated before claiming cross-platform
parity. No Worker or TaskQueue source was modified during this recheck.
- Staged archive: `.tools/wasmtime/staging/wasmtime-v51.0.0-x86_64-windows.zip`
- Size: 12,560,257 bytes.
- SHA-256 observed: `D2F5853A1CDED9B1256B45C...` (the full digest was checked by the
  prior B372 provenance record; this audit did not independently re-fetch the
- Staged archive: `.tools/wasmtime/staging/wasmtime-v51.0.0-x86_64-windows.zip`
- Size: 12,560,257 bytes.
- SHA-256 observed in this audit: `D2F5853A1CDED9B1256B45CD57686B6855E69049EF07FD4AD78FEB78DFA3AADA`.
- No `wasmtime.exe` was present in the staging directory or the expected tools
  root path.
The archive was not extracted or executed. The runtime adapter remains absent;
therefore plugin execution remains disabled and fail-closed.

## Collaboration boundary

This round did not modify `web-portal/app.js`, `web-portal/index.html`,
`web_portal.py`, product import APIs, `worker.py`, or `task_queue.py`.
The creator-feedback import proposal and Worker ownership question remain
coordination items; no ownership is assumed from a read-only inspection.

## Remaining gates

1. Confirm the active owner/branch for Worker failure-event plumbing before any
   source edit if the failure reappears.
2. Verify the staged Wasmtime archive against independently retrieved official
   release metadata/signature before extraction or execution.
3. Implement and test a real sandbox adapter, capability broker, resource limits,
   Commander one-shot authorization, audit, revocation, and kill switch before
   enabling any plugin code.
