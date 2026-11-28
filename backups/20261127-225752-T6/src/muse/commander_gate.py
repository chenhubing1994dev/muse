"""Global Commander gate shared by every dispatch path (single kill switch for all of Muse).

MuseCommander, MuseRuntime.dispatch_commander_once (Ruflo DAG) and AutonomousSupervisor.run_tick
historically kept separate safety state. This module makes them obey one switch:

* ``<project>/data/commander/KILL_SWITCH`` (or ``$MUSE_COMMANDER_ROOT/KILL_SWITCH``) — presence means
  stop everything. ``MuseCommander.kill`` creates it; ``reset-kill`` removes it.
* ``<root>/safety.sqlite3`` — MuseCommander's SafetyController mode (``paused``/``killed``) is read
  in read-only mode, so checking the gate never creates or mutates state.

``check_global_gate`` never raises on a missing store (fresh install = running) but a corrupt
store fails closed.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

KILL_FILE_NAME = "KILL_SWITCH"


class GlobalGateBlocked(PermissionError):
    """Muse Commander's global kill switch or pause is engaged."""


def commander_root(workspace: str | Path | None = None) -> Path:
    override = os.environ.get("MUSE_COMMANDER_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    base = Path(workspace).resolve() if workspace else Path(__file__).resolve().parents[2]
    return (base / "data" / "commander").resolve()


def check_global_gate(workspace: str | Path | None = None) -> dict[str, Any]:
    root = commander_root(workspace)
    if (root / KILL_FILE_NAME).exists():
        return {"allowed": False, "reason": "kill_switch", "source": str(root / KILL_FILE_NAME)}
    db = root / "safety.sqlite3"
    if not db.exists():
        return {"allowed": True, "reason": "no_commander_state", "source": str(db)}
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("SELECT mode, reason FROM supervisor_safety WHERE singleton=1").fetchone()
        finally:
            con.close()
    except sqlite3.Error as exc:
        return {"allowed": False, "reason": "commander_state_unreadable", "detail": f"{type(exc).__name__}: {exc}"[:200]}
    if row is None or row[0] == "running":
        return {"allowed": True, "reason": "ok", "source": str(db)}
    return {"allowed": False, "reason": "kill_switch" if row[0] == "killed" else "paused", "detail": row[1]}


def require_global_gate(workspace: str | Path | None = None, *, path: str) -> dict[str, Any]:
    decision = check_global_gate(workspace)
    if not decision["allowed"]:
        raise GlobalGateBlocked(f"{path} blocked by Muse Commander global gate: {decision['reason']}")
    return decision


def engage_kill_file(workspace: str | Path | None = None, *, actor: str, reason: str) -> Path:
    root = commander_root(workspace)
    root.mkdir(parents=True, exist_ok=True)
    path = root / KILL_FILE_NAME
    path.write_text(f"actor={actor}\nreason={reason}\n", encoding="utf-8")
    return path


def clear_kill_file(workspace: str | Path | None = None) -> bool:
    path = commander_root(workspace) / KILL_FILE_NAME
    if path.exists():
        path.unlink()
        return True
    return False
