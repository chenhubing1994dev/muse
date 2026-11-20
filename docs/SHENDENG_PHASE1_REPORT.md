# Shendeng-inspired independent Phase 1 implementation report

Updated: 2026-11-20

## Scope

This phase implements an original Muse-owned context inquiry contract based on the gap analysis in `SHENDENG_FUSION_ASSESSMENT.md`. It does not copy, translate, adapt, package, or distribute upstream Skill text or the upstream method entries. The upstream CC BY-NC 4.0 restriction remains unchanged; this implementation is independently authored and contains no upstream formula text.

## Files added

- `src/muse/contextual_inquiry.py`
- `tests/test_contextual_inquiry.py`

## Contract coverage

- Provenance-aware observations: user quote, observable fact, third-party statement, and system record.
- Competing hypotheses with source references, counter-evidence references, confidence bounds, explicit unsourced labeling, and falsification conditions.
- Stakeholder perspectives are either linked to an actual third-party statement or explicitly marked unknown.
- Safety routes distinguish normal, professional support, urgent support, and out-of-scope handling; non-normal routes cannot carry ordinary action experiments.
- Action experiments require an owner, ISO due date, resource constraints, observable expected signal, stop condition, and reversibility.
- Feedback can record adverse outcomes and contradicted hypotheses; the contract does not promote claims or change policy.
- Persistence is not implemented. A caller must pass an explicit consent gate before persisting the case.

## Architectural boundaries

This is a pure immutable data-contract layer. It has no database, long-term memory, network, model-provider, scheduler, Commander, or tool-execution dependency. It is not yet connected to `WorkflowContract`, ResearchKnowledgeStore, MemoryStore, or the UI. Those integrations remain Phase 2/3 work and must reuse the existing stores and Commander rather than introducing parallel infrastructure.

## Validation evidence

- Focused test module: 11 tests passed.
- Python compile checks: `src/muse/contextual_inquiry.py` and `tests/test_contextual_inquiry.py` compiled successfully.
- Execution route: the actual test/compile payload ran in a detached psmux window through `scripts/webcodex_psmux_exec.py`.
- No real user case, personal data, third-party code, external API, or paid provider was used.

## Review limitation and concurrent work

- Automated WebCodex workspace review failed twice with `workspace review metadata observation failed`; the bounded Git diff reader also failed before starting source execution. Therefore this report does not claim an independent automated review.
- The workspace has concurrent changes from other Muse tasks, including `AGENTS.md`, `docs/PROGRESS.md`, `docs/CAPABILITY_BACKLOG.md`, Scrapling-related files, and psmux files. These were not edited by this task.
- `docs/PROGRESS.md` was intentionally left untouched to avoid conflicting with active work.

## Next phase

1. Obtain an independent review when workspace metadata observation is healthy.
2. Add compatibility tests for an optional mapping into the existing `WorkflowContract`; do not change its schema until all callers are checked.
3. Add an explicit, user-approved handoff to ResearchKnowledgeStore for hypotheses and evidence references; do not persist personal case narratives by default.
4. Run focused integration and regression tests before enabling any UI or runtime flow.
