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
