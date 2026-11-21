# Muse Video Stage Checkpoint Design

**Status:** Store, lease-aware history verification, persistent per-Attempt/per-epoch staging, Builder shot callbacks, and Worker cross-epoch resume are integrated for non-narrated multi-scene Attempts with a persisted recipe SHA-256. Execution remains preview-only pending Commander capability/role admission and real-handler authorization.

## Goal

Resume a long video-production DAG from the last verified stage while keeping `InitiativeStore` as the only durable initiative/video-attempt ledger and Muse Commander as the only scheduler and approval authority.

## Existing invariants to preserve

- `initiative_video_attempts` remains the parent lifecycle record (`queued`, `running`, `succeeded`, `failed`, `cancelled`).
- `initiative_video_attempt_events` remains the append-only lifecycle audit stream. Do not add fake `started` events for individual stages or overload its constrained `event_type` values.
- Worker leases remain authoritative. Only the current unexpired owner of a running Attempt may write a stage checkpoint.
- Explicit retry reuses the same parent Attempt and its immutable recipe/approval identity; it does not create another queue or ledger.
- Commander still validates and approves each bounded plan. A checkpoint is evidence of a completed stage, not permission to dispatch the next one.

## Same-database table (implemented; migration and integrity tests passed)

`initiative_video_stage_checkpoints` is created by an additive `CREATE TABLE IF NOT EXISTS` migration in `InitiativeStore`:

- `sequence INTEGER PRIMARY KEY AUTOINCREMENT`.
- `attempt_id TEXT NOT NULL REFERENCES initiative_video_attempts(attempt_id) ON DELETE RESTRICT`.
- `dag_sha256 TEXT NOT NULL` — full canonical fingerprint of the complete plan/profile/stage manifest.
- `batch_id TEXT NOT NULL` and `batch_fingerprint TEXT NOT NULL` — stable batch identity plus its full SHA-256 fingerprint.
- `step_id TEXT NOT NULL` — distinct from `stage_id`; permits dynamic IDs such as `shot_001`.
- `stage_id TEXT NOT NULL` — fixed capability/stage category from `video_stages.yaml` (for example `motion_design`).
- `lease_epoch INTEGER NOT NULL` — the parent Attempt's `attempt_count` at claim time; this is the fencing token.
- `checkpoint_key TEXT NOT NULL`, `input_sha256 TEXT NOT NULL`, `output_sha256 TEXT NOT NULL`.
- `recipe_sha256 TEXT NOT NULL` and `approval_task_id TEXT NOT NULL` — copied from the parent Attempt inside the transaction.
- `worker_id TEXT NOT NULL`, bounded canonical `metadata_json`, and `created_at TEXT NOT NULL`.

The uniqueness key is `UNIQUE(attempt_id,dag_sha256,batch_id,step_id,lease_epoch)`. Add timeline and step-history indexes plus no-update/no-delete triggers. A new lease epoch may append new evidence for the same step; prior epochs remain immutable and readable. Use the existing database; do not add another ledger.

## Store API (implemented; focused regression passed)
### `record_video_stage_checkpoint(attempt_id, *, worker_id, lease_epoch, dag_sha256, batch_id, batch_fingerprint, step_id, stage_id, checkpoint_key, input_sha256, output_sha256, metadata=None)`

1. Validate safe bounded identifiers, lowercase SHA-256 digests, a positive integer `lease_epoch`, and JSON metadata no larger than 4 KiB. `step_id` is a safe dynamic identifier; `stage_id` must exist in the fixed stage manifest.
2. Start `BEGIN IMMEDIATE`; load the parent Attempt.
3. Require non-null parent recipe/approval identity, `status='running'`, matching `worker_id`, an unexpired lease, and `lease_epoch == attempt_count`. The epoch check fences an old process even if a worker ID is reused after retry/reclaim.
4. Copy parent `recipe_sha256` and `approval_task_id` into the checkpoint row inside the transaction.
5. Enforce idempotency within `(attempt_id, dag_sha256, batch_id, step_id, lease_epoch)`: identical writes return the existing row; conflicting writes within the same epoch fail closed. A later epoch may append a new result for the same step.
6. Commit and return the stored row. Roll back on every exception.

### `list_video_stage_checkpoints(attempt_id, *, worker_id, lease_epoch, dag_sha256, batch_id=None, batch_fingerprint=None, step_id=None)`

- The reader must present the current owning `worker_id` and current `lease_epoch`; the Store checks `status='running'`, `attempt_count == lease_epoch`, and an unexpired lease inside `BEGIN IMMEDIATE`.
- This current-lease check authorizes reading historical evidence, not writing under an old epoch. Returned rows intentionally include prior successful epochs so a newly claimed worker can validate/reuse their artifacts.
- Optional `batch_id`, `batch_fingerprint`, and `step_id` filters narrow the result to an exact batch/step. DAG, recipe, and approval identity remain mandatory filters.
- Wrong worker, stale epoch, queued/terminal Attempt, or expired lease fails closed. The method never treats a checkpoint as proof that its output artifact still exists or is intact.

- A changed DAG, recipe, or approval must not silently reuse old checkpoints. Return no records for mismatched parent fingerprints; expose the mismatch as a diagnostic rather than treating it as completion.
- The Store only returns lease-authorized, fingerprint-matching evidence; it does not derive a dependency-safe resume frontier. The runtime adapter must compare every required predecessor and recompute stage input fingerprints before reuse. A later completed stage cannot satisfy a missing prerequisite.

### Lease renewal prerequisite for long-running stages

`InitiativeStore.renew_video_attempt_lease(attempt_id, *, worker_id, lease_epoch, lease_seconds=3600, now=None)` extends the current unexpired lease under `BEGIN IMMEDIATE`. It requires the exact current worker and `attempt_count` fencing epoch; an expired lease cannot be resurrected, a stale worker/epoch cannot extend it, and a shorter renewal request never shortens an existing lease. Renewal does not increment the epoch or append noisy lifecycle events. Stage executors should renew before/after long operations and still rely on checkpoint writes to revalidate lease ownership.

## Read-only resume evidence adapter (implemented; wired into the multi-scene Worker path)

`muse.video_checkpoint_adapter.evaluate_video_checkpoint_resume(...)` evaluates existing batch history under the caller's current lease. It is deliberately read-only and returns `execution_eligible=false` and `dispatch_started=false`.

- Requires the caller to supply recomputed `expected_input_sha256` values for current inputs and already verified predecessor outputs; absent or malformed digests fail closed.
- Requires every batch's `required_completed_steps` and each in-batch predecessor to be verified before later steps can be reused.
- Queries the Store using the current worker/epoch and exact `dag_sha256 + batch_id + batch_fingerprint`.
- A candidate row must match the fixed `stage_id` mapping and recomputed input digest. Its `metadata.artifact_relative_path` must be a workspace-relative POSIX path without traversal or symlinks; the target must be a regular file under the 2 GiB limit.
- The adapter hashes the actual file bytes and checks that file identity/size/mtime remained stable while hashing. Only a matching `output_sha256` is reusable. Missing, modified, unsafe or mismatched artifacts are treated as missing evidence, not completion.
- Returns `reusable_steps`, `missing_steps`, `blocked_steps`, and rejection reasons. It does not create checkpoints, dispatch Commander, render media, call providers, or mutate Attempt state.

`VideoTaskWorker` now recomputes the scene plan from the approved normalized scenes, persisted recipe digest, Builder version and generation settings; creates an `epoch_<lease_epoch>` staging child; constructs `VideoSceneCheckpointSession`; and passes only verified prior artifacts plus `session.record` into `VideoProductBuilder.build_from_scenes()`. Builder verifies old-epoch artifacts remain inside the same Attempt staging root, checks MP4 container identity, duration, and independently probed width/height/frame rate/decoded frame count before checkpoint callbacks. This evidence path still does not dispatch through Commander or authorize execution.
## Resume protocol

1. Commander validates the complete DAG and generates deterministic bounded batches (currently plan-only).
2. The runtime binds each checkpoint key to the exact parent `attempt_id`, passes the current `attempt_count` as `lease_epoch`, and verifies parent recipe/approval identity.
3. Before a batch, an adapter checks every `required_completed_steps` entry against durable checkpoints and recomputes each stage input fingerprint from current inputs/upstream output digests.
4. The current store records caller-supplied input/output digests but does not inspect artifact bytes or independently verify them. The runtime adapter must recompute input fingerprints and validate output artifacts before treating a row as reusable. Missing/mismatched stages are planned again through Commander; do not skip dependencies.
5. Each stage writes its checkpoint only after its own output validation succeeds. Failure leaves earlier valid checkpoints intact.
6. A retry of the same failed Attempt acquires a new `lease_epoch`. Old epochs can never write, but their matching evidence remains readable; the adapter may reuse it only after checking batch ID/fingerprint, required predecessors, recomputed input digests, and verified output artifacts. A changed recipe, approval, stage manifest, DAG, or stage input invalidates reuse.
7. Publishing remains behind the existing human `publish_gate`; a checkpoint never publishes or bypasses approval.

### VideoStudio batch identity contract (implemented)

- The full DAG fingerprint includes the SHA-256 of `video_stages.yaml`; each batch has an independent canonical SHA-256 over batch order, step dependencies/capabilities, fixed-stage mapping, required predecessor frontier, and DAG identity.
- `VideoStudioBatch.as_dict()` exposes both full digests and the `step_id -> stage_id` mapping. Dynamic shot IDs such as `shot_opening` map to fixed manifest stage `shot_generation`; ambiguous capability mappings fail closed.
- All 12 pipeline profiles are covered by the mapping regression; `tests/test_video_studio.py` passed **14/14** through psmux after this change.

### Attempt terminal lease fencing (implemented)

- `complete_video_attempt()`, `fail_video_attempt()`, and `release_video_attempt()` execute under `BEGIN IMMEDIATE` and condition their updates on the current `worker_id`, `attempt_count` fencing epoch, and an unexpired lease.
- `VideoTaskWorker` captures the claimed `worker_id` and `attempt_count` and explicitly passes them to release/complete/fail. The existing synchronous status-API/local-assets path binds its `mark_video_attempt_running()` lease to the current execution context and resolves the same owner/epoch for its terminal write.
- A queued Attempt may be terminalized directly only when `attempt_count == 0` (legacy/admin queued path). If a previous lease expired and claim recovery returned the Attempt to queued, terminal writes are rejected until a new Worker claims a new epoch. This closes the stale-worker queued-state bypass.
- Additional regression after the recorded 2,160-test full-suite run: an expired Attempt that is still `running` (before a later claim triggers recovery) rejects complete/fail/release and remains unchanged. Both this case and the SQLite lock-wait expiry case now pass in `tests/test_initiative_engine.py`.
- Lease time is sampled only after `BEGIN IMMEDIATE` is acquired for claim, renewal, completion, failure and release; the legacy `mark_video_attempt_running()` path also starts under a write transaction and checks its queued-state CAS. This avoids using a pre-lock timestamp after a contended write wait.
- Python 3.11 focused validation: InitiativeEngine **30/30 passed**; VideoTaskWorker + Attempt Ledger + Cantonese TTS + VideoStudio + Lightpanda **53/53 passed**, all through Muse psmux. Post-B306/B307 full regression subsequently passed **2,153 tests, 0 failures, 0 errors, 1 skipped** with `MUSE_PYTHON_COMPILEALL_OK` (psmux Job `8f00b5deffa041029b5acec54a93fad5`, exit 0; `muse-dev run --timeout 600`).
- Follow-up after the lock-timing fix: `tests/test_initiative_engine.py` **32/32**, `tests/test_video_studio.py` **16/16**, `tests/test_lightpanda_adapter.py` **6/6**, `tests/test_video_checkpoint_adapter.py` **9/9**, and `tests/test_video_task_worker.py` **17/17**; **80/80 focused tests passed** via Muse psmux. `initiative_engine.py` `py_compile` also passed. This is targeted evidence, not a replacement for a fresh full-suite run.
### Worker/Builder integration (implemented for eligible multi-scene Attempts)

1. Worker claims one approved Attempt and renews the exact `(worker_id, lease_epoch)` lease on a background heartbeat.
2. For a multi-scene brief with a valid persisted `recipe_sha256` and no narration-driven duration reallocation, Worker recomputes `build_video_scene_checkpoint_plan()` using the approved normalized scenes and current Builder version.
3. Worker creates `data/video_attempt_staging/<attempt_id>/epoch_<lease_epoch>`, opens the existing `InitiativeStore` and `TaskQueue`, then constructs `VideoSceneCheckpointSession` with the current lease and exact input digests.
4. Only `session.reusable_step_ids` with matching historical artifact paths and verified file digests are passed to Builder. Builder revalidates MP4 container/size/path and FFprobe width/height/frame rate/decoded frame count, keeps prior epoch files under the same Attempt root, and invokes `session.record(step_id, artifact_path, generation_metadata)` only after these checks pass.
5. Before each new shot dispatch, `scene_generation_guard(step_id)` rechecks the current lease heartbeat and the latest persisted human decision for the exact Commander ticket. Worker rechecks the latest approval again after product filesystem verification and immediately before terminal success.
6. Lease loss is fail-closed: no new shot begins and stale Worker cannot complete/fail/release the newer lease. Failed Attempts keep their staging/checkpoints for retry; the next lease epoch can reuse only digest-verified shots.
7. The Builder still returns a preview product; human content review/publish gates remain separate. The checkpoint session itself exposes `execution_eligible=false` and `dispatch_started=false`; it records evidence only and never creates a second scheduler or bypasses Commander.

Eligibility limitations: legacy Attempts without a persisted recipe digest run without checkpoint persistence; Cantonese narration/TTS scene plans also remain non-resumable because TTS can reallocate shot durations after synthesis. These cases are not silently represented as checkpoint-enabled.

## Required tests before runtime integration is enabled

- [x] Additive migration opens a pre-checkpoint database without changing existing Attempt/event rows.
- [x] Stage-only legacy checkpoint rows migrate losslessly into the legacy batch/step/epoch representation.
- [x] Record requires a running Attempt, current worker lease, matching `lease_epoch`, approval task and recipe digest.
- [x] Identical writes are idempotent per epoch; conflicting writes fail; concurrent identical writers cannot create duplicates.
- [x] A new epoch can append the same step while an old epoch is fenced from writing.
- [x] Checkpoints are append-only and foreign-key constrained; dynamic step IDs remain distinct from fixed stage IDs.
- [x] Checkpoints survive explicit failed-Attempt retry; a newly claimed current lease can read prior epoch evidence, while stale worker/epoch and expired lease reads fail closed.
- [x] Batch ID, batch fingerprint and step ID filters narrow checkpoint history without bypassing current lease or DAG/recipe/approval checks.
- [x] Lease renewal preserves the fencing epoch, never shortens a live lease, and rejects expired, stale-worker and stale-epoch renewals.
- [x] Resume verifier rejects incomplete required predecessors, changed input digests, unsafe artifact paths and output-byte hash mismatches.
- [x] Resume verifier hashes actual workspace artifact bytes, rejects path traversal/symlinks, and blocks dependent steps when an upstream artifact is missing or changed.
- [x] Running Attempt complete/fail/release require current owner, lease epoch and unexpired lease in one transaction.
- [x] A previously claimed Attempt requeued after lease expiry cannot be terminalized by its old worker before a new claim; attempt_count=0 queued legacy compatibility remains covered.
- [x] A terminal writer that waits on a SQLite write lock re-reads wall-clock time after acquiring the lock and rejects a lease that expired during the wait (covered by the passing contention regression).
- [x] Scene checkpoint plan builds deterministic `shot_001…shot_020` DAGs and separates per-scene input digests from DAG identity.
- [x] Attempt-scoped persistent staging directory preparation rejects symlink/non-directory components and path escape.
- [x] Checkpoint artifact metadata stores allowlisted generation facts alongside actual artifact path and byte digest.
- [x] `VideoSceneCheckpointSession` exposes verified generation metadata for resumed shots and a Commander-approved per-step artifact callback; a missing checkpoint remains pending rather than being treated as a rejected historical row.
- [x] Workspace/artifact path guards reject Windows reparse points as well as symbolic links.
- [x] A rejected checkpoint from an old lease epoch may be replaced by a newly generated checkpoint in a new epoch; a rejected checkpoint in the current epoch cannot be overwritten.
- Unknown stages, malformed hashes, oversized/non-finite metadata and invalid identifiers are rejected.
- A DAG with an incomplete upstream stage cannot resume a downstream stage.
- No checkpoint operation creates Commander Tickets, dispatches workers, renders media, calls providers or changes the parent Attempt lifecycle.

## Validation and remaining blockers

- Previous full Python 3.11 regression after the B316 lease-lock timing fix: **2,175 tests, 0 failures, 0 errors, 1 skipped**, with `MUSE_PYTHON_COMPILEALL_OK` (psmux Job `2df0e69f861f4600bdcc9e40ae178e9b`, exit 0; outer WebCodex Job `wc_job_S8oYZXgSf4gx2DYS` exit 0; `muse-dev run --timeout 600`).
- Latest full Python 3.11 regression: **2,237 tests, 0 failures, 0 errors, 1 skipped**, and `COMPILEALL_SRC_MUSE: PASS` (psmux Job `0a4658eb35f54c588aa4698a12ea82ef`, command_succeeded=true / exit 0; outer WebCodex Job `wc_job_nC7NNiywwcHbCE9L`, exit 0). This run includes the video Worker, Builder, checkpoint adapter and Studio tests; it is not a real GPU render/TTS smoke test.
- Latest adapter-specific regression: `tests/test_video_checkpoint_adapter.py` **9/9 passed** (psmux Job `957cf9ff52174e208416fa77de0419a8`, exit 0). It covers first-run missing checkpoints, same-epoch rejection, cross-epoch replacement, artifact-byte tamper detection, and generation metadata rehydration.
- `VideoTaskWorker` explicitly passes `worker_id` and `lease_epoch` for release/complete/fail; Store terminal writes enforce owner/epoch/unexpired-lease fencing and reject stale queued attempts that were previously claimed.
- `VideoSceneCheckpointSession` now gives the Worker owner a single bridge: `resume_scene_metadata` plus `record(step_id, artifact_path, generation_metadata)`. It rechecks exact Commander approval and hashes actual artifact bytes before persisting.
- Worker/Builder integration is now present for non-narrated multi-scene Attempts carrying a persisted recipe digest: lease heartbeat, epoch staging, checkpoint read/write, FFprobe-verified cross-epoch resume, per-shot approval guard, and final approval recheck. It does not make VideoStudio executable; `execution_eligible=false`, no Commander batch dispatch has been authorized, and no checkpoint publishes media.
- Separate Commander capability gate remains blocked on the real runtime, not just untested. The local Wan 2.1/ComfyUI adapter does exist: it uses a fixed built-in node allowlist, loopback-only HTTP, measured GPU headroom, and the cross-process GPU lease, but it is called directly by `VideoTaskWorker`/Builder rather than registered as a Commander stage handler. The current persisted `data/capabilities/muse.sqlite3` has ready FFmpeg/FFprobe and desktop-media capabilities but no ready `comfyui_generation` entry. `media.discover_media_tools()` does not discover a ComfyUI generation executable, `build_adapter_handlers()` exposes only `ffmpeg_transcode`, and the current `worker.py` handler map has no Commander video-shot generation handler. `AgentRoleRegistry` supports exact scoped grants, but no video-shot role grant/handler binding is wired into this Worker path. The Studio admission helper correctly fails closed when capability, scoped role authorization, or callable handler is missing. Do not register a metadata-only capability or treat stage-manifest hints as executable authorization; adapt the existing safe local Wan adapter into a Commander-owned, approval/lease-aware stage handler and verify its role grant before dispatch.
