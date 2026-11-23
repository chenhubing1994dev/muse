"""Auditable, manually approved Agent Track projection outbox for MemPalace."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from muse.memory import MemoryStore

PROJECTABLE_KINDS = frozenset({"skill", "case", "foresight"})
MAX_PROJECTED_CONTENT_CHARS = 16_000
MAX_OUTBOX_PAYLOAD_BYTES = 64 * 1024


class MemPalaceProjectionOutbox:
    """Stage approved Agent Track revisions and deliver them only on explicit request."""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database).resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with MemoryStore(self.database):
            pass
        self._connection = sqlite3.connect(self.database, timeout=10)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS mempalace_projection_outbox (
                outbox_id TEXT PRIMARY KEY,
                memory_id INTEGER NOT NULL,
                revision INTEGER NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'failed', 'delivered')),
                attempts INTEGER NOT NULL DEFAULT 0,
                approved_by TEXT NOT NULL,
                approved_at TEXT NOT NULL,
                delivered_by TEXT,
                delivered_at TEXT,
                last_error TEXT,
                remote_result_json TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(memory_id, revision),
                FOREIGN KEY(memory_id) REFERENCES memories(id)
            )"""
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_mempalace_projection_status "
            "ON mempalace_projection_outbox(status, created_at)"
        )
        self._connection.commit()

    def __enter__(self) -> "MemPalaceProjectionOutbox":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _approver(value: str | None, field: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 128:
            raise ValueError(f"{field} must be non-empty text up to 128 characters")
        cleaned = value.strip()
        if any(ord(character) < 32 for character in cleaned):
            raise ValueError(f"{field} must not contain control characters")
        MemoryStore._validate_candidate_content(cleaned)
        return cleaned

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        encoded_remote = result.pop("remote_result_json")
        result["remote_result"] = json.loads(encoded_remote) if encoded_remote else None
        return result

    @staticmethod
    def _checkpoint_payload(response: Any) -> dict[str, Any]:
        if not isinstance(response, dict) or response.get("isError") is True:
            raise RuntimeError("MemPalace checkpoint returned an invalid or error response")
        structured = response.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        for item in response.get("content", []):
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                try:
                    decoded = json.loads(item["text"])
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, dict):
                    return decoded
        raise RuntimeError("MemPalace checkpoint response contained no structured acknowledgement")

    @classmethod
    def _validate_checkpoint_ack(cls, response: Any, expected_content: str) -> dict[str, Any]:
        payload = cls._checkpoint_payload(response)
        errors = payload.get("errors", [])
        added = payload.get("added", [])
        duplicates = payload.get("duplicates", [])
        if not isinstance(errors, list) or errors:
            raise RuntimeError("MemPalace checkpoint reported filing errors")
        if not isinstance(added, list) or not isinstance(duplicates, list):
            raise RuntimeError("MemPalace checkpoint acknowledgement has invalid result lists")
        if len(added) == 1 and not duplicates:
            drawer = added[0]
            drawer_id = (drawer.get("drawer_id") or drawer.get("id")) if isinstance(drawer, dict) else None
            if not isinstance(drawer_id, str) or not drawer_id.strip():
                raise RuntimeError("MemPalace added acknowledgement has no drawer ID")
            return {"outcome": "added", "drawer_id": drawer_id}
        if not added and len(duplicates) == 1:
            duplicate = duplicates[0]
            if not isinstance(duplicate, dict):
                raise RuntimeError("MemPalace duplicate acknowledgement is malformed")
            candidates: list[dict[str, Any]] = []
            if isinstance(duplicate.get("content"), str):
                candidates.append({"content": duplicate["content"]})
            matches = duplicate.get("matches", [])
            if isinstance(matches, list):
                candidates.extend(match for match in matches if isinstance(match, dict))
            exact = next(
                (candidate for candidate in candidates if candidate.get("content") == expected_content),
                None,
            )
            if exact is None:
                raise RuntimeError("MemPalace reported a semantic duplicate without exact content equality")
            drawer_id = exact.get("id") or duplicate.get("drawer_id")
            if not isinstance(drawer_id, str) or not drawer_id.strip():
                raise RuntimeError("MemPalace exact duplicate acknowledgement has no drawer ID")
            return {"outcome": "exact_duplicate", "drawer_id": drawer_id}
        raise RuntimeError("MemPalace checkpoint did not confirm exactly one added or exact duplicate item")

    def get(self, outbox_id: str) -> dict[str, Any] | None:
        if not isinstance(outbox_id, str) or not outbox_id.strip():
            raise ValueError("outbox_id must be non-empty text")
        row = self._connection.execute(
            "SELECT * FROM mempalace_projection_outbox WHERE outbox_id = ?",
            (outbox_id.strip(),),
        ).fetchone()
        return self._decode(row)

    def list_pending(self, *, limit: int = 50, include_failed: bool = False) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        statuses = ("pending", "failed") if include_failed else ("pending",)
        placeholders = ",".join("?" for _ in statuses)
        rows = self._connection.execute(
            f"SELECT * FROM mempalace_projection_outbox WHERE status IN ({placeholders}) "
            "ORDER BY created_at, outbox_id LIMIT ?",
            (*statuses, limit),
        ).fetchall()
        return [self._decode(row) for row in rows if row is not None]

    def health_summary(self) -> dict[str, Any]:
        """Return privacy-safe outbox health counters; never expose projected content."""
        rows = self._connection.execute(
            "SELECT status, COUNT(*) AS item_count FROM mempalace_projection_outbox GROUP BY status"
        ).fetchall()
        counts = {"pending": 0, "failed": 0, "delivered": 0}
        for row in rows:
            status = row["status"]
            if status in counts:
                counts[status] = int(row["item_count"])
        oldest_pending = self._connection.execute(
            "SELECT MIN(created_at) FROM mempalace_projection_outbox WHERE status = 'pending'"
        ).fetchone()[0]
        latest_delivery = self._connection.execute(
            "SELECT MAX(delivered_at) FROM mempalace_projection_outbox WHERE status = 'delivered'"
        ).fetchone()[0]
        return {
            "status": "attention_required" if counts["failed"] else "pending" if counts["pending"] else "healthy",
            "total": sum(counts.values()),
            "counts": counts,
            "oldest_pending_at": oldest_pending,
            "latest_delivered_at": latest_delivery,
            "content_included": False,
        }

    def queue(
        self,
        memory_id: int,
        *,
        approved: bool = False,
        approved_by: str | None = None,
        min_confidence: float = 0.75,
    ) -> dict[str, Any]:
        """Queue one current Agent Track skill/case/foresight revision after review."""
        if approved is not True:
            raise PermissionError("Agent Track projection requires per-record approval")
        reviewer = self._approver(approved_by, "approved_by")
        if isinstance(memory_id, bool) or not isinstance(memory_id, int) or memory_id < 1:
            raise ValueError("memory_id must be a positive integer")
        if isinstance(min_confidence, bool) or not isinstance(min_confidence, (int, float)):
            raise ValueError("min_confidence must be between 0 and 1")
        if not 0 <= min_confidence <= 1:
            raise ValueError("min_confidence must be between 0 and 1")

        with MemoryStore(self.database) as memory:
            record = memory.get(memory_id)
        if record is None:
            raise ValueError("memory does not exist or is soft-deleted")
        if record["track"] != "agent":
            raise PermissionError("only Agent Track memories may be projected")
        if record["kind"] not in PROJECTABLE_KINDS:
            raise ValueError("only skill, case and foresight memories may be projected")
        if record["confidence"] < float(min_confidence):
            raise ValueError("memory confidence is below the projection threshold")
        project = MemoryStore._validate_candidate_content(record["project"])
        source = MemoryStore._validate_candidate_content(record["source"])
        if len(project) > 120 or len(source) > 500:
            raise ValueError("memory project/source metadata exceeds projection bounds")

        original_content = MemoryStore._validate_candidate_content(record["content"])
        content_hash = hashlib.sha256(original_content.encode("utf-8")).hexdigest()
        idempotency_key = f"muse-memory:{record['id']}:r{record['revision']}:{content_hash}"
        provenance = {
            "source_system": "Muse MemoryStore",
            "memory_id": record["id"],
            "revision": record["revision"],
            "project": project,
            "kind": record["kind"],
            "source": source,
            "confidence": record["confidence"],
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
            "content_sha256": content_hash,
            "idempotency_key": idempotency_key,
            "track": "agent",
        }
        projected_content = (
            original_content
            + "\n\n[Muse Provenance]\n"
            + json.dumps(provenance, ensure_ascii=False, sort_keys=True)
        )
        if len(projected_content) > MAX_PROJECTED_CONTENT_CHARS:
            raise ValueError("projected content exceeds MemPalace's 16,000-character bound")
        payload = {
            "items": [{
                "wing": project,
                "room": record["kind"],
                "content": projected_content,
            }],
            "dedup_threshold": 0.95,
        }
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if len(payload_json.encode("utf-8")) > MAX_OUTBOX_PAYLOAD_BYTES:
            raise ValueError("projection outbox payload exceeds 64 KiB")
        now = self._now()
        self._connection.execute(
            """INSERT OR IGNORE INTO mempalace_projection_outbox
               (outbox_id, memory_id, revision, idempotency_key, payload_json,
                content_sha256, status, attempts, approved_by, approved_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)""",
            (
                uuid.uuid4().hex, record["id"], record["revision"], idempotency_key,
                payload_json, content_hash, reviewer, now, now,
            ),
        )
        self._connection.commit()
        row = self._connection.execute(
            "SELECT * FROM mempalace_projection_outbox WHERE memory_id = ? AND revision = ?",
            (record["id"], record["revision"]),
        ).fetchone()
        result = self._decode(row)
        assert result is not None
        return result

    def _mark_failed(self, outbox_id: str, error_name: str) -> None:
        self._connection.execute(
            """UPDATE mempalace_projection_outbox
               SET status = 'failed', attempts = attempts + 1, last_error = ?
               WHERE outbox_id = ? AND status != 'delivered'""",
            (error_name[:128], outbox_id),
        )
        self._connection.commit()

    def deliver_with_approval_ticket(
        self,
        outbox_id: str,
        adapter: Any,
        *,
        task_database: str | Path,
        approval_task_id: str,
    ) -> dict[str, Any]:
        """Deliver only when a persisted Commander Ticket approves this exact outbox item."""
        from muse.task_queue import TaskQueue

        if not isinstance(approval_task_id, str) or not approval_task_id.strip():
            raise PermissionError("a persisted Commander approval task is required")
        record = self.get(outbox_id)
        if record is None:
            raise KeyError(outbox_id)
        with TaskQueue(task_database) as queue:
            task = queue.get(approval_task_id.strip())
            events = queue.events(approval_task_id.strip(), limit=500) if task else []
        if task is None:
            raise PermissionError("approval Ticket does not exist")
        payload = task.get("payload")
        if not isinstance(payload, dict) or payload.get("kind") != "mempalace_projection_approval":
            raise PermissionError("Ticket is not a MemPalace projection approval")
        if payload.get("outbox_id") != record["outbox_id"] or payload.get("idempotency_key") != record["idempotency_key"]:
            raise PermissionError("approval Ticket does not match this exact projection")
        if task.get("status") not in {"queued", "running", "succeeded"}:
            raise PermissionError("approval Ticket is not in an approved state")
        approval = payload.get("_approval")
        if payload.get("_approved") is not True or not isinstance(approval, dict):
            raise PermissionError("approval Ticket has no persisted approval metadata")
        approver = approval.get("approved_by")
        approved_at = approval.get("approved_at")
        matching_decision = any(
            event.get("event_type") == "human.decision"
            and event.get("actor") == approver
            and isinstance(event.get("payload"), dict)
            and event["payload"].get("decision") == "approved"
            and TaskQueue.approval_timestamp_matches_event(approved_at, event.get("created_at"))
            for event in events
        )
        if (not isinstance(approver, str) or not approver.strip()
                or not isinstance(approved_at, str) or not approved_at.strip()
                or not matching_decision):
            raise PermissionError("approval metadata is not backed by a persisted human decision")
        return self.deliver(
            outbox_id, adapter, approved=True, approved_by=approver,
        )

    def deliver(
        self,
        outbox_id: str,
        adapter: Any,
        *,
        approved: bool = False,
        approved_by: str | None = None,
    ) -> dict[str, Any]:
        """Deliver exactly one staged record; never run in a background loop."""
        if approved is not True:
            raise PermissionError("MemPalace delivery requires per-call approval")
        reviewer = self._approver(approved_by, "approved_by")
        record = self.get(outbox_id)
        if record is None:
            raise KeyError(outbox_id)
        if record["status"] == "delivered":
            return record

        with MemoryStore(self.database) as memory:
            current = memory.get(record["memory_id"])
        if current is None or current["revision"] != record["revision"] or current["track"] != "agent":
            self._mark_failed(outbox_id, "StaleSourceRevision")
            raise RuntimeError("source memory changed or was deleted after approval; queue its current revision again")

        payload = record["payload"]
        try:
            response = adapter.checkpoint(
                payload["items"],
                approved=True,
                dedup_threshold=payload["dedup_threshold"],
            )
            summary = self._validate_checkpoint_ack(
                response, payload["items"][0]["content"],
            )
        except Exception as exc:
            self._mark_failed(outbox_id, type(exc).__name__)
            raise

        now = self._now()
        self._connection.execute(
            """UPDATE mempalace_projection_outbox
               SET status = 'delivered', attempts = attempts + 1,
                   delivered_by = ?, delivered_at = ?, last_error = NULL,
                   remote_result_json = ?
               WHERE outbox_id = ? AND status != 'delivered'""",
            (reviewer, now, json.dumps(summary, sort_keys=True), outbox_id),
        )
        self._connection.commit()
        delivered = self.get(outbox_id)
        assert delivered is not None
        return delivered
