"""SQLite-backed, user-auditable long-term memory for Muse."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MEMORY_KINDS = frozenset({"profile", "preference", "fact", "task", "interest", "episode", "atomic_fact", "foresight", "skill", "case"})
MEMORY_TRACKS = frozenset({"user", "agent"})

class MemoryStore:
    """Persist searchable memories with provenance and revision history metadata."""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.database, timeout=10)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL,
                confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
                kind TEXT NOT NULL DEFAULT 'fact',
                track TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                deleted_at TEXT
            )"""
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS memory_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER NOT NULL,
                revision INTEGER NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL,
                confidence REAL NOT NULL,
                kind TEXT NOT NULL DEFAULT 'fact',
                track TEXT NOT NULL DEFAULT 'user',
                changed_at TEXT NOT NULL,
                FOREIGN KEY(memory_id) REFERENCES memories(id)
            )"""
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_revisions_memory "
            "ON memory_revisions(memory_id, revision DESC)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_project_updated "
            "ON memories(project, updated_at DESC)"
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS memory_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL,
                confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
                kind TEXT NOT NULL DEFAULT 'fact',
                track TEXT NOT NULL DEFAULT 'user',
                status TEXT NOT NULL CHECK(status IN ('pending', 'approved', 'rejected')),
                dedupe_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                reviewed_by TEXT,
                review_reason TEXT,
                memory_id INTEGER,
                FOREIGN KEY(memory_id) REFERENCES memories(id)
            )"""
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_candidates_status "
            "ON memory_candidates(status, created_at, id)"
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS skill_candidate_evaluations (
                candidate_id INTEGER PRIMARY KEY,
                evaluator_id TEXT NOT NULL,
                passed INTEGER NOT NULL CHECK(passed IN (0, 1)),
                report_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(candidate_id) REFERENCES memory_candidates(id)
            )"""
        )
        self._migrate_memory_metadata()
        self._connection.commit()
    def _migrate_memory_metadata(self) -> None:
        """Add typed memory fields to databases created by earlier Muse versions."""
        for table in ("memories", "memory_revisions", "memory_candidates"):
            columns = {row[1] for row in self._connection.execute(f"PRAGMA table_info({table})")}
            if "kind" not in columns:
                self._connection.execute(f"ALTER TABLE {table} ADD COLUMN kind TEXT NOT NULL DEFAULT 'fact'")
            if "track" not in columns:
                self._connection.execute(f"ALTER TABLE {table} ADD COLUMN track TEXT NOT NULL DEFAULT 'user'")
        self._connection.commit()

    @staticmethod
    def _validate_kind(kind: str) -> str:
        if not isinstance(kind, str) or kind not in MEMORY_KINDS:
            raise ValueError(f"kind must be one of: {', '.join(sorted(MEMORY_KINDS))}")
        return kind

    @staticmethod
    def _validate_track(track: str) -> str:
        if not isinstance(track, str) or track not in MEMORY_TRACKS:
            raise ValueError("track must be 'user' or 'agent'")
        return track

    def __enter__(self) -> MemoryStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _validate_text(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must not be empty")
        return value.strip()

    @staticmethod
    def _validate_confidence(confidence: float) -> float:
        value = float(confidence)
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return value

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": row["id"],
            "project": row["project"],
            "content": row["content"],
            "source": row["source"],
            "confidence": row["confidence"],
            "kind": row["kind"],
            "track": row["track"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "revision": row["revision"],
        }

    def add(
        self,
        content: str,
        *,
        project: str = "general",
        source: str,
        confidence: float = 1.0,
        kind: str = "fact",
        track: str = "user",
    ) -> dict[str, Any]:
        content = self._validate_text(content, "content")
        project = self._validate_text(project, "project")
        source = self._validate_text(source, "source")
        confidence = self._validate_confidence(confidence)
        now = self._now()
        kind = self._validate_kind(kind)
        track = self._validate_track(track)
        cursor = self._connection.execute(
            """INSERT INTO memories
               (project, content, source, confidence, kind, track, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (project, content, source, confidence, kind, track, now, now),
        )
        memory_id = int(cursor.lastrowid)
        self._connection.execute(
            """INSERT INTO memory_revisions
               (memory_id, revision, content, source, confidence, kind, track, changed_at)
               VALUES (?, 1, ?, ?, ?, ?, ?, ?)""",
            (memory_id, content, source, confidence, kind, track, now),
        )
        self._connection.commit()
        saved = self.get(int(cursor.lastrowid))
        assert saved is not None
        return saved

    def get(self, memory_id: int) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM memories WHERE id = ? AND deleted_at IS NULL",
            (memory_id,),
        ).fetchone()
        return self._decode(row)

    def search(
        self,
        query: str,
        *,
        project: str | None = None,
        kind: str | None = None,
        track: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if not isinstance(query, str):
            raise ValueError("query must be text")
        raw_terms = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", query.casefold())
        terms: list[str] = []
        for token in raw_terms:
            if re.search(r"[\u3400-\u9fff]", token):
                if len(token) == 1:
                    terms.append(token)
                else:
                    terms.extend(token[index:index + 2] for index in range(len(token) - 1))
            else:
                terms.append(token)
        terms = list(dict.fromkeys(term for term in terms if term))
        if not terms:
            return []
        if limit < 1:
            raise ValueError("limit must be at least 1")

        searchable = "lower(content || ' ' || source || ' ' || project)"
        patterns = [f"%{term}%" for term in terms]
        relevance = " + ".join(
            f"CASE WHEN {searchable} LIKE ? THEN 1 ELSE 0 END" for _ in terms
        )
        matches = " OR ".join(f"{searchable} LIKE ?" for _ in terms)
        sql = (
            f"SELECT *, ({relevance}) AS relevance FROM memories "
            f"WHERE deleted_at IS NULL AND ({matches})"
        )
        parameters: list[Any] = [*patterns, *patterns]
        if project is not None:
            sql += " AND project = ?"
            parameters.append(self._validate_text(project, "project"))
        if kind is not None:
            sql += " AND kind = ?"
            parameters.append(self._validate_kind(kind))
        if track is not None:
            sql += " AND track = ?"
            parameters.append(self._validate_track(track))
        sql += " ORDER BY relevance DESC, updated_at DESC, id DESC LIMIT ?"
        parameters.append(limit)
        rows = self._connection.execute(sql, parameters).fetchall()
        return [self._decode(row) for row in rows if row is not None]

    def revise(
        self,
        memory_id: int,
        content: str,
        *,
        source: str | None = None,
        confidence: float | None = None,
        kind: str | None = None,
        track: str | None = None,
    ) -> dict[str, Any] | None:
        content = self._validate_text(content, "content")
        current = self.get(memory_id)
        if current is None:
            return None
        next_source = current["source"] if source is None else self._validate_text(source, "source")
        next_confidence = (
            current["confidence"]
            if confidence is None
            else self._validate_confidence(confidence)
        )
        next_kind = current["kind"] if kind is None else self._validate_kind(kind)
        next_track = current["track"] if track is None else self._validate_track(track)
        self._connection.execute(
            """INSERT INTO memory_revisions
               (memory_id, revision, content, source, confidence, kind, track, changed_at)
               SELECT ?, ?, ?, ?, ?, ?, ?, ?
               WHERE NOT EXISTS (
                   SELECT 1 FROM memory_revisions WHERE memory_id = ? AND revision = ?
               )""",
            (
                memory_id, current["revision"], current["content"], current["source"],
                current["confidence"], current["kind"], current["track"], self._now(),
                memory_id, current["revision"],
            ),
        )
        self._connection.execute(
            """UPDATE memories
               SET content = ?, source = ?, confidence = ?, kind = ?, track = ?,
                   updated_at = ?, revision = revision + 1
               WHERE id = ? AND deleted_at IS NULL""",
            (content, next_source, next_confidence, next_kind, next_track, self._now(), memory_id),
        )
        self._connection.commit()
        return self.get(memory_id)
    def list_memories(
        self, *, project: str | None = None, kind: str | None = None,
        track: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """List active memories, optionally restricted by project, kind and track."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        sql = "SELECT * FROM memories WHERE deleted_at IS NULL"
        parameters: list[Any] = []
        if project is not None:
            sql += " AND project = ?"
            parameters.append(self._validate_text(project, "project"))
        if kind is not None:
            sql += " AND kind = ?"
            parameters.append(self._validate_kind(kind))
        if track is not None:
            sql += " AND track = ?"
            parameters.append(self._validate_track(track))
        sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        parameters.append(limit)
        rows = self._connection.execute(sql, parameters).fetchall()
        return [self._decode(row) for row in rows if row is not None]

    def history(self, memory_id: int) -> list[dict[str, Any]]:
        """Return every saved version, oldest first, including the current version."""
        rows = self._connection.execute(
            """SELECT revision, content, source, confidence, kind, track, changed_at
               FROM memory_revisions WHERE memory_id = ?
               UNION ALL
               SELECT revision, content, source, confidence, kind, track, updated_at AS changed_at
               FROM memories WHERE id = ? AND deleted_at IS NULL
                 AND revision > COALESCE((
                     SELECT MAX(revision) FROM memory_revisions WHERE memory_id = ?
                 ), 0)
               ORDER BY revision""",
            (memory_id, memory_id, memory_id),
        ).fetchall()
        return [dict(row) for row in rows]
    def delete(self, memory_id: int) -> bool:
        current = self.get(memory_id)
        if current is None:
            return False
        self._connection.execute(
            """INSERT INTO memory_revisions
               (memory_id, revision, content, source, confidence, kind, track, changed_at)
               SELECT ?, ?, ?, ?, ?, ?, ?, ?
               WHERE NOT EXISTS (
                   SELECT 1 FROM memory_revisions WHERE memory_id = ? AND revision = ?
               )""",
            (memory_id, current["revision"], current["content"], current["source"],
             current["confidence"], current["kind"], current["track"], self._now(), memory_id, current["revision"]),
        )
        now = self._now()
        cursor = self._connection.execute(
            """UPDATE memories SET deleted_at = ?, updated_at = ?,
                   revision = revision + 1
               WHERE id = ? AND deleted_at IS NULL""",
            (now, now, memory_id),
        )
        self._connection.commit()
        return cursor.rowcount == 1
    @staticmethod
    def _validate_candidate_content(content: str) -> str:
        value = MemoryStore._validate_text(content, "content")
        credential_patterns = (
            r"(?i)\b(password|passwd|passphrase|api[_ -]?key|access[_ -]?token|secret[_ -]?key|bearer)\b\s*(?::|=|is)\s*\S+",
            r"(?i)\bsk-[a-z0-9_-]{16,}\b",
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        )
        if any(re.search(pattern, value) for pattern in credential_patterns):
            raise ValueError("sensitive credential-like content cannot be stored as a memory candidate")
        return value
    @staticmethod
    def _decode_candidate(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {key: row[key] for key in row.keys()}

    def propose(
        self, content: str, *, project: str = "general", source: str,
        confidence: float = 1.0, kind: str = "fact", track: str = "user",
    ) -> dict[str, Any]:
        """Stage a memory for explicit review; pending candidates are never searchable."""
        content = self._validate_candidate_content(content)
        project = self._validate_text(project, "project")
        source = self._validate_text(source, "source")
        confidence = self._validate_confidence(confidence)
        kind = self._validate_kind(kind)
        track = self._validate_track(track)
        dedupe_key = hashlib.sha256(
            (project + "|" + content + "|" + source + "|" + kind + "|" + track).encode("utf-8")
        ).hexdigest()
        now = self._now()
        self._connection.execute(
            "INSERT OR IGNORE INTO memory_candidates "
            "(project, content, source, confidence, kind, track, status, dedupe_key, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (project, content, source, confidence, kind, track, dedupe_key, now),
        )
        self._connection.commit()
        row = self._connection.execute(
            "SELECT * FROM memory_candidates WHERE dedupe_key = ?", (dedupe_key,)
        ).fetchone()
        result = self._decode_candidate(row)
        assert result is not None
        return result

    def list_pending(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        rows = self._connection.execute(
            "SELECT * FROM memory_candidates WHERE status='pending' "
            "ORDER BY created_at, id LIMIT ?", (limit,)
        ).fetchall()
        return [self._decode_candidate(row) for row in rows if row is not None]

    def get_candidate(self, candidate_id: int) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM memory_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
        return self._decode_candidate(row)

    def approve_candidate(self, candidate_id: int, *, approved_by: str) -> dict[str, Any]:
        approved_by = self._validate_text(approved_by, "approved_by")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._connection.execute(
                "SELECT * FROM memory_candidates WHERE id = ?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise ValueError("memory candidate does not exist")
            if row["status"] == "approved":
                self._connection.commit()
                result = self.get_candidate(candidate_id)
                assert result is not None
                return result
            if row["status"] != "pending":
                raise ValueError("only pending memory candidates can be approved")
            if row["kind"] == "skill" and row["track"] == "agent":
                evaluation = self._connection.execute(
                    "SELECT passed FROM skill_candidate_evaluations WHERE candidate_id=?",
                    (candidate_id,),
                ).fetchone()
                if evaluation is None or evaluation["passed"] != 1:
                    raise ValueError("Agent Track Skill candidates require a passing evaluation before approval")
            now = self._now()
            cursor = self._connection.execute(
                "INSERT INTO memories (project, content, source, confidence, kind, track, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (row["project"], row["content"], row["source"], row["confidence"], row["kind"], row["track"], now, now),
            )
            memory_id = int(cursor.lastrowid)
            self._connection.execute(
                "INSERT INTO memory_revisions "
                "(memory_id, revision, content, source, confidence, kind, track, changed_at) "
                "VALUES (?, 1, ?, ?, ?, ?, ?, ?)",
                (memory_id, row["content"], row["source"], row["confidence"], row["kind"], row["track"], now),
            )
            updated = self._connection.execute(
                "UPDATE memory_candidates SET status='approved', reviewed_at=?, reviewed_by=?, memory_id=? "
                "WHERE id=? AND status='pending'",
                (now, approved_by, memory_id, candidate_id),
            )
            if updated.rowcount != 1:
                raise ValueError("memory candidate changed during approval")
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        result = self.get_candidate(candidate_id)
        assert result is not None
        return result

    def reject_candidate(
        self, candidate_id: int, *, rejected_by: str, reason: str = ""
    ) -> dict[str, Any]:
        rejected_by = self._validate_text(rejected_by, "rejected_by")
        if not isinstance(reason, str) or len(reason) > 500:
            raise ValueError("reason must be text of at most 500 characters")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._connection.execute(
                "SELECT status FROM memory_candidates WHERE id = ?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise ValueError("memory candidate does not exist")
            if row["status"] == "rejected":
                self._connection.commit()
                result = self.get_candidate(candidate_id)
                assert result is not None
                return result
            if row["status"] != "pending":
                raise ValueError("only pending memory candidates can be rejected")
            now = self._now()
            updated = self._connection.execute(
                "UPDATE memory_candidates SET status='rejected', reviewed_at=?, reviewed_by=?, review_reason=? "
                "WHERE id=? AND status='pending'",
                (now, rejected_by, reason.strip(), candidate_id),
            )
            if updated.rowcount != 1:
                raise ValueError("memory candidate changed during rejection")
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        result = self.get_candidate(candidate_id)
        assert result is not None
        return result

    def export_json(self) -> str:
        rows = self._connection.execute(
            """SELECT * FROM memories WHERE deleted_at IS NULL
               ORDER BY created_at, id"""
        ).fetchall()
        records = [self._decode(row) for row in rows]
        return json.dumps(records, ensure_ascii=False, indent=2)
