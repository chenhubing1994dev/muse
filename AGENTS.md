# Muse Project Instructions

## Muse product charter (mandatory)

- Follow [docs/VISION.md](docs/VISION.md) as the four-pillar product charter: inspiration/memory, research-and-code evolution, one Commander, and resource-aware parallel execution.
- Treat metaphysical systems as inspiration and hypothesis-generation only; label them clearly and never promote them to factual evidence or bypass tests/authorization.
- Muse Commander remains the sole scheduling authority. Reuse Ruflo, dsh patterns, Party integrations and agent-desktop through adapters; do not create competing schedulers or duplicate ticket stores.
- Parallelism must be bounded by observed host resources and explicit hard limits. Unknown hardware capacity is unknown, not an invitation to guess or overcommit.
- Inspiration briefs may combine active User Track memories with symbolic lenses, preserving source/confidence; label metaphysical content as hypotheses, do not read Agent Track as personal facts, and never auto-write insight candidates back to User Track.

## WebCodex interface safety (mandatory)

Before **every** WebCodex/MCP operation:

1. Inspect the tools actually exposed in the current ChatGPT session. The current host tool catalogue is authoritative; chat history, saved memory, copied examples, and old documentation are not.
2. Discover the target runtime tool with the current WebCodex `read_tool_manifest` and read its exact input schema and route before calling it, unless that exact contract was already verified in the current uninterrupted task and has not changed.
3. Call only the currently exposed callable or the exact route prescribed by the manifest. Do not guess names, arguments, or gateway routes.
4. Never blindly call legacy names such as `system_status` or `workspace_list`. If a tool is absent or an argument is rejected, stop, refresh discovery, inspect the current schema, and retry only after correcting the evidence-based call.
5. The familiar v0.5.0 capabilities are runtime status, project discovery, and project work. Their commonly exposed names are `get_runtime_status`, `list_projects`, and `work_on_project`; these are examples, not permanent aliases. Re-discover them each time the tool surface may have changed.
6. Discover Projects with `list_projects` using its current manifest route and schema. Select by exact canonical Project ID. Never guess an ID or silently choose the first result when more than one Project could match.
7. Keep the WebCodex runtime tool manifest separate from the ChatGPT host MCP schema. `read_tool_manifest` discovers runtime tools; a Runner plugin-provider reload does not prove that ChatGPT refreshed its host `tools/list` schema. Never claim host schema refresh without observable host-side evidence.
8. Report only observed outcomes. A plan, accepted request, queued Job, or internal reload is not proof that a file changed, a test passed, or a host schema refreshed.

## Change discipline

- Preserve unrelated work and existing project conventions.
- For project edits, inspect first, make the smallest reviewable change, review the resulting files, run task-fit non-destructive validation, and report exact evidence.
- Do not upgrade/reconfigure WebCodex Server or Runner unless the user explicitly asks.
- The detailed runbook and acceptance checklist live in [docs/WEBCODEX_INTERFACE.md](docs/WEBCODEX_INTERFACE.md).
