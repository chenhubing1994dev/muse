# Muse ValueForge

## Purpose

ValueForge turns reusable workflow patterns into ranked, reviewable hypotheses that can become Muse initiatives. It is a discovery and evaluation layer, not a scheduler and not a second task or business ledger.

## First pattern set

- **Passive watchtower** — monitor explicitly selected sources and alert on meaningful changes.
- **Personal context to action** — turn user-authorized documents and saved information into traceable drafts and tasks.
- **Paperwork and negotiation** — compare bills or terms and prepare evidence-linked drafts for user review.
- **Workflow to micro-app** — convert a repeatable manual process into a small local tool.
- **Research to video** — turn reviewed research into a sourced script, Cantonese narration, video preview and QA report.
- **Demand to business pilot** — validate a real customer problem and willingness to pay before building.
- **Capability gap to experiment** — convert recurring failure signals into bounded research and regression tests.

The patterns are independently phrased abstractions. The upstream inspiration index is https://github.com/jaxxchen003/muse-use-cases. Muse does not bulk-import or redistribute its case descriptions. The repository distinguishes its software license from its case-content license; check upstream license files before reusing case text or commercially redistributing it.

## Explainable scoring

Each dimension is rated from 1 to 5:

| Dimension | Weight | Meaning |
| --- | ---: | --- |
| Impact | 25 | Potential user/business impact |
| Recurrence | 15 | How often the problem occurs |
| Capability fit | 20 | Fit with existing Muse capabilities |
| Evidence quality | 15 | Maturity of supporting evidence |
| Feasibility | 25 | Cost, complexity and delivery likelihood |
| Risk | penalty | Risk subtracts up to 15 points |

The weighted base is reduced by a linear risk penalty. The score is a heuristic ranking, never a probability, revenue forecast or proof of demand. Every proposal remains hypothesis_only and requires validation even when its evidence-quality rating is high. Attached references are traceability pointers, not automatically verified evidence.

## Existing Muse integration
Every completed autonomous research report is evaluated by `ValueForge.evaluate_research_report`. It ranks the research-to-video path and, when experiments or counter-evidence exist, a capability-gap experiment path. Evidence quantity is based on distinct, syntactically valid HTTP(S) source URLs; malformed records and duplicate URLs cannot inflate the score. A single cycle never establishes recurrence or demand, and source/report pointers are not independently verified evidence. Results are included in the report as `value_forge` and summarized in the existing creator brief as reviewable value opportunities; this evaluation does not create a second initiative or add a dispatch.

`scan_capabilities()` also attaches `value_forge` to its existing result. The adapter scores only existing `created`/`existing` Radar proposals and uses `signal_key` to join them to redacted signal aggregates. Recurrence is raised only when the same signal is present in both non-overlapping windows; without a prior comparable window it remains at the minimum. The score is explicitly operational, not market demand, customer willingness to pay, or causal proof. The adapter is read-only and does not create, update, approve, promote, or dispatch proposals.

- Non-commercial/productivity ideas are proposed through the existing InitiativeStore, with autonomy_level="draft".
- Commercial opportunity promotion delegates to BusinessOpportunityStore.promote_to_initiative; its append-only evidence ledger remains authoritative.
- Research already creates creator initiatives in autonomous_research.py; ValueForge is an additive pattern/scoring layer and does not replace that path.
- Muse Commander remains the sole scheduler and approval authority. ValueForge never dispatches work, starts GPU generation, sends messages, purchases, publishes or performs external transactions.

## CLI

- muse-value patterns
- muse-value evaluate --pattern passive_watchtower --ratings-json '{"impact":4,"recurrence":3,"capability_fit":5,"evidence_quality":2,"feasibility":4,"risk":2}'
- muse-value propose --pattern research_to_video --ratings-json '{"impact":4,"recurrence":3,"capability_fit":5,"evidence_quality":2,"feasibility":4,"risk":2}' --initiative-database data/initiatives/muse.sqlite3

Use an explicitly selected initiative database path for propose. Review the generated draft before downstream work.
