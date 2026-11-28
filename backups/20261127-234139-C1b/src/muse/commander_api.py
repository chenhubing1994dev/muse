"""HTTP surface for Muse Commander, mounted inside status_api's authenticated handler.

Routes (loopback + Bearer + Origin are enforced by status_api before dispatch here):
  GET  /api/v1/commander/status      task counts, safety state, receipt-chain verdict
  GET  /api/v1/commander/approvals   tickets waiting for a human decision
  GET  /api/v1/commander/capacity    observed host capacity (unknown stays unknown)
  GET  /api/v1/commander/adapters    four-state adapter health
  GET  /api/v1/commander/fleet       expert fleet executor bindings
  POST /api/v1/commander/approve     {"task_id", "approved_by": "user:…", "confirmation": "APPROVE"}
  POST /api/v1/commander/cancel      {"task_id", "by": "user:…"}
  POST /api/v1/commander/kill        {"actor": "user:…", "reason", "confirmation": "KILL"}
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

MAX_BODY = 4096
PREFIX = "/api/v1/commander/"


def commander_root(workspace_root: str | Path) -> Path:
    return (Path(workspace_root) / "data" / "commander").resolve()


def _approvals(root: Path) -> list[dict[str, Any]]:
    db = root / "tasks.sqlite3"
    if not db.exists():
        return []
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        rows = con.execute("SELECT id, title, payload_json, created_at FROM tasks WHERE status='waiting_approval' "
                           "ORDER BY created_at LIMIT 200").fetchall()
    finally:
        con.close()
    out = []
    for task_id, title, payload_json, created_at in rows:
        payload = json.loads(payload_json or "{}")
        out.append({"task_id": task_id, "title": title, "created_at": created_at,
                    "risk": payload.get("risk"), "risk_reasons": payload.get("risk_reasons", []),
                    "plan_id": payload.get("plan_id"), "goal": payload.get("goal")})
    return out


def handle_commander(method: str, path: str, raw: bytes, workspace_root: str | Path) -> tuple[int, dict[str, Any]]:
    from muse.muse_commander import MuseCommander

    route = path[len(PREFIX):] if path.startswith(PREFIX) else ""
    root = commander_root(workspace_root)
    if method == "GET":
        if route == "capacity":
            return 200, {"api_version": 1, "capacity": MuseCommander.capacity()}
        if route == "adapters":
            from muse.adapter_hub import health_report
            return 200, {"api_version": 1, "adapters": health_report()}
        if route == "fleet":
            from muse.expert_fleet import fleet_health
            return 200, {"api_version": 1, "fleet": fleet_health()}
        if route == "approvals":
            return 200, {"api_version": 1, "approvals": _approvals(root)}
        if route == "status":
            with MuseCommander(root) as commander:
                return 200, {"api_version": 1, "status": commander.status()}
        return 404, {"error": "unknown commander route"}
    if method != "POST":
        return 405, {"error": "method not allowed"}
    if len(raw) > MAX_BODY:
        return 413, {"error": "body too large"}
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, {"error": "invalid JSON"}
    if not isinstance(body, dict):
        return 400, {"error": "JSON object required"}
    try:
        with MuseCommander(root) as commander:
            if route == "approve":
                if body.get("confirmation") != "APPROVE":
                    return 400, {"error": "explicit APPROVE confirmation required"}
                task_id = str(body.get("task_id", ""))
                approved = commander.approve(task_id, approved_by=str(body.get("approved_by", "")))
                execution = None
                handler = str((approved.get("payload") or {}).get("handler", ""))
                if handler.startswith("route."):
                    # Commander-routed API tickets have a known in-process handler: run it now.
                    from muse.commander_routes import build_route_handlers
                    for name, fn in build_route_handlers(workspace_root).items():
                        commander.register_handler(name, fn)
                    execution = commander.run_next(task_id=task_id, timeout_seconds=30)
                    execution = {"status": execution.get("status"), "task_id": execution.get("task_id")}
                return 200, {"api_version": 1, "task": approved, "execution": execution}
            if route == "cancel":
                by = str(body.get("by", ""))
                if not by.startswith(("user:", "human:")):
                    return 403, {"error": "cancel requires a human identity"}
                return 200, {"api_version": 1, "result": commander.cancel(str(body.get("task_id", "")), by=by,
                                                                           reason=str(body.get("reason", ""))[:500])}
            if route == "kill":
                actor = str(body.get("actor", ""))
                if body.get("confirmation") != "KILL" or not actor.startswith(("user:", "human:")):
                    return 400, {"error": "explicit KILL confirmation and human actor required"}
                return 200, {"api_version": 1, "result": commander.kill(actor=actor, reason=str(body.get("reason", "manual"))[:300])}
    except PermissionError as exc:
        return 403, {"error": str(exc)[:300]}
    except (ValueError, RuntimeError) as exc:
        return 409, {"error": str(exc)[:300]}
    return 404, {"error": "unknown commander route"}
