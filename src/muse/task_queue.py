"""Durable SQLite queue for resumable Muse tasks."""

from __future__ import annotations

import json
import re
import sqlite3
import statistics
import time
import uuid

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class TaskQueue:
    """Small persistent queue; workers are deliberately managed elsewhere."""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.database, timeout=10)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout=10000")
        try:
            self._enable_wal_mode()
        except Exception:
            self._connection.close()
            raise
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                idempotency_key TEXT UNIQUE,
                title TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN
                    ('queued', 'running', 'waiting_approval', 'succeeded', 'failed', 'cancelled')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                lease_owner TEXT,
                lease_expires_at TEXT,
                heartbeat_at TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                priority INTEGER NOT NULL DEFAULT 50,
                estimated_seconds INTEGER NOT NULL DEFAULT 900,
                workload_class TEXT NOT NULL DEFAULT 'general',
                recovery_policy TEXT NOT NULL DEFAULT 'retry',
                depends_on_json TEXT NOT NULL DEFAULT '[]'
            )"""
        )
        # Backward-compatible migration for databases created by earlier Muse versions.
        columns = {
            row["name"] for row in self._connection.execute("PRAGMA table_info(tasks)")
        }
        for name, declaration in (
            ("idempotency_key", "TEXT"),
            ("lease_owner", "TEXT"),
            ("lease_expires_at", "TEXT"),
            ("heartbeat_at", "TEXT"),
            ("attempts", "INTEGER NOT NULL DEFAULT 0"),
            ("priority", "INTEGER NOT NULL DEFAULT 50"),
            ("estimated_seconds", "INTEGER NOT NULL DEFAULT 900"),
            ("workload_class", "TEXT NOT NULL DEFAULT 'general'"),
            ("recovery_policy", "TEXT NOT NULL DEFAULT 'retry'"),
            ("depends_on_json", "TEXT NOT NULL DEFAULT '[]'"),
        ):
            if name not in columns:
                self._connection.execute(f"ALTER TABLE tasks ADD COLUMN {name} {declaration}")
        self._connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_idempotency_key "
            "ON tasks(idempotency_key) WHERE idempotency_key IS NOT NULL"
        )
        if "recovery_policy" not in columns:
            legacy_media = self._connection.execute(
                "SELECT id, payload_json FROM tasks"
            ).fetchall()
            for row in legacy_media:
                try:
                    payload = json.loads(row["payload_json"])
                except (TypeError, ValueError):
                    continue
                if isinstance(payload, dict) and payload.get("kind") == "ffmpeg_transcode":
                    self._connection.execute(
                        "UPDATE tasks SET recovery_policy = 'manual' WHERE id = ?",
                        (row["id"],),
                    )
        self._connection.commit()
        self._connection.execute("""CREATE TABLE IF NOT EXISTS task_events (
            event_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, event_type TEXT NOT NULL,
            actor TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
        )""")
        self._connection.execute("CREATE INDEX IF NOT EXISTS idx_task_events_task_time ON task_events(task_id, created_at, event_id)")
        self._connection.commit()

    def _enable_wal_mode(self) -> None:
        """Enable WAL once, tolerating another concurrent opener doing the same."""
        for attempt in range(10):
            try:
                mode = str(self._connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
                if mode == "wal":
                    return
                mode = str(self._connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
                if mode == "wal":
                    return
                raise sqlite3.OperationalError("database is busy while enabling WAL mode")
            except sqlite3.OperationalError as exc:
                message = str(exc).casefold()
                if "locked" not in message and "busy" not in message:
                    raise
                if attempt == 9:
                    raise
                time.sleep(min(0.05 * (attempt + 1), 0.25))

    def __enter__(self) -> TaskQueue:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": row["id"],
            "idempotency_key": row["idempotency_key"],
            "title": row["title"],            "payload": json.loads(row["payload_json"]),
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "lease_owner": row["lease_owner"],
            "lease_expires_at": row["lease_expires_at"],
            "heartbeat_at": row["heartbeat_at"],
            "attempts": row["attempts"],
            "priority": row["priority"],
            "estimated_seconds": row["estimated_seconds"],
            "workload_class": row["workload_class"],
            "recovery_policy": row["recovery_policy"],
            "depends_on": json.loads(row["depends_on_json"]),
        }
    def enqueue(
        self, title: str, payload: dict[str, Any], *,
        priority: int = 50, estimated_seconds: int | None = None,
        recovery_policy: str = "retry", idempotency_key: str | None = None,
        depends_on: tuple[str, ...] = (), require_approval: bool = False,
        workload_class: str = "general",
    ) -> dict[str, Any]:
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Task title must not be empty")
        if not isinstance(require_approval, bool):
            raise ValueError("require_approval must be a boolean")
        initial_status = "waiting_approval" if require_approval else "queued"
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200
        ):
            raise ValueError("idempotency_key must be non-empty text of at most 200 characters")
        if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 100:
            raise ValueError("priority must be an integer from 0 to 100")
        if not isinstance(workload_class, str) or workload_class not in {"general", "research", "coding", "media", "writing", "operations"}:
            raise ValueError("workload_class must be a supported class")
        if estimated_seconds is None:
            estimated_seconds = self.runtime_estimate_seconds(workload_class=workload_class)["estimated_seconds"]
        if isinstance(estimated_seconds, bool) or not isinstance(estimated_seconds, int) or estimated_seconds <= 0:
            raise ValueError("estimated_seconds must be a positive integer")
        if not isinstance(recovery_policy, str) or recovery_policy not in {"retry", "manual"}:
            raise ValueError("recovery_policy must be 'retry' or 'manual'")
        if not isinstance(depends_on, tuple) or any(
            not isinstance(item, str) or not item.strip() for item in depends_on
        ):
            raise ValueError("depends_on must be a tuple of non-empty task IDs")
        if len(set(depends_on)) != len(depends_on):
            raise ValueError("depends_on must not contain duplicate task IDs")
        if idempotency_key is not None:
            existing = self._connection.execute(
                "SELECT id FROM tasks WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing:
                task = self.get(existing["id"])
                assert task is not None
                return task
        if depends_on:
            placeholders = ",".join("?" for _ in depends_on)
            found = {
                row["id"] for row in self._connection.execute(
                    f"SELECT id FROM tasks WHERE id IN ({placeholders})", depends_on
                ).fetchall()
            }
            missing = sorted(set(depends_on) - found)
            if missing:
                raise ValueError(f"unknown dependency task IDs: {', '.join(missing)}")
        now = self._now()
        task_id = uuid.uuid4().hex
        try:
            self._connection.execute(
                "INSERT INTO tasks (id, idempotency_key, title, payload_json, status, created_at, updated_at, priority, estimated_seconds, workload_class, recovery_policy, depends_on_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, idempotency_key, title.strip(), json.dumps(payload, ensure_ascii=False),
                 initial_status, now, now, priority, estimated_seconds, workload_class, recovery_policy, json.dumps(depends_on)),
            )
            self._record_event(task_id, "ticket.created", "commander", {"title": title.strip(), "priority": priority})
            if require_approval:
                self._record_event(task_id, "approval.requested", "commander", {"required": True})
            self._connection.commit()
        except sqlite3.IntegrityError:
            self._connection.rollback()
            if idempotency_key is None:
                raise
            existing = self._connection.execute(
                "SELECT id FROM tasks WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is None:
                raise
            task = self.get(existing["id"])
            assert task is not None
            return task
        task = self.get(task_id)
        assert task is not None
        return task

    def _record_event(self, task_id: str, event_type: str, actor: str, payload: Any = None) -> None:
        """Append a bounded lifecycle event inside the caller's transaction."""
        if not isinstance(event_type, str) or not event_type.strip() or len(event_type) > 80:
            raise ValueError("event_type must be non-empty text of at most 80 characters")
        if not isinstance(actor, str) or not actor.strip() or len(actor) > 120:
            raise ValueError("actor must be non-empty text of at most 120 characters")
        encoded = json.dumps(payload if payload is not None else {}, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > 16384:
            raise ValueError("task event payload exceeds 16 KiB")
        self._connection.execute(
            "INSERT INTO task_events VALUES (?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, task_id, event_type.strip(), actor.strip(), encoded, self._now()),
        )

    def record_event(self, task_id: str, event_type: str, *, actor: str, payload: Any = None) -> dict[str, Any]:
        """Persist an Assignment, Mandate, Report, or human-decision event."""
        if self.get(task_id) is None:
            raise KeyError(task_id)
        self._record_event(task_id, event_type, actor, payload)
        self._connection.commit()
        return self.events(task_id)[-1]

    def workflow_revision(self, task_id: str) -> int:
        if self.get(task_id) is None:
            raise KeyError(task_id)
        return int(self._connection.execute(
            "SELECT COUNT(*) FROM task_events WHERE task_id=? AND event_type LIKE 'workflow.%'",
            (task_id,),
        ).fetchone()[0])

    def workflow_events(self, task_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        """Read the binding plus recent workflow events from the existing Ticket log."""
        if not isinstance(limit, int) or isinstance(limit, bool) or not 2 <= limit <= 500:
            raise ValueError("workflow event limit must be between 2 and 500")
        if self.get(task_id) is None:
            raise KeyError(task_id)
        first = self._connection.execute(
            "SELECT event_id, task_id, event_type, actor, payload_json, created_at "
            "FROM task_events WHERE task_id=? AND event_type='workflow.bound' ORDER BY rowid LIMIT 1",
            (task_id,),
        ).fetchone()
        tail = self._connection.execute(
            "SELECT event_id, task_id, event_type, actor, payload_json, created_at "
            "FROM task_events WHERE task_id=? AND event_type LIKE 'workflow.%' "
            "AND event_type!='workflow.bound' ORDER BY rowid DESC LIMIT ?",
            (task_id, limit - (1 if first else 0)),
        ).fetchall()
        rows = ([first] if first else []) + list(reversed(tail))
        return [{"event_id": row["event_id"], "task_id": row["task_id"],
                 "event_type": row["event_type"], "actor": row["actor"],
                 "payload": json.loads(row["payload_json"]), "created_at": row["created_at"]}
                for row in rows]

    def workflow_status(self, task_id: str) -> str:
        """Derive workflow control state from the latest durable control event."""
        if self.get(task_id) is None:
            raise KeyError(task_id)
        row = self._connection.execute(
            "SELECT event_type FROM task_events WHERE task_id=? "
            "AND event_type IN ('workflow.paused','workflow.resumed','workflow.cancelled','workflow.completed','workflow.failed') "
            "ORDER BY rowid DESC LIMIT 1", (task_id,)
        ).fetchone()
        if row is None or row["event_type"] == "workflow.resumed":
            return "active"
        return {
            "workflow.paused": "paused",
            "workflow.cancelled": "cancelled",
            "workflow.completed": "completed",
            "workflow.failed": "failed",
        }[row["event_type"]]

    def record_workflow_event(
        self, task_id: str, event_type: str, *, actor: str, payload: Any = None,
        expected_revision: int | None = None, expected_worker_id: str | None = None,
    ) -> dict[str, Any]:
        """Append a workflow event with CAS and optional live-lease fencing."""
        if not isinstance(event_type, str) or not event_type.startswith("workflow.") or len(event_type) > 80:
            raise ValueError("workflow event type must use the workflow.* namespace")
        if expected_revision is not None and (
            isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0
        ):
            raise ValueError("expected_revision must be a non-negative integer")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            task = self.get(task_id)
            if task is None:
                raise KeyError(task_id)
            revision = self.workflow_revision(task_id)
            if expected_revision is not None and revision != expected_revision:
                raise RuntimeError(f"workflow revision conflict: expected {expected_revision}, found {revision}")
            if event_type == "workflow.paused" and (
                task["status"] != "queued" or self.workflow_status(task_id) != "active"
            ):
                raise ValueError("only active queued workflow Tickets can be paused")
            if event_type == "workflow.resumed" and (
                task["status"] != "queued" or self.workflow_status(task_id) != "paused"
            ):
                raise ValueError("only paused queued workflow Tickets can be resumed")
            if event_type == "workflow.cancelled" and task["status"] != "cancelled":
                raise ValueError("workflow cancellation event requires a cancelled Ticket")
            if event_type in {"workflow.bound", "workflow.stage_changed", "workflow.completed", "workflow.failed"} and expected_worker_id is not None:
                if task["status"] != "running" or task["lease_owner"] != expected_worker_id:
                    raise PermissionError("workflow update rejected by stale or foreign Worker lease")
                expiry = datetime.fromisoformat(task["lease_expires_at"])
                if expiry.tzinfo is None or expiry.astimezone(timezone.utc) <= datetime.now(timezone.utc):
                    raise PermissionError("workflow update rejected because Worker lease has expired")
            if event_type in {"workflow.stage_changed", "workflow.completed", "workflow.failed"}:
                if self.workflow_status(task_id) != "active":
                    raise ValueError("terminal or paused workflow cannot accept further stage events")
                if event_type == "workflow.stage_changed" and not expected_worker_id:
                    raise PermissionError("workflow stage update requires a current Worker lease")
            self._record_event(task_id, event_type, actor, payload)
            row = self._connection.execute(
                "SELECT event_id, task_id, event_type, actor, payload_json, created_at "
                "FROM task_events WHERE task_id=? ORDER BY rowid DESC LIMIT 1", (task_id,)
            ).fetchone()
            self._connection.commit()
            return {"event_id": row["event_id"], "task_id": row["task_id"],
                    "event_type": row["event_type"], "actor": row["actor"],
                    "payload": json.loads(row["payload_json"]), "created_at": row["created_at"]}
        except Exception:
            self._connection.rollback()
            raise

    def record_workflow_events(self, items: list[dict[str, Any]], *, actor: str) -> list[dict[str, Any]]:
        """Atomically bind a complete Commander plan across its existing queued Tickets."""
        if not isinstance(items, list) or not 1 <= len(items) <= 32:
            raise ValueError("workflow binding requires 1-32 Ticket events")
        task_ids = [item.get("task_id") for item in items if isinstance(item, dict)]
        if len(task_ids) != len(items) or len(set(task_ids)) != len(task_ids):
            raise ValueError("workflow binding requires unique Ticket IDs")
        if any(item.get("event_type") != "workflow.bound" for item in items):
            raise ValueError("batch workflow binding accepts only workflow.bound events")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            for item in items:
                task = self.get(item["task_id"])
                if task is None:
                    raise KeyError(item["task_id"])
                if task["status"] != "queued" or self.workflow_revision(item["task_id"]) != 0:
                    raise ValueError("all bound Tickets must be queued and not already workflow-bound")
            results = []
            for item in items:
                self._record_event(item["task_id"], item["event_type"], actor, item.get("payload"))
                row = self._connection.execute(
                    "SELECT event_id, task_id, event_type, actor, payload_json, created_at "
                    "FROM task_events WHERE task_id=? ORDER BY rowid DESC LIMIT 1", (item["task_id"],)
                ).fetchone()
                results.append({"event_id": row["event_id"], "task_id": row["task_id"],
                                "event_type": row["event_type"], "actor": row["actor"],
                                "payload": json.loads(row["payload_json"]), "created_at": row["created_at"]})
            self._connection.commit()
            return results
        except Exception:
            self._connection.rollback()
            raise

    def events(self, task_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        rows = self._connection.execute(
            "SELECT event_id, task_id, event_type, actor, payload_json, created_at FROM task_events WHERE task_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (task_id, limit),
        ).fetchall()
        return [{"event_id": row["event_id"], "task_id": row["task_id"],
                 "event_type": row["event_type"], "actor": row["actor"],
                 "payload": json.loads(row["payload_json"]), "created_at": row["created_at"]}
                for row in reversed(rows)]

    def queued_work_items(self) -> list[dict[str, Any]]:
        """Return only scheduling metadata for queued work; omit payloads/titles."""
        rows = self._connection.execute(
            "SELECT id, priority, estimated_seconds FROM tasks "
            "WHERE status='queued' ORDER BY priority DESC, created_at, id"
        ).fetchall()
        return [dict(row) for row in rows]
    def get_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT id FROM tasks WHERE idempotency_key = ?", (key,)
        ).fetchone()
        return self.get(row["id"]) if row else None

    def get(self, task_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return self._decode(row)
    def list_tasks(self, *, kind: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """List recent durable tickets, optionally filtered by payload kind."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if kind is not None and (not isinstance(kind, str) or not kind.strip()):
            raise ValueError("kind must be non-empty text")
        if kind is None:
            rows = self._connection.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM tasks WHERE json_extract(payload_json, '$.kind') = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?", (kind, limit)
            ).fetchall()
        return [task for row in rows if (task := self._decode(row)) is not None]
    def runtime_estimate_seconds(
        self, *, workload_class: str = "general", default_estimate_seconds: int = 900,
        min_samples: int = 3, max_events: int = 4000,
    ) -> dict[str, Any]:
        """Calibrate a conservative class-specific estimate from successful attempts.

        Only lifecycle timestamps and the fixed succeeded status are read. Titles,
        payloads, worker identities and event contents are never returned.
        """
        if not isinstance(workload_class, str) or workload_class not in {"general", "research", "coding", "media", "writing", "operations"}:
            raise ValueError("workload_class must be a supported class")
        for name, value, minimum, maximum in (
            ("default_estimate_seconds", default_estimate_seconds, 1, 86400),
            ("min_samples", min_samples, 1, 100),
            ("max_events", max_events, 2, 20000),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError(f"{name} is outside its allowed range")
        rows = self._connection.execute(
            """SELECT task_id, event_type, payload_json, created_at FROM (
                SELECT task_events.task_id, task_events.event_type,
                       task_events.payload_json, task_events.created_at,
                       task_events.rowid AS event_rowid
                FROM task_events
                JOIN tasks ON tasks.id = task_events.task_id
                WHERE task_events.event_type IN ('assignment.claimed', 'ticket.status_changed')
                  AND tasks.workload_class = ?
                ORDER BY task_events.created_at DESC, task_events.rowid DESC LIMIT ?
            ) ORDER BY created_at ASC, event_rowid ASC""",
            (workload_class, max_events),
        ).fetchall()
        active_claims: dict[str, datetime] = {}
        durations: list[int] = []
        for row in rows:
            try:
                timestamp = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                timestamp = timestamp.astimezone(timezone.utc)
                if row["event_type"] == "assignment.claimed":
                    active_claims[row["task_id"]] = timestamp
                    continue
                payload = json.loads(row["payload_json"])
                if not isinstance(payload, dict) or payload.get("status") != "succeeded":
                    continue
                started = active_claims.pop(row["task_id"], None)
                if started is None:
                    continue
                duration = (timestamp - started).total_seconds()
                if 1 <= duration <= 86400:
                    durations.append(int(round(duration)))
            except (TypeError, ValueError, AttributeError):
                continue
        sample_count = len(durations)
        if sample_count < min_samples:
            return {
                "estimated_seconds": default_estimate_seconds,
                "median_seconds": None, "sample_count": sample_count,
                "source": "configured_default", "minimum_samples": min_samples,
                "workload_class": workload_class,
            }
        median_seconds = int(round(statistics.median(durations)))
        return {
            "estimated_seconds": median_seconds,
            "median_seconds": median_seconds, "sample_count": sample_count,
            "source": "historical_median", "minimum_samples": min_samples,
            "workload_class": workload_class,
        }

    def aggregate_attempt_metrics(self, *, since: str) -> dict[str, int]:
        """Return recent task/retry aggregates without reading titles or payloads."""
        if not isinstance(since, str) or not since.strip():
            raise ValueError("since must be a non-empty ISO-8601 timestamp")
        try:
            cutoff = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("since must be a valid ISO-8601 timestamp") from exc
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("since must include a timezone")
        stamp = cutoff.astimezone(timezone.utc).isoformat()
        row = self._connection.execute(
            "SELECT COUNT(*) AS tasks_seen, "
            "COALESCE(SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END), 0) AS failed_tasks, "
            "COALESCE(SUM(CASE WHEN attempts > 1 THEN 1 ELSE 0 END), 0) AS retried_tasks, "
            "COALESCE(SUM(CASE WHEN attempts > 1 THEN attempts - 1 ELSE 0 END), 0) AS retry_attempts "
            "FROM tasks WHERE updated_at >= ?",
            (stamp,),
        ).fetchone()
        return {key: int(row[key]) for key in ("tasks_seen", "failed_tasks", "retried_tasks", "retry_attempts")}

    def aggregate_failure_metrics(
        self, *, since: str, max_events: int = 20000
    ) -> dict[str, Any]:
        """Aggregate sanitized failure classifications without exposing task content."""
        if not isinstance(since, str) or not since.strip():
            raise ValueError("since must be a non-empty ISO-8601 timestamp")
        try:
            cutoff = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("since must be a valid ISO-8601 timestamp") from exc
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("since must include a timezone")
        if isinstance(max_events, bool) or not isinstance(max_events, int) or not 1 <= max_events <= 20000:
            raise ValueError("max_events must be an integer between 1 and 20000")
        rows = self._connection.execute(
            "SELECT payload_json FROM task_events WHERE event_type='failure.classified' "
            "AND created_at >= ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (cutoff.astimezone(timezone.utc).isoformat(), max_events),
        ).fetchall()
        by_code: dict[str, int] = {}
        by_code_detail: dict[str, dict[str, Any]] = {}
        by_category: dict[str, int] = {}
        retryable = non_retryable = 0
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            code = payload.get("error_code")
            category = payload.get("failure_category")
            can_retry = payload.get("retryable")
            if not isinstance(code, str) or not isinstance(category, str) or not isinstance(can_retry, bool):
                continue
            by_code[code] = by_code.get(code, 0) + 1
            by_category[category] = by_category.get(category, 0) + 1
            detail = by_code_detail.setdefault(code, {
                "category": category, "occurrences": 0, "retryable": 0, "non_retryable": 0,
            })
            if detail["category"] != category:
                detail["category"] = "mixed"
            detail["occurrences"] += 1
            if can_retry:
                retryable += 1
                detail["retryable"] += 1
            else:
                non_retryable += 1
                detail["non_retryable"] += 1
        return {
            "total_failures": sum(by_code.values()),
            "by_code": dict(sorted(by_code.items())),
            "by_code_detail": {key: by_code_detail[key] for key in sorted(by_code_detail)},
            "by_category": dict(sorted(by_category.items())),
            "retryable": retryable,
            "non_retryable": non_retryable,
            "sample_limit": max_events,
        }

    def snapshot(self, *, limit: int = 20) -> dict[str, Any]:
        """Return a payload-free operational view of queue health and active work."""
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        rows = self._connection.execute(
            "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
        ).fetchall()
        counts = {
            status: 0 for status in (
                "queued", "running", "waiting_approval", "succeeded", "failed", "cancelled"
            )
        }
        counts.update({row["status"]: row["count"] for row in rows})
        active = self._connection.execute(
            "SELECT id, title, status, updated_at, lease_owner, lease_expires_at, attempts, depends_on_json "
            "FROM tasks WHERE status IN ('queued', 'running', 'waiting_approval') "
            "ORDER BY created_at, id LIMIT ?",
            (limit,),
        ).fetchall()
        active_items = []
        for row in active:
            item = dict(row)
            item["depends_on"] = json.loads(item.pop("depends_on_json"))
            active_items.append(item)
        return {
            "counts": counts,
            "active": active_items,
            "active_truncated": counts["queued"] + counts["running"] + counts["waiting_approval"] > len(active),
        }

    def _recover_expired_locked(self, timestamp: str, max_attempts: int) -> int:
        rows = self._connection.execute(
            "SELECT id, payload_json, attempts, recovery_policy, lease_owner "
            "FROM tasks WHERE status = 'running' AND lease_expires_at IS NOT NULL "
            "AND lease_expires_at <= ?",
            (timestamp,),
        ).fetchall()
        recovered = 0
        for row in rows:
            payload_json = row["payload_json"]
            if row["attempts"] >= max_attempts:
                status = "failed"
            elif row["recovery_policy"] == "manual":
                payload = json.loads(payload_json)
                payload.pop("_approved", None)
                payload.pop("_approval", None)
                payload["_recovery"] = {
                    "reason": "worker_lease_expired",
                    "previous_worker": row["lease_owner"],
                    "detected_at": timestamp,
                    "requires_review": True,
                }
                payload_json = json.dumps(payload, ensure_ascii=False)
                status = "waiting_approval"
            else:
                status = "queued"
            cursor = self._connection.execute(
                "UPDATE tasks SET status = ?, payload_json = ?, lease_owner = NULL, "
                "lease_expires_at = NULL, heartbeat_at = NULL, updated_at = ? "
                "WHERE id = ? AND status = 'running'",
                (status, payload_json, timestamp, row["id"]),
            )
            recovered += cursor.rowcount
            if cursor.rowcount and status == "failed":
                self._record_event(row["id"], "ticket.status_changed", "queue-recovery", {"status": "failed"})
                self._record_event(row["id"], "failure.classified", "queue-recovery", {
                    "error_code": "worker.attempt_limit", "failure_category": "worker",
                    "retryable": False,
                    "recovery_guidance": "Inspect the expired Worker attempts and lease history before manually re-queuing this Ticket.",
                })
        return recovered

    def recover_expired(
        self, *, now: str | None = None, max_attempts: int = 3
    ) -> int:
        """Recover expired leases; non-idempotent work is paused for human review."""
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        timestamp = now or self._now()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            recovered = self._recover_expired_locked(timestamp, max_attempts)
            self._connection.commit()
            return recovered
        except Exception:
            self._connection.rollback()
            raise
    def claim_next(
        self, *, worker_id: str = "legacy-worker", lease_seconds: int = 60,
        max_attempts: int = 3, task_id: str | None = None,
    ) -> dict[str, Any] | None:
        if task_id is not None and (not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 128):
            raise ValueError("task_id must be non-empty text of at most 128 characters")
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be greater than zero")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        now = datetime.now(timezone.utc)
        timestamp = now.isoformat()
        expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._recover_expired_locked(timestamp, max_attempts)
            query = "SELECT id, payload_json, depends_on_json FROM tasks WHERE status = 'queued'"
            parameters: tuple[Any, ...] = ()
            if task_id is not None:
                query += " AND id = ?"
                parameters = (task_id,)
            query += " ORDER BY priority DESC, created_at, id"
            rows = self._connection.execute(query, parameters).fetchall()
            task_id = None
            for candidate in rows:
                if self.workflow_status(candidate["id"]) in {"paused", "cancelled"}:
                    continue
                dependencies = json.loads(candidate["depends_on_json"])
                if dependencies:
                    placeholders = ",".join("?" for _ in dependencies)
                    dependency_rows = self._connection.execute(
                        f"SELECT id, status FROM tasks WHERE id IN ({placeholders})",
                        dependencies,
                    ).fetchall()
                    dependency_status = {row["id"]: row["status"] for row in dependency_rows}
                    failed_dependencies = sorted(
                        dependency_id for dependency_id, status in dependency_status.items()
                        if status in {"failed", "cancelled"}
                    )
                    if failed_dependencies:
                        payload = json.loads(candidate["payload_json"])
                        payload["_dependency_failure"] = {"dependencies": failed_dependencies}
                        self._connection.execute(
                            "UPDATE tasks SET status='failed', payload_json=?, updated_at=? "
                            "WHERE id=? AND status='queued'",
                            (json.dumps(payload, ensure_ascii=False), timestamp, candidate["id"]),
                        )
                        self._record_event(candidate["id"], "ticket.status_changed", worker_id, {"status": "failed"})
                        self._record_event(candidate["id"], "failure.classified", worker_id, {
                            "error_code": "dependency.failed", "failure_category": "dependency",
                            "retryable": False,
                            "recovery_guidance": "Resolve or replace the failed upstream Ticket before re-queuing this dependent task.",
                        })
                        continue
                    if any(status != "succeeded" for status in dependency_status.values()):
                        continue
                task_id = candidate["id"]
                break
            if task_id is None:
                self._connection.commit()
                return None
            self._connection.execute(
                "UPDATE tasks SET status = 'running', updated_at = ?, lease_owner = ?, "
                "lease_expires_at = ?, heartbeat_at = ?, attempts = attempts + 1 "
                "WHERE id = ? AND status = 'queued'",
                (timestamp, worker_id, expires, timestamp, task_id),
            )
            self._record_event(task_id, "assignment.claimed", worker_id, {"attempt": int(self._connection.execute("SELECT attempts FROM tasks WHERE id=?", (task_id,)).fetchone()[0])})
            self._connection.commit()
            return self.get(task_id)
        except Exception:
            self._connection.rollback()
            raise

    def heartbeat(
        self, task_id: str, *, worker_id: str, lease_seconds: int = 60
    ) -> dict[str, Any] | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be greater than zero")
        now = datetime.now(timezone.utc)
        timestamp = now.isoformat()
        expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        cursor = self._connection.execute(
            "UPDATE tasks SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ? "
            "WHERE id = ? AND status = 'running' AND lease_owner = ?",
            (timestamp, expires, timestamp, task_id, worker_id),
        )
        self._connection.commit()
        return self.get(task_id) if cursor.rowcount == 1 else None

    def finish(
        self, task_id: str, *, status: str, result: Any = None,
        worker_id: str | None = None,
    ) -> dict[str, Any] | None:
        if status not in {"succeeded", "failed", "cancelled", "waiting_approval"}:
            raise ValueError("invalid terminal or paused task status")
        now = self._now()
        owner_clause = " AND lease_owner = ?" if worker_id is not None else ""
        parameters: tuple[Any, ...] = (status, now, task_id)
        if worker_id is not None:
            parameters += (worker_id,)
        cursor = self._connection.execute(
            "UPDATE tasks SET status = ?, updated_at = ?, lease_owner = NULL, "
            "lease_expires_at = NULL, heartbeat_at = NULL "
            "WHERE id = ? AND status = 'running'" + owner_clause,
            parameters,
        )
        if cursor.rowcount != 1:
            self._connection.rollback()
            return None
        self._record_event(task_id, "ticket.status_changed", worker_id or "worker", {"status": status})
        if status == "failed" and isinstance(result, dict):
            error_code = result.get("error_code")
            failure_category = result.get("failure_category")
            retryable = result.get("retryable")
            recovery_guidance = result.get("recovery_guidance")
            allowed_categories = {
                "authorization", "configuration", "dependency", "execution", "input",
                "provider", "review", "storage", "system", "handler", "worker",
            }
            if (
                isinstance(error_code, str) and len(error_code) <= 80
                and re.fullmatch(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*", error_code)
                and isinstance(failure_category, str) and failure_category in allowed_categories
                and isinstance(retryable, bool)
                and isinstance(recovery_guidance, str) and recovery_guidance.strip()
                and len(recovery_guidance) <= 240
            ):
                self._record_event(task_id, "failure.classified", worker_id or "worker", {
                    "error_code": error_code, "failure_category": failure_category,
                    "retryable": retryable, "recovery_guidance": recovery_guidance.strip(),
                })
        if result is not None:
            self._record_event(task_id, "report.submitted", worker_id or "worker", {"has_result": True})
            row = self._connection.execute(
                "SELECT payload_json FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            payload = json.loads(row["payload_json"])
            payload["_result"] = result
            self._connection.execute(
                "UPDATE tasks SET payload_json = ? WHERE id = ?",
                (json.dumps(payload, ensure_ascii=False), task_id),
            )
        self._connection.commit()
        return self.get(task_id)

    def cancel(
        self, task_id: str, *, cancelled_by: str, reason: str = "",
        workflow_expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Cancel work that has not started; running handlers require cooperative stop."""
        if not isinstance(cancelled_by, str) or not cancelled_by.strip() or len(cancelled_by) > 120:
            raise ValueError("cancelled_by must be non-empty text of at most 120 characters")
        if not isinstance(reason, str) or len(reason) > 500:
            raise ValueError("reason must be text of at most 500 characters")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._connection.execute(
                "SELECT status, payload_json FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None or row["status"] not in {"queued", "waiting_approval"}:
                raise ValueError("only queued or waiting_approval tasks can be cancelled")
            if workflow_expected_revision is not None:
                if (isinstance(workflow_expected_revision, bool)
                        or not isinstance(workflow_expected_revision, int)
                        or workflow_expected_revision < 0):
                    raise ValueError("workflow_expected_revision must be a non-negative integer")
                actual_revision = self.workflow_revision(task_id)
                if actual_revision != workflow_expected_revision:
                    raise RuntimeError(
                        f"workflow revision conflict: expected {workflow_expected_revision}, found {actual_revision}"
                    )
            now = self._now()
            payload = json.loads(row["payload_json"])
            payload["_cancellation"] = {
                "cancelled_by": cancelled_by.strip(),
                "cancelled_at": now,
                "reason": reason.strip(),
            }
            cursor = self._connection.execute(
                "UPDATE tasks SET status = 'cancelled', payload_json = ?, updated_at = ?, "
                "lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL "
                "WHERE id = ? AND status IN ('queued', 'waiting_approval')",
                (json.dumps(payload, ensure_ascii=False), now, task_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("task is no longer cancellable")
            self._record_event(task_id, "ticket.cancelled", cancelled_by.strip(), {"reason": reason.strip()})
            if workflow_expected_revision is not None:
                self._record_event(
                    task_id, "workflow.cancelled", cancelled_by.strip(),
                    {"stop_reason": reason.strip() or "operator_cancel", "next_action": None},
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        task = self.get(task_id)
        assert task is not None
        return task

    def approve(self, task_id: str, *, approved_by: str) -> dict[str, Any]:
        """Approve a paused task and return it to the queue for a fresh claim."""
        if not approved_by.strip():
            raise ValueError("approved_by must not be empty")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._connection.execute(
                "SELECT payload_json FROM tasks WHERE id = ? AND status = 'waiting_approval'",
                (task_id,),
            ).fetchone()
            if row is None:
                raise ValueError("task is not waiting for approval")
            payload = json.loads(row["payload_json"])
            if (
                payload.get("kind") == "contextual_inquiry_review"
                and payload.get("requires_context_reentry") is True
                and payload.get("case_context_attached") is not True
            ):
                raise PermissionError(
                    "contextual inquiry review cannot be approved until its case context is attached"
                )
            payload["_approved"] = True
            payload["_approval"] = {"approved_by": approved_by.strip(), "approved_at": self._now()}
            cursor = self._connection.execute(
                "UPDATE tasks SET payload_json = ?, status = 'queued', updated_at = ? "
                "WHERE id = ? AND status = 'waiting_approval'",
                (json.dumps(payload, ensure_ascii=False), self._now(), task_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("task is not waiting for approval")
            self._record_event(task_id, "human.decision", approved_by.strip(), {"decision": "approved"})
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        task = self.get(task_id)
        assert task is not None
        return task