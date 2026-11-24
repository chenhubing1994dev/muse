"""Read-only audit of persisted Commander workflow bindings in local Muse SQLite stores.

This script opens existing databases in SQLite URI mode=ro and sets query_only=ON.
It never creates schemas, migrates, inserts, updates, deletes, or checkpoints a database.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def inspect_database(relative: str) -> dict:
    path = Path(relative)
    if not path.is_file():
        return {"path": relative, "exists": False, "tables": []}
    uri = path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        tables = [
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        result: dict = {"path": relative, "exists": True, "tables": tables}
        if "tasks" in tables:
            task_rows = connection.execute(
                "SELECT id, title, status, payload_json, created_at FROM tasks ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
            bound_rows = []
            if "task_events" in tables:
                bound_rows = connection.execute(
                    "SELECT task_id, event_type, actor, payload_json, created_at "
                    "FROM task_events WHERE event_type='workflow.bound' "
                    "ORDER BY created_at DESC LIMIT 500"
                ).fetchall()
            bound_by_task: dict[str, list[dict]] = {}
            for event in bound_rows:
                payload = json.loads(event["payload_json"] or "{}")
                bound_by_task.setdefault(event["task_id"], []).append({
                    "event_type": event["event_type"],
                    "actor": event["actor"],
                    "workflow_id": payload.get("workflow_id"),
                    "commander_plan_id": payload.get("commander_plan_id"),
                    "plan_source": payload.get("plan_source"),
                    "step_ids": payload.get("step_ids"),
                    "created_at": event["created_at"],
                })
            tickets = []
            for row in task_rows:
                try:
                    payload = json.loads(row["payload_json"] or "{}")
                except (TypeError, ValueError):
                    payload = {}
                kind = payload.get("kind") if isinstance(payload, dict) else None
                if kind == "business_opportunity_video_approval" or "video" in str(kind or "").lower():
                    tickets.append({
                        "id": row["id"],
                        "title": row["title"],
                        "status": row["status"],
                        "kind": kind,
                        "initiative_id": payload.get("initiative_id"),
                        "attempt_id": payload.get("attempt_id"),
                        "workflow_bound": row["id"] in bound_by_task,
                        "workflow_events": bound_by_task.get(row["id"], []),
                        "created_at": row["created_at"],
                    })
            result["task_count_sampled"] = len(task_rows)
            result["workflow_bound_event_count_sampled"] = len(bound_rows)
            result["video_related_tickets_sampled"] = tickets
        video_tables = [name for name in tables if "video" in name.lower() and "attempt" in name.lower()]
        if video_tables:
            result["video_attempt_tables"] = {}
            for table in video_tables:
                count = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                result["video_attempt_tables"][table] = {"row_count": count}
        return result
    finally:
        connection.close()


def main() -> int:
    report = {
        "mode": "read_only",
        "databases": [
            inspect_database("data/tasks/muse.sqlite3"),
            inspect_database("data/initiatives/muse.sqlite3"),
            inspect_database("data/business/opportunities.sqlite3"),
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
