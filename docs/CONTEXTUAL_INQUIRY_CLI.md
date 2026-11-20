# Muse Contextual Inquiry CLI

## Purpose and current scope

The local CLI is a consent-gated way to create a review placeholder in Muse's existing Commander TaskQueue. It is not yet an end-to-end case-review runner.

Run it from the Muse project root:

```powershell
py -3.11 scripts/muse_contextual_inquiry.py --input .\inquiry.json --database .\data\tasks\muse.sqlite3
```

The input is read locally and is not uploaded. The CLI rejects a `consent_to_store` field in the JSON; a data file cannot grant consent on the user's behalf.

## Minimal input shape

```json
{
  "inquiry_id": "inquiry-example-001",
  "user_goal": "Understand the situation without assuming motives.",
  "observations": [
    {
      "observation_id": "observation-001",
      "source_kind": "user_quote",
      "content": "A directly observed or quoted statement.",
      "source_ref": "local-reference-001",
      "observed_at": "2026-11-20"
    }
  ],
  "hypotheses": [
    {
      "hypothesis_id": "hypothesis-001",
      "statement": "A tentative explanation to test.",
      "source_refs": ["observation-001"],
      "counter_refs": [],
      "confidence": 0.35,
      "unsourced": false,
      "falsification_condition": "An independent source contradicts this explanation."
    }
  ],
  "unknowns": ["The other participant's perspective is not known."],
  "safety_route": "normal"
}
```

Supported `source_kind` values: `user_quote`, `observable_fact`, `third_party_statement`, `system_record`.

Supported `safety_route` values: `normal`, `professional_support`, `urgent_support`, `out_of_scope`.

Optional fields include `stakeholder_views`, `action_experiments`, and `schema_version`. The input is validated against the existing `ContextualInquiry` data contract.

## Three independent confirmations

1. **Create review task?** A yes creates one task in the existing TaskQueue with waiting_approval status. A no creates no database and performs no write.
2. **Store user goal in task record?** A yes stores only the exact user_goal string in the task's local SQLite workflow contract. A no keeps a generic goal. This is not execution approval or long-term knowledge-memory consent.
3. **Store complete case context encrypted?** A yes stores the complete validated inquiry only as a versioned Windows DPAPI CurrentUser ciphertext envelope in the same TaskQueue. This includes observations, hypotheses, unknowns, stakeholder statements and proposed experiments. Decryption requires the same Windows user profile. DPAPI is at-rest protection, not isolation from malware or other programs already running as that same user. A no stores no case envelope.

The task payload records:

- case_context_envelope_stored: true only after the third confirmation; otherwise false
- context_status: encrypted_pending_handler when ciphertext is present, otherwise awaiting_user_reentry
- case_context_attached: false
- requires_context_reentry: true
- long_term_storage_performed: false

The TaskQueue receives no plaintext observations, source references, hypothesis statements, stakeholder statements, unknowns, or action narratives. If the user separately consents to storing the goal, that one field remains plaintext in the task's workflow contract.

The task remains approval-gated and is not dispatched by this CLI. `load_encrypted_case_for_review()` can decrypt and revalidate a pending encrypted case for pre-approval inspection, but it does not attach context, change task state, approve, or dispatch. It is not yet registered as a Commander handler or an authorization boundary against other code running as the same Windows user. `TaskQueue.approve()` still rejects the placeholder while `case_context_attached` is false and `requires_context_reentry` is true. Do not treat this as a complete case-processing workflow.

## Privacy and retention boundary

The input JSON file remains wherever the user saved it. The CLI reads it but does not delete it or create a plaintext copy. If the user consents to goal storage, that goal remains in the local TaskQueue database as plaintext under the project's existing task retention controls. If the user separately consents to complete case storage, only DPAPI-protected ciphertext is added to the existing TaskQueue payload. Decryption requires the same Windows user profile; moving the database to another account or machine is not a supported recovery path. DPAPI does not isolate data from malicious programs running as the same Windows user. The CLI creates no second case database and makes no encryption claim for the original input file.

Long-term hypothesis retention is not performed by this CLI. The separate `persist_hypotheses` API requires explicit consent and stores proposed claims only. When an observation's `source_ref` exactly identifies one active, unique source node, it records an unadjudicated evidence candidate through the existing ResearchKnowledgeStore; missing, ambiguous, inactive, deleted, or non-source references remain unlinked. These candidates create only `relates` edges—not `supports` or `refutes` conclusions. The focused regression batch passed 137 tests; the full Muse suite passed 1,741 tests with 1 skipped.


## Upstream license boundary

This capability is independently authored. No Shendeng Skill text, formula, or method entry is copied, translated, packaged, or adapted. The upstream CC BY-NC 4.0 restriction remains applicable to any future direct reuse of upstream materials.
