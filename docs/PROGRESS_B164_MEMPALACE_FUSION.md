# B164 MemPalace selective fusion — implementation report

Date: 2026-11-19

## Current outcome

- MP-M1 adapter foundation: complete.
- MP-M2 pinned local installation, D-drive model cache and live stdio MCP qualification: complete on the CPU inference path.
- MP-M3 optional read-only retrieval federation: implemented and end-to-end tested; remains disabled by default.
- MP-M4 Agent Track projection outbox foundation: implemented and synthetic live-tested; no production memory has been projected. MP-M5 lifecycle UI and production enablement: not complete.

## Implemented

- Official upstream research: MemPalace/mempalace, MIT; checked official README, Palace/KG/MCP docs, changelog, PyPI release metadata and provenance.
- Added src/muse/mempalace_adapter.py using Muse's existing bounded MCPStdioClient, fixed argv and a strict child-environment allowlist.
- Added an explicit [mempalace] section in config/muse.toml. The integration is disabled by default; enabling it requires local paths to an already-installed runtime, workspace, palace, model cache and profile directory.
- Added optional MuseRuntime.search_memories federation. MemPalace results are returned separately as unverified_retrieval_candidate records with drawer/source metadata. Muse MemoryStore remains canonical; no score merging, automatic checkpoint or dual-write occurs.
- The only exposed MemPalace write operation is mempalace_checkpoint, tagged added_by=muse-agent-track. MemPalaceProjectionOutbox accepts only Agent Track skill/case/foresight records with confidence >=0.75. Delivery requires a persisted Commander approval Ticket bound to the exact outbox ID/idempotency key and a matching human.decision=approved event; caller-supplied approver labels alone are not authorization. Source revisions are rechecked. Checkpoint acknowledgements require exactly one added record or an exact-content duplicate, with no reported errors. User Track writes remain blocked until a separate palace and separately scoped retrieval path exist.
- No raw transcript mining, deletion, bulk deletion, sync-apply, hooks or remote administration are exposed.

## Local installation and live qualification

- Installed pinned mempalace==3.12.0 in D:\AI AGENT\MuseRuntime\MemPalace-3.12.0\venv; Muse's existing .venv was not modified.
- PyPI wheel SHA-256: 3832534f377f3a81a324f741f90bf3c1ec277d49404608257cde1b5b0d52626f. The downloaded wheel, PyPI metadata and provenance statement agree; the provenance certificate identity names the official MemPalace/mempalace publish workflow at tag v3.12.0.
- Downloaded/cached multilingual EmbeddingGemma on D: (about 330 MB). Offline search passed with HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1.
- Local stdio MCP discovered 47 upstream tools; all 21 tools in Muse's allowlist were present. Status, taxonomy and semantic search calls succeeded.
- MuseRuntime-to-MemPalace end-to-end recall succeeded. A synthetic Agent Track record also completed the real local outbox -> MCP checkpoint -> semantic search path in a separate evaluation palace. No MemPalace HTTP daemon or remote hub was configured; production palace remains empty and uninitialized (status reports missing chroma.sqlite3).
- SQLite PRAGMA integrity_check returned ok. The read-only audit command completed; its 50/100 score was from the intentionally minimal synthetic evaluation palace (one generic room, no KG/tunnels), not the empty production palace.
- ONNX Runtime exposed CPU/Azure providers only; DirectML was not active. The configured inference device is therefore CPU, not the AMD GPU.

## Retrieval smoke evaluation

- Used only synthetic, non-personal evaluation text.
- Five queries covered Cantonese, Chinese temporal recall, English temporal recall, Chinese provenance and English provenance.
- Recall@5: 5/5. Top-1: 3/5. This is a small smoke set, not a statistically meaningful production benchmark.
- Production palace remains empty and uninitialized. No personal conversation, User Track or private file was transferred.

## Tests and verification

- tests/test_mempalace_adapter.py: 16 passed.
- tests/test_mempalace_projection.py: 11 passed.
- tests/test_unified_runtime.py: 46 passed.
- Combined targeted tests: 79 passed (adapter 16, projection 11, acknowledgement contract 6, UnifiedRuntime 46); py_compile passed.
- Live MuseRuntime federation test returned MemPalace candidates while preserving canonical_store=Muse MemoryStore and write_performed=false.
- Latest filtered full regression: 1,475 passed, 8 skipped; one NumPy-dependent video-motion test was excluded because NumPy is not installed in the project environment.
- The latest unfiltered attempt ran 1,463 tests and exposed two errors: a malformed line break in tools/scene_annotation_server.py (fixed and its 5 tests now pass), and the single NumPy-dependent test noted above. The filtered run then passed.

## Not done

- No production memory projection, User Track transfer, automated reconciliation/background sync, backup/restore or index rebuild. The Commander Ticket path is tested, but production UI approval workflow and durable operator identity controls still need integration.
- No UI health/freshness indicator, automatic service startup or background synchronization.
- No formal large-scale Chinese/Cantonese benchmark.
- No DirectML acceleration claim; CPU inference only until provider compatibility is independently resolved.

## Next

MP-M4 next: add backup/restore and index rebuild tests, validate crash-after-remote-acceptance recovery against exact idempotent content, and expose outbox/approval state in the UI. Keep User Track in Muse's canonical store until a separate palace and retrieval boundary are implemented.
