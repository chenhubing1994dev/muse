# ARIS Fusion Research: Auto-Research-In-Sleep → Muse

Research date: 2026-11-03
Upstream: https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep
License declared by upstream: MIT

## Executive decision

Absorb ARIS as a research methodology and a set of workflow patterns, not as a second platform or wholesale runtime. Muse already has several substantial ARIS-like foundations: multi-angle evidence collection, typed User/Agent memory, independent review, bounded verification, skill candidate evaluation, causal Trace, operational watchdog and approval-gated improvement proposals.

The largest architectural gap is not another agent loop. It is a durable, queryable research knowledge graph that links sources, hypotheses, experiments, claims, decisions and failed approaches across tasks and projects, plus a safe meta-optimization process that learns from operational outcomes without silently changing runtime behavior.

This review used the upstream public README and its documented workflow/design sections. No upstream code was cloned, installed, executed or copied. Exact source-level ports require a pinned upstream commit and file-by-file review.

## ARIS patterns worth absorbing

1. **Research Wiki / compounding memory**
   - Typed entities: source/paper, idea/hypothesis, experiment/run, claim/decision.
   - Typed edges: supports, invalidates, inspired_by, contradicts, addresses_gap, supersedes, tested_by.
   - Failed ideas and negative experiments are first-class records, not discarded logs.
   - Before a new investigation, retrieve relevant prior claims, known gaps, failed approaches and unresolved questions; after each stage, append evidence-backed updates.
   - Add linting for orphan nodes, stale claims, contradictory status and missing provenance.
   - Preserve append-only history and explicit claim status: verified, refuted, unproven, superseded, disputed.

2. **Cross-model adversarial review**
   - Separate executor from reviewer; the reviewer inspects source evidence and raw diffs/results, not only the executor's narrative.
   - Keep the reviewer independent by provider/model family or at minimum by agent identity and isolated context.
   - Require structured findings, evidence references, severity and falsification questions.
   - A loop may establish that steps ran; it cannot certify correctness or quality by itself ("drive, never acquit").

3. **Idea discovery and research funnel**
   - Search from multiple angles, identify gaps, generate diverse hypotheses, rank by novelty/feasibility/cost/risk, then run cheap pilots before expensive work.
   - Freeze a problem statement and assumptions ledger before dependent implementation.
   - Record eliminated ideas and why they were eliminated to prevent repeated exploration.

4. **Experiment bridge**
   - Convert a research hypothesis into a reproducible experiment manifest: question, hypothesis, inputs, code/config revision, environment, budget, metrics, expected evidence and stop criteria.
   - Capture actual run artifacts and results; do not infer success from process exit alone.
   - Feed results back into claim status and future research.

5. **Meta-Optimize**
   - Analyze aggregate operational signals: repeated failures, manual corrections, overridden defaults, review regressions, retry churn and user feedback.
   - Generate minimal, evidence-backed proposals with expected impact, acceptance tests and rollback plan.
   - Independent review plus explicit human approval before changing skills, defaults, policies or code.
   - Compare before/after metrics; retain rollback and proposal history.

6. **Watchdog and iteration health**
   - Distinguish a live process from a productive process.
   - Monitor heartbeats, last meaningful progress, repeated empty iterations, changing evidence and blocked states.
   - Alert or request a direction change after bounded stagnation; never silently restart a verdict-bearing run or erase its state.
   - A watchdog observes and escalates; Commander remains the only authority that can schedule/retry work.

7. **Evidence integrity**
   - Preserve URL/query attribution, content hash, discovery/fetch time and source signals.
   - Distinguish source relevance from source credibility and corroboration from proof.
   - Every claim should point to evidence and every evidence record should retain provenance.
   - For academic citation workflows, resolve identifiers and verify bibliographic metadata against authoritative registries before treating citations as confirmed.

## Existing Muse implementation discovered

| ARIS pattern | Existing Muse module | Assessment |
|---|---|---|
| Multi-angle literature/search collection | `research_pipeline.py`, `research_fetch.py` | Already searches multiple angles, collects sources concurrently under resource limits, preserves URL/query attribution and hashes bounded extracts. No persistent typed paper/idea/experiment/claim graph found in this review. |
| Long-term memory | `memory.py` | Typed User/Agent tracks, provenance, revisions, candidate staging and approval. Suitable foundation for agent learnings; not a substitute for a research entity graph. |
| Independent reviewer | `review_gate.py` | Reviewer identity must differ from builder; findings are structured and high/critical findings veto approval. |
| Verify/fix/reverify | `verification.py`, `workflow_profiles.py` | Bounded attempts/runtime, explicit retryability and idempotency, persisted stage events. |
| Skillify | `skillify.py` | Successful evidence-backed run only; test cases required; pending until evaluation and explicit human approval; skills grant no tools. |
| Causal Trace | `trace.py` | 2–5 competing hypotheses, references to existing Ticket events, falsification checks and evidence-backed resolutions. |
| Operational watchdog | `watchdog.py`, `progress.py` | Worker heartbeat/liveness, expired lease and 15-minute task-stall checks, redacted reminders. No unattended verdict-bearing auto-restart. |
| Meta-optimization seed | `capability_radar.py`, `improvement.py` | Aggregates task/test/retry/feedback signals into deduplicated approval-gated proposals with acceptance criteria and rollback plans. It is currently rule/spec-driven, not ARIS-style general skill/default optimization from rich run traces. |
| Workflow durability | `workflow_profiles.py`, `task_queue.py` | Versioned profile and Ticket-backed lifecycle; no competing scheduler required. |

## Fusion decisions

### Reuse directly
- ResearchPipeline and ResearchEvidenceStore for public source discovery, bounded fetch and provenance.
- MemoryStore Agent Track for approved reusable agent knowledge and skill candidates.
- TaskQueue events for run/ticket lifecycle and evidence references.
- ReviewGate for independent adversarial review.
- WorkflowController + BoundedVerificationLoop for bounded verification.
- FailureTraceRecorder for competing root-cause hypotheses.
- CapabilityRadar + ImprovementStore for approval-gated improvement proposals.
- ProgressEventStore + Watchdog for health and stagnation signals.

### Build natively in Muse
1. **ResearchKnowledgeStore (highest priority):** SQLite-backed typed nodes/edges, stable IDs, provenance, versioned status and append-only event history. Link existing research evidence rows, attribution records, TaskQueue events, workflow IDs and experiment artifacts instead of copying their payloads.
2. **Research memory retrieval:** before a new research task, query related sources, claims, prior failures, unresolved hypotheses and superseded findings; produce a compact evidence pack for Commander.
3. **Claim lifecycle:** proposed → supported / refuted / unproven / disputed / superseded. Status transitions require evidence references and reviewer identity; a search-result score alone cannot promote a claim to supported.
4. **Experiment manifest and result bridge:** define reproducible, budgeted experiments and link results to claims. Start with software tests/benchmarks and local, reversible experiments; add GPU execution only through approved existing adapters.
5. **Research funnel:** multi-angle discovery → diverse hypotheses → novelty/feasibility/cost/risk screen → independent critique → cheap pilot → evidence-based ranking → approved implementation Ticket.
6. **Meta-Optimize v2:** enrich the current deterministic CapabilityRadar with anonymized aggregate signals from review findings, iteration outcomes, user correction tags and workflow overrides; proposals remain in ImprovementStore and require independent review + human approval.
7. **Stagnation detector:** extend Watchdog with bounded per-workflow progress deltas (new evidence, resolved claims, completed experiments, accepted tests), not merely process heartbeat. Alert/ask Commander to re-plan; never create a second scheduler.

## Integration architecture

`User goal / proactive research opportunity`
→ `Commander intake + policy + approval`
→ `Research memory retrieval`
→ `multi-angle search / local evidence`
→ `hypothesis generation and prioritization`
→ `independent cross-model critique`
→ `validated experiment manifest / Commander DAG`
→ `Ruflo + existing Worker/TaskQueue`
→ `verify / reviewer / bounded fix`
→ `ResearchKnowledgeStore claim and graph update`
→ `Skillify candidate / Meta-Optimize proposal`
→ `human approval where required`

Commander remains the only scheduler and retry authority. Research services store and retrieve knowledge; they do not dispatch tasks themselves. Agent Track remains separate from User Track. Research findings do not become personal facts. External content is untrusted data, not instructions.

## Implementation priority

### A0 — Research Wiki / knowledge graph
- Define schema/versioned entity and edge types.
- Store stable IDs, source refs, content hashes, provenance, timestamps and confidence/status.
- Add append-only node/edge events and deterministic idempotent upserts.
- Implement query, stats and lint for orphan/contradictory/stale/unproven claims.
- Link to ResearchEvidenceStore and TaskQueue by references, not duplicate content.
- Tests: migration, deduplication, referential integrity, status transitions, provenance, contradictory claims, deletion/tombstones and reopen/recovery.

### A1 — Research context pack + claim lifecycle
- Retrieve relevant prior work before ideation.
- Store both accepted and rejected ideas, with reasons and evidence.
- Require independent review and evidence for supported/refuted claim transitions.
- Never convert lexical relevance scores into credibility or truth.

### A1 — Meta-Optimize v2 and stagnation signals
- Extend current aggregate-based radar with privacy-safe outcome metrics.
- Detect empty/repeated iterations and lack of new evidence.
- Create proposals only; never auto-edit prompts, skills, code, policies or provider settings.
- Require independent reviewer, explicit user approval, tests, measurable before/after and rollback.

### A2 — Experiment bridge
- Add versioned manifest, budget, environment capture, reproducible output refs and claim linkage.
- Start with deterministic test/benchmark handlers and existing Worker approvals.
- No automatic paid GPU/cloud allocation, dependency installation or external side effects.

## Safeguards / non-goals

- No second scheduler, queue, task database or background daemon.
- No unlimited overnight loop. Every run needs deadline, budget, cancellation, stagnation threshold and human escalation.
- Watchdog never restarts or overwrites a verdict-bearing run.
- No automatic promotion of claims, memories, skills or improvement proposals.
- No raw prompts, credentials, private task payloads or personal User Track memories in global telemetry.
- External pages, papers, repositories and Agent outputs are untrusted input.
- No source code copied in this research pass. The upstream repository declares MIT; any future source port must pin a commit and preserve required notices.
- Do not claim actual autonomous overnight operation unless a persistent local Runner is installed, running and observed.

## Upstream references

- https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep
- https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep/blob/main/README.md
- Research Wiki design: README section "Research Wiki — Persistent Research Memory"
- Meta-Optimize design: README section "Workflow M: Meta-Optimize"
- Watchdog / iteration-health patterns: README changelog entries and tools/watchdog.py (review exact source before any port)
- Acceptance doctrine: shared-references/acceptance-gate.md (review exact source before any port)
