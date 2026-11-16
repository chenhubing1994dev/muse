# B129 · Muse Feature Inventory & Guided User Manual

Date: 2026-11-13

## User request

Explain every Muse function, show how to use it, distinguish real implementation from future vision, and proceed feature-by-feature rather than presenting an aspirational mock product.

## Delivered

### 1. Audited the real product surface

Reviewed the project charter, README, UI product spec, architecture, API contract, B127/B128 reports, current Svelte UI, typed API client and Python status API routes.

Created `docs/MUSE_USER_GUIDE_B129.md` with:
- A full module status matrix (desktop-ready, partial, backend-only/UI missing, or future productization).
- Step-by-step usage for inspiration capture, research consent, both video modes, product delivery/review, Agent monitoring, task monitoring, evolution feedback, event history and LAN pairing.
- Clear boundaries for what each module does not yet do.
- A diagram of the intended idea-to-reality loop, explicitly labelled as a target flow rather than a claim that every transition is automated.
- A prioritized delivery sequence for the remaining desktop work.

### 2. Added an in-app guided manual

- Added `ui-prototype/user-guide.js` with nine validated, data-driven guide steps.
- Added a top-bar **使用指南** entry point.
- Added a responsive modal with previous/next navigation, progress, purpose, exact usage steps, expected result, current capability boundary, and a button to jump to the relevant screen.
- Escape closes the guide; overlay click closes it.
- Guide copy deliberately explains consent, local dependencies, read-only panels and human approval gates instead of implying invisible automation.

### 3. Regression tests

Added `ui-prototype/user-guide.test.js`:
- All nine steps have stable IDs, explanatory copy and valid existing section anchors.
- Step selection clamps to the first/last entry.
- Progress calculation is bounded and one-based.

TDD evidence: the test first failed because `user-guide.js` did not exist; after implementation, it passed.

## Verified results

- Svelte check: **0 errors / 0 warnings**.
- Frontend test suite: **25/25 passed**.
- Vite production build: passed.
- Windows Tauri Release build via `scripts/Muse-Desktop.ps1`: passed; refreshed desktop shortcuts.
- EXE: `desktop/src-tauri/target/release/muse-desktop.exe`, 8,684,544 bytes, last write 2026-11-13 20:09:35.
- Launch script reported successful launch. A subsequent process/window observation did not find a persistent Muse window/process; therefore no actual screenshot or click-through acceptance is claimed.

## Product findings / boundaries

- The desktop currently provides live status overview, inspiration capture, video generation/FFmpeg entry points, product library and review timeline, Agent monitoring, read-only task/Commander monitoring, structured feedback/evolution proposal review, and bounded event history.
- Backend/CLI already contains broader capabilities: local memory tracks, routines/reminders/watchdog, autonomous research/evolution cycles, knowledge graph, resource-aware orchestration, agent adapters and provider/tool adapters. Several are not yet first-class desktop screens.
- Task approval/cancel/retry/recovery controls are not exposed in the current desktop queue. Do not present a visible count as an actionable approval inbox.
- Full Research Wiki graph browsing, evidence/counter-evidence comparison, memory management, tool/provider/resource diagnostics, and settings/installer/tray workflows remain desktop gaps.
- AI video requires the local ComfyUI/Wan stack; engine readiness is not proof that an actual film has been generated and accepted.
- LAN pairing is for a trusted private network; HTTP traffic is not encrypted.

## Next sequential implementation

1. Task center with backend-authorized, audited approve/cancel/retry/recovery actions only after verifying the TaskQueue state machine and authorization contract.
2. Research Wiki explorer with node/edge browsing, source provenance, support/counter-evidence and epistemic labels.
3. User/Agent memory explorer with track separation, provenance and revision history.
4. Runtime operations panel for routines, reminders, watchdog, pause/resume and resource snapshots.
5. Agent/provider/tool management and capability diagnostics.
6. Real desktop screenshot, keyboard/mouse acceptance and installer/tray/update work.

Do not start all six as parallel UI-only mockups. Deliver each module end-to-end, with real API, persistence, tests and operator instructions.
