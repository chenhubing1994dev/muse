# Contextual Inquiry Secure Handoff — Engineering Decision

Date: 2026-11-20
Status: DPAPI ciphertext storage and exact evidence-candidate resolution are integrated; review execution remains disabled. Latest full Muse regression: 1,741 passed, 1 skipped.

## Current verified state

- The CLI validates a local `ContextualInquiry` JSON document and creates only a generic approval-gated placeholder. Its input file does not grant consent.
- The existing TaskQueue stores `payload_json` as ordinary SQLite text. Its task-created event records only the title and priority, not the task payload. Therefore, putting raw case data in the payload would persist plaintext in the task database.
- `TaskQueue.approve()` currently blocks the contextual-inquiry placeholder while `case_context_attached` is false and `requires_context_reentry` is true.
- The existing offline runtime deliberately selects only `local_workflow`, `video_pipeline`, and `review_followup_research`; it does not dispatch contextual inquiry tasks. The worker's default handler registry also has no contextual-inquiry handler.
- The current project source scan found no pre-existing DPAPI, Windows Credential Manager, keyring, Fernet, or equivalent application-level case-encryption adapter.
- A new standard-library-only DPAPI CurrentUser primitive exists in `src/muse/contextual_inquiry_security.py`; it emits a strict versioned ciphertext envelope, uses application-specific optional entropy and UI-forbidden native calls, and has no plaintext fallback. The primitive itself does not write files or modify TaskQueue.
- The CLI and bridge support a third, independent case-storage consent. When granted, only the DPAPI ciphertext envelope is placed in the existing TaskQueue payload; `case_context_attached` remains false and the task remains non-approvable.
- `load_encrypted_case_for_review()` now provides a narrow pre-approval read path: it accepts only a pending contextual-inquiry Ticket with the expected ciphertext flags, decrypts under the current Windows user, reconstructs and revalidates the full data contract, checks the Ticket identity and workflow contract, and returns an in-memory object with knowledge-memory consent reset to false. It never mutates the Ticket, marks context attached, approves, or dispatches it. The API is not yet registered as a Commander handler and is not an authorization boundary against other code running as the same user.
- The focused regression batch completed 137 tests successfully across contextual inquiry, ResearchKnowledgeStore, TaskQueue, and WorkflowProfile. Python compilation of the three changed runtime modules also passed.
- The latest full Muse suite completed successfully: 1,741 tests passed, 1 skipped, 0 failures, in 204.386 seconds. It ran on the Windows Runner after the evidence-resolution changes; no shared video-worker files were changed in this phase.
- Hypothesis export now resolves each local observation ID to its declared `source_ref`, then requires exactly one active `source` node with that exact ID or external reference. Missing, ambiguous, inactive, deleted, and non-source matches are skipped. Resolved references are recorded through the existing `record_claim_evidence()` API as unadjudicated support/counter search candidates; that API creates only `relates` edges. No `supports`/`refutes` edge or claim promotion is created by this bridge.

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
- Add a durable, audited context-attachment transition with compare-and-swap semantics. The current reader intentionally performs no mutation, and no caller may set `case_context_attached` through generic payload editing.
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

The DPAPI primitive, optional ciphertext-in-TaskQueue path, read-only pre-approval context loader, and exact source-to-evidence-candidate resolution are implemented. The 137-test focused batch and the full Muse suite (1,741 passed, 1 skipped) succeeded; the three changed runtime modules compile. No real case data has been stored during development; the loader does not attach context; no contextual-review Commander handler exists; and the placeholder remains intentionally non-approvable.
