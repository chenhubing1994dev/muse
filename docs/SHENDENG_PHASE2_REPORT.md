# Shendeng-inspired contextual inquiry integration report

Updated: 2026-11-21

## Delivered

Added independently authored contextual-inquiry integration and consent-entry layers:

- \`src/muse/contextual_inquiry_bridge.py\`
- \`src/muse/contextual_inquiry_cli.py\`
- \`src/muse/contextual_inquiry_security.py\`
- \`scripts/muse_contextual_inquiry.py\`
- \`docs/CONTEXTUAL_INQUIRY_CLI.md\`
- \`docs/CONTEXTUAL_INQUIRY_SECURE_HANDOFF.md\`
- \`tests/test_contextual_inquiry_bridge.py\`
- \`tests/test_contextual_inquiry_cli.py\`
- \`tests/test_contextual_inquiry_security.py\`

No upstream Skill text or method entries were copied, translated, packaged, or adapted. The CC BY-NC 4.0 upstream boundary recorded in \`SHENDENG_FUSION_ASSESSMENT.md\` remains in force.

## Existing Commander integration

\`build_workflow_contract(inquiry)\` maps evidence-preservation requirements, exclusions, safety escalation and feedback expectations into the existing versioned \`WorkflowContract\`. The default goal is generic to avoid leaking personal case text into TaskQueue event logs; including the exact user goal requires \`include_user_goal=True\` and explicit storage consent. The function itself is an in-memory projection.

A tested integration path passes that contract to the existing \`WorkflowController.bind_plan(..., contract=...)\`. The test confirms the bound Ticket snapshot returns the exact serialized contract and advances the existing workflow revision. No second scheduler, ticket queue, or workflow schema was added.

\`enqueue_review_task(queue, inquiry)\` creates a single approval-gated task in the existing Commander \`TaskQueue\`:

- Uses a generic task title and stores only the existing serialized \`WorkflowContract\` by default.
- Exact user-goal storage requires a separate explicit consent; the consent check occurs before queue writes.
- Complete case storage requires a third, independent consent. When granted, only a versioned Windows DPAPI CurrentUser ciphertext envelope is stored in the existing TaskQueue payload.
- The encryption primitive is standard-library-only, uses UI-forbidden DPAPI calls and application-specific optional entropy, validates envelope shape/version/size, and fails closed. It does not create a second database or plaintext sidecar. DPAPI protects data at rest for the current Windows user profile; it does not isolate data from malicious programs already running as that user.
- Case-storage consent is independent of \`ContextualInquiry.consent_to_store\`, which remains reserved for explicit long-term knowledge-memory operations. The serializer removes that flag from the encrypted case document.
- Submission is idempotent. Reusing an inquiry ID with a different workflow contract, case-storage consent state, or decrypted case content is rejected rather than silently replacing or reusing the prior Ticket.
- The CLI input file cannot self-grant consent: \`consent_to_store\` is rejected as an unsupported input field and the model is initialized with consent false. Declining task creation creates no database. Declining goal storage leaves the task goal generic. Declining case storage omits the ciphertext envelope.
- The standard TaskQueue.approve() path rejects this placeholder while case_context_attached=false and requires_context_reentry=true. Even when encrypted context is present, the task remains in waiting_approval; the CLI does not attach, approve, or dispatch it.
- TaskQueue.authorize_contextual_inquiry_review_only() records a separate, human-declared review_only decision with task-version CAS and an atomic reserved audit event. It deliberately does not change waiting_approval, set _approved, attach context, or dispatch a worker. The actor string is audit metadata, not identity authentication.
- load_encrypted_case_for_review() is a narrow read-only pre-approval loader. It checks task kind/status and envelope flags, decrypts under the current Windows user, reconstructs and revalidates the full inquiry schema, verifies the task idempotency identity and workflow contract, and returns an in-memory inquiry with knowledge-memory consent reset to false. It never mutates or approves the Ticket.
- The loader is not yet registered as a Commander handler and is not an authorization boundary against other code running as the same Windows user. The generic Commander still has no contextual-inquiry case-review handler. This is not an end-to-end case-review workflow.
- The CLI is not wired into \`unified_runtime.py\` or the generic \`chatgpt_bridge.submit()\` path; the latter continues to store ordinary request text as a task goal and must not be used for sensitive case payloads.

Usage, input shape, consent scope, privacy boundaries, and current limitations are documented in \`docs/CONTEXTUAL_INQUIRY_CLI.md\`.

## Existing ResearchKnowledgeStore integration

\`persist_hypotheses(store, inquiry)\` is an explicit export helper, not an automatic runtime hook:

- Requires \`inquiry.consent_to_store=True\`; otherwise it raises before any write.
- Writes hypothesis statements as \`claim\` nodes in \`proposed\` status.
- Preserves confidence, unsourced status, falsification condition, and local observation-reference identifiers as metadata.
- Uses stable external references so repeated exports are idempotent.
- Does not copy raw observation content, source text, third-party statements, unknowns, or action narratives.
- Resolves each hypothesis observation ID through the inquiry's observation table, then requires the observation's \`source_ref\` to match exactly one active \`source\` node by ID or external reference.
- Missing, ambiguous, inactive, deleted, and non-source references remain unlinked; the bridge does not fabricate source nodes.
- Resolved references are recorded through the existing \`record_claim_evidence()\` API as unadjudicated support/counter search candidates. The existing store creates \`relates\` edges only; it does not create \`supports\`/\`refutes\` edges or promote claims.
- Any future evidence verdict must use the existing verified citation-anchor, quality/independence, review, and claim-transition lifecycle.

## Validation evidence

- The earlier focused regression batch across contextual inquiry, ResearchKnowledgeStore, TaskQueue, and WorkflowProfile completed **137 tests, 0 failures**. After review-only authorization, the latest psmux-backed focused batch completed **68 tests, 0 failures**: TaskQueue 38, bridge 17, CLI 5, security 8.
- Python 3.11 compilation of the changed src/muse/task_queue.py and tests/test_task_queue.py passed.
- The latest full Muse suite completed **1,752 passed, 1 skipped, 0 failures** in 178.619 seconds through the psmux-backed project command path. An earlier attempt overlapped with concurrent shared status/video edits and failed to import several tests; the clean rerun after those edits settled passed.
- Tests cover DPAPI CurrentUser round-trip, ciphertext tamper rejection, strict envelope/version handling, canonical payload reconstruction, separate consent scopes, ciphertext-only TaskQueue persistence, idempotency mismatch rejection, read-only pre-approval loading, task identity binding, exact source resolution, ambiguous/missing/inactive/non-source reference rejection, the approval guard, rejection of forged attachment flags, rejection of generic attachment-event forgery, review-only authorization separation, stale task revision rejection, replay rejection, and reserved review-authorization event protection.

## Review-only lease primitives — 2026-11-21

- `TaskQueue.claim_contextual_inquiry_review_only()` adds a dedicated atomic transition from `waiting_approval` to `running` after checking the review-only authorization payload and matching reserved audit event. It creates a worker lease, increments attempts, records a bounded claim event, and never sets `_approved` or uses the generic `claim_next()` scheduler.
- `load_encrypted_case_for_review()` retains the pending-ticket read-only path and now also verifies the exact unexpired Worker lease owner before decrypting a running review-only Ticket. It checks the authorization event and revalidates the inquiry schema; long-term-memory consent remains false.
- The queue/reader primitives do not constitute an end-to-end review workflow. There is still no registered Commander handler, review-model call, result-attachment transaction, or downstream approval.
- Latest focused psmux regression: **71 tests passed, 0 failures**. Python compilation passed.
- The latest full-suite attempt ran **1,807 tests, 1 skipped, 1 error**. The error is in the concurrently developed `test_video_prompt_composer.VideoPromptComposerTests.test_rejects_overlong_compiled_clip_prompt`: it constructs `creative_intent="目标" * 1200` (2,400 characters) before the expected-exception block, but `VideoPromptSpec` caps the field at 1,200. This track did not edit the video prompt files.

## Review and concurrency limits

- WebCodex \`review_changes\` previously failed at workspace metadata observation. Files were read back and tests were run, but an independent automated code review is not claimed.
- Another peer was active in the same Muse project during this work. No concurrent video-worker files were modified by this integration.
- \`docs/PROGRESS.md\` was intentionally not modified while shared peer development was active. The dedicated engineering decision and this report contain the latest contextual-inquiry status.
- Shared-core changes in this track remain narrowly scoped: TaskQueue.approve() requires validated context flags plus the reserved trusted attachment audit event; TaskQueue.record_event() rejects both reserved contextual-inquiry security events; and authorize_contextual_inquiry_review_only() writes a separate review-only decision without approving or dispatching. The trusted lease-fenced attachment writer and review handler remain future work; other changes are isolated bridge/security/CLI/launcher/tests and dedicated documentation.
## Remaining integration work

1. Register a dedicated contextual-review handler only in the existing Commander/worker path. It must use the narrow reader and explicitly attach validated context through a durable, audited compare-and-swap transition before approval can proceed.
2. Keep the generic Commander path unable to consume contextual-inquiry ciphertext; a missing or unregistered handler must fail closed.
3. Add a separate explicit long-term retention flow for selected hypotheses; the CLI deliberately does not persist claims to long-term memory.
4. Add reviewed citation-anchor capture and human evidence adjudication before any support/refute verdict or claim promotion.
5. Route action feedback into the existing experiment/initiative review flow without automatically executing proposed experiments or promoting hypotheses.
6. Obtain independent automated code review when workspace metadata observation is available.
