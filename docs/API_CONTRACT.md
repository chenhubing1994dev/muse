# Muse Local Status API v1

Status (2026-11-13): read-only local status API v1 is implemented; Tauri 2 + Rust Release EXE compiles and launches with the Python API child process, and the Svelte real-data UI passes type checks, 19 API tests and production build. Installer packaging and full interactive desktop acceptance remain pending.
Base URL: http://127.0.0.1:<port> only. The server binds explicitly to IPv4 loopback; it does not expose LAN or public interfaces.

Authentication: Authorization: Bearer <ephemeral-token>. create_status_server() returns a random token to its caller. The CLI prints it once to its launching terminal; the Tauri host captures it from the backend bootstrap pipe and exposes it only through a narrow IPC command. Never put the token in URLs, logs, browser local storage or source control. Requests with a disallowed Origin are rejected. Accepted origins are tauri://localhost and HTTP(S) origins whose hostname is localhost/127.0.0.1/::1. CORS responses echo only the validated Origin (never wildcard or credentials); preflight permits GET with Authorization/Accept headers only.
All endpoints are GET-only and return Cache-Control: no-store:

| Endpoint | Purpose |
|---|---|
| /api/v1/health | API health, version and whether the Agent Registry database is configured |
| /api/v1/overview | Payload-free task queue snapshot, worker heartbeats and recent progress events |
| /api/v1/events?limit=30 | Recent bounded progress events; limit 1–100 |
| /api/v1/capabilities | Display-safe capability metadata; executable paths and opaque metadata are omitted |
| /api/v1/agents?limit=100 | {api_version, registry_configured, count, agents}; display-safe identity, declared capabilities, derived status, heartbeat and aggregate effectiveness |

Each Agent contains agent_id, role, display_name, capabilities, source, enabled, status (online|busy|offline|disabled), last_seen_at, stale_after_seconds, current_task_id and effectiveness. Success and Reviewer pass rates remain null until there are real samples. The response distinguishes a missing registry database (registry_configured=false) from a configured but empty registry.

/api/v1/agents does not trigger discovery or probe endpoints. It is available even without agent_db, but then explicitly reports registry_configured=false. Agent source and capabilities are descriptive metadata, not execution permissions. Discovery is an explicit CLI action over configured loopback URLs only.

Mutating HTTP verbs return 405. Task payloads and chat/conversation content are never returned by this API. Orchestration run details are not yet exposed; a follow-up endpoint should add an explicit payload-free OrchestrationStore listing method before the UI displays run cards. The server caps concurrent request handlers at 8, uses a backlog of 16 and applies a 5-second socket timeout. Requests beyond the concurrency cap are closed without spawning more threads.

The API module is not started automatically when used standalone. The Tauri host starts it as a child process, captures the one-time bootstrap token over stdout, exposes the connection only to the UI through get_status_connection IPC, and terminates the child on application exit. The desktop host requires an existing Python 3.11 launcher and a Muse project root; packaging a self-contained Python sidecar, token rotation and structured redacted diagnostics remain future work. This prototype is a local read-only bridge, not a complete security sandbox.