# B128 · Video Job Recovery & Desktop Feedback Integration

Date: 2026-11-13

## Scope

Continue the Muse desktop productivity work as a synchronized frontend/backend slice. The goal was to remove a real failure mode in the video-production workflow: job state lived only in the status API process memory, while the durable video-attempt ledger already existed in SQLite.

## Delivered

### Backend / durable task ledger

- Added `InitiativeStore.get_video_attempt(attempt_id)` with the same strict opaque-ID validation used by the ledger's other video-attempt operations.
- Updated `GET /api/v1/video-jobs/{id}` to prefer the live in-memory job and identify it with `worker_available: true`.
- When the live job is absent, the API now looks up the persisted `initiative_video_attempts` row and returns its durable status with `worker_available: false`.
- Recovered failed jobs expose only the stable failure code and exception class, not raw exception text or private GPU/process details.
- Recovered succeeded jobs expose product identity so the desktop can refresh the product library. A recovered queued/running record is not presented as proof that a worker is still executing.

### Frontend / operator experience

- Extended the typed `VideoJobRecord` contract with `worker_available` and the persisted `cancelled` terminal state.
- Video creation polling now stops when a persisted queued/running attempt has no live worker, refreshes the dashboard, and tells the operator the task record survived but the render process is unavailable. It does not falsely display an endless “generating” state.
- Failed and cancelled terminal jobs are surfaced as terminal outcomes.
- Research report, product ZIP, MP4 download and product preview errors now use a dedicated download/preview feedback message instead of incorrectly appearing in the inspiration submission message area.

### Regression coverage

- Added `test_video_job_endpoint_recovers_durable_attempt_after_api_restart`, which seeds a persisted running attempt, starts a fresh API process context, and verifies the durable status is returned with `worker_available: false`.

## Validation evidence

- `tests/test_status_api.py`: **23 tests passed** (including the new recovery test).
- Full Python unittest regression: **1,115 tests passed**, 122.597 seconds, exit code 0.
- Svelte check: **0 errors / 0 warnings**.
- Frontend TAP suite: **22/22 passed**.
- Vite production build: passed.
- Tauri Windows Release build via `scripts/Muse-Desktop.ps1`: passed; executable produced at `desktop/src-tauri/target/release/muse-desktop.exe`.
- Final executable observation: 8,680,448 bytes; last write 2026-11-13 19:53:20.
- The launch script completed and refreshed both desktop shortcuts. A later process observation found zero surviving `muse-desktop` processes; the Runner may clean up launched child processes, so this is not evidence of a persistent running desktop window.

## Limitations / next iteration

- A real interactive Tauri screenshot/click-through acceptance pass is still outstanding; do not substitute a mockup or claim visual acceptance.
- Persisted queued/running jobs without a live worker are surfaced honestly, but this iteration does not attempt automatic replay/restart of a render. Replaying video generation automatically could consume GPU resources or duplicate work; a separate explicit retry/recovery policy is needed.
- The durable task history is still the source of truth; the in-memory job map remains a live-process optimization.
- Next: add UI-level tests for restart recovery messaging and download feedback, improve queue filtering only against existing backend fields, and perform an actual desktop interaction/screenshot pass in a stable desktop session.
