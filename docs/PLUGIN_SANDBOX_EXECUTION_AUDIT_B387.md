# B387 — Plugin sandbox execution boundary audit

Date: 2026-11-24

## Audited implementation state

Read-only inspection of `src/muse/plugin_platform.py` confirms:

- `validate_approved_plugin_ticket()` revalidates Commander ticket state, human approval audit, current Worker lease owner/expiry/attempt, plugin enabled state, manifest version/hash, publisher signature identity, and declared capability/permissions.
- Its docstring explicitly says it is an admission/audit check only; it never executes code.
- A successful result is `approved_for_worker_review`, with `execution_enabled=false` and `runtime_adapter_registered=false`.
- Plugin capability registration/request paths also retain `executable=null` and `execution_enabled=false`.
- The current state is therefore intentionally fail-closed. A publisher signature, enabled plugin state, or human approval alone is not an execution grant.

## Required execution chain

No third-party plugin code may execute until all of these gates are implemented and independently tested:

1. **Artifact identity** — immutable artifact digest, manifest digest, publisher key identity, signature verification, version/ABI compatibility, and revocation recheck immediately before execution.
2. **Commander authority** — one current approved Commander ticket; approval event and actor match; current Worker lease owner/epoch matches; stale, replayed, revoked, expired, or reassigned tickets are rejected.
3. **Capability grant** — exact capability ID and input schema; explicit per-capability filesystem/network/process permissions; default deny; no ambient host credentials or inherited environment secrets.
4. **WASM isolation** — a real Wasmtime (or equivalently audited) runtime with no WASI preopens by default, no implicit host imports, bounded linear memory, fuel/instruction budget, wall-clock deadline, and deterministic termination. The host must expose only narrow, audited capability shims.
5. **Resource broker** — quota enforcement for memory, CPU/fuel, output bytes, concurrent instances, and execution duration; quotas are checked before instantiation and enforced during execution.
6. **Audit and recovery** — append-only start/finish/denial/timeout/kill records bound to task, attempt, worker, artifact digest, capability grant, and resource usage; process/runtime crash must not leave a ticket marked succeeded.
7. **Kill switch and rollback** — operator disable/revocation blocks new starts immediately; active instances can be terminated; failed or suspicious versions can be quarantined without deleting evidence.
8. **Worker integration** — only the unique Commander Worker may consume the approved grant once, immediately before runtime instantiation. Generic LLM/Ruflo/DAG dispatchers must continue rejecting plugin execution envelopes.

## Minimum acceptance tests

- Missing runtime, missing approval, invalid signature, revoked key, changed manifest/artifact, stale ticket, expired lease, wrong Worker, replayed grant, and undeclared capability all deny before instantiation.
- Default filesystem and network access are unavailable; only explicitly granted broker calls succeed.
- Memory/fuel/deadline/output/concurrency limits are enforced under adversarial workloads.
- Timeout, cancellation, Worker crash, runtime trap, and audit-store failure all fail closed; none may mark the task succeeded.
- A one-shot grant cannot be consumed twice or by a different Worker/attempt.
- Kill switch and key revocation block subsequent execution and terminate eligible active work.
- Tests use temporary isolated databases and workspaces; they must not touch canonical Muse user data.

## Current decision

Keep plugin execution disabled. Do not register a placeholder adapter that reports success, do not treat a subprocess or a copied workspace as a security sandbox, and do not expose host filesystem/network access to a plugin. Implement the runtime adapter only after the runtime dependency and its isolation guarantees are available in the actual Runner environment.

## Related verified baseline

- B386 TaskQueue / PluginPlatform / Watchdog regression: 103/103 passed.
- B386 inspiration/productization/video admission/Status API/Web Portal integration regression: 110/110 passed.
- New SQLite lease-expiry lock-contention regression: five consecutive passes.
- The preceding isolated full-suite baseline: 2,475 tests passed. It predates the B386 test-only addition and was not repeated in this slice.
