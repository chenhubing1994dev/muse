"""B395 dual-track memory lifecycle on top of :class:`muse.memory.MemoryStore`.

Sidecar tables live in the same SQLite file (the store's schema is untouched):

* expiry reminders + staleness           ``memory_lifecycle`` (expires_at, remind_before, weight, archived)
* demotion / archival                    weight decay, ``archive`` (never silently deleted)
* revision diff                          unified diff between any two saved revisions
* auditable forgetting                   content is purged from memories *and* revisions; only a SHA-256
                                         tombstone, actor, reason and time remain (append-only audit)
* cross-project causal timeline          ``memory_causal_links`` with cycle refusal
* multimodal anchor index                ``memory_anchors`` (image/audio/video/document/url/code + sha256 + span)
* Obsidian one-way mirror                Muse → vault only; a note edited in Obsidian is detected through its
                                         recorded hash and reported as a conflict, never overwritten
* MemPalace Agent-Track approval receipts  hash-chained receipts for every approved agent-track candidate
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from muse.memory import MemoryStore

MODALITIES = frozenset({"image", "audio", "video", "document", "url", "code", "text"})
CAUSAL_RELATIONS = frozenset({"caused", "enabled", "blocked", "refined", "contradicted", "followed"})
FORGET_PHRASE = "FORGET-MEMORY"
_SAFE_NAME = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class MemoryLifecycle:
    def __init__(self, store: MemoryStore, *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        if not isinstance(store, MemoryStore):
            raise TypeError("store must be a MemoryStore")
        self.store = store
        self.db: sqlite3.Connection = store._connection  # same file, same transaction domain
        self.clock = clock
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS memory_lifecycle(
                memory_id INTEGER PRIMARY KEY, expires_at TEXT, remind_before_seconds INTEGER NOT NULL DEFAULT 86400,
                weight REAL NOT NULL DEFAULT 1.0 CHECK(weight>=0 AND weight<=1), archived_at TEXT,
                archive_reason TEXT NOT NULL DEFAULT '', reminded_at TEXT);
            CREATE TABLE IF NOT EXISTS memory_forget_audit(
                id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id INTEGER NOT NULL, content_sha256 TEXT NOT NULL,
                revisions_purged INTEGER NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL, forgotten_at TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS memory_forget_audit_no_update BEFORE UPDATE ON memory_forget_audit
                BEGIN SELECT RAISE(ABORT,'forget audit is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS memory_forget_audit_no_delete BEFORE DELETE ON memory_forget_audit
                BEGIN SELECT RAISE(ABORT,'forget audit is append-only'); END;
            CREATE TABLE IF NOT EXISTS memory_causal_links(
                id INTEGER PRIMARY KEY AUTOINCREMENT, cause_id INTEGER NOT NULL, effect_id INTEGER NOT NULL,
                relation TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(cause_id, effect_id, relation));
            CREATE TABLE IF NOT EXISTS memory_anchors(
                id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id INTEGER NOT NULL, modality TEXT NOT NULL,
                uri TEXT NOT NULL, sha256 TEXT NOT NULL DEFAULT '', span TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_memory_anchors_sha ON memory_anchors(sha256);
            CREATE INDEX IF NOT EXISTS idx_memory_anchors_uri ON memory_anchors(uri);
            CREATE TABLE IF NOT EXISTS memory_obsidian_mirror(
                memory_id INTEGER PRIMARY KEY, relative_path TEXT NOT NULL UNIQUE, revision INTEGER NOT NULL,
                written_sha256 TEXT NOT NULL, mirrored_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS memory_mirror_conflicts(
                id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id INTEGER NOT NULL, relative_path TEXT NOT NULL,
                expected_sha256 TEXT NOT NULL, found_sha256 TEXT NOT NULL, detected_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS agent_track_receipts(
                seq INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id INTEGER NOT NULL UNIQUE, memory_id INTEGER NOT NULL,
                approved_by TEXT NOT NULL, content_sha256 TEXT NOT NULL, prev_hash TEXT NOT NULL,
                receipt_hash TEXT NOT NULL, issued_at TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS agent_track_receipts_no_update BEFORE UPDATE ON agent_track_receipts
                BEGIN SELECT RAISE(ABORT,'agent-track receipts are append-only'); END;
        """)
        self.db.commit()

    # ------------------------------------------------------------------ helpers
    def _require(self, memory_id: int) -> dict[str, Any]:
        memory = self.store.get(int(memory_id))
        if memory is None:
            raise ValueError("memory not found or already forgotten")
        return memory

    def _ensure_row(self, memory_id: int) -> None:
        self.db.execute("INSERT OR IGNORE INTO memory_lifecycle(memory_id) VALUES(?)", (int(memory_id),))

    # ------------------------------------------------------------------ expiry / reminders
    def set_expiry(self, memory_id: int, *, expires_at: datetime, remind_before: timedelta = timedelta(days=1)) -> None:
        self._require(memory_id)
        with self.db:
            self._ensure_row(memory_id)
            self.db.execute("UPDATE memory_lifecycle SET expires_at=?, remind_before_seconds=?, reminded_at=NULL"
                            " WHERE memory_id=?", (_iso(expires_at), int(remind_before.total_seconds()), int(memory_id)))

    def due_reminders(self, *, mark: bool = True) -> list[dict[str, Any]]:
        now = self.clock()
        due = []
        for row in self.db.execute("SELECT * FROM memory_lifecycle WHERE expires_at IS NOT NULL AND archived_at IS NULL"
                                   " AND reminded_at IS NULL").fetchall():
            expires = datetime.fromisoformat(row["expires_at"])
            if now >= expires - timedelta(seconds=row["remind_before_seconds"]):
                memory = self.store.get(row["memory_id"])
                if memory is None:
                    continue
                due.append({"memory_id": row["memory_id"], "expires_at": row["expires_at"],
                            "expired": now >= expires, "preview": memory["content"][:120]})
        if mark and due:
            with self.db:
                for item in due:
                    self.db.execute("UPDATE memory_lifecycle SET reminded_at=? WHERE memory_id=?",
                                    (_iso(now), item["memory_id"]))
        return due

    # ------------------------------------------------------------------ demotion / archive
    def demote(self, memory_id: int, *, factor: float = 0.5) -> float:
        self._require(memory_id)
        if not 0 < factor < 1:
            raise ValueError("factor must be between 0 and 1")
        with self.db:
            self._ensure_row(memory_id)
            self.db.execute("UPDATE memory_lifecycle SET weight=MAX(0, weight*?) WHERE memory_id=?", (factor, int(memory_id)))
        return float(self.db.execute("SELECT weight FROM memory_lifecycle WHERE memory_id=?", (int(memory_id),)).fetchone()[0])

    def archive(self, memory_id: int, *, reason: str) -> None:
        self._require(memory_id)
        with self.db:
            self._ensure_row(memory_id)
            self.db.execute("UPDATE memory_lifecycle SET archived_at=?, archive_reason=?, weight=0 WHERE memory_id=?",
                            (_iso(self.clock()), str(reason)[:300], int(memory_id)))

    def sweep_expired(self) -> list[int]:
        """Archive (not delete) memories whose expiry passed."""
        now = _iso(self.clock())
        ids = [r[0] for r in self.db.execute(
            "SELECT memory_id FROM memory_lifecycle WHERE expires_at IS NOT NULL AND expires_at<=? AND archived_at IS NULL",
            (now,))]
        for memory_id in ids:
            if self.store.get(memory_id) is not None:
                self.archive(memory_id, reason="expired")
        return ids

    def ranked(self, *, project: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        memories = self.store.list_memories(project=project, limit=200)
        weights = {r[0]: (r[1], r[2]) for r in self.db.execute("SELECT memory_id, weight, archived_at FROM memory_lifecycle")}
        result = []
        for memory in memories:
            weight, archived = weights.get(memory["id"], (1.0, None))
            if archived:
                continue
            result.append({**memory, "lifecycle_weight": weight, "score": round(weight * memory["confidence"], 4)})
        return sorted(result, key=lambda m: (-m["score"], -m["id"]))[:limit]

    # ------------------------------------------------------------------ revision diff
    def diff(self, memory_id: int, from_revision: int, to_revision: int) -> dict[str, Any]:
        versions = {item["revision"]: item for item in self.store.history(int(memory_id))}
        if from_revision not in versions or to_revision not in versions:
            raise ValueError("revision not found")
        a, b = versions[from_revision], versions[to_revision]
        lines = list(difflib.unified_diff(a["content"].splitlines(), b["content"].splitlines(),
                                          fromfile=f"r{from_revision}", tofile=f"r{to_revision}", lineterm=""))
        changed = {k: (a[k], b[k]) for k in ("source", "confidence", "kind", "track") if a[k] != b[k]}
        return {"memory_id": int(memory_id), "from": from_revision, "to": to_revision, "diff": lines,
                "metadata_changes": changed}

    # ------------------------------------------------------------------ auditable forgetting
    def forget(self, memory_id: int, *, actor: str, reason: str, confirmation: str) -> dict[str, Any]:
        if confirmation != FORGET_PHRASE:
            raise PermissionError(f"confirmation phrase must be {FORGET_PHRASE}")
        if not str(actor).strip() or not str(reason).strip():
            raise ValueError("actor and reason are required for an auditable forget")
        memory = self._require(memory_id)
        digest = _sha(memory["content"])
        self.db.execute("BEGIN IMMEDIATE")
        try:
            purged = self.db.execute("UPDATE memory_revisions SET content='[forgotten]', source='[forgotten]'"
                                     " WHERE memory_id=?", (int(memory_id),)).rowcount
            now = _iso(self.clock())
            self.db.execute("UPDATE memories SET content='[forgotten]', source='[forgotten]', deleted_at=?, updated_at=?,"
                            " revision=revision+1 WHERE id=?", (now, now, int(memory_id)))
            self.db.execute("DELETE FROM memory_anchors WHERE memory_id=?", (int(memory_id),))
            self.db.execute("UPDATE memory_evidence SET excerpt='[forgotten]' WHERE memory_id=?", (int(memory_id),))
            self.db.execute("INSERT INTO memory_forget_audit(memory_id,content_sha256,revisions_purged,actor,reason,"
                            "forgotten_at) VALUES(?,?,?,?,?,?)",
                            (int(memory_id), digest, purged, str(actor)[:120], str(reason)[:300], now))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return {"memory_id": int(memory_id), "content_sha256": digest, "revisions_purged": purged}

    def forget_audit(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM memory_forget_audit ORDER BY id")]

    # ------------------------------------------------------------------ causal timeline
    def link(self, cause_id: int, effect_id: int, *, relation: str, created_by: str, note: str = "") -> None:
        if relation not in CAUSAL_RELATIONS:
            raise ValueError("unsupported causal relation")
        if int(cause_id) == int(effect_id):
            raise ValueError("a memory cannot cause itself")
        self._require(cause_id)
        self._require(effect_id)
        # refuse cycles: effect must not already (transitively) cause the cause
        frontier, seen = [int(effect_id)], set()
        while frontier:
            node = frontier.pop()
            if node == int(cause_id):
                raise ValueError("causal link would create a cycle")
            if node in seen:
                continue
            seen.add(node)
            frontier.extend(r[0] for r in self.db.execute("SELECT effect_id FROM memory_causal_links WHERE cause_id=?", (node,)))
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO memory_causal_links(cause_id,effect_id,relation,note,created_by,created_at)"
                            " VALUES(?,?,?,?,?,?)", (int(cause_id), int(effect_id), relation, note[:300],
                                                     str(created_by)[:120], _iso(self.clock())))

    def causal_timeline(self, memory_id: int, *, depth: int = 4) -> dict[str, Any]:
        nodes, edges, frontier = {}, [], [(int(memory_id), 0)]
        while frontier:
            node, level = frontier.pop()
            if node in nodes or level > depth:
                continue
            memory = self.store.get(node)
            if memory is None:
                continue
            nodes[node] = {"id": node, "project": memory["project"], "created_at": memory["created_at"],
                           "preview": memory["content"][:100]}
            for row in self.db.execute("SELECT * FROM memory_causal_links WHERE cause_id=? OR effect_id=?", (node, node)):
                edge = (row["cause_id"], row["effect_id"], row["relation"])
                if edge not in edges:
                    edges.append(edge)
                frontier.append((row["effect_id"] if row["cause_id"] == node else row["cause_id"], level + 1))
        ordered = sorted(nodes.values(), key=lambda n: (n["created_at"], n["id"]))
        return {"root": int(memory_id), "events": ordered, "projects": sorted({n["project"] for n in ordered}),
                "edges": [{"cause": c, "effect": e, "relation": r} for c, e, r in edges
                          if c in nodes and e in nodes]}

    # ------------------------------------------------------------------ multimodal anchors
    def add_anchor(self, memory_id: int, *, modality: str, uri: str, sha256: str = "", span: str = "") -> int:
        self._require(memory_id)
        if modality not in MODALITIES:
            raise ValueError("unsupported anchor modality")
        if not isinstance(uri, str) or not uri.strip() or len(uri) > 2000 or "\x00" in uri:
            raise ValueError("anchor uri is invalid")
        if sha256 and not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError("anchor sha256 must be lowercase hex")
        with self.db:
            cursor = self.db.execute("INSERT INTO memory_anchors(memory_id,modality,uri,sha256,span,created_at)"
                                     " VALUES(?,?,?,?,?,?)", (int(memory_id), modality, uri.strip(), sha256, span[:120],
                                                             _iso(self.clock())))
        return int(cursor.lastrowid)

    def find_by_anchor(self, *, sha256: str | None = None, uri: str | None = None, modality: str | None = None) -> list[dict[str, Any]]:
        clauses, params = [], []
        if sha256:
            clauses.append("sha256=?")
            params.append(sha256)
        if uri:
            clauses.append("uri=?")
            params.append(uri)
        if modality:
            clauses.append("modality=?")
            params.append(modality)
        if not clauses:
            raise ValueError("an anchor filter is required")
        rows = self.db.execute("SELECT * FROM memory_anchors WHERE " + " AND ".join(clauses) + " ORDER BY id", params).fetchall()
        return [dict(r) for r in rows if self.store.get(r["memory_id"]) is not None]

    # ------------------------------------------------------------------ Obsidian one-way mirror
    @staticmethod
    def _note(memory: dict[str, Any]) -> str:
        return ("---\n" f"muse_memory_id: {memory['id']}\n" f"muse_revision: {memory['revision']}\n"
                f"muse_project: {json.dumps(memory['project'], ensure_ascii=False)}\n"
                f"muse_kind: {memory['kind']}\n" f"muse_track: {memory['track']}\n"
                "muse_mirror: one_way_read_only\n---\n\n" + memory["content"].rstrip() + "\n")

    def mirror_to_obsidian(self, vault_root: str | Path, *, folder: str = "Muse Memory", project: str | None = None) -> dict[str, Any]:
        root = Path(vault_root).resolve(strict=True)
        if _SAFE_NAME.sub("", folder) != folder.replace(" ", ""):
            raise ValueError("mirror folder name contains unsafe characters")
        target_dir = (root / folder)
        target_dir.mkdir(parents=True, exist_ok=True)
        if root not in target_dir.resolve().parents:
            raise ValueError("mirror folder escapes the vault")
        written, unchanged, conflicts = [], [], []
        for memory in self.store.list_memories(project=project, limit=200):
            name = _SAFE_NAME.sub("-", f"{memory['id']:06d}-{memory['content'][:40]}").strip("-.") + ".md"
            record = self.db.execute("SELECT * FROM memory_obsidian_mirror WHERE memory_id=?", (memory["id"],)).fetchone()
            relative = record["relative_path"] if record else f"{folder}/{name}"
            path = root / relative
            if path.is_symlink():
                conflicts.append({"memory_id": memory["id"], "path": relative, "reason": "symlink"})
                continue
            content = self._note(memory)
            if record is not None and path.exists():
                found = hashlib.sha256(path.read_bytes()).hexdigest()
                if found != record["written_sha256"]:
                    conflicts.append({"memory_id": memory["id"], "path": relative, "reason": "edited_in_obsidian"})
                    with self.db:
                        self.db.execute("INSERT INTO memory_mirror_conflicts(memory_id,relative_path,expected_sha256,"
                                        "found_sha256,detected_at) VALUES(?,?,?,?,?)",
                                        (memory["id"], relative, record["written_sha256"], found, _iso(self.clock())))
                    continue
                if record["revision"] == memory["revision"]:
                    unchanged.append(memory["id"])
                    continue
            elif record is None and path.exists():
                conflicts.append({"memory_id": memory["id"], "path": relative, "reason": "foreign_file_exists"})
                continue
            data = content.encode("utf-8")
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".muse-mirror-", suffix=".tmp")
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            os.replace(tmp, path)
            with self.db:
                self.db.execute("INSERT INTO memory_obsidian_mirror(memory_id,relative_path,revision,written_sha256,mirrored_at)"
                                " VALUES(?,?,?,?,?) ON CONFLICT(memory_id) DO UPDATE SET revision=excluded.revision,"
                                " written_sha256=excluded.written_sha256, mirrored_at=excluded.mirrored_at",
                                (memory["id"], relative, memory["revision"], hashlib.sha256(data).hexdigest(),
                                 _iso(self.clock())))
            written.append(memory["id"])
        return {"written": written, "unchanged": unchanged, "conflicts": conflicts, "direction": "muse_to_obsidian_only"}

    # ------------------------------------------------------------------ Agent-Track receipts
    def approve_agent_track(self, candidate_id: int, *, approved_by: str) -> dict[str, Any]:
        candidate = self.store.get_candidate(int(candidate_id))
        if candidate is None or candidate.get("track") != "agent":
            raise ValueError("candidate is not an Agent-Track memory")
        if approved_by.strip().casefold() in {"muse", "agent", "muse-agent", str(candidate.get("source", "")).casefold()}:
            raise PermissionError("Agent-Track memories need a human approver distinct from the proposer")
        approved = self.store.approve_candidate(int(candidate_id), approved_by=approved_by)
        existing = self.db.execute("SELECT * FROM agent_track_receipts WHERE candidate_id=?", (int(candidate_id),)).fetchone()
        if existing is not None:
            return dict(existing)
        prev = self.db.execute("SELECT receipt_hash FROM agent_track_receipts ORDER BY seq DESC LIMIT 1").fetchone()
        prev_hash = prev[0] if prev else "0" * 64
        issued = _iso(self.clock())
        body = {"candidate_id": int(candidate_id), "memory_id": approved["memory_id"], "approved_by": approved_by,
                "content_sha256": _sha(approved["content"]), "prev_hash": prev_hash, "issued_at": issued}
        receipt_hash = _sha(json.dumps(body, sort_keys=True, ensure_ascii=False))
        with self.db:
            self.db.execute("INSERT INTO agent_track_receipts(candidate_id,memory_id,approved_by,content_sha256,prev_hash,"
                            "receipt_hash,issued_at) VALUES(?,?,?,?,?,?,?)",
                            (body["candidate_id"], body["memory_id"], approved_by, body["content_sha256"], prev_hash,
                             receipt_hash, issued))
        return {**body, "receipt_hash": receipt_hash}

    def verify_receipt_chain(self) -> dict[str, Any]:
        prev = "0" * 64
        rows = self.db.execute("SELECT * FROM agent_track_receipts ORDER BY seq").fetchall()
        for row in rows:
            body = {"candidate_id": row["candidate_id"], "memory_id": row["memory_id"], "approved_by": row["approved_by"],
                    "content_sha256": row["content_sha256"], "prev_hash": row["prev_hash"], "issued_at": row["issued_at"]}
            if row["prev_hash"] != prev or _sha(json.dumps(body, sort_keys=True, ensure_ascii=False)) != row["receipt_hash"]:
                return {"ok": False, "broken_at": row["seq"]}
            prev = row["receipt_hash"]
        return {"ok": True, "receipts": len(rows), "head": prev}
