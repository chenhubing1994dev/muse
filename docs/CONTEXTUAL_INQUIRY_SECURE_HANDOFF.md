# Contextual Inquiry Secure Handoff — Engineering Decision

Date: 2026-11-21
Status: DPAPI ciphertext storage, exact evidence-candidate resolution, anti-forgery approval gating, separate review-only authorization, dedicated review-only lease claiming, and worker-lease-bound case reading are integrated. No review handler or Commander dispatch is enabled. Latest focused tests: 71 passed. Latest full-suite attempt: 1,807 tests, 1 error, 1 skipped; the error is in a concurrently developed video-prompt test, not this track.
## Current verified state

- The CLI validates a local `ContextualInquiry` JSON document and creates only a generic approval-gated placeholder. Its input file does not grant consent.
- The existing TaskQueue stores `payload_json` as ordinary SQLite text. Its task-created event records only the title and priority, not the task payload. Therefore, putting raw case data in the payload would persist plaintext in the task database.
- `TaskQueue.approve()` now requires `case_context_attached=true`, `requires_context_reentry=false`, `context_status=validated_attached`, and a trusted `contextual_inquiry.context_attached` audit event with the expected actor/payload. The public `record_event()` API rejects this reserved event type, so forged payload flags alone cannot pass approval. This is an application-level API guardrail, not process isolation from arbitrary code running as the same Windows user. The trusted lease-fenced attachment writer is not implemented yet.
- `TaskQueue.authorize_contextual_inquiry_review_only()` records a distinct human review-only decision under `BEGIN IMMEDIATE`, with task `updated_at` compare-and-swap, DPAPI envelope-shape/pending-state checks, a reserved audit event, and replay rejection. It deliberately leaves the task in `waiting_approval`, never sets `_approved`, and does not dispatch work. The caller-supplied actor label is audit metadata, not authenticated identity.
- The existing offline runtime deliberately selects only `local_workflow`, `video_pipeline`, and `review_followup_research`; it does not dispatch contextual inquiry tasks. The worker's default handler registry also has no contextual-inquiry handler.
- The current project source scan found no pre-existing DPAPI, Windows Credential Manager, keyring, Fernet, or equivalent application-level case-encryption adapter.
- A new standard-library-only DPAPI CurrentUser primitive exists in `src/muse/contextual_inquiry_security.py`; it emits a strict versioned ciphertext envelope, uses application-specific optional entropy and UI-forbidden native calls, and has no plaintext fallback. The primitive itself does not write files or modify TaskQueue.
- The CLI and bridge support a third, independent case-storage consent. When granted, only the DPAPI ciphertext envelope is placed in the existing TaskQueue payload; `case_context_attached` remains false and the task remains non-approvable.
- `load_encrypted_case_for_review()` provides a narrow read-only path for a pending Ticket, and after dedicated review-only claim accepts a running Ticket only when the exact unexpired Worker lease owner is supplied. It decrypts under the current Windows user, reconstructs and revalidates the full data contract, checks Ticket identity/workflow contract and review-only authorization, and returns an in-memory object with knowledge-memory consent reset to false. It never mutates the Ticket, marks context attached, approves, or dispatches it. The API is not yet registered as a Commander handler and is not an authorization boundary against other code running as the same user.
- The previous shared-project full-suite snapshot completed successfully through the psmux-backed project command path: 1,752 passed, 1 skipped, 0 failures in 178.619 seconds. The latest attempt after the lease primitives ran 1,807 tests and reported one error plus one skip; see the validation section below. No video-worker implementation files were changed in this contextual-inquiry phase.


## Review-only lease primitives — 2026-11-21

- `TaskQueue.claim_contextual_inquiry_review_only()` adds a dedicated `BEGIN IMMEDIATE` transition from `waiting_approval` to `running`, fenced by `updated_at` compare-and-swap. It requires the separate review-only authorization payload and matching reserved audit event, validates the encrypted-envelope shape and pending-state flags, issues a worker lease, and never sets `_approved`. It does not use the generic `claim_next()` scheduler.
- `load_encrypted_case_for_review()` retains its read-only pending-ticket path and now also accepts a running Ticket only when the caller supplies the exact live lease owner. The running path checks lease expiry, separate review-only authorization, and its matching audit event before decrypting and revalidating the case. The returned object still carries no knowledge-memory consent.
- This is a queue/reader primitive, not a registered Commander handler or an isolation boundary against arbitrary code running as the same Windows user. No actual review model invocation, result attachment, long-term persistence, or downstream approval is enabled.
- Focused psmux regression: **71 tests passed, 0 failures**. Python compilation passed in the full-suite command.
- The previous shared-project full-suite snapshot reported 1,752 passed, 1 skipped, 0 failures. The latest attempt after this change ran 1,807 tests and reported 1 error plus 1 skip. The error is in `test_video_prompt_composer.VideoPromptComposerTests.test_rejects_overlong_compiled_clip_prompt`: its fixture constructs `creative_intent="目标" * 1200` (2,400 characters) before entering the expected-exception block, while `VideoPromptSpec` rejects `creative_intent` above 1,200 characters. This concurrently developed video test was not modified in this track.

## Decision: preserve the existing queue, encrypt before persistence

The smallest viable durable handoff is an encrypted envelope in the existing TaskQueue payload, not a second case database, queue, scheduler, or unencrypted sidecar file.

The Windows-only, standard-library DPAPI adapter is implemented in `src/muse/contextual_inquiry_security.py`. It uses CurrentUser scope and UI-forbidden flags, a fixed application-specific optional entropy value, strict envelope shape/version/size checks, and fails closed on unsupported platforms or native API errors. It must never fall back to plaintext, a hard-coded encryption key, a machine-wide key, or a generated key stored beside the database. DPAPI protects data at rest for this Windows user profile; it is not isolation from malware or other programs already running as the same user.

The CLI asks for a third, independent confirmation before storing the case context. The complete case document—including observations, hypotheses, unknowns, third-party statements and proposed experiments—is persisted only as DPAPI-protected ciphertext in the existing local TaskQueue. Declining preserves the generic placeholder behavior.

The TaskQueue may contain only a versioned envelope with ciphertext and non-sensitive format metadata. It must not contain plaintext case fields, excerpts, source text, stakeholder narratives, or plaintext hashes that expose case content. The new pre-approval read helper decrypts only after checking task kind, pending-approval state, envelope flags, Ticket identity, canonical JSON, and the workflow contract. It returns the validated object in memory and does not attach or approve it. It is not yet a registered Commander handler.

## Approval and execution gates

1. No case-storage consent: keep the current context-reentry placeholder and keep approval blocked.
2. Case-storage consent: persist only DPAPI-protected ciphertext. This alone does not authorize review execution.
3. Until a trusted contextual-review handler is registered and can decrypt/validate the envelope, keep the task non-approvable. A missing handler must fail closed, not become a generic fallback.
4. Only after handler registration, successful decrypt/validation, and a separate human task approval may the Commander dispatch review.
5. Decryption must be scoped to the current Windows user profile. Moving the database to another Windows account or machine is not a supported recovery path; the user must re-enter the case there.
6. The handler must not automatically execute proposed action experiments or write raw case narratives to long-term memory.

## Evidence-linking requirements

- Resolve every hypothesis `source_refs` and `counter_refs` through the inquiry's observation table.
- Resolve each observation's `source_ref` to exactly one active `source` node in the existing ResearchKnowledgeStore. Missing, ambiguous, inactive, deleted, and non-source references remain unlinked.
- Record resolved references as unadjudicated claim-evidence candidates through the existing store API. It creates `relates` edges only; it does not assert support or refutation.
- Preserve the existing claim lifecycle: newly persisted hypotheses remain `proposed`; any promotion requires verified citation anchors, evidence review, and the store's claim-transition rules.

## Required implementation and validation before enabling execution

- Register a dedicated contextual-review handler only in the existing Commander/worker path. It must call the narrow reader, conduct the evidence-aware review, and explicitly attach the validated context before the task can become approvable. Do not add a competing scheduler.
- Add the trusted lease-fenced context-attachment writer with compare-and-swap semantics. The approval gate now rejects forged flags and generic audit-event insertion, but the current reader intentionally performs no mutation and no trusted writer yet emits the required attachment event.
- Verify idempotent task submission cannot silently reuse a different stored envelope or consent state; reject mismatches without mutating the existing task.
- Ensure generic task snapshots, event logs, CLI output, progress telemetry, and errors never reveal plaintext case content.
- If evidence candidates later receive a review, require verified citation anchors, quality/independence assessment, and the existing ResearchKnowledgeStore review lifecycle before any claim transition or supports/refutes edge.
- Run focused tests through the project's psmux-backed `muse_dev.py` command path, then run the broader suite and report unrelated failures separately.

## Explicit non-goals

- No second SQLite database or parallel task queue.
- No plaintext case JSON copy, temporary plaintext handoff file, or plaintext fallback.
- No direct reuse of Shendeng's CC BY-NC 4.0 skill text, formulas, or method entries.
- No automatic psychological diagnosis, motive attribution, or action execution.
- No changes to concurrent video-worker files or shared `docs/PROGRESS.md` in this phase.

## Current completion boundary
The DPAPI primitive, optional ciphertext-in-TaskQueue path, read-only case loader, exact source-to-evidence-candidate resolution, anti-forgery approval gate, separate review-only authorization, dedicated review-only lease claim, and lease-bound running-case reader are implemented. The latest focused batch passed 71 tests. The latest full-suite attempt ran 1,807 tests with 1 error and 1 skip. No real case data has been stored during development; no review handler or Commander dispatch is registered, no trusted context-attachment writer exists, and the Ticket remains non-approvable for downstream work.


## Execution-path audit — 2026-11-21

A read-only source audit of the current TaskQueue, worker, and AutonomousRuntime establishes additional constraints for the next implementation phase:

- `TaskQueue.claim_next()` claims only `queued` Tickets, creates a worker lease, and returns the full decoded payload. A `waiting_approval` contextual-inquiry Ticket cannot be processed by the current worker without a distinct, explicit transition into a review-only queue state.
- `TaskQueue.approve()` is the existing generic human approval transition. It changes `waiting_approval` to `queued` and sets `_approved`; contextual-inquiry approval now requires the validated attachment flags plus a durable trusted attachment event. The public `record_event()` API rejects that reserved event type, so setting payload flags alone cannot bypass the gate. Reusing generic approval to authorize pre-approval review would still collapse two different decisions: permission to inspect/analyze a case and approval of downstream work.
- `worker.run_once()` calls handlers with `(payload, task_id)` or `(payload)`; it does not pass the worker identity or a lease token into the handler. A handler therefore cannot independently perform a lease-fenced attachment using the current interface without a narrow execution-context extension or a trusted queue-side operation.
- `TaskQueue.record_workflow_event()` supports workflow-revision CAS and, for selected workflow events, verifies the current unexpired worker lease. It appends an event only; it does not atomically update the task payload. It is not by itself a context-attachment API.
- `TaskQueue.finish()` checks `status='running'` and optionally the lease owner, then stores every non-null handler result in the ordinary SQLite `payload_json` as `_result`. It does not enforce lease expiry. Consequently, a review handler must never return plaintext case fields, source excerpts, stakeholder statements, or unredacted model output. Any durable result must be explicitly sanitized or separately DPAPI-protected, and its attachment/event must be atomic and lease-fenced.
- Worker progress telemetry currently uses generic summaries rather than task payload text. That invariant must remain true for the contextual-review path, including exceptions and failure guidance.
- `AutonomousSupervisor._next_local_workflow_id()` explicitly selects only `local_workflow`, `video_pipeline`, and `review_followup_research`; `contextual_inquiry_review` is not part of its offline dispatch allowlist. `DEFAULT_HANDLERS` also has no contextual-review handler. Do not widen the offline allowlist or register a generic fallback merely to make the task run.

### Required two-decision model

1. Keep the existing initial Ticket in `waiting_approval`. Case-storage consent permits ciphertext retention only; it does not authorize review execution.
2. A narrowly named, audited `review_only` human decision is now recorded by `authorize_contextual_inquiry_review_only()` with a task-version CAS. It does not queue or dispatch the Ticket, set the downstream `_approved` flag, or mark context attached. A separate, Commander-owned review dispatch mechanism is still required.
3. Register the handler only in the existing Commander-owned path, not the generic default worker registry or offline autonomous allowlist. It must decrypt and analyze in memory, never execute proposed experiments, never write long-term claims, and return only a bounded privacy-safe result (or an encrypted result envelope).
4. Add one TaskQueue transaction that updates the relevant payload state and appends its audit event under `BEGIN IMMEDIATE`, with expected state/revision and a current worker-lease fence. A standalone workflow event or generic payload mutation is insufficient.
5. After the review result is available, require a **separate downstream human decision** before any knowledge persistence, claim adjudication, or action experiment. Preserve `ContextualInquiry.consent_to_store` as a distinct long-term-memory consent; the DPAPI case-storage consent must never imply it.
6. Add negative tests for wrong task kind/status, absent or mismatched ciphertext, stale/foreign/expired lease, CAS conflict, duplicate replay, result leakage, generic worker dispatch, and attempts to use review-only authorization as downstream approval.

No handler or trusted attachment writer was added in this iteration. Review-only authorization now has its own payload-plus-audit transaction and task-version CAS, but it is only a durable decision marker; it does not dispatch work. The separate context-attachment transaction still needs a current worker-lease fence and a handler execution context with lease identity. The approval gate requires the future trusted attachment event, and the public event API cannot forge it; private Python methods are not an isolation boundary against arbitrary same-user code. Enabling review execution before the writer and handler exist would weaken the documented boundary; the placeholder remains intentionally non-approvable.
