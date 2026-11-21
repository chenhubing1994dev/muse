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

## Fourth implementation slice: existing-Core-only event transport

The current upstream source was checked at `herdrdev/herdr-core` commit `c7517808d64f06609e4ecf0db1757005373cc3a3`:

- The `hc events` CLI command calls `client::connect_or_start`; it is not safe for an existing-Core-only observer. On non-macOS, the app connection helper `platform::connect_for_app` also calls `connect_or_start`.
- The Core client has a `require_existing` helper that only calls the platform pipe connector, but it is an internal Rust API, not a documented external CLI command. Muse therefore does not shell out to `hc` for event subscription.
- In v0.1.21, the upstream Windows named-pipe connector uses CreateFileW, OPEN_EXISTING, FILE_FLAG_OVERLAPPED and SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION; its normal client retries ERROR_PIPE_BUSY with WaitNamedPipeW. Muse deliberately omits that retry loop: its observer makes one existing-pipe open attempt and fails immediately if unavailable. Muse then uses PeekNamedPipe plus bounded reads for cancellable observation. This is a narrower read-only path, not a full transport clone.
- Endpoint resolution follows upstream precedence: non-empty `HC_SOCKET`, then `socket` in `%APPDATA%/herdr-core/config.toml` (or `XDG_CONFIG_HOME`), then the default `\\.\pipe\herdr-core-{ASCII-alphanumeric-USERNAME}-{HC_SESSION}.sock`. `HC_SESSION` defaults to `default`, trims whitespace, and rejects path/control characters. Custom endpoints must be local `\\.\pipe\` paths; remote UNC and filesystem paths fail closed.
- HerdrCoreEventSubscription.connect() without expected_version sends one JSON-RPC events.subscribe request; with an expected_version pin it first performs rpc.discover, validates OpenRPC info.version and the events.subscribe method, then sends events.subscribe on the same pipe. Both paths validate the matching response and authoritative snapshot. It exposes only next_event() and close(), with no hc subprocess, autostart path, terminal mutation method, reconnect loop or Commander integration. The native Windows stream uses PeekNamedPipe before os.read, so each read is limited to bytes already available; connect() bounds/cancels the handshake and next_event() accepts a caller stop event, timeout and poll interval. A custom injected stream must implement readline_cancellable() to provide these guarantees. Callers still run the reader under Muse's existing supervisor, not the UI thread; after EOF/error, create a fresh subscription and consume its authoritative snapshot.
- The schema's `missed` notification is a separate JSON-RPC method (`method: "missed"`), not merely `method: "event"` with `params.type: "missed"`. The reducer now handles the actual notification shape and atomically replaces its snapshot.

Fake-stream tests verify endpoint precedence/validation, exact request shape, snapshot bootstrap, status and `missed` notification reduction, invalid reply closure, and a single connection attempt when the Core is absent. They do not exercise a real Windows named pipe or a live Core. The transport is not yet wired into AgentRegistry, a daemon, or a default provider path; real pinned-Core smoke and supervised lifecycle integration remain acceptance gates.

## Fifth implementation slice: cancellable read lifecycle

- `_WindowsPipeStream.readline_cancellable()` checks cancellation/closure between bounded polls. `PeekNamedPipe` reports available bytes, and `os.read` consumes no more than that count; partial lines remain buffered until a newline arrives. This avoids an uninterruptible blocking read on the Windows pipe while waiting for the next byte.
- `HerdrCoreEventSubscription.connect()` now accepts `stop_event`, a bounded handshake timeout and poll interval. `next_event()` accepts the same cancellation/timeout controls. Closing the stream wakes its polling reader; EOF, timeout and cancellation remain distinct outcomes for the existing supervisor to handle.
- Injected/test streams that request bounded or cancellable behavior must implement `readline_cancellable`; unsupported streams fail explicitly rather than silently promising cancellation they cannot provide. The transport still does not own a thread, reconnect policy, AgentRegistry writes, or Commander dispatch.
- `tests/test_herdr_transport.py` covers pre-cancelled connection (no pipe open), handshake cancellation/timeout, event-read cancellation/timeout, close wake-up, fresh snapshot after sequence reset, and a mocked `PeekNamedPipe` reader that consumes only available bytes. These mocked Win32 tests validate reader logic, not a live Windows pipe/Core connection.
- Existing-Core-only safety remains unchanged: no `hc` process launch, no `WaitNamedPipe`, no auto-start, no automatic terminal→Agent binding. Real pinned-Core Windows smoke remains a prerequisite before wiring this into the existing `AutonomousSupervisor` lifecycle or any default provider path.


## Sixth implementation slice: pinned stable release and live smoke harness

The stable Windows compatibility target is now pinned to Herdr Core v0.1.21:
- Source tag/commit: v0.1.21 / 1b0393f80c35b1dae34f37934d33d0144a8fc8d7.
- API generation: 1; target: windows-x86_64.
- Archive: https://releases.herdr.dev/core/0.1.21/herdr-core-windows-x86_64.zip.
- Versioned manifest: https://releases.herdr.dev/core/0.1.21/manifest.json.
- Windows ZIP SHA-256 from that manifest, fetched read-only on 2026-11-21: a8984634c35b58ba82d0aec078d5a5e6004da01d5a25b3daf822ab5e31c7312b.
- Upstream distribution/install.ps1 confirms the archive contains herdr-core.exe and conpty/conpty.dll; the ZIP must be treated as a pair, not as a standalone executable.

The earlier transport source review used c7517808d64f06609e4ecf0db1757005373cc3a3, which is 10 commits ahead of the v0.1.21 stable tag. A GitHub compare confirms crates/herdr-core/src/platform/windows.rs was not changed in those 10 commits, while events.rs and the OpenRPC schema did change. The pinned release's schema/README.md documents plain JSON-RPC 2.0, rpc.discover, events.subscribe, event/heartbeat/missed notifications, and a protocol generation-1 schema. Its OpenRPC schema is version 1.3.2 with Git blob SHA 2048664078012a88f90e49d1819d44f8f1e18b86. The release tag's paths.rs, platform/windows.rs, and events.rs were separately inspected.

Windows transport distinction: v0.1.21 upstream opens the local pipe with CreateFileW, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, and identification-only QoS; its normal connector retries ERROR_PIPE_BUSY via WaitNamedPipeW. Muse deliberately does not copy that retry loop: its observer makes one existing-pipe open attempt and fails immediately if unavailable. The Muse reader uses PeekNamedPipe and bounded reads so its own worker can be cancelled. This is an intentionally narrower read-only observer, not a complete replacement for Herdr's client transport.

HerdrCoreEventSubscription.connect(expected_version="0.1.21", expected_openrpc_version="1.3.2") performs rpc.discover and validates both OpenRPC info.version and the OpenRPC schema version, plus the events.subscribe method; it then sends events.subscribe on the same pipe and validates its authoritative snapshot. The same-connection sequence avoids a version-check/subscribe race. Calls without expected_version retain the previous single-request behavior.

New files:
- src/muse/herdr_core_release.py stores the stable source/target/manifest/archive pin and streams SHA-256 verification.
- src/muse/herdr_core_smoke.py and the muse-herdr-core-smoke entry point provide an opt-in, read-only live smoke. It requires a ZIP the user has already downloaded; verifies the pinned archive hash; safely extracts to a temporary directory only; checks the extracted binary's --version and schema --json; then checks the running Core's rpc.discover version on the same connection, subscribes, and waits for a heartbeat.
- The smoke never downloads, installs, updates, launches, stops, lists terminal output, sends input, or dispatches work. It reports counts only, then closes its observer pipe. It does not restart the Core; restart/reconnect acceptance still requires a separately authorized operational test.
- Archive extraction rejects traversal, alternate-stream/drive paths, duplicate case-insensitive Windows paths, symlinks/special files, oversized archives, and incomplete ZIPs.

Upstream security documentation states that the release service's HTTPS manifest checksum is not a separately signed release manifest and Windows Authenticode signing is conditional. The pin therefore proves byte equality with the official HTTPS manifest's published SHA-256, not an independent publisher signature. Do not treat the checksum as stronger than that trust model.

The environment check still found no hc.exe and no running hc process. The live smoke has intentionally not been run. A real pipe heartbeat against the pinned Core remains the gate before any AutonomousSupervisor integration.


