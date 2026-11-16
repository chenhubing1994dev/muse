"""Persistent per-thread conversation checkpoints for Muse."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ConversationStore:
    """Store the latest message checkpoint for each independent conversation thread."""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.database, timeout=10)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS conversation_checkpoints (
                thread_id TEXT PRIMARY KEY,
                messages_json TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            )"""
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> ConversationStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def save(self, thread_id: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("thread_id must not be empty")
        if not isinstance(messages, list):
            raise ValueError("messages must be a list")
        serialized = json.dumps(messages, ensure_ascii=False)
        now = self._now()
        self._connection.execute(
            """INSERT INTO conversation_checkpoints(thread_id, messages_json, revision, updated_at)
               VALUES (?, ?, 1, ?)
               ON CONFLICT(thread_id) DO UPDATE SET
                 messages_json = excluded.messages_json,
                 revision = conversation_checkpoints.revision + 1,
                 updated_at = excluded.updated_at""",
            (thread_id.strip(), serialized, now),
        )
        self._connection.commit()
        return self.load(thread_id.strip())  # type: ignore[return-value]

    def load(self, thread_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM conversation_checkpoints WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "thread_id": row["thread_id"],
            "messages": json.loads(row["messages_json"]),
            "revision": row["revision"],
            "updated_at": row["updated_at"],
        }

    def list_threads(self, limit: int = 50) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        rows = self._connection.execute(
            "SELECT thread_id, revision, updated_at FROM conversation_checkpoints "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
