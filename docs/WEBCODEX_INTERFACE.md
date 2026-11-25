# WebCodex Interface Runbook

This document prevents interface drift between Muse work and the WebCodex MCP runtime.

## Authority order

1. Current ChatGPT-session callable tool catalogue (what can actually be called now).
2. Current WebCodex `read_tool_manifest` result for the exact runtime tool (schema, route, effect, and authority).
3. This runbook as workflow policy.
4. Historical conversations, screenshots, cached schemas, and remembered command names are not interface authority.

## Preflight sequence

1. Inspect the current host tool catalogue for WebCodex callables.
2. Confirm the current runtime discovery entry point; do not assume the name is stable.
3. Read the exact manifest entry for each runtime tool to be used.
4. Check `input_schema`, required fields, route, effect/risk, and authority scope.
5. For runtime status, discover and call the currently admitted status tool.
6. For project selection, discover `list_projects`, follow its exact manifest route, and identify the target by exact canonical Project ID. If ambiguous, stop and ask; never select the first row by default.
7. For project work, discover `work_on_project`, use the exact Project ID or an authorized exact Session continuation, and request the project/workflow context required by its current contract.
8. After execution, inspect the actual result and validate the changed deliverable.

## v0.5.0 familiar capability names (not hard-coded aliases)

The 0.5.0 runtime commonly exposes these capability names:

- `get_runtime_status`: runtime health, version/build alignment, Runner and Session observations.
- `list_projects`: caller-visible registered Projects and exact Runtime Project IDs.
- `work_on_project`: start or resume work against an explicitly selected Project.

Always verify current availability and schema. The current host surface may prefix these names (for example, `mcp__code__...`) or expose runtime tools through a gateway. Follow the manifest; do not infer a route from a name.

## Failure recovery

- **Tool not found:** do not call the legacy equivalent. Re-inspect the current host catalogue and runtime manifest.
- **Schema/argument rejection:** stop blind retries; fetch the exact current schema, compare required fields/types, then make one corrected call.
- **Manifest route is gateway:** use the exact currently exposed gateway and the manifest's exact target and arguments. Do not call a similarly named direct tool as a workaround.
- **Manifest route is direct:** use the exact direct callable. A gateway fallback is allowed only when the manifest explicitly defines it.
- **Project ambiguity:** show the candidate Projects and request an explicit choice.
- **Host Schema appears stale:** distinguish host-side MCP schema from runtime tool discovery. Ask for/rely on an actual ChatGPT connector reconnect or host refresh, then verify the host catalogue again. A runtime manifest read or Runner provider reload is not host refresh evidence.
- **Outcome unknown:** inspect Session/Job/file state before retrying any effect. Never replay a possibly completed mutation blindly.

## Adapter boundary

Muse's local Python runtime does not inherently have access to ChatGPT's live host tool catalogue or WebCodex MCP credentials. Therefore, do not pretend a local Python module can refresh or introspect the host MCP surface.

When a real Muse-to-WebCodex bridge is implemented, keep Muse-facing operations stable (for example, `runtime_status`, `projects_list`, `project_work`) and put tool-name discovery, manifest/schema parsing, route selection, and argument validation in one adapter. The adapter must:
- receive the current tool catalogue and manifest as inputs rather than hard-code stale aliases;
- reject missing tools and unknown/extra arguments before dispatch;
- preserve the exact manifest route and authority/effect metadata;
- distinguish runtime discovery from ChatGPT host schema refresh;
- expose structured errors for `tool_missing`, `schema_mismatch`, `route_unavailable`, `project_ambiguous`, and `outcome_unknown`;
- never auto-select a Project or replay uncertain mutations.



## ChatGPT -> Muse local bridge (Runner CLI)

The supported first-party path is the existing ChatGPT WebCodex Runner callable invoking the Muse CLI in the registered Project. This avoids creating a second MCP server, scheduler, queue, or Commander.

From ChatGPT, invoke the current `run_process` callable with the exact Project ID and literal argv:
- executable: `py`
- args: `["-3", "src/muse/chatgpt_bridge.py", "health"]`
- cwd: Project root (`.`)

For local classification, use args `["-3", "src/muse/chatgpt_bridge.py", "classify", "--request", "<request text>"]`. `classify` calls the loaded LM Studio model directly and returns an advisory classification only. `route` applies deterministic intent rules first and consults Gemma only for unknown intent. `submit` creates a durable ticket in the existing SQLite TaskQueue with status `waiting_approval`; it never dispatches work. `approve` records a named human approval and returns the ticket to `queued`; it still does not dispatch it.

Every response explicitly reports `execution: not_started`. Never treat a classifier suggestion as a tool selection, permission, or approval. Keep LM Studio on loopback only. Do not put tokens or private credentials in argv.

This is a Runner-mediated bridge, not a dynamically registered ChatGPT MCP tool. ChatGPT must call the currently exposed WebCodex `run_process` tool; `read_tool_manifest` does not register new Host tools. The single Muse Commander and its existing TaskQueue remain the only scheduling/ticket owners.

### Windows development terminal: detached psmux job windows

The Windows workstation uses a detached native psmux session named `muse-dev`, rooted at `<MUSE_ROOT>`, started at user logon by Task Scheduler task `\Muse\Muse-pSmux-Background`. The WSL tmux session is a separate fallback and must not be deleted or described as migrated.

For Windows project commands, use `scripts/webcodex_psmux_exec.py` as the default execution adapter. The WebCodex `run_script` PowerShell call is only a short bootstrap: it pipes a JSON request to `py -3.11 scripts/webcodex_psmux_exec.py`. Requests accept inline `script` text or an existing project-relative `script_path`; supported languages are `powershell` (default), `python`/`py`, `bash`/`sh`, and `batch`/`bat`/`cmd`. Existing `.ps1`, `.py`, `.sh`, `.bat`, and `.cmd` files infer their language from the extension unless `language` is explicit, and are confined to the Project root. The payload runs in a detached `psmux new-window -d` job. The adapter also canonicalizes `expected_cwd` and rejects any working directory outside the Muse Project root; only the root and existing in-project subdirectories are allowed. Inline PowerShell is written to a UTF-8-with-BOM `.ps1` payload and launched in a child `powershell.exe -File`, so an explicit `exit N` cannot skip the adapter result marker. Python uses the adapter's interpreter and adds the Project plus `src` to `PYTHONPATH`; batch uses `cmd.exe /d /c`; Bash prefers Git Bash and falls back to `wsl.exe --exec bash -lc`, converting drive paths to `/mnt/<drive>/...`. Each job has its own output/result files and returns the actual exit code. Do not type long commands into the main `muse-dev` prompt.

This default applies to top-level project/development commands launched by WebCodex. Do not blindly route every `subprocess` call inside Muse through psmux: synchronous helper processes (for example, a browser CLI returning structured JSON, an isolated TTS helper, Kinliuren calculation, or FFmpeg) require stdin/stdout pipes, precise timeouts, and a caller-owned lifecycle. They now use `muse.process_utils.no_console_window_kwargs()` on Windows to suppress transient child consoles while preserving those pipes and their existing lifecycle. Long-running services belong to their existing Supervisor, not an interactive development shell.

Example request:
```json
{"language":"python","script":"print('hello from psmux')","session":"muse-dev","expected_cwd":"D:\\\\AI AGENT\\\\Muse","timeout_secs":120}
```

Job scripts, language payloads, output logs, result markers, and the per-session active-job record live under `%LOCALAPPDATA%\Muse\psmux-jobs`. The adapter serializes work per session; an unfinished/unknown job blocks another submission. A completed result includes the actual exit code and captured output. If the adapter returns `outcome_unknown`, retain the job ID and use the recover action; never blindly resubmit the script.

The concise project-local CLI wraps that same adapter (it does not start a second executor):
```powershell
py -3.11 scripts/muse_dev.py run --language python --script-path scripts/check.py --cwd . --session muse-dev --timeout 120
py -3.11 scripts/muse_dev.py inspect --session muse-dev
py -3.11 scripts/muse_dev.py list --session muse-dev --limit 20
py -3.11 scripts/muse_dev.py recover <job_id>
```
Use `--script "print('hello')"` instead of `--script-path` for inline code. `--script` and `--script-path` are mutually exclusive; `--cwd` is a project-relative existing directory, while `--script-path` is project-root-relative even when `--cwd` selects a subdirectory. Supported explicit `--language` values are `powershell`, `python`, `bash`, and `batch`; when omitted, a file path infers language from `.ps1`, `.py`, `.sh`, `.bat`, or `.cmd`, while inline scripts default to PowerShell. The CLI prints the adapter's structured JSON result, propagates a completed script's real exit code, returns `3` for `outcome_unknown`, and `2` for adapter/validation failures. It never uses `shell=True`; all actual payload execution remains inside the detached psmux adapter. `list` is a local read-only inventory of recent job folders. It classifies each record as `completed`, `outcome_unknown` (active record without a result marker), `incomplete_untracked`, or an explicit unreadable state; completed entries include the real exit code and a bounded output tail. It does not acquire/probe the per-session execution lock, clear active markers, delete payloads, or mutate jobs. The response exposes the one-in-flight-job-per-session policy, active job marker, and `lock_probe=not_performed_read_only`; this avoids a racy lock probe masquerading as a passive read. New jobs also receive an atomic `job.json` manifest (job ID, session, language, working directory, window, timestamps and lifecycle status); recovery records the real exit code and finish time without deleting the manifest. Older jobs without a manifest remain visible with `session=unknown`. Status rows include a conservative `next_action`: inspect completed output, reconcile unknown jobs, and never assume an untracked incomplete directory proves a process is stopped or safe to retry. Use `recover <job_id>` only for a known job and inspect its result before any resubmission. The CLI is intentionally invoked by project path rather than installed as a global command or PATH shim.

### Commander Ticket controls through `muse-dev`

`muse-dev commander` delegates to the existing `muse.chatgpt_bridge` functions and the single SQLite `TaskQueue`; it does not create another scheduler or Ticket store:
```powershell
py -3.11 scripts/muse_dev.py commander list --limit 20
py -3.11 scripts/muse_dev.py commander status --task-id <task_id>
py -3.11 scripts/muse_dev.py commander submit --request "<goal>" --request-id "<stable-request-id>"
py -3.11 scripts/muse_dev.py commander approve --task-id <task_id> --approved-by "<human-name>"
```
`submit` requires a stable request ID so repeat calls resolve through TaskQueue's unique idempotency key; all new bridge Tickets use `recovery_policy=manual` and `status=waiting_approval`. `approve` writes the existing `human.decision` event and returns the Ticket to `queued`, but it does not dispatch. `status` reports safe Ticket metadata and event headers only (no payload or event payload); `list` uses TaskQueue's payload-free operational snapshot. All four commands report `execution=not_started`. Execution remains the separate, explicitly approved `MuseRuntime.dispatch_commander_once(task_id=..., approved=True)` operation; this CLI deliberately does not expose dispatch, automatic retry, or unknown-outcome replay. The local bridge is Runner-mediated, not a new ChatGPT MCP tool.


Verified on 2026-11-19: the adapter and ChatGPT bridge regression suites compile and pass (25 tests, Python 3.11). Live detached psmux jobs completed for PowerShell (exit 0, including a 2-second task), Python (exit 9), batch (exit 7), and Bash via WSL (exit 11); each returned its marker output and matching real exit code. A project-relative Python `script_path` also ran the adapter's 20-test module successfully in a detached psmux window. The WSL startup emits a host warning in the captured log, but the Bash payload still ran and its exit code was preserved. Runner config validates at generation 1 with no restart required; process inspection shows `webcodex-runner.exe` hosted by `pythonw.exe` and the desktop launcher by `wscript.exe`, so leave the Runner outside psmux. `run_shell`, `run_script`, and `run_process` remain Runner-owned and are not globally intercepted: project work must explicitly use this adapter to enter psmux. Zero-flash behavior has not been visually verified. The logon task recreates the psmux session after login; it does not restore terminal process state across reboot/logoff.
Further verified on 2026-11-19: 67 targeted tests across process utilities, browser, Cantonese TTS, Kinliuren, FFmpeg adapters, psmux execution, and ChatGPT bridge passed under Python 3.11. The Windows helper reports `CREATE_NO_WINDOW` (`creationflags=134217728`). A live PowerShell payload containing `exit 7` returned `exit_code=7` and its marker output, proving the outer result marker survives user exit statements. `muse-dev` remained available after the test jobs. These checks prove process-level quiet-launch configuration and command routing, not a visual zero-flash guarantee.
Full Muse regression on 2026-11-19: `unittest` discovery ran 1,614 tests in 182.355 seconds; all passed, with 1 intentionally skipped. The run was executed as a detached psmux job and returned exit code 0.

## Acceptance checklist

- [ ] Current host catalogue inspected; no legacy name assumed.
- [ ] Exact runtime tool manifest read for each operation.
- [ ] Tool schema and route match the actual call.
- [ ] Runtime status obtained from a currently admitted tool.
- [ ] Project list obtained through its current manifest route.
- [ ] Exact canonical Project ID selected; ambiguity handled explicitly.
- [ ] Project work uses the current schema and required context.
- [ ] Missing-tool and schema-error recovery follows rediscovery, not blind retries.
- [ ] Host schema refresh is reported only with host-side evidence.
- [ ] Deliverables reviewed and task-fit tests run; results reported accurately.
## 2026-11-19 Runner interception and desktop observation boundary

The current Host catalogue and Runner manifest expose Runner-owned `run_shell`, `run_script`, and `run_process`; no admitted global command-interception hook or Runner configuration writer was found. `check_runner_config` is read-only (`valid=true`, generation 1, `restart_required=false`); `reload_runner_config` activates an existing candidate and does not create or edit one. The startup config is outside the Muse Project's authorized write root, so do not use Project shell, GUI automation, or private filesystem access to bypass that boundary. Keep the Runner outside psmux.

A read-only desktop snapshot (1920×1080 display sampled at 1280×720) showed OrcaTerm in the foreground and a PowerShell window behind it. This is one frame only: it does not establish which component owns that window, whether it flashed, or a zero-flash guarantee. Use before/after window observations for future visual verification; do not activate or manipulate the user's desktop as a diagnostic shortcut.

Until an official Project-level hook or authorized Desktop configuration path is available, the supported policy remains adapter-first for project commands: short Runner bootstrap → `scripts/webcodex_psmux_exec.py` → detached psmux job. This is a project workflow convention, not a transparent global replacement for Runner execution.

Live recheck on 2026-11-19: a detached Python smoke returned marker `20261119` with exit code 0 and `transport=psmux-detached-window`; subsequent adapter inspection showed the `muse-dev` main window and `active_job_id=null`. A post-run window enumeration no longer listed a standalone PowerShell surface. This validates the specific smoke path and cleanup state, not a universal no-flash guarantee.
