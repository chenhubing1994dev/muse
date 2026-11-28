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


def _omega_gate(workspace: str | Path | None) -> dict[str, Any] | None:
    """The Omega dashboard keeps its own SafetyController; either switch stops everything (fail closed)."""
    base = Path(workspace).resolve() if workspace else Path(__file__).resolve().parents[2]
    if (base / KILL_FILE_NAME).exists():
        return {"allowed": False, "reason": "kill_switch", "source": str(base / KILL_FILE_NAME)}
    db = base / "data" / "autonomy" / "safety.sqlite3"
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("SELECT mode, reason FROM supervisor_safety WHERE singleton=1").fetchone()
        finally:
            con.close()
    except sqlite3.Error as exc:
        return {"allowed": False, "reason": "omega_state_unreadable", "detail": f"{type(exc).__name__}: {exc}"[:200]}
    if row is None or row[0] == "running":
        return None
    return {"allowed": False, "reason": "kill_switch" if row[0] == "killed" else "paused", "detail": row[1],
            "source": str(db)}


def check_global_gate(workspace: str | Path | None = None) -> dict[str, Any]:
    root = commander_root(workspace)
    if (root / KILL_FILE_NAME).exists():
        return {"allowed": False, "reason": "kill_switch", "source": str(root / KILL_FILE_NAME)}
    omega = _omega_gate(workspace)
    if omega is not None:
        return omega
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


# ---------------------------------------------------------------- Ruflo execution scope (T6)
import contextlib as _contextlib
import contextvars as _contextvars
import json as _json
import time as _time

_RUFLO_SCOPE: _contextvars.ContextVar[dict[str, Any] | None] = _contextvars.ContextVar("muse_ruflo_scope", default=None)


class BypassCommanderError(PermissionError):
    """A Commander-owned execution path was invoked outside a running Commander ticket."""


@_contextlib.contextmanager
def ruflo_ticket_scope(ticket_db: str | Path, task_id: str | None, worker_id: str | None, workspace: str | Path):
    """Bind Ruflo execution in this context to one ticket; verified again at every tool call."""
    token = _RUFLO_SCOPE.set({"ticket_db": str(Path(ticket_db).resolve()), "task_id": task_id,
                              "worker_id": worker_id, "workspace": str(Path(workspace).resolve())})
    try:
        yield
    finally:
        _RUFLO_SCOPE.reset(token)


def _audit(root: Path, name: str, entry: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with (root / name).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(_json.dumps({"at": _time.strftime("%Y-%m-%dT%H:%M:%S%z"), **entry}, ensure_ascii=False) + "\n")


def require_ruflo_scope(tool: str, *, workspace: str | Path | None = None) -> dict[str, Any]:
    """Fail closed unless a live, leased ticket owns this Ruflo execution and the global gate is open."""
    scope = _RUFLO_SCOPE.get()
    root = commander_root(scope["workspace"] if scope else workspace)
    reason = ""
    if scope is None:
        reason = "no commander ticket scope"
    elif not scope.get("task_id") or not scope.get("worker_id"):
        reason = "scope has no ticket/worker"
    else:
        gate = check_global_gate(scope["workspace"])
        if not gate["allowed"]:
            reason = f"global gate closed: {gate['reason']}"
        else:
            db = Path(scope["ticket_db"])
            try:
                con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
                try:
                    row = con.execute("SELECT status, lease_owner FROM tasks WHERE id=?", (scope["task_id"],)).fetchone()
                finally:
                    con.close()
            except sqlite3.Error as exc:
                row, reason = None, f"ticket store unreadable: {type(exc).__name__}"
            if not reason:
                if row is None:
                    reason = "ticket not found"
                elif row[0] != "running":
                    reason = f"ticket is {row[0]}, not running"
                elif row[1] != scope["worker_id"]:
                    reason = "ticket lease held by another worker"
    if reason:
        _audit(root, "bypass_audit.jsonl", {"function": f"ruflo.call_tool:{tool}", "reason": reason,
                                            "claimed_task_id": str((scope or {}).get("task_id") or "")[:64]})
        from muse.error_journal import record_suppressed_error
        record_suppressed_error(f"commander_gate.ruflo_bypass:{tool}", BypassCommanderError(reason))
        raise BypassCommanderError(f"Ruflo tool {tool} must run inside a running Muse Commander ticket ({reason})")
    _audit(root, "ruflo_calls.jsonl", {"tool": tool, "task_id": scope["task_id"], "worker_id": scope["worker_id"],
                                       "ticket_db": scope["ticket_db"]})
    return scope
