"""Privacy-conscious structured progress events for Muse workflows."""

from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CATEGORIES = frozenset({"task", "agent", "research", "test", "review", "system", "evolution"})
EVENTS = frozenset({"started", "progress", "waiting_approval", "completed", "failed", "cancelled", "interrupted"})

class ProgressEventStore:
    """Persist bounded operational summaries, never task payloads or chat content."""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.database, timeout=10)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout=10000")
        journal_mode = self._connection.execute("PRAGMA journal_mode").fetchone()[0]
        if str(journal_mode).lower() != "wal":
            self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS progress_events (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                event TEXT NOT NULL,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL,
                error_code TEXT
            )"""
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_progress_created ON progress_events(created_at, id)"
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS worker_runtime (
                worker_id TEXT PRIMARY KEY,
                pid INTEGER NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('active', 'stopped')),
                started_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS watchdog_checks (
                id TEXT PRIMARY KEY,
                checked_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('healthy', 'warning')),
                expired_leases INTEGER NOT NULL,
                stalled_tasks INTEGER NOT NULL,
                stale_workers INTEGER NOT NULL,
                dead_workers INTEGER NOT NULL,
                queued_tasks INTEGER NOT NULL,
                failed_tasks INTEGER NOT NULL,
                waiting_approval INTEGER NOT NULL,
                endpoint_unavailable INTEGER NOT NULL
            )"""
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_watchdog_checks_time ON watchdog_checks(checked_at, id)"
        )
        self._connection.commit()

        columns = {row["name"] for row in self._connection.execute("PRAGMA table_info(progress_events)")}
        if "error_code" not in columns:
            self._connection.execute("ALTER TABLE progress_events ADD COLUMN error_code TEXT")
        self._connection.commit()

    def __enter__(self) -> ProgressEventStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _runtime_timestamp(value: datetime | None = None) -> str:
        current = value or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc).isoformat()

    def register_worker(
        self, worker_id: str, *, pid: int, now: datetime | None = None
    ) -> None:
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 160:
            raise ValueError("worker_id must be non-empty text of at most 160 characters")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1:
            raise ValueError("pid must be a positive integer")
        timestamp = self._runtime_timestamp(now)
        self._connection.execute(
            """INSERT INTO worker_runtime (worker_id, pid, state, started_at, heartbeat_at, updated_at)
               VALUES (?, ?, 'active', ?, ?, ?)
               ON CONFLICT(worker_id) DO UPDATE SET pid=excluded.pid, state='active',
               started_at=excluded.started_at, heartbeat_at=excluded.heartbeat_at,
               updated_at=excluded.updated_at""",
            (worker_id.strip(), pid, timestamp, timestamp, timestamp),
        )
        self._connection.commit()

    def heartbeat_worker(
        self, worker_id: str, *, now: datetime | None = None
    ) -> bool:
        timestamp = self._runtime_timestamp(now)
        cursor = self._connection.execute(
            "UPDATE worker_runtime SET heartbeat_at=?, updated_at=? "
            "WHERE worker_id=? AND state='active'",
            (timestamp, timestamp, worker_id),
        )
        self._connection.commit()
        return cursor.rowcount == 1

    def stop_worker(
        self, worker_id: str, *, now: datetime | None = None
    ) -> bool:
        timestamp = self._runtime_timestamp(now)
        cursor = self._connection.execute(
            "UPDATE worker_runtime SET state='stopped', updated_at=? "
            "WHERE worker_id=? AND state='active'",
            (timestamp, worker_id),
        )
        self._connection.commit()
        return cursor.rowcount == 1

    def list_workers(
        self, *, now: datetime | None = None,
        stale_after_seconds: int = 90, limit: int = 20,
    ) -> list[dict[str, Any]]:
        if isinstance(stale_after_seconds, bool) or not isinstance(stale_after_seconds, int) or stale_after_seconds < 1:
            raise ValueError("stale_after_seconds must be a positive integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        rows = self._connection.execute(
            "SELECT worker_id, pid, state, started_at, heartbeat_at, updated_at "
            "FROM worker_runtime ORDER BY heartbeat_at DESC, worker_id LIMIT ?",
            (limit,),
        ).fetchall()
        workers = []
        for row in rows:
            item = dict(row)
            try:
                heartbeat = datetime.fromisoformat(item["heartbeat_at"])
                if heartbeat.tzinfo is None:
                    heartbeat = heartbeat.replace(tzinfo=timezone.utc)
                stale = item["state"] == "active" and (
                    current.astimezone(timezone.utc) - heartbeat.astimezone(timezone.utc)
                ).total_seconds() > stale_after_seconds
            except (TypeError, ValueError):
                stale = item["state"] == "active"
            item["stale"] = stale
            workers.append(item)
        return workers

    def record_watchdog_snapshot(
        self, *, status: str, expired_leases: int = 0, stalled_tasks: int = 0,
        stale_workers: int = 0, dead_workers: int = 0, queued_tasks: int = 0,
        failed_tasks: int = 0, waiting_approval: int = 0, endpoint_unavailable: int = 0,
        now: datetime | None = None,
    ) -> None:
        """Persist aggregate-only Watchdog observations for bounded trend analysis."""
        if not isinstance(status, str) or status not in {"healthy", "warning"}:
            raise ValueError("status must be healthy or warning")
        if now is not None and not isinstance(now, datetime):
            raise ValueError("now must be a datetime")
        metrics = {
            "expired_leases": expired_leases, "stalled_tasks": stalled_tasks,
            "stale_workers": stale_workers, "dead_workers": dead_workers,
            "queued_tasks": queued_tasks, "failed_tasks": failed_tasks,
            "waiting_approval": waiting_approval, "endpoint_unavailable": endpoint_unavailable,
        }
        for name, value in metrics.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        self._connection.execute(
            """INSERT INTO watchdog_checks (
                id, checked_at, status, expired_leases, stalled_tasks, stale_workers,
                dead_workers, queued_tasks, failed_tasks, waiting_approval, endpoint_unavailable
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (uuid.uuid4().hex, self._runtime_timestamp(now), status, *metrics.values()),
        )
        self._connection.commit()

    def watchdog_trend(
        self, *, now: datetime | None = None, window_hours: int = 24,
    ) -> dict[str, Any]:
        """Compare aggregate Watchdog checks in adjacent windows without exposing row identities."""
        if isinstance(window_hours, bool) or not isinstance(window_hours, int) or not 1 <= window_hours <= 168:
            raise ValueError("window_hours must be between 1 and 168")
        current = now or datetime.now(timezone.utc)
        if not isinstance(current, datetime):
            raise ValueError("now must be a datetime")
        if current.tzinfo is None or current.utcoffset() is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc)
        current_start = current - timedelta(hours=window_hours)
        previous_start = current - timedelta(hours=window_hours * 2)
        metric_names = (
            "expired_leases", "stalled_tasks", "stale_workers", "dead_workers",
            "queued_tasks", "failed_tasks", "waiting_approval", "endpoint_unavailable",
        )

        def summarize(start: datetime, end: datetime) -> dict[str, int]:
            expressions = ["COUNT(*) AS checks", "SUM(CASE WHEN status='warning' THEN 1 ELSE 0 END) AS warning_checks"]
            expressions.extend(f"COALESCE(SUM({name}), 0) AS {name}" for name in metric_names)
            row = self._connection.execute(
                f"SELECT {', '.join(expressions)} FROM watchdog_checks WHERE checked_at >= ? AND checked_at < ?",
                (start.isoformat(), end.isoformat()),
            ).fetchone()
            return {key: int(row[key] or 0) for key in ("checks", "warning_checks", *metric_names)}

        current_summary = summarize(current_start, current + timedelta(microseconds=1))
        previous_summary = summarize(previous_start, current_start)
        trend = "insufficient_data"
        if current_summary["checks"] >= 2 and previous_summary["checks"] >= 2:
            current_rate = current_summary["warning_checks"] / current_summary["checks"]
            previous_rate = previous_summary["warning_checks"] / previous_summary["checks"]
            if current_rate <= previous_rate - 0.05:
                trend = "improving"
            elif current_rate >= previous_rate + 0.05:
                trend = "worsening"
            else:
                trend = "stable"
        return {
            "window_hours": window_hours, "trend": trend,
            "current": current_summary, "previous": previous_summary,
        }

    def record(
        self, category: str, subject_id: str, event: str, *,
        summary: str = "", error_code: str | None = None,
    ) -> dict[str, Any]:
        if category not in CATEGORIES:
            raise ValueError(f"unsupported progress category: {category!r}")
        if event not in EVENTS:
            raise ValueError(f"unsupported progress event: {event!r}")
        if not isinstance(subject_id, str) or not subject_id.strip() or len(subject_id) > 160:
            raise ValueError("subject_id must be non-empty text of at most 160 characters")
        if not isinstance(summary, str) or len(summary) > 500:
            raise ValueError("summary must be text of at most 500 characters")
        if error_code is not None:
            if event != "failed" or not isinstance(error_code, str) or len(error_code) > 80 or not re.fullmatch(
                r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*", error_code
            ):
                raise ValueError("error_code must be a bounded lowercase code on failed events")
        event_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        self._connection.execute(
            "INSERT INTO progress_events (id, category, subject_id, event, summary, created_at, error_code) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_id, category, subject_id.strip(), event, summary.strip(), created_at, error_code),
        )
        self._connection.commit()
        row = self._connection.execute(
            "SELECT * FROM progress_events WHERE id = ?", (event_id,)
        ).fetchone()
        return dict(row)


    def aggregate_event_counts(self, *, since: datetime) -> dict[tuple[str, str], int]:
        """Return bounded event counts only; summaries and subject identifiers never leave SQLite."""
        if not isinstance(since, datetime):
            raise ValueError("since must be a datetime")
        if since.tzinfo is None or since.utcoffset() is None:
            since = since.replace(tzinfo=timezone.utc)
        cutoff = since.astimezone(timezone.utc).isoformat()
        rows = self._connection.execute(
            "SELECT category, event, COUNT(*) AS count FROM progress_events "
            "WHERE created_at >= ? GROUP BY category, event ORDER BY category, event",
            (cutoff,),
        ).fetchall()
        return {(row["category"], row["event"]): int(row["count"]) for row in rows}

    def aggregate_failure_codes(self, *, since: datetime) -> dict[str, int]:
        """Return recent failure-code counts only, never event summaries or subject IDs."""
        if not isinstance(since, datetime):
            raise ValueError("since must be a datetime")
        if since.tzinfo is None or since.utcoffset() is None:
            since = since.replace(tzinfo=timezone.utc)
        cutoff = since.astimezone(timezone.utc).isoformat()
        rows = self._connection.execute(
            "SELECT error_code, COUNT(*) AS count FROM progress_events "
            "WHERE event='failed' AND error_code IS NOT NULL AND created_at >= ? "
            "GROUP BY error_code ORDER BY error_code",
            (cutoff,),
        ).fetchall()
        return {row["error_code"]: int(row["count"]) for row in rows}

    def list_events(
        self, *, category: str | None = None, subject_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if category is not None and category not in CATEGORIES:
            raise ValueError(f"unsupported progress category: {category!r}")
        clauses: list[str] = []
        parameters: list[Any] = []
        if category is not None:
            clauses.append("category = ?")
            parameters.append(category)
        if subject_id is not None:
            clauses.append("subject_id = ?")
            parameters.append(subject_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._connection.execute(
            "SELECT * FROM progress_events" + where + " ORDER BY created_at DESC, id DESC LIMIT ?",
            (*parameters, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def new_evolution_cycle_id() -> str:
    """Create a stable, opaque identifier for one cross-module Muse cycle."""
    return "cycle_" + uuid.uuid4().hex


def record_evolution_cycle_event(
    database: str | Path, cycle_id: str, event: str, *,
    summary: str = "", error_code: str | None = None,
) -> dict[str, Any]:
    """Append one bounded lifecycle event under a shared Evolution Cycle ID."""
    if not isinstance(cycle_id, str) or not re.fullmatch(r"cycle_[0-9a-f]{32}", cycle_id):
        raise ValueError("cycle_id must be generated by new_evolution_cycle_id")
    with ProgressEventStore(database) as store:
        return store.record("evolution", cycle_id, event, summary=summary, error_code=error_code)

