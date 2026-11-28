"""Red-line static audit policy (backlog D) — an alarm line, NOT a sandbox.

scripts/muse_diag.py L1.6 statically scans src/, scripts/ and tests/ and fails on any hit that is not
allowlisted here. Static scanning cannot prove absence of behaviour (dynamic imports, string-built
commands, indirect calls are out of reach); it only stops the obvious patterns from silently appearing.
Every allowlist entry carries the reason recorded in docs/DECISIONS.md (D-028).
"""
from __future__ import annotations

# Files allowed to contain a literal ``pip install`` / ``npm install`` argv.
PACKAGE_INSTALL_ALLOWLIST: dict[str, str] = {
    "scripts/build_sidecar.py": "human-run packaging script (double-click build); never imported by the runtime",
    "scripts/autopilot_stage2_client_and_github.py": "human-run stage-2 build/release script; never imported by the runtime",
}

# src modules that both use a network API and contain a non-loopback URL literal.
NETWORK_ALLOWLIST: dict[str, str] = {
    "src/muse/research.py": "public search adapters (SearXNG/DDG/Crossref/arXiv/Bing RSS) behind validate_outbound_url",
    "src/muse/research_fetch.py": "public source reader with DNS/redirect guard (_validate_public_url)",
    "src/muse/autonomous_research.py": "research orchestration over the guarded readers",
    "src/muse/intake_runtime.py": "GitHub public issues intake adapter (read-only public API)",
    "src/muse/mingli_provider.py": "optional external chart engine (disabled unless configured)",
    "src/muse/openrouter.py": "paid/free cloud provider adapter gated by provider_policy (paid cloud disabled by default)",
    "src/muse/skill_fusion.py": "capability fusion fetch of public archives behind explicit FUSE-CAPABILITIES approval",
}

# src files allowed to write the User Track directly (track="user") or promote candidates into it.
USER_TRACK_WRITERS: dict[str, str] = {
    "src/muse/dual_track_memory.py": "the Dual-Track gate itself (user_stated origin or human-confirmed promotion)",
}
CANDIDATE_APPROVERS: dict[str, str] = {
    "src/muse/dual_track_memory.py": "confirm_promotion requires a user:/human: approver",
    "src/muse/control_center_api.py": "decide_memory_candidate: explicit human decision in the control panel",
    "src/muse/memory_cli.py": "local CLI typed by the user",
    "src/muse/memory_lifecycle.py": "approve_agent_track: Agent-Track candidates only, human approver distinct from proposer",
    "src/muse/skillify.py": "Agent-Track skill candidates only, after a passing evaluation and human approval",
}

# Absolute paths outside the Muse root that may appear inside write/delete calls (none today).
OUTSIDE_WRITE_ALLOWLIST: dict[str, str] = {}
