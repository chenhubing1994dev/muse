"""Durable metadata, heartbeat and evidence-based outcomes for trusted Muse agents.

This registry never stores executable callables or launches discovered processes.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from functools import wraps
from threading import RLock


def _serialized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


class AgentRegistry:
    """SQLite registry with derived online/busy/offline state and outcome metrics."""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        self._lock = RLock()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database, timeout=10, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS agents (
                agent_id TEXT PRIMARY KEY,
                role TEXT NOT NULL,
                display_name TEXT NOT NULL,
                capabilities_json TEXT NOT NULL,
                source TEXT NOT NULL,
                enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                registered_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_seen_at TEXT,
                current_task_id TEXT
            );
            CREATE TABLE IF NOT EXISTS agent_outcomes (
                agent_id TEXT NOT NULL,
                task_kind TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                succeeded INTEGER NOT NULL DEFAULT 0,
                reviewed INTEGER NOT NULL DEFAULT 0,
                review_approved INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(agent_id, task_kind),
                FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
            );
            CREATE INDEX IF NOT EXISTS idx_agents_last_seen ON agents(last_seen_at);
            """
        )
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(agents)")}
        if "title" not in columns:
            self.connection.execute("ALTER TABLE agents ADD COLUMN title TEXT NOT NULL DEFAULT ''")
        if "reports_to" not in columns:
            self.connection.execute("ALTER TABLE agents ADD COLUMN reports_to TEXT")
        if "reported_state" not in columns:
            self.connection.execute("ALTER TABLE agents ADD COLUMN reported_state TEXT")
        if "reported_message" not in columns:
            self.connection.execute("ALTER TABLE agents ADD COLUMN reported_message TEXT")
        if "reported_source" not in columns:
            self.connection.execute("ALTER TABLE agents ADD COLUMN reported_source TEXT")
        if "reported_at" not in columns:
            self.connection.execute("ALTER TABLE agents ADD COLUMN reported_at TEXT")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_agents_reports_to ON agents(reports_to)")
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS agent_org_events (
                event_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        self.connection.commit()

    @_serialized
    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "AgentRegistry":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _now(value: datetime | None = None) -> datetime:
        current = value or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return current.astimezone(timezone.utc)

    @staticmethod
    def _text(value: str, field: str, maximum: int = 160) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
            raise ValueError(f"{field} must be non-empty text of at most {maximum} characters")
        return value.strip()

    @_serialized
    def register(
        self, agent_id: str, *, role: str, capabilities: Iterable[str] = (),
        display_name: str | None = None, source: str = "local:injected", enabled: bool = True,
        now: datetime | None = None,
    ) -> dict[str, object]:
        agent_id = self._text(agent_id, "agent_id")
        role = self._text(role, "role", 96)
        display_name = self._text(display_name or agent_id, "display_name", 160)
        source = self._text(source, "source", 256)
        raw_capabilities = tuple(capabilities)
        if any(not isinstance(item, str) or not item.strip() or len(item) > 128 for item in raw_capabilities):
            raise ValueError("capabilities must contain non-empty strings of at most 128 characters")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        timestamp = self._now(now).isoformat()
        self.connection.execute(
            """INSERT INTO agents (agent_id, role, display_name, capabilities_json, source, enabled,
               registered_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(agent_id) DO UPDATE SET role=excluded.role,
               display_name=excluded.display_name, capabilities_json=excluded.capabilities_json,
               source=excluded.source, enabled=excluded.enabled, updated_at=excluded.updated_at""",
            (agent_id, role, display_name, json.dumps(sorted(set(raw_capabilities)), ensure_ascii=False),
             source, int(enabled), timestamp, timestamp),
        )
        self.connection.commit()
        result = self.get(agent_id, now=now)
        assert result is not None
        return result

    @_serialized
    def heartbeat(
        self, agent_id: str, *, busy: bool = False, current_task_id: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        agent_id = self._text(agent_id, "agent_id")
        if not isinstance(busy, bool):
            raise ValueError("busy must be boolean")
        if busy:
            current_task_id = self._text(current_task_id or "", "current_task_id")
        elif current_task_id is not None:
            raise ValueError("current_task_id is only valid while busy")
        timestamp = self._now(now).isoformat()
        cursor = self.connection.execute(
            "UPDATE agents SET last_seen_at=?, current_task_id=?, updated_at=? WHERE agent_id=?",
            (timestamp, current_task_id, timestamp, agent_id),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    @_serialized
    def report_state(
        self, agent_id: str, state: str, *, message: str | None = None,
        source: str = "herdr-osc7501", now: datetime | None = None,
    ) -> bool:
        """Persist an agent-reported lifecycle state without executing or approving work."""
        agent_id = self._text(agent_id, "agent_id")
        allowed_states = {"working", "blocked", "idle", "done", "unknown"}
        if not isinstance(state, str) or state not in allowed_states:
            raise ValueError("state must be working, blocked, idle, done or unknown")
        source = self._text(source, "source", 80)
        if any(not char.isascii() or not (char.isalnum() or char in "._:-") for char in source):
            raise ValueError("source must be an ASCII identifier")
        if message is not None:
            if (not isinstance(message, str) or len(message) > 160
                    or any(ord(char) < 32 or ord(char) == 127 for char in message)):
                raise ValueError("message must be at most 160 characters without control characters")
            message = message.strip() or None
        timestamp = self._now(now).isoformat()
        cursor = self.connection.execute(
            "UPDATE agents SET last_seen_at=?, reported_state=?, reported_message=?, "
            "reported_source=?, reported_at=?, updated_at=? WHERE agent_id=?",
            (timestamp, state, message, source, timestamp, timestamp, agent_id),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    @_serialized
    def set_enabled(self, agent_id: str, enabled: bool) -> bool:
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        cursor = self.connection.execute(
            "UPDATE agents SET enabled=?, updated_at=? WHERE agent_id=?",
            (int(enabled), self._now().isoformat(), agent_id),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def _decode(self, row: sqlite3.Row | None, *, now: datetime | None = None,
                stale_after_seconds: int = 90) -> dict[str, object] | None:
        if row is None:
            return None
        if isinstance(stale_after_seconds, bool) or not isinstance(stale_after_seconds, int) or stale_after_seconds < 1:
            raise ValueError("stale_after_seconds must be a positive integer")
        item = dict(row)
        item["title"] = item.get("title") or item["role"]
        item["reports_to"] = item.get("reports_to")
        item["capabilities"] = json.loads(item.pop("capabilities_json"))
        item["enabled"] = bool(item["enabled"])
        reported_state = item.get("reported_state")
        if not item["enabled"]:
            status = "disabled"
        elif not item["last_seen_at"]:
            status = "offline"
        else:
            last_seen = datetime.fromisoformat(str(item["last_seen_at"]))
            if self._now(now) - last_seen > timedelta(seconds=stale_after_seconds):
                status = "offline"
            elif reported_state == "blocked":
                status = "blocked"
            elif reported_state == "working":
                status = "busy"
            elif reported_state in {"idle", "done"}:
                status = "online"
            else:
                status = "busy" if item["current_task_id"] else "online"
        item["status"] = status
        item["stale_after_seconds"] = stale_after_seconds
        return item
    @_serialized
    def get(self, agent_id: str, *, now: datetime | None = None,
            stale_after_seconds: int = 90) -> dict[str, object] | None:
        row = self.connection.execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        return self._decode(row, now=now, stale_after_seconds=stale_after_seconds)

    @_serialized
    def list_agents(self, *, now: datetime | None = None, stale_after_seconds: int = 90,
                    limit: int = 100) -> list[dict[str, object]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        rows = self.connection.execute("SELECT * FROM agents ORDER BY agent_id LIMIT ?", (limit,)).fetchall()
        return [item for row in rows if (item := self._decode(row, now=now, stale_after_seconds=stale_after_seconds)) is not None]

    @_serialized
    def set_org_assignment(
        self, agent_id: str, *, title: str, reports_to: str | None = None,
        actor: str = "operator",
    ) -> dict[str, object]:
        """Assign one reporting parent; reject missing managers and every cycle."""
        agent_id = self._text(agent_id, "agent_id")
        title = self._text(title, "title", 96)
        actor = self._text(actor, "actor", 120)
        if reports_to is not None:
            reports_to = self._text(reports_to, "reports_to")
        timestamp = self._now().isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.connection.execute(
                "SELECT title, reports_to FROM agents WHERE agent_id=?", (agent_id,)
            ).fetchone()
            if current is None:
                raise ValueError("agent is not registered")
            if reports_to is not None:
                if self.connection.execute(
                    "SELECT 1 FROM agents WHERE agent_id=?", (reports_to,)
                ).fetchone() is None:
                    raise ValueError("reporting manager is not registered")
                ancestor = reports_to
                visited: set[str] = set()
                while ancestor is not None:
                    if ancestor == agent_id:
                        raise ValueError("reporting assignment would create an organization cycle")
                    if ancestor in visited:
                        raise RuntimeError("existing organization hierarchy contains a cycle")
                    visited.add(ancestor)
                    row = self.connection.execute(
                        "SELECT reports_to FROM agents WHERE agent_id=?", (ancestor,)
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("existing organization hierarchy contains an unknown manager")
                    ancestor = row["reports_to"]
            previous = current["reports_to"]
            self.connection.execute(
                "UPDATE agents SET title=?, reports_to=?, updated_at=? WHERE agent_id=?",
                (title, reports_to, timestamp, agent_id),
            )
            payload = json.dumps({
                "title": title, "reports_to": reports_to,
                "previous_reports_to": previous,
            }, ensure_ascii=False, allow_nan=False)
            self.connection.execute(
                "INSERT INTO agent_org_events VALUES (?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, agent_id, "org.assignment_changed", actor, payload, timestamp),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        result = self.get(agent_id)
        assert result is not None
        return result

    @_serialized
    def org_chart(self, *, limit: int = 500) -> dict[str, object]:
        """Return a bounded, display-safe node/edge snapshot of the reporting tree."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        total = int(self.connection.execute("SELECT COUNT(*) FROM agents").fetchone()[0])
        agents = self.list_agents(limit=limit)
        included = {str(item["agent_id"]) for item in agents}
        edges = sorted([
            {"manager_id": str(item["reports_to"]), "report_id": str(item["agent_id"])}
            for item in agents if item.get("reports_to") in included
        ], key=lambda edge: (edge["manager_id"], edge["report_id"]))
        unresolved = sum(
            1 for item in agents
            if item.get("reports_to") is not None and item.get("reports_to") not in included
        )
        nodes = [{
            "agent_id": item["agent_id"], "title": item["title"], "role": item["role"],
            "display_name": item["display_name"], "status": item["status"],
            "enabled": item["enabled"], "reports_to": item["reports_to"],
            "capabilities": item["capabilities"],
        } for item in agents]
        return {
            "nodes": nodes, "edges": edges, "total": total,
            "truncated": total > len(agents), "unresolved_parent_count": unresolved,
        }

    @_serialized
    def list_org_events(self, *, agent_id: str | None = None, limit: int = 100) -> list[dict[str, object]]:
        """Read recent organization changes without exposing credentials or task payloads."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if agent_id is not None:
            agent_id = self._text(agent_id, "agent_id")
            rows = self.connection.execute(
                "SELECT * FROM agent_org_events WHERE agent_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (agent_id, limit),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM agent_org_events ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{
            "event_id": row["event_id"], "agent_id": row["agent_id"],
            "event_type": row["event_type"], "actor": row["actor"],
            "payload": json.loads(row["payload_json"]), "created_at": row["created_at"],
        } for row in rows]

    @_serialized
    def record_outcome(self, agent_id: str, *, task_kind: str, succeeded: bool,
                       reviewed: bool = False, review_approved: bool | None = None,
                       now: datetime | None = None) -> None:
        agent_id = self._text(agent_id, "agent_id")
        task_kind = self._text(task_kind, "task_kind", 96)
        if not isinstance(succeeded, bool) or not isinstance(reviewed, bool):
            raise ValueError("succeeded and reviewed must be boolean")
        if review_approved is not None and not isinstance(review_approved, bool):
            raise ValueError("review_approved must be boolean or None")
        if not reviewed and review_approved is not None:
            raise ValueError("review_approved requires reviewed=True")
        if reviewed and review_approved is None:
            raise ValueError("reviewed outcomes require an explicit review_approved value")
        timestamp = self._now(now).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            exists = self.connection.execute("SELECT 1 FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
            if exists is None:
                raise ValueError("agent is not registered")
            self.connection.execute(
                """INSERT INTO agent_outcomes (agent_id, task_kind, completed, succeeded, reviewed, review_approved, updated_at)
                   VALUES (?, ?, 1, ?, ?, ?, ?)
                   ON CONFLICT(agent_id, task_kind) DO UPDATE SET
                   completed=completed+1, succeeded=succeeded+excluded.succeeded,
                   reviewed=reviewed+excluded.reviewed, review_approved=review_approved+excluded.review_approved,
                   updated_at=excluded.updated_at""",
                (agent_id, task_kind, int(succeeded), int(reviewed), int(bool(review_approved)), timestamp),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    @_serialized
    def record_review_outcome(self, agent_id: str, *, task_kind: str, approved: bool,
                              now: datetime | None = None) -> None:
        """Record a review decision without counting the already-completed task twice."""
        agent_id = self._text(agent_id, "agent_id")
        task_kind = self._text(task_kind, "task_kind", 96)
        if not isinstance(approved, bool):
            raise ValueError("approved must be boolean")
        timestamp = self._now(now).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            exists = self.connection.execute("SELECT 1 FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
            if exists is None:
                raise ValueError("agent is not registered")
            self.connection.execute(
                """INSERT INTO agent_outcomes (agent_id, task_kind, completed, succeeded, reviewed, review_approved, updated_at)
                   VALUES (?, ?, 0, 0, 1, ?, ?)
                   ON CONFLICT(agent_id, task_kind) DO UPDATE SET
                   reviewed=reviewed+1, review_approved=review_approved+excluded.review_approved,
                   updated_at=excluded.updated_at""",
                (agent_id, task_kind, int(approved), timestamp),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    @_serialized
    def effectiveness(self, agent_id: str) -> dict[str, object]:
        if self.connection.execute("SELECT 1 FROM agents WHERE agent_id=?", (agent_id,)).fetchone() is None:
            raise ValueError("agent is not registered")
        rows = self.connection.execute(
            "SELECT task_kind, completed, succeeded, reviewed, review_approved FROM agent_outcomes WHERE agent_id=? ORDER BY task_kind",
            (agent_id,),
        ).fetchall()
        completed = sum(int(row["completed"]) for row in rows)
        succeeded = sum(int(row["succeeded"]) for row in rows)
        reviewed = sum(int(row["reviewed"]) for row in rows)
        review_approved = sum(int(row["review_approved"]) for row in rows)
        return {
            "agent_id": agent_id, "completed": completed, "succeeded": succeeded,
            "failed": completed - succeeded, "reviewed": reviewed,
            "review_approved": review_approved,
            "success_rate": succeeded / completed if completed else None,
            "review_pass_rate": review_approved / reviewed if reviewed else None,
            "by_task_kind": [dict(row) for row in rows],
        }
