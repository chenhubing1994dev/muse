"""Optional Herdr Program Status Protocol (OSC 7501) bridge for Muse Agents.

This module does not install or launch Herdr. It provides a safe, explicit
status-reporting surface that can be used from a Herdr Core terminal and keeps
Muse's SQLite AgentRegistry as the authoritative local status ledger.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import TextIO

from muse.agent_registry import AgentRegistry

OSC_STATES = frozenset({"working", "blocked", "idle", "done"})
APP_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")


def muse_state_to_herdr(state: str) -> str | None:
    """Map Muse's observed lifecycle labels to Herdr's terminal status vocabulary."""
    mapping = {
        "busy": "working", "working": "working", "blocked": "blocked",
        "online": "idle", "idle": "idle", "done": "done",
        "offline": None, "disabled": None, "unknown": None,
    }
    if not isinstance(state, str) or state not in mapping:
        raise ValueError("unsupported Muse Agent state")
    return mapping[state]


def format_osc7501_status(state: str, *, app: str = "muse") -> str:
    """Return one OSC 7501 status sequence; never interpolate arbitrary text."""
    if not isinstance(state, str) or state not in OSC_STATES:
        raise ValueError("state must be working, blocked, idle or done")
    if not isinstance(app, str) or not APP_PATTERN.fullmatch(app):
        raise ValueError("app must contain 1-32 ASCII letters, digits, '_' or '-'")
    return f"\x1b]7501;state={state}:app={app}\x1b\\"


def write_osc7501_status(stream: TextIO, state: str, *, app: str = "muse") -> None:
    """Write and flush an explicitly requested OSC 7501 status report."""
    stream.write(format_osc7501_status(state, app=app))
    stream.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report a Muse Agent lifecycle state; optionally emit Herdr OSC 7501.")
    parser.add_argument("--db", default="data/agents/muse.sqlite3")
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--state", required=True, choices=("working", "blocked", "idle", "done", "unknown"))
    parser.add_argument("--message", default=None, help="Short operator-facing status summary; never use prompts or secrets")
    parser.add_argument("--source", default="muse-agent-status")
    parser.add_argument("--osc", action="store_true", help="Also emit an OSC 7501 sequence to stdout")
    parser.add_argument("--app", default="muse")
    args = parser.parse_args(argv)
    # Validate the optional terminal control sequence before touching the registry.
    osc_sequence = None
    if args.osc:
        osc_state = muse_state_to_herdr(args.state)
        if osc_state is not None:
            osc_sequence = format_osc7501_status(osc_state, app=args.app)
    with AgentRegistry(args.db) as registry:
        updated = registry.report_state(args.agent_id, args.state, message=args.message, source=args.source)
    if not updated:
        print(json.dumps({"updated": False, "reason": "agent_not_registered"}))
        return 2
    if osc_sequence is not None:
        sys.stdout.write(osc_sequence)
        sys.stdout.flush()
    elif not args.osc:
        print(json.dumps({"updated": True, "agent_id": args.agent_id, "state": args.state}, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
