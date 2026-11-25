# B126 · Cosmic Desktop UI acceptance and user guide

Date: 2026-11-13

## Result

B126 carries the B125 cosmic command-center design into the Tauri release build and fixes a duplicated legacy navigation block found during live source inspection.

- The top bar is now a compact MUSE brand/identity bar with the refresh action.
- The single primary navigation rail remains in the application layout; the duplicate rail that had been nested inside the top bar was removed.
- Existing application sections and their real API integrations are preserved.
- Release executable rebuilt at `desktop/src-tauri/target/release/muse-desktop.exe` (8,678,400 bytes; build completed successfully).
- The desktop launch script completed successfully. A direct process probe observed a `muse-desktop` window titled `MUSE · 灵枢调度中心`; the Runner later cleaned up the process, so persistent user-session runtime is not claimed.

## Validation evidence

- `npm run check`: 0 errors, 0 warnings.
- `npm test`: 19/19 passed.
- `npm run build`: passed.
- Python 3.11 `.venv`: `compileall -q src tests` passed.
- Python full regression: 1,114 tests, 0 failures, 0 errors.
- Rust `cargo test`: 3/3 passed.
- Rust release build via `scripts/Muse-Desktop.ps1`: passed.

## Screenshot / visual acceptance

A genuine screenshot was requested. It was not attached because the Computer snapshot service did not expose the Muse Tauri window as an observable window surface, and its display-monitor identity call failed. The browser test surface could not reach the Runner-hosted localhost Vite server (`ERR_CONNECTION_REFUSED`). No generated mockup is substituted for a real screenshot. Therefore screenshot-based visual regression remains an explicit acceptance gap.

## Interface map

1. **意识中枢** — product overview, local-core connection state, active-agent count, and inspiration/product record counts. The cosmic orbit is a visual metaphor, not a consciousness meter.
2. **智能体舰队** — registered agents, role, source, declared capabilities, online/busy/offline state, heartbeat age, completed work, and measured effectiveness/review rates where samples exist.
3. **任务与 Commander 工作流** — queue counts, active tasks, attempts, dependencies, workflow stage, next action, and Ticket/Flow identifiers. This is observational; the Commander remains the only scheduler.
4. **灵感工坊** — save an idea locally. External research is opt-in per submission; unchecked submissions remain local. Review inspiration history and download generated research reports.
5. **视频创作与成果** — choose Wan 2.1 prompt-to-video or local FFmpeg asset editing, configure title/prompt/aspect ratio/duration or local source folder/audio, submit work, inspect products, and download MP4/package artifacts. Actual generation still depends on local ComfyUI/model readiness and each output needs human review.
6. **创作时间线与知识关联** — inspect Initiative timeline, open linked knowledge nodes, and see product/failure/feedback events when the backend has recorded them.
7. **进化实验室** — submit structured usability/workflow feedback, run the capability radar, inspect improvement proposals. Proposal creation is not automatic code approval; review/test gates remain.
8. **星轨日志** — inspect recent events and task/agent activity from the local API.

## Recommended first-use flow

1. Double-click the desktop **Muse** shortcut. If it is not available, run `<MUSE_ROOT>\scripts\Muse-Desktop.ps1` from PowerShell.
2. Wait for the local core connection indicator to become online; use **刷新状态** if needed.
3. Open **智能体舰队** and check whether the Agent Registry is configured and agents have recent heartbeats. Empty/offline states are real states, not demo agents.
4. Submit a short idea in **灵感工坊**. Leave external research unchecked for a private local note; check it only when you explicitly want public-source research.
5. For video, first confirm FFmpeg/ComfyUI/Wan engine status. Start with a short preview, then inspect the resulting MP4 and delivery ZIP before accepting it.
6. Open a product's timeline, record accept/revise/reject feedback with concrete notes, and inspect the resulting research/evolution records.
7. In **进化实验室**, review proposals and evidence. Do not treat a proposal as an approved code change.

## Known boundaries

- “宇宙最强意识体” is the long-term product ambition and interface metaphor, not a claim that Muse has subjective consciousness.
- Empty registry, missing model, offline API, missing artifact, unknown metrics, or absent evidence must remain visible as such; no fake agents, consciousness scores, or estimated success metrics.
- Screenshot/visual acceptance is still pending.
- This pass does not claim MSI/NSIS installer, tray integration, login startup, auto-update/rollback, or exhaustive click-through of every business workflow.
