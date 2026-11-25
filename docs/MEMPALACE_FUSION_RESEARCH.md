# MemPalace selective fusion research

Date: 2026-11-19

## Decision

Adopt MemPalace as an optional, local-only semantic recall and associative-memory layer behind a Muse adapter. Do not replace Muse's canonical stores and do not create a second Commander, TaskQueue, approval ledger, or Research Wiki.

Official upstream: https://github.com/MemPalace/mempalace

Official documentation: https://mempalaceofficial.com/
License reported by upstream: MIT.

## What is worth reusing

- Palace hierarchy: wings (people/projects), rooms (topics), halls (memory categories), drawers (verbatim source text), and cross-wing tunnels. This provides predictable scoping for retrieval.
- Local semantic search over verbatim drawers, with project/wing/room and date filters.
- Temporal entity graph: subject/predicate/object facts with validity windows, historical queries, invalidation, and supersession.
- Per-agent diaries and checkpoint writes for curated work-session summaries.
- MCP interface and pluggable vector backends.

The hierarchy is useful organization and metadata filtering; it is not a novel retrieval algorithm by itself. Upstream's public benchmark claims are retrieval-recall metrics, not end-to-end answer accuracy. The upstream history records retractions/corrections to earlier benchmark comparisons and AAAK compression claims. Muse must run its own Chinese/Cantonese benchmark before adopting a performance claim.

## Muse ownership boundaries

| Concern | Canonical owner | MemPalace role |
|---|---|---|
| User Track facts/preferences, approvals, revisions, soft deletion | Muse MemoryStore (SQLite) | Optional recall index only; no automatic export |
| Agent Track episodes/lessons | Muse MemoryStore + ProgressEventStore | Curated verbatim drawer and agent diary mirror |
| Claims, evidence, counter-evidence, decisions, failures | Muse Research Wiki | Associative/temporal navigation only; Research Wiki remains authoritative |
| Tasks, approvals, retries, execution and Commander | Muse TaskQueue / Commander | No scheduling or ticket ownership |
| Human-readable long-term notes | Obsidian Vault | Existing one-way SQLite-to-Markdown mirror remains |
| Runtime agent coordination | Muse Commander / AgentRegistry | Diary/context only; no second orchestrator |

## Integration guardrails

1. Keep the integration disabled unless an operator explicitly configures an already-installed local MemPalace MCP executable. The adapter never installs packages, downloads models, starts services at boot, or configures a remote hub.
2. Use Muse's existing bounded MCPStdioClient, fixed argv, shell=False, scrubbed child environment, timeout and output-size limits.
3. Expose a small read allowlist (status, taxonomy, semantic search, KG reads, traversal, diary read) plus only mempalace_checkpoint as a write. Do not expose mine, delete, bulk-delete, sync-apply, hook-settings, arbitrary KG writes, or remote/hub administration.
4. Every Agent Track checkpoint requires per-call approved=True. User Track is currently blocked even if the consent flag is set, until it has a separate palace and separately scoped retrieval path. No raw user transcripts are auto-mined. Credential-like text is rejected.
5. Preserve provenance on every imported record: Muse record/node/event ID, source kind, source hash, source timestamp, confidence and track. MemPalace search results are retrieval candidates, never policy or factual authority.
6. Retrieval is additive and sidecar-only: MuseRuntime.search_memories returns MemPalace candidates in a separate field with source metadata; it does not merge scores or promote them into canonical memories. Muse claim/evidence/review logic remains the authority. Source-identity deduplication is a later phase.
7. No dual-write authority. Muse commits first; MemPalace is a derived, rebuildable index. Failed mirror writes must not roll back or corrupt canonical Muse state.
8. Backups and restore tests must include the MemPalace palace directory separately from Muse SQLite and Obsidian. Do not merge or migrate databases in place.

## Important upstream facts and caveats

- Upstream README describes local-first verbatim storage, ChromaDB by default, alternative exact SQLite/native vector backends, a temporal SQLite knowledge graph, MCP tools and agent diaries.
- Upstream docs recommend multilingual EmbeddingGemma for 100+ languages; MiniLM is English-only. Muse selected EmbeddingGemma and completed a small synthetic Chinese/Cantonese/English smoke test; a larger fixed benchmark is still required.
- Upstream README currently reports 96.6% raw R@5 and 98.4% held-out hybrid R@5 on LongMemEval; these are upstream claims, not Muse results. AAAK is lossy and upstream reports lower raw recall in AAAK mode.
- Live release verification on 2026-11-19: PyPI publishes 3.12.0 (wheel uploaded 2026-11-12). The downloaded universal wheel SHA-256 `3832534f377f3a81a324f741f90bf3c1ec277d49404608257cde1b5b0d52626f` matches the PyPI metadata and provenance statement; the provenance certificate identity names the official `MemPalace/mempalace` `publish.yml` workflow at tag `v3.12.0`. The install is pinned to this exact release, not a floating latest.
- Upstream history records a prior shell-injection concern in hooks and later hardening work. Muse should not install or enable upstream hooks in the first integration phase. Only use the official GitHub repository, PyPI, and mempalaceofficial.com; avoid lookalike domains.

## Phased implementation

### MP-M1 — adapter foundation (this change)
- Add a local stdio transport reusing Muse's existing MCP client.
- Add strict read-only semantic recall, temporal KG read and diary read methods.
- Allow only one explicit Agent Track checkpoint write, gated by a persisted Commander approval Ticket and matching human decision; User Track writes remain blocked until a separate palace and retrieval scope exist.
- Unit-test allowlists, approval gates, payload bounds and fixed argv.

### MP-M2 — local package/runtime qualification (completed; CPU inference path)
- Installed pinned `mempalace==3.12.0` into `<MUSE_ROOT>Runtime\\MemPalace-3.12.0\\venv`; Muse's existing `.venv` was not modified.
- Cached multilingual EmbeddingGemma on D: (about 330 MB); the live MCP search also passed with `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`.
- Live local stdio MCP smoke test found 47 upstream tools; every one of Muse's 21 allowlisted tools was present. Status, taxonomy and semantic search calls succeeded.
- SQLite `PRAGMA integrity_check` returned `ok`. The read-only audit command ran; its 50/100 score belongs to the intentionally minimal synthetic evaluation palace (one generic room, no KG/tunnels), not the empty production palace.
- ONNX Runtime exposed only CPU/Azure providers; DirectML was not active. Muse is explicitly configured for CPU rather than claiming AMD GPU acceleration.
- No MemPalace HTTP daemon or remote hub was configured. Production palace remains empty; no personal conversations or User Track data were transferred.

### MP-M3 — read-only retrieval federation (implemented; opt-in)
- Added an explicitly disabled-by-default `[mempalace]` config loader. It passes only bounded local paths/offline model settings to Muse's existing MCPStdioClient and uses fixed argv.
- `MuseRuntime.search_memories` now returns MemPalace results in a separate `mempalace` sidecar field, tagged `unverified_retrieval_candidate` with drawer/source metadata. Muse MemoryStore remains canonical; Ruflo and local retrieval remain independent fallbacks; no cross-store score merge or automatic write occurs.
- Live MuseRuntime-to-MemPalace end-to-end search succeeded. Five synthetic Cantonese/Chinese/English/temporal/provenance queries achieved Recall@5 5/5 and Top-1 3/5. This is a small smoke set, not a production benchmark.
- Formal larger recall benchmark, source-identity deduplication, UI health/freshness and production enablement remain outstanding.

### MP-M4 — curated memory projection (outbox foundation implemented; no production transfer)
- Added MemPalaceProjectionOutbox and MuseRuntime queue/list/deliver methods. The outbox is idempotent per memory_id/revision, stores a SHA-256 provenance envelope and deterministic idempotency key, and rechecks the source revision. Delivery now requires a persisted Commander approval Ticket bound to that exact outbox/key and a matching human.decision=approved event.
- Only Agent Track skill/case/foresight records at confidence >=0.75 are eligible. Credential-like content/source metadata is rejected; failed deliveries are recorded and can be retried explicitly. There is no background sync loop.
- A synthetic Agent Track record completed the real local outbox -> MCP checkpoint -> semantic search path in a separate evaluation palace. The production palace remains empty and uninitialized (read-only status reports missing chroma.sqlite3); no real Muse record was projected.
- User Track projection remains blocked even when user_data_authorized=True; it requires a separate palace and separately scoped retrieval path before any user data can be written.
- Checkpoint acknowledgements are parsed: exactly one added record or exact-content duplicate and no errors are required before marking delivered. A real local synthetic checkpoint replay returned the same drawer ID on the exact duplicate. A fault-injected crash after remote acceptance but before local commit now verifies restart + exact-duplicate recovery. Backup/restore helpers use a SHA-256 manifest, explicit quiesced confirmation, no-overwrite restore and staging verification. A separate synthetic local Palace completed backup → restore → upstream `repair rebuild-index` → semantic-search verification with offline EmbeddingGemma; production Palace was not touched. See `docs/PROGRESS_B186_MEMPALACE_REBUILD_SMOKE.md`. Production UI integration, durable operator identity, larger multilingual benchmark and production lifecycle acceptance remain required before production projection.

### MP-M5 — lifecycle and UX (not performed)
- Show connection/health/index freshness and last successful sync in Muse UI.
- Add pause/resume, bounded sync, failure reporting and user-visible source inspection.
- No auto-start/service installation without separate approval.

## Acceptance criteria

- Adapter tests pass without installing MemPalace or downloading any model.
- Unapproved writes, all current User Track writes, unknown MCP tools, deletion tools and raw transcript mining are rejected.
- Muse continues to function when MemPalace is absent or offline.
- No canonical Muse database is migrated, overwritten, or made dependent on MemPalace.
- Windows local MCP and small Chinese/Cantonese smoke tests have passed; a larger fixed benchmark and production data-lifecycle acceptance are still required before enabling the integration.
