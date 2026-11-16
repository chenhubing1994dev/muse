# OMC Fusion Research: oh-my-claudecode → Muse

Research date: 2026-11-03
Upstream: https://github.com/Yeachan-Heo/oh-my-claudecode
Website: https://oh-my-claudecode.dev/
License reported by upstream repository: MIT

## Executive decision

Absorb OMC's orchestration patterns, not its Claude Code plugin runtime. OMC is built around Claude Code hooks, slash skills, agent prompts, CLI/tmux workers and its own session state. Muse is a local-first Python/Rust/Svelte system with a single Commander, SQLite TaskQueue, resource-aware DAG execution, Ruflo adapter, approval ledger and structured progress events. Copying OMC wholesale would introduce a second control plane and a runtime dependency that Muse does not need.

No OMC source code, package, dependency or executable was copied or installed during this research. This is an architecture-level review of the public repository and its architecture documentation. Any future source-level port must pin a commit, review the exact files and preserve the MIT license/notice.

## What OMC does well

1. Team-first staged execution: team-plan → team-prd → team-exec → team-verify → team-fix.
2. Persistent completion discipline: execute/verify/fix loops, explicit stop/cancel and state recovery.
3. Consensus planning: Planner, Architect and Critic challenge a plan before execution.
4. Layered skills: one primary execution workflow, zero-or-more enhancements, optional completion guarantee.
5. Specialized role catalog and model/role routing.
6. Evidence-driven tracing: competing causal hypotheses are checked against observations.
7. Skillify/Learner: extract reusable problem-solving patterns from completed work.
8. Hooks and session checkpoints: preserve active mode, TODOs, task anchors and state across context compaction.
9. HUD and replay artifacts: make agent activity, decisions, costs and failures observable.
10. Provider advisor flow: collect independent provider opinions and synthesize them under one lead.

## Muse mapping: reuse before rewrite

| OMC pattern | Existing Muse foundation | Fusion decision |
|---|---|---|
| Team pipeline | CommanderPlanner, MultiAgentOrchestrator.run_dag, SpecialistRouter, Ruflo adapter | Extend the existing Commander DAG; do not create an OMC scheduler or another queue |
| Plan/Architect/Critic consensus | LocalModelCommanderPlanner, specialist profiles, AgentRegistry | Add a pre-dispatch plan critique stage with independent identity and bounded revision; never let the planner approve itself |
| Ralph / Execute / Verify | TaskQueue leases, Worker fencing/recovery, IndependentReviewGate, failure taxonomy | Add bounded verify→fix attempts as TaskQueue-linked workflow state; never an unbounded loop or hidden retry |
| Autopilot | MuseRuntime, approved Ticket dispatch, autonomy proposals | Keep Commander as the only loop/scheduling authority; model plans remain proposals until policy and approval checks pass |
| Agent/model routing | SpecialistRouter, AgentRegistry effectiveness, FreeOnlyRouter, ProviderBudget | Expand role/model compatibility and cost/latency evidence; free-only and quota fail-closed policy remains authoritative |
| Skillify / Learner | MemoryStore typed records, Agent Track, memory extraction/reflection | Add evaluated Skill draft → test/eval → approval → activation; never auto-promote a model-generated skill |
| Trace | failure taxonomy, ProgressEventStore, research evidence | Add competing hypotheses with evidence links and falsification checks; do not treat model confidence as proof |
| Hooks/checkpoints | ProgressEventStore, task events, handoff, routines | Use typed domain events and durable checkpoints; avoid Claude-specific hook scripts |
| HUD/replay | Tauri/Svelte console, Status API, progress events | Build real Agent/DAG/timeline/cost views from existing data; no mock telemetry presented as live |
| Provider advisor | ProviderAdapter, Ruflo, multi-agent orchestration | Independent review/advice fan-out with bounded output and one Commander synthesis |

## Direct code-port assessment

- **Do not vendor the OMC plugin/CLI wholesale.** Its core entrypoints are Claude Code-specific hooks, slash commands, prompt/skill files and CLI/tmux process management. These are not drop-in Python modules for Muse.
- **Port small algorithms only after targeted source review.** Candidate areas are structured stage transitions, checkpoint serialization/recovery, skill extraction/evaluation and causal-trace report schemas. Reimplement against Muse's SQLite/event interfaces where the upstream implementation is coupled to Claude APIs.
- **Adapt prompts, not runtime assumptions.** OMC role/skill instructions can inform Muse role templates, but every template must declare inputs, outputs, allowed tools, trust tier, timeout, cost budget and acceptance criteria.
- **Reuse Muse's stronger local primitives.** TaskQueue/Worker already provide idempotency, leases, fencing, approvals and recovery. Replacing them with OMC session files or tmux worker state would regress durability and Windows integration.
- **License.** The repository declares MIT. If a future exact source file is copied, preserve its copyright/license notice and record upstream commit, file path, modifications and tests in the fusion ledger.

## Proposed native Muse architecture

`User goal`
→ `Muse Commander intake + policy/approval`
→ `Plan consensus (Planner + Architect + independent Critic)`
→ `validated Commander DAG / Ticket links`
→ `Ruflo role selection + resource-aware MultiAgentOrchestrator`
→ `Worker / registered handlers`
→ `evidence verification + independent Reviewer`
→ `bounded fix/reverify attempts`
→ `human approval where required`
→ `ProgressEventStore + Agent Track Skill candidate + HUD`

The Commander owns the only durable workflow state and loop authority. Ruflo chooses/coordinates agents only inside the approved plan. Worker executes registered handlers only. Reviewer cannot be the builder or proposal author. Skills and provider suggestions never grant tool permissions.

## Implementation sequence

### P0 — OMC-inspired workflow contract
- Define versioned workflow profiles that compose a primary execution path, optional enhancements and a bounded verification policy.
- Map profiles to existing Commander DAG + TaskQueue IDs; do not add a second task store.
- Persist stage, attempt, checkpoint, evidence references, stop reason and next action as structured events.
- Support pause, cancel, resume and stale-lease fencing.

### P1 — Plan consensus
- Add Planner → Architect → Critic review before dispatch.
- Require distinct agent identities for proposer and critic; record findings and the revised plan hash.
- Bound critique rounds (default one revision); if consensus is not reached, return a blocked plan for human decision.
- Validate every revised DAG through CommanderPlanner before any Ticket is claimed.

### P1 — Verify/fix loop
- Reuse IndependentReviewGate and failure taxonomy.
- Only retry explicitly retryable, idempotent steps, with a small configurable attempt ceiling, total deadline and resource budget.
- Each fix is a new Assignment/attempt linked to the same authorized Ticket; Mandate and approval scope cannot widen.
- Stop on authorization, privacy, security, provider quota, unknown outcome or non-idempotent side effects.

### P1 — Skillify with evaluation
- Extract candidate skills only from completed, evidence-backed runs.
- Store candidate in Agent Track as kind=skill with source, confidence, version and test cases.
- Run regression/evaluation examples; independent Reviewer plus user approval required before activation.
- Skill matching may suggest context but cannot grant tools or bypass role policy.

### P2 — Trace and HUD
- Add evidence-linked competing hypotheses for difficult failures.
- Add timeline views for DAG waves, Agent assignments, review verdicts, resource snapshots and provider usage.
- Keep prompts, private payloads, credentials and raw sensitive outputs out of default telemetry.

## Non-goals / safeguards

- No second scheduler, queue, ticket database or independent Ralph loop.
- No unbounded "never stop" execution. Every loop has attempt, time, cost, resource and cancellation ceilings.
- No paid fallback. Unknown or stale free quota means no cloud dispatch.
- No auto-install or auto-execution of upstream skills/code. Pin revision, inspect license/dependencies/security, isolate, test and obtain approval.
- No claim of real multi-model execution when providers are offline or credentials are absent.
- No service installation, startup registration or background deployment as part of this fusion without separate approval.

## Sources

- https://github.com/Yeachan-Heo/oh-my-claudecode
- https://github.com/Yeachan-Heo/oh-my-claudecode/blob/main/docs/ARCHITECTURE.md
- https://oh-my-claudecode.dev/
