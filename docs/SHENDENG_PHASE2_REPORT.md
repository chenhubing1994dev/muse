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

- Focused regressions: 167 contextual-inquiry tests passed; 12 narration-prosody tests passed; 9 visual-evidence/OpenDesign tests passed; 42 status API tests passed; 55 frontend API tests passed; 22 video Worker tests and 1 video-product visual-review integration test passed; the loopback redirect suite passed 7 tests; Svelte check reported 0 errors/0 warnings and Vite production build passed.
- Targeted post-checkpoint-fix regressions passed: AutonomousResearch 61, AutonomousRuntime 27, InspirationEvolution 4, FeedbackExperiment 6 (including a nested 61-test run), Progress 13, and InitiativeStore 29. The latest clean full Python baseline completed **2,175 tests, 0 failures, 0 errors, 1 skipped**, and Python compileall reported `MUSE_PYTHON_COMPILEALL_OK`. Later shared runs were not clean during concurrent edits; the latest 2,038-test snapshot had 2 failures, 18 errors, and 1 skip, primarily transient `orchestration.py` splice/indentation errors and a Windows short-path/long-path mismatch in video attempt staging. The orchestration file was later read back with repaired syntax, but no clean full-suite rerun has been completed.
- The earlier clean shared-project full-suite snapshot remains a historical baseline: 1,752 passed, 1 skipped, 0 failures in 178.619 seconds.
- Tests cover DPAPI CurrentUser round-trip, ciphertext tamper rejection, strict envelope/version handling, canonical payload reconstruction, separate consent scopes, ciphertext-only TaskQueue persistence, idempotency mismatch rejection, read-only pre-approval loading, read-only human review-result retrieval, task identity binding, exact source resolution, ambiguous/missing/inactive/non-source reference rejection, the approval guard, rejection of forged attachment flags, rejection of generic attachment-event forgery, review-only authorization separation, stale task revision rejection, replay rejection, and reserved review-authorization event protection.

## Review-only lease primitives — 2026-11-21

- `TaskQueue.claim_contextual_inquiry_review_only()` adds a dedicated atomic transition from `waiting_approval` to `running` after checking the review-only authorization payload and matching reserved audit event. It creates a worker lease, increments attempts, records a bounded claim event, and never sets `_approved` or uses the generic `claim_next()` scheduler.
- `load_encrypted_case_for_review()` retains the pending-ticket read-only path and now also verifies the exact unexpired Worker lease owner before decrypting a running review-only Ticket. It checks the authorization event and revalidates the inquiry schema; long-term-memory consent remains false.
- The lease-fenced encrypted review-result completion transaction, WorkerExecutionContext ciphertext submission method, explicit run_contextual_inquiry_review_once() runner, and optional AutonomousSupervisor Commander hook now exist. The hook is inert by default and dispatches only when a trusted handler is explicitly injected and a matching review_only authorization audit is present.
- Focused regressions passed 136 tests across six modules. The shared-project full suite passed 2,080 tests with 1 skipped, and Python compileall passed.
- A concrete local-only review handler now exists in `src/muse/contextual_inquiry_local_review.py`. It is instantiated only with `autonomous_runtime run|once --enable-contextual-review`, requires an explicitly configured loopback LM Studio/llama.cpp endpoint and loaded model, bypasses system proxies, rejects non-normal safety routes, validates candidate-only findings, and returns DPAPI ciphertext. It is not enabled by default and has no cloud fallback.

## Review and concurrency limits

- WebCodex \`review_changes\` previously failed at workspace metadata observation. Files were read back and tests were run, but an independent automated code review is not claimed.
- Another peer was active in the same Muse project during this work. No concurrent video-worker files were modified by this integration.
- \`docs/PROGRESS.md\` was intentionally not modified while shared peer development was active. The dedicated engineering decision and this report contain the latest contextual-inquiry status.
- Shared-core changes in this track remain narrowly scoped: TaskQueue.approve() requires validated context flags plus the reserved trusted attachment audit event; TaskQueue.record_event() rejects both reserved contextual-inquiry security events; authorize_contextual_inquiry_review_only() records a separate review-only decision; complete_contextual_inquiry_review_only() is the lease-fenced encrypted result attachment transaction; load_contextual_inquiry_review_result() is the read-only human review reader; WorkerExecutionContext exposes the ciphertext submission method; run_contextual_inquiry_review_once() is the dedicated runner; AutonomousSupervisor exposes an optional Commander hook; and an explicit CLI flag controls local analysis only.
## Remaining integration work

1. Complete end-to-end Tauri desktop acceptance for the authenticated local human-review panel. Status API tests (89) and frontend API tests (55) pass; Svelte typecheck and production build pass.
2. Keep the generic Commander path unable to consume contextual-inquiry ciphertext; a missing or unregistered handler must fail closed. This remains the default behavior.
3. Add a separate explicit long-term retention flow for selected hypotheses; the CLI deliberately does not persist claims to long-term memory.
4. Add reviewed citation-anchor capture and human evidence adjudication before any support/refute verdict or claim promotion.
5. Route action feedback into the existing experiment/initiative review flow without automatically executing proposed experiments or promoting hypotheses.
6. Obtain independent automated code review when workspace metadata observation is available.

## Local-only review handler — 2026-11-21

- The local review model is configured through the existing `config/muse.toml` `[models]` section or the existing LM Studio/llama.cpp environment overrides. Only providers `lmstudio` and `llama_cpp`, a credential-free HTTP loopback `/v1` URL, and a model ID currently returned by `/models` are accepted.
- CLI activation is explicit: `autonomous_runtime once --enable-contextual-review` or `autonomous_runtime run --enable-contextual-review`. Without the flag, the Commander receives no review handler. There is no cloud fallback, API-key path, or proxy-mediated request for case data.
- The handler sends only a review-authorized inquiry to the local model, treats the entire case as untrusted data, rejects duplicate JSON keys and malformed output, checks references against the inquiry, rejects verdict promotion, and returns only a DPAPI ciphertext envelope. Professional/urgent/out-of-scope safety routes are not sent to the ordinary local reviewer.
- No live model call, actual case review, downstream approval, or long-term-memory persistence was performed during this implementation.


## Authenticated human review UI — 2026-11-21

- Added `GET /api/v1/contextual-inquiry-reviews?limit=...` as a metadata-only list (task ID and timestamps) and `GET /api/v1/contextual-inquiry-reviews/{task_id}` as a read-only adapter to `TaskQueue.load_contextual_inquiry_review_result()`.
- Both routes inherit the status API's loopback, ephemeral Bearer token, Origin checks, and `Cache-Control: no-store`. The detail route returns generic 404/409/503 errors and never exposes case ciphertext or raw inquiry data.
- Added a `个案审查` view to the existing Svelte dashboard. Results are fetched only after a user click, rendered as escaped text, and cleared when the panel closes or navigation leaves the view. No approval, claim adjudication, or long-term-memory write is exposed here.
- Validation passed: status API tests 42/42; frontend API tests 55/55; Svelte/TypeScript check 0 errors and 0 warnings; Vite production build succeeded. Tauri desktop acceptance remains pending.

## Loopback model redirect hardening — 2026-11-21

- `LMStudioClient(bypass_proxies=True)` now rejects HTTP redirects instead of following them, so a loopback model endpoint cannot forward a case prompt to an external redirect target.
- A local HTTP test server returns a 307 redirect to `example.invalid`; the client raises `ModelError` and the server observes exactly one request. All 7 tests in `test_conversations_and_model_client.py` passed.
- Human review display must use the read-only TaskQueue reader only from an authenticated local UI. Do not print results through psmux/stdout or persist plaintext; display does not change task state, and final approval remains a separate human action.
