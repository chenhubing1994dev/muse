"""Dual-Track Memory gate over :class:`muse.memory.MemoryStore`.

* **User Track** — only what the user stated or explicitly confirmed (facts, preferences,
  goals, authorisations).
* **Agent Track** — Muse's own run experience, cases, skills, experiments and failures.

Hard rule: an Agent inference is never written to the User Track directly. It can only be
*proposed* as a pending candidate, which becomes active after a human approver (``user:`` or
``human:`` identity) confirms it. Every memory carries an epistemic stamp (tag, origin,
confidence, approval status) in the ``memory_epistemics`` side table of the same SQLite file,
so the existing ``memories`` schema, revision chain and soft-delete stay authoritative.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from muse.epistemics import (
    AGENT_ORIGINS, USER_ORIGINS, EpistemicTagError, UserTrackContamination,
    assert_user_track_admissible, normalize_tag,
)
from muse.memory import MemoryStore

USER_KINDS = frozenset({"profile", "preference", "fact", "task", "interest", "atomic_fact"})
AGENT_KINDS = frozenset({"episode", "case", "skill", "foresight", "atomic_fact", "fact"})
HUMAN_APPROVER_PREFIXES = ("user:", "human:")


class DualTrackMemory:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database).resolve()
        self.store = MemoryStore(self.database)
        self._db = sqlite3.connect(self.database, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS memory_epistemics (
                ref_kind TEXT NOT NULL CHECK(ref_kind IN ('memory','candidate')),
                ref_id INTEGER NOT NULL,
                track TEXT NOT NULL CHECK(track IN ('user','agent')),
                epistemic_tag TEXT NOT NULL,
                origin TEXT NOT NULL,
                approval_status TEXT NOT NULL,
                approved_by TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                PRIMARY KEY(ref_kind, ref_id))""")
        self._db.commit()

    def close(self) -> None:
        try:
            self._db.close()
        finally:
            self.store.close()

    def __enter__(self) -> "DualTrackMemory":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S%z")

    def _stamp(self, ref_kind: str, ref_id: int, track: str, tag: str, origin: str,
               status: str, approved_by: str = "") -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO memory_epistemics VALUES (?,?,?,?,?,?,?,?)",
            (ref_kind, int(ref_id), track, tag, origin, status, approved_by, self._now()))
        self._db.commit()

    def stamp_of(self, memory_id: int) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT * FROM memory_epistemics WHERE ref_kind='memory' AND ref_id=?", (int(memory_id),)).fetchone()
        return dict(row) if row else None

    # ---- User Track -------------------------------------------------------------------
    def remember_user_statement(self, content: str, *, source: str, tag: str = "fact", kind: str = "fact",
                                project: str = "general", confidence: float = 1.0,
                                origin: str = "user_stated") -> dict[str, Any]:
        if origin not in USER_ORIGINS:
            raise UserTrackContamination("remember_user_statement only accepts user_stated/user_confirmed origins")
        if kind not in USER_KINDS:
            raise EpistemicTagError(f"kind {kind!r} is not a User Track kind")
        resolved = assert_user_track_admissible(tag=tag, origin=origin)
        saved = self.store.add(content, project=project, source=source, confidence=confidence, kind=kind, track="user")
        self._stamp("memory", saved["id"], "user", resolved, origin, "user_confirmed")
        return {**saved, "epistemic": self.stamp_of(saved["id"])}

    # ---- Agent Track ------------------------------------------------------------------
    def record_agent_experience(self, content: str, *, source: str, tag: str = "experience", kind: str = "episode",
                                project: str = "general", confidence: float = 0.6,
                                origin: str = "agent_experience") -> dict[str, Any]:
        if origin not in AGENT_ORIGINS:
            raise EpistemicTagError("agent memories need an agent origin")
        if kind not in AGENT_KINDS:
            raise EpistemicTagError(f"kind {kind!r} is not an Agent Track kind")
        resolved = normalize_tag(tag)
        saved = self.store.add(content, project=project, source=source, confidence=confidence, kind=kind, track="agent")
        self._stamp("memory", saved["id"], "agent", resolved, origin, "agent_recorded")
        return {**saved, "epistemic": self.stamp_of(saved["id"])}

    def write(self, content: str, *, track: str, source: str, tag: str, origin: str, **kwargs: Any) -> dict[str, Any]:
        """Generic entry point used by other modules; enforces the contamination rule."""
        if track == "user":
            if origin not in USER_ORIGINS:
                raise UserTrackContamination(
                    f"origin {origin!r} cannot write the User Track; use propose_user_promotion()")
            return self.remember_user_statement(content, source=source, tag=tag, origin=origin, **kwargs)
        if track == "agent":
            return self.record_agent_experience(content, source=source, tag=tag, origin=origin, **kwargs)
        raise ValueError("track must be 'user' or 'agent'")

    # ---- Promotion (Agent inference -> User Track, human-gated) ------------------------
    def propose_user_promotion(self, content: str, *, source: str, tag: str = "interpretation",
                               kind: str = "preference", project: str = "general",
                               confidence: float = 0.5) -> dict[str, Any]:
        normalize_tag(tag)
        if kind not in USER_KINDS:
            raise EpistemicTagError(f"kind {kind!r} is not a User Track kind")
        candidate = self.store.propose(content, project=project, source=f"agent_inference:{source}",
                                       confidence=confidence, kind=kind, track="user")
        self._stamp("candidate", candidate["id"], "user", normalize_tag(tag), "agent_inference", "pending_user_confirmation")
        return candidate

    def confirm_promotion(self, candidate_id: int, *, approved_by: str) -> dict[str, Any]:
        if not isinstance(approved_by, str) or not approved_by.startswith(HUMAN_APPROVER_PREFIXES):
            raise UserTrackContamination("only a human approver (user:/human:) can confirm a User Track promotion")
        row = self._db.execute(
            "SELECT * FROM memory_epistemics WHERE ref_kind='candidate' AND ref_id=?", (int(candidate_id),)).fetchone()
        tag = row["epistemic_tag"] if row else "interpretation"
        assert_user_track_admissible(tag=tag, origin="agent_inference", user_confirmed=True)
        approved = self.store.approve_candidate(int(candidate_id), approved_by=approved_by)
        memory_id = approved.get("memory_id")
        if memory_id is not None:
            self._stamp("memory", int(memory_id), "user", tag, "user_confirmed", "user_confirmed", approved_by)
        self._stamp("candidate", int(candidate_id), "user", tag, "agent_inference", "approved", approved_by)
        return approved

    # ---- Audits -------------------------------------------------------------------------
    def contamination_audit(self) -> dict[str, Any]:
        """User Track rows that lack a user origin stamp are contamination suspects."""
        # Full-table scan in SQL: MemoryStore.list_memories is paged (max 200) by design.
        rows = self._db.execute(
            "SELECT m.id AS id, e.origin AS origin FROM memories m "
            "LEFT JOIN memory_epistemics e ON e.ref_kind='memory' AND e.ref_id=m.id "
            "WHERE m.track='user' AND m.deleted_at IS NULL ORDER BY m.id").fetchall()
        unstamped = [int(r["id"]) for r in rows if r["origin"] is None]
        legacy = [int(r["id"]) for r in rows if r["origin"] == "legacy_import"]
        bad_origin = [int(r["id"]) for r in rows if r["origin"] is not None and r["origin"] not in USER_ORIGINS
                      and r["origin"] != "legacy_import"]
        return {"user_track_rows": len(rows), "unstamped_legacy_count": len(unstamped),
                "unstamped_legacy": unstamped[:200], "legacy_unverified_count": len(legacy),
                "agent_origin_in_user_track": bad_origin[:200], "clean": not bad_origin}
