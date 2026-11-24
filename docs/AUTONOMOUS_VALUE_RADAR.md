# Muse Autonomous Value Radar

## Purpose

Give Muse a durable daily habit of looking for lawful, low-cost ways to create value. This is a value-discovery routine, not a "survival after owner death" simulation and not a revenue guarantee.

## Current implementation in this increment

- src/muse/autonomous_value_radar.py ranks existing local research reports through the existing ValueForge patterns.
- Every result remains hypothesis_only; source counts, experiments and counter-evidence are preserved.
- The report recommends reversible work Muse can do autonomously: research synthesis, script/storyboard drafts, asset matching, narration/preview preparation and local QA.
- The report explicitly prohibits spending/transfers, account or wallet creation, customer contact, external publishing, bypassing platform restrictions and evading shutdown.
- A deterministic cycle ID and atomic local JSON artifact make a daily run idempotent and auditable.
- make_value_radar_routine_handler returns a handler compatible with the existing RoutineStore execution-context contract. It is an adapter, not a second scheduler.

## Required integration before it runs unattended

The current AutonomousSupervisor routine registry is owned by the Commander/runtime integration workstream. This increment deliberately does not modify autonomous_runtime.py or create a competing scheduler. The runtime owner must:

1. inject an authorized local research-report loader;
2. register the handler under autonomous_value_radar;
3. add daily-value-radar to the existing AutonomousSupervisor.ROUTINES at 86,400 seconds;
4. expose the report summary in the existing status/reporting surface;
5. test pause/stop, lease loss, idempotent restart and no external side effects.

Validation on 2026-11-24: Python 3.11 py_compile passed for the new module and test file; the official scripts/muse_dev.py → psmux focused regression covering the new radar plus ValueForge, RoutineStore, BusinessOpportunityStore and ResearchBusinessBridge passed 60/60 (Job 72f6a1d1c3b74596a6cccf9325aa22be, exit 0). The dedicated radar tests also passed 7/7 (Job 2de6d1c34c8f4ae2a2cd57e906dc61fa, exit 0).\n\nUntil the Commander runtime integration steps above land, the module and adapter are implemented and tested but the daily routine is not yet active by default.

## Safety and truthfulness

The radar never infers willingness-to-pay or demand from research alone. It creates no business opportunity or Creator Initiative without an explicit, human-framed brief and the existing evidence-aware bridge. Video generation continues through the existing Commander approval/admission path; this routine never starts GPU work. Publishing, customer outreach, spending and transactions remain separately authorized actions.
