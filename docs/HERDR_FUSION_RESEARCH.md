# Herdr / herdr-core capability fusion assessment

Date: 2026-11-21  
Target: `D:\AI AGENT\Muse`  
Decision: adapter-first; do not replace Muse Commander or psmux.

## Upstream identity and version boundary

The supplied `herdrdev/herdr` repository describes the original Rust terminal multiplexer/TUI. The current upstream ecosystem also has a separate `herdr-core` runtime and `herdr-app` desktop client. These are related but distinct execution surfaces; agents in the TUI's server are not automatically shared with a separate Core instance.

- `herdr`: https://github.com/herdrdev/herdr
- `herdr-core`: https://github.com/herdrdev/herdr-core
- `herdr-app`: https://github.com/herdrdev/herdr-app
- Core overview: https://herdr.dev/core/
- Agent status protocol: https://herdr.dev/tui/docs/add-herdr-support/
- Socket API: https://herdr.dev/tui/docs/socket-api/

The upstream Core is Apache-2.0, Rust-based, and runs terminal processes outside the client window. CLI (`hc`) is the simplest automation layer; the socket API supports custom clients and event subscriptions. OSC 7501 lets a process report its own state instead of relying only on screen scraping.

## Capability assessment

| Herdr capability | Muse baseline | Fusion decision |
|---|---|---|
| Client detach without killing terminal work | Muse has detached psmux Jobs and a local autonomous daemon | Keep psmux as the current Windows executor; evaluate Core as an optional terminal backend after a real Windows smoke |
| Persistent state / recovery | Muse has Job manifests, SQLite task leases, heartbeats and manual recovery | Reuse existing TaskQueue and Job manifests; never create a parallel ticket ledger |
| Working / blocked / idle status | AgentRegistry currently derives online/busy/offline from heartbeat and task ID | Add agent-reported state fields and OSC 7501 formatter; stale heartbeat still wins and becomes offline |
| Multi-machine SSH | No general Agent federation is currently accepted as complete | Future explicit machine registry + allowlist; never auto-run unknown remote hosts or infer permissions from remote metadata |
| Agent-to-agent prompt / wait | Muse Commander owns assignment and approval; existing AgentRegistry is metadata-only | Keep Commander as sole scheduler; a future Herdr bridge may expose status/read/wait, but prompt/send requires explicit task authority and a separate permission gate |
| Layout and terminal UI | Muse has its own Rust desktop/dashboard direction | Do not embed a second full terminal UI yet; expose provider-neutral terminal/session records to Muse UI later |

## Important recovery semantics

Do not market all Herdr restart paths as process resurrection. The Core documentation says client close/update does not stop terminals, but if the machine reboots or Core stops, programs stop; saved panes may return as fresh shells. The original Herdr README similarly distinguishes restored layouts from original processes. Muse must continue to rely on its own durable TaskQueue, leases, idempotency keys and explicit recovery policy for work resumption.

## First implementation slice

1. Extend the existing AgentRegistry with `reported_state`, `reported_message`, `reported_source` and `reported_at`, using additive SQLite column migrations.
2. Accept only `working`, `blocked`, `idle`, `done`, `unknown`; map fresh reports to Muse `busy` / `blocked` / `online`. A stale heartbeat overrides any reported state and remains `offline`.
3. Add a narrow OSC 7501 formatter and `muse-agent-status` CLI. OSC fields are constrained to fixed status tokens and a validated app slug; arbitrary message text is never interpolated into terminal control sequences.
4. Expose only status, timestamp and source in the Dashboard Agent API; keep free-text message in the local registry and out of the API projection.
5. Preserve the current psmux path. Its detached Job output is redirected to files, so it is not yet proven that OSC sequences from nested jobs reach an outer Herdr terminal.

## Local status-reporting examples

Use the module entry directly before the installed project script is refreshed:

```powershell
py -3.11 -m muse.herdr_adapter --agent-id coder --state blocked --message "Waiting for operator"
py -3.11 -m muse.herdr_adapter --agent-id coder --state blocked --osc
```

The first command updates Muse's local AgentRegistry. The second does the same and emits OSC 7501 to the current terminal; it must run in a terminal that forwards OSC 7501. `--message` is a short local display summary, never a prompt, secret, or approval token.

## Not done / acceptance gates

- No Herdr binary was installed or launched; no remote install script was executed.
- No `hc` process, SSH machine or Herdr socket connection was created.
- OSC 7501 forwarding on the user's Windows terminal and through the current psmux topology has not been verified. The initial CLI emits OSC only when explicitly passed `--osc` and run in a terminal that forwards it.
- No arbitrary agent prompt, keystroke injection, remote execution, approval or task dispatch is exposed by this adapter.
- A future `herdr-core` backend must prove Windows process ownership, client detach, terminal reconnect, graceful stop, crash behavior, status events, and safe recovery before it can be enabled by default.

## License and supply-chain notes

The Core project is Apache-2.0. Any vendoring or code reuse must preserve license/notice files and review bundled dependencies. Prefer invoking a verified upstream binary through a narrow adapter over copying large parts of its terminal/process implementation into Muse.

## Second implementation slice: provider-neutral discovery

The upstream Core CLI contract was checked against the current generated command reference (docs/commands.md): `hc --version`, `hc schema --json`, and `hc ls --json` are documented commands. `hc schema --json` returns the OpenRPC API description; `hc ls --json` is the read-only terminal inventory command. The older Herdr TUI CLI is a separate product and is not used here.

Muse now has a deliberately narrow `TerminalRuntimeProvider` discovery contract:

- `PsmuxProvider` wraps the existing `webcodex_psmux_exec.inspect_session()` and `list_jobs()` observers. It does not create another executor, probe/acquire the psmux lock, or alter Job manifests.
- `HerdrCoreProvider` discovers `hc` from PATH (or accepts an explicit argv prefix for tests), runs only the documented version/schema/list argv, uses `shell=False`, bounded timeouts and output limits, and validates JSON before returning observations.
- `muse-dev terminal probe` and `muse-dev terminal list` expose the same read-only surface. The default remains `psmux`; Herdr Core is opt-in with `--provider herdr-core`.
- No provider method can run a task, send input, attach, kill, add a machine, or dispatch an Agent. Commander and TaskQueue remain the only assignment/approval/execution authority.

Examples:

```powershell
py -3.11 scripts/muse_dev.py terminal probe
py -3.11 scripts/muse_dev.py terminal list
py -3.11 scripts/muse_dev.py terminal probe --provider herdr-core
py -3.11 scripts/muse_dev.py terminal list --provider herdr-core
```

This is discovery and status interoperability, not yet a Herdr execution backend. The actual `hc` binary is not installed or invoked in this phase; fake executable contract tests verify the exact argv and JSON handling. Real Windows Core installation, live `hc ls`, event subscriptions, detach/reconnect, process ownership and crash/recovery remain acceptance gates.

## Third implementation slice: Core event protocol reducer

The Core protocol is not the older TUI socket protocol. Core uses JSON-RPC 2.0 over newline-delimited messages; `events.subscribe` returns `{seq, terminals}` and then pushes `event` notifications without ids plus 15-second `heartbeat` notifications. Event sequence numbers restart with each Core process. A `missed` notification carries a replacement terminal snapshot; clients must replace local state and discard assumptions about missing event history. See the upstream [herdr-core API schema guide](https://github.com/herdrdev/herdr-core/blob/master/schema/README.md#events).

Muse now has `src/muse/herdr_events.py`, a pure protocol reducer:

- Bootstrap from a subscription snapshot and retain only terminal identity + reported status; never retain argv, cwd, command output or other terminal data.
- Accept only explicit `reported=true` statuses `idle`, `working`, `done`, `blocked`; `error`, absent status and unreported state become `unknown` and cannot update an Agent.
- Ignore duplicate/stale sequence events, handle heartbeats, atomically replace status projection on `missed`, and require fresh bootstrap after reconnect/Core restart.
- `lost`, `finished` and `removed` clear stale working state; terminal completion is not interpreted as successful task completion.
- Map a terminal to a Muse Agent only through a caller-supplied explicit binding. No implicit terminal-id-to-agent-id guess, no auto-registration, no Commander dispatch, no approvals or input injection.
- Snapshot recovery exposes a separate explicit projection function so a caller can reconcile only pre-bound Agents after a `missed` snapshot.

The reducer is transport-independent and tested against synthetic protocol messages. It does not yet open the Windows named pipe, run `hc events`, start Core, or create a reconnecting daemon. That next transport stage must use a dedicated least-privilege reader and be proven against a real pinned `hc` build before any default-path integration.
