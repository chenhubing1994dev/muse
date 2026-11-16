"""Durable crash-recovery journal for bounded source-edit transactions."""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


class EvolutionJournal:
    TERMINAL = frozenset({"committed", "rolled_back"})

    def __init__(self, database: str | Path, *, progress_database: str | Path | None = None) -> None:
        self.database = str(database)
        self.progress_database = str(progress_database) if progress_database is not None else None
        if self.database == ":memory:":
            raise ValueError("evolution journal must use a durable on-disk database")
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS evolution_transactions (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evolution_transaction_files (
                    transaction_id TEXT NOT NULL REFERENCES evolution_transactions(id) ON DELETE CASCADE,
                    relative_path TEXT NOT NULL,
                    original_bytes BLOB NOT NULL,
                    original_sha256 TEXT NOT NULL,
                    candidate_sha256 TEXT NOT NULL,
                    PRIMARY KEY (transaction_id, relative_path)
                );
                CREATE INDEX IF NOT EXISTS idx_evolution_transactions_status
                    ON evolution_transactions(status, created_at);
                CREATE TABLE IF NOT EXISTS evolution_transaction_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    transaction_id TEXT NOT NULL REFERENCES evolution_transactions(id) ON DELETE CASCADE,
                    task_id TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_evolution_transaction_events
                    ON evolution_transaction_events(transaction_id, sequence);
                """
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _publish_progress_event(self, transaction_id: str, status: str) -> None:
        if self.progress_database is None:
            return
        from muse.progress import ProgressEventStore

        if status == "applying":
            event = "started"
        elif status == "committed":
            event = "completed"
        elif status in {"rolled_back", "blocked"}:
            event = "failed"
        else:
            event = "progress"
        with ProgressEventStore(self.progress_database) as progress:
            progress.record(
                "evolution", transaction_id, event,
                summary=f"Code evolution transaction: {status}",
            )

    def begin(self, *, task_id: str, proposal_id: str, files: list[dict[str, Any]]) -> str:
        transaction_id, now = uuid.uuid4().hex, self._now()
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO evolution_transactions VALUES (?, ?, ?, 'applying', ?, ?)",
                (transaction_id, task_id, proposal_id, now, now),
            )
            connection.execute(
                """INSERT INTO evolution_transaction_events
                   (transaction_id, task_id, proposal_id, status, created_at)
                   VALUES (?, ?, ?, 'applying', ?)""",
                (transaction_id, task_id, proposal_id, now),
            )
            for item in files:
                connection.execute(
                    "INSERT INTO evolution_transaction_files VALUES (?, ?, ?, ?, ?)",
                    (transaction_id, item["relative_path"], sqlite3.Binary(item["original_bytes"]),
                     item["original_sha256"], item["candidate_sha256"]),
                )
        self._publish_progress_event(transaction_id, "applying")
        return transaction_id

    def update_status(self, transaction_id: str, status: str) -> None:
        if status not in {"applying", "testing", "reviewing", "promotion_pending", "committed", "rolled_back", "blocked"}:
            raise ValueError("unsupported evolution journal status")
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE evolution_transactions SET status=?, updated_at=? WHERE id=?",
                (status, self._now(), transaction_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("evolution journal transaction does not exist")
            transaction = connection.execute(
                "SELECT task_id, proposal_id FROM evolution_transactions WHERE id=?",
                (transaction_id,),
            ).fetchone()
            connection.execute(
                """INSERT INTO evolution_transaction_events
                   (transaction_id, task_id, proposal_id, status, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (transaction_id, transaction["task_id"], transaction["proposal_id"], status, self._now()),
            )
        self._publish_progress_event(transaction_id, status)

    def list_incomplete(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM evolution_transactions WHERE status NOT IN ('committed','rolled_back') ORDER BY created_at,id"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_transaction(self, transaction_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM evolution_transactions WHERE id=?", (transaction_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def get_files(self, transaction_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM evolution_transaction_files WHERE transaction_id=? ORDER BY relative_path",
                (transaction_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_events(self, *, transaction_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Return the append-only lifecycle history for one evolution transaction."""
        if not isinstance(transaction_id, str) or not transaction_id.strip() or len(transaction_id) > 160:
            raise ValueError("transaction_id must be non-empty bounded text")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT sequence, transaction_id, task_id, proposal_id, status, created_at
                   FROM evolution_transaction_events WHERE transaction_id=?
                   ORDER BY sequence DESC LIMIT ?""",
                (transaction_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]
