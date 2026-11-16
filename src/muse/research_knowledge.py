"""Persistent typed research knowledge graph for Muse.

The store keeps compact knowledge metadata and references to the existing evidence
and task stores; it never duplicates fetched source bodies or owns task scheduling.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid
from typing import Any, Iterable


NODE_TYPES = frozenset({"source", "idea", "experiment", "claim", "decision", "failure"})
EDGE_TYPES = frozenset({
    "supports", "refutes", "relates", "inspired_by", "contradicts", "alternative",
    "supersedes", "tested_by", "addresses_gap",
})
CLAIM_STATUSES = frozenset({"proposed", "unverified", "supported", "refuted", "contested", "superseded"})
_ADVANCED_CLAIM_STATUSES = frozenset({"supported", "refuted", "contested", "superseded"})
CLAIM_EVIDENCE_DIRECTIONS = frozenset({
    "support_search_candidate", "counter_search_candidate", "context_candidate",
})
EVIDENCE_QUALITY_LEVELS = frozenset({"unassessed", "very_low", "low", "moderate", "high"})
SOURCE_INDEPENDENCE_LEVELS = frozenset({"unassessed", "independent", "dependent", "unclear"})
CLAIM_CONFIDENCE_LEVELS = frozenset({"unassessed", "low", "moderate", "high"})
EVIDENCE_REVIEW_VERDICTS = frozenset({"supports", "refutes", "mentions", "insufficient"})
SOURCE_RELATIONSHIPS = frozenset({
    "cites", "derived_from", "syndicated_from", "same_publisher",
    "same_ownership", "independent_of", "related",
})
SOURCE_IDENTITY_CANDIDATE_TYPES = frozenset({
    "canonical_equivalent", "same_publisher", "same_author", "cites", "derived_from",
})



class ResearchKnowledgeStore:
    """SQLite knowledge graph with idempotent nodes and append-only audit events."""

    SCHEMA_VERSION = 5
    def __init__(self, database: str | Path) -> None:
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS research_knowledge_meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS knowledge_nodes (
                id TEXT PRIMARY KEY,
                node_type TEXT NOT NULL CHECK(node_type IN
                    ('source','idea','experiment','claim','decision','failure')),
                natural_key TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                external_ref TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                provenance_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                deleted_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_knowledge_nodes_type_status
                ON knowledge_nodes(node_type, status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_knowledge_nodes_external_ref
                ON knowledge_nodes(external_ref);
            CREATE TABLE IF NOT EXISTS knowledge_edges (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES knowledge_nodes(id),
                target_id TEXT NOT NULL REFERENCES knowledge_nodes(id),
                edge_type TEXT NOT NULL CHECK(edge_type IN
                    ('supports','refutes','relates','inspired_by','contradicts',
                     'supersedes','tested_by','addresses_gap','alternative')),
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE(source_id, target_id, edge_type),
                CHECK(source_id <> target_id)
            );
            CREATE INDEX IF NOT EXISTS idx_knowledge_edges_target
                ON knowledge_edges(target_id, edge_type);
            CREATE TABLE IF NOT EXISTS claim_evidence (
                id TEXT PRIMARY KEY,
                claim_id TEXT NOT NULL REFERENCES knowledge_nodes(id),
                source_node_id TEXT NOT NULL REFERENCES knowledge_nodes(id),
                direction TEXT NOT NULL CHECK(direction IN
                    ('support_search_candidate','counter_search_candidate','context_candidate')),
                evidence_type TEXT NOT NULL,
                quality TEXT NOT NULL CHECK(quality IN
                    ('unassessed','very_low','low','moderate','high')),
                independence TEXT NOT NULL CHECK(independence IN
                    ('unassessed','independent','dependent','unclear')),
                search_query TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(claim_id,source_node_id,direction,search_query)
            );
            CREATE INDEX IF NOT EXISTS idx_claim_evidence_claim
                ON claim_evidence(claim_id,created_at);
            CREATE TRIGGER IF NOT EXISTS claim_evidence_no_update
                BEFORE UPDATE ON claim_evidence BEGIN
                    SELECT RAISE(ABORT, 'claim evidence is append-only');
                END;
            CREATE TRIGGER IF NOT EXISTS claim_evidence_no_delete
                BEFORE DELETE ON claim_evidence BEGIN
                    SELECT RAISE(ABORT, 'claim evidence is append-only');
                END;
            CREATE TABLE IF NOT EXISTS source_relationships (
                id TEXT PRIMARY KEY,
                source_node_id TEXT NOT NULL REFERENCES knowledge_nodes(id),
                related_source_node_id TEXT NOT NULL REFERENCES knowledge_nodes(id),
                relationship TEXT NOT NULL CHECK(relationship IN
                    ('cites','derived_from','syndicated_from','same_publisher',
                     'same_ownership','independent_of','related')),
                confidence TEXT NOT NULL CHECK(confidence IN
                    ('unassessed','low','moderate','high')),
                basis TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                CHECK(source_node_id <> related_source_node_id)
            );
            CREATE INDEX IF NOT EXISTS idx_source_relationships_source
                ON source_relationships(source_node_id,created_at);
            CREATE INDEX IF NOT EXISTS idx_source_relationships_related
                ON source_relationships(related_source_node_id,created_at);
            CREATE TRIGGER IF NOT EXISTS source_relationships_no_update
                BEFORE UPDATE ON source_relationships BEGIN
                    SELECT RAISE(ABORT, 'source relationships are append-only');
                END;
            CREATE TRIGGER IF NOT EXISTS source_relationships_no_delete
                BEFORE DELETE ON source_relationships BEGIN
                    SELECT RAISE(ABORT, 'source relationships are append-only');
                END;
            CREATE TABLE IF NOT EXISTS claim_evidence_reviews (
                id TEXT PRIMARY KEY,
                claim_evidence_id TEXT NOT NULL REFERENCES claim_evidence(id),
                reviewer_id TEXT NOT NULL,
                verdict TEXT NOT NULL CHECK(verdict IN
                    ('supports','refutes','mentions','insufficient')),
                quality TEXT NOT NULL CHECK(quality IN
                    ('unassessed','very_low','low','moderate','high')),
                independence TEXT NOT NULL CHECK(independence IN
                    ('unassessed','independent','dependent','unclear')),
                rationale TEXT NOT NULL,
                reviewed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_claim_evidence_reviews_item
                ON claim_evidence_reviews(claim_evidence_id,reviewed_at);
            CREATE TRIGGER IF NOT EXISTS claim_evidence_reviews_no_update
                BEFORE UPDATE ON claim_evidence_reviews BEGIN
                    SELECT RAISE(ABORT, 'claim evidence reviews are append-only');
                END;
            CREATE TRIGGER IF NOT EXISTS claim_evidence_reviews_no_delete
                BEFORE DELETE ON claim_evidence_reviews BEGIN
                    SELECT RAISE(ABORT, 'claim evidence reviews are append-only');
                END;
            CREATE TABLE IF NOT EXISTS source_identity_candidates (
                id TEXT PRIMARY KEY,
                source_node_id TEXT NOT NULL REFERENCES knowledge_nodes(id),
                related_source_node_id TEXT NOT NULL REFERENCES knowledge_nodes(id),
                candidate_type TEXT NOT NULL CHECK(candidate_type IN
                    ('canonical_equivalent','same_publisher','same_author','cites','derived_from')),
                confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
                evidence_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(source_node_id,related_source_node_id,candidate_type),
                CHECK(source_node_id <> related_source_node_id)
            );
            CREATE INDEX IF NOT EXISTS idx_source_identity_candidates_source
                ON source_identity_candidates(source_node_id,created_at);
            CREATE INDEX IF NOT EXISTS idx_source_identity_candidates_related
                ON source_identity_candidates(related_source_node_id,created_at);
            CREATE TRIGGER IF NOT EXISTS source_identity_candidates_no_update
                BEFORE UPDATE ON source_identity_candidates BEGIN
                    SELECT RAISE(ABORT, 'source identity candidates are append-only');
                END;
            CREATE TRIGGER IF NOT EXISTS source_identity_candidates_no_delete
                BEFORE DELETE ON source_identity_candidates BEGIN
                    SELECT RAISE(ABORT, 'source identity candidates are append-only');
                END;
            CREATE TABLE IF NOT EXISTS source_identity_candidate_reviews (
                id TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL REFERENCES source_identity_candidates(id),
                reviewer_id TEXT NOT NULL,
                decision TEXT NOT NULL CHECK(decision IN ('accepted','rejected')),
                rationale TEXT NOT NULL,
                reviewed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_source_identity_candidate_reviews
                ON source_identity_candidate_reviews(candidate_id,reviewed_at);
            CREATE TRIGGER IF NOT EXISTS source_identity_candidate_reviews_no_update
                BEFORE UPDATE ON source_identity_candidate_reviews BEGIN
                    SELECT RAISE(ABORT, 'source identity candidate reviews are append-only');
                END;
            CREATE TRIGGER IF NOT EXISTS source_identity_candidate_reviews_no_delete
                BEFORE DELETE ON source_identity_candidate_reviews BEGIN
                    SELECT RAISE(ABORT, 'source identity candidate reviews are append-only');
                END;
            CREATE TABLE IF NOT EXISTS knowledge_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                entity_type TEXT NOT NULL CHECK(entity_type IN ('node','edge','task')),
                entity_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_knowledge_events_entity
                ON knowledge_events(entity_type, entity_id, sequence);
            CREATE TRIGGER IF NOT EXISTS knowledge_events_no_update
                BEFORE UPDATE ON knowledge_events BEGIN
                    SELECT RAISE(ABORT, 'knowledge events are append-only');
                END;
            CREATE TRIGGER IF NOT EXISTS knowledge_events_no_delete
                BEFORE DELETE ON knowledge_events BEGIN
                    SELECT RAISE(ABORT, 'knowledge events are append-only');
                END;
        """)
        self.connection.execute(
            "INSERT OR IGNORE INTO research_knowledge_meta(key,value) VALUES('schema_version',?)",
            (str(self.SCHEMA_VERSION),),
        )
        version = self.connection.execute(
            "SELECT value FROM research_knowledge_meta WHERE key='schema_version'"
        ).fetchone()["value"]

        if version in {"1", "2", "3", "4"}:
            with self.connection:
                edge_schema = self.connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='knowledge_edges'"
                ).fetchone()
                if edge_schema is not None and "'alternative'" not in str(edge_schema["sql"]):
                    self.connection.execute("ALTER TABLE knowledge_edges RENAME TO knowledge_edges_legacy")
                    self.connection.execute("""
                        CREATE TABLE knowledge_edges (
                            id TEXT PRIMARY KEY,
                            source_id TEXT NOT NULL REFERENCES knowledge_nodes(id),
                            target_id TEXT NOT NULL REFERENCES knowledge_nodes(id),
                            edge_type TEXT NOT NULL CHECK(edge_type IN
                                ('supports','refutes','relates','inspired_by','contradicts',
                                 'supersedes','tested_by','addresses_gap','alternative')),
                            metadata_json TEXT NOT NULL DEFAULT '{}',
                            created_at TEXT NOT NULL,
                            UNIQUE(source_id, target_id, edge_type),
                            CHECK(source_id <> target_id)
                        )
                    """)
                    self.connection.execute(
                        "INSERT INTO knowledge_edges(id,source_id,target_id,edge_type,metadata_json,created_at) "
                        "SELECT id,source_id,target_id,edge_type,metadata_json,created_at FROM knowledge_edges_legacy"
                    )
                    self.connection.execute("DROP TABLE knowledge_edges_legacy")
                    self.connection.execute(
                        "CREATE INDEX IF NOT EXISTS idx_knowledge_edges_target ON knowledge_edges(target_id,edge_type)"
                    )
                self.connection.execute(
                    "UPDATE research_knowledge_meta SET value=? WHERE key='schema_version'",
                    (str(self.SCHEMA_VERSION),),
                )
            version = str(self.SCHEMA_VERSION)
        if version != str(self.SCHEMA_VERSION):
            self.connection.close()
            raise RuntimeError(f"unsupported research knowledge schema version: {version}")
        self.connection.commit()
    @staticmethod
    def _text(value: Any, field: str, limit: int, *, optional: bool = False) -> str:
        if optional and value is None:
            return ""
        if not isinstance(value, str) or (not optional and not value.strip()) or len(value) > limit:
            raise ValueError(f"{field} must be text of at most {limit} characters")
        return value.strip()

    @staticmethod
    def _json_object(value: Any, field: str) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError(f"{field} must be an object")
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if len(encoded.encode("utf-8")) > 16000:
            raise ValueError(f"{field} exceeds 16 KiB")
        return value

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ResearchKnowledgeStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _event(self, entity_type: str, entity_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO knowledge_events(event_id,entity_type,entity_id,event_type,payload_json,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (uuid.uuid4().hex, entity_type, entity_id, event_type,
             json.dumps(payload, ensure_ascii=False, sort_keys=True), self._now()),
        )

    @staticmethod
    def _decode_node(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": row["id"], "node_type": row["node_type"], "title": row["title"],
            "summary": row["summary"], "status": row["status"],
            "external_ref": row["external_ref"],
            "metadata": json.loads(row["metadata_json"]),
            "provenance": json.loads(row["provenance_json"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def upsert_node(
        self, node_type: str, title: str, *, summary: str = "",
        external_ref: str | None = None, metadata: dict[str, Any] | None = None,
        provenance: Iterable[str] = (), status: str | None = None,
    ) -> dict[str, Any]:
        if node_type not in NODE_TYPES:
            raise ValueError("unsupported research knowledge node type")
        title = self._text(title, "title", 500)
        summary = self._text(summary, "summary", 2000, optional=True)
        if external_ref is not None:
            external_ref = self._text(external_ref, "external_ref", 4096)
        status = self._text(status if status is not None else ("proposed" if node_type == "claim" else "active"), "status", 40)
        if node_type == "claim" and status not in CLAIM_STATUSES:
            raise ValueError("claim nodes must use a supported claim lifecycle status")
        if node_type == "claim" and status in _ADVANCED_CLAIM_STATUSES:
            raise ValueError("claim status requires transition_claim with evidence and reviewer")
        clean_metadata = self._json_object(metadata, "metadata")
        if isinstance(provenance, (str, bytes)):
            raise ValueError("provenance must be an iterable of references")
        clean_provenance = list(dict.fromkeys(
            self._text(item, "provenance reference", 4096) for item in provenance
        ))
        if len(clean_provenance) > 32:
            raise ValueError("provenance supports at most 32 references")
        natural_key = (
            f"{node_type}:{external_ref}" if external_ref
            else f"{node_type}:id:{uuid.uuid4().hex}"
        )
        now = self._now()
        with self.connection:
            existing = self.connection.execute(
                "SELECT * FROM knowledge_nodes WHERE natural_key=?", (natural_key,)
            ).fetchone()
            if existing:
                if existing["deleted_at"] is not None:
                    raise ValueError("tombstoned knowledge nodes cannot be silently resurrected")
                if node_type == "claim" and existing["status"] != status:
                    raise ValueError("claim lifecycle status can only change through transition_claim")
                old_meta = json.loads(existing["metadata_json"])
                merged_meta = {**old_meta, **clean_metadata}
                # Attribution records accumulate without duplicating the source body.
                for list_key in ("attributions", "task_ids"):
                    if isinstance(old_meta.get(list_key), list) and isinstance(clean_metadata.get(list_key), list):
                        merged_meta[list_key] = list({
                            json.dumps(item, ensure_ascii=False, sort_keys=True): item
                            for item in [*old_meta[list_key], *clean_metadata[list_key]]
                        }.values())
                old_provenance = json.loads(existing["provenance_json"])
                merged_provenance = list(dict.fromkeys([*old_provenance, *clean_provenance]))
                self.connection.execute(
                    "UPDATE knowledge_nodes SET title=?,summary=?,status=?,metadata_json=?,"
                    "provenance_json=?,updated_at=? WHERE id=?",
                    (title, summary or existing["summary"], status,
                     json.dumps(merged_meta, ensure_ascii=False, sort_keys=True),
                     json.dumps(merged_provenance, ensure_ascii=False), now, existing["id"]),
                )
                node_id, event_type = existing["id"], "node_upserted"
            else:
                node_id = hashlib.sha256(natural_key.encode("utf-8")).hexdigest()[:32]
                self.connection.execute(
                    "INSERT INTO knowledge_nodes(id,node_type,natural_key,title,summary,status,"
                    "external_ref,metadata_json,provenance_json,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (node_id, node_type, natural_key, title, summary, status, external_ref,
                     json.dumps(clean_metadata, ensure_ascii=False, sort_keys=True),
                     json.dumps(clean_provenance, ensure_ascii=False), now, now),
                )
                event_type = "node_created"
            self._event("node", node_id, event_type, {
                "node_type": node_type, "external_ref": external_ref,
                "metadata_keys": sorted(clean_metadata), "provenance": clean_provenance,
            })
        result = self.get_node(node_id)
        assert result is not None
        return result

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM knowledge_nodes WHERE id=? AND deleted_at IS NULL", (node_id,)
        ).fetchone()
        return self._decode_node(row)

    def tombstone_node(self, node_id: str, *, actor: str, reason: str) -> dict[str, str]:
        node_id = self._text(node_id, "node_id", 128)
        actor = self._text(actor, "actor", 120)
        reason = self._text(reason, "reason", 500)
        node = self.get_node(node_id)
        if node is None:
            raise ValueError("knowledge node does not exist or is already tombstoned")
        now = self._now()
        with self.connection:
            self.connection.execute(
                "UPDATE knowledge_nodes SET status='deleted',deleted_at=?,updated_at=? WHERE id=?",
                (now, now, node_id),
            )
            self._event("node", node_id, "node_tombstoned", {
                "node_type": node["node_type"], "actor": actor, "reason": reason,
            })
        return {"id": node_id, "status": "deleted", "deleted_at": now}

    def list_nodes(
        self, *, node_type: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        clauses, params = ["deleted_at IS NULL"], []
        if node_type is not None:
            if node_type not in NODE_TYPES:
                raise ValueError("unsupported research knowledge node type")
            clauses.append("node_type=?")
            params.append(node_type)
        if status is not None:
            clauses.append("status=?")
            params.append(self._text(status, "status", 40))
        rows = self.connection.execute(
            "SELECT * FROM knowledge_nodes WHERE " + " AND ".join(clauses) +
            " ORDER BY updated_at DESC LIMIT ?", (*params, limit),
        ).fetchall()
        return [self._decode_node(row) for row in rows]

    def list_nodes_for_evolution_cycle(self, evolution_cycle_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        """Return active knowledge nodes explicitly tagged with one exact Muse cycle.

        Both the original singular field and the bounded multi-cycle association field
        are recognized. This avoids scanning unrelated graph nodes when linking work.
        """
        if not isinstance(evolution_cycle_id, str) or not re.fullmatch(r"cycle_[0-9a-f]{32}", evolution_cycle_id):
            raise ValueError("evolution_cycle_id must be a valid Muse Evolution Cycle ID")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        rows = self.connection.execute(
            "SELECT * FROM knowledge_nodes WHERE deleted_at IS NULL AND ("
            "json_extract(metadata_json,'$.evolution_cycle_id')=? OR EXISTS ("
            "SELECT 1 FROM json_each(CASE "
            "WHEN json_type(metadata_json,'$.evolution_cycle_ids')='array' "
            "THEN json_extract(metadata_json,'$.evolution_cycle_ids') ELSE '[]' END) "
            "WHERE value=?)) ORDER BY updated_at DESC,id LIMIT ?",
            (evolution_cycle_id, evolution_cycle_id, limit),
        ).fetchall()
        return [self._decode_node(row) for row in rows]

    def _insert_edge(
        self, source_id: str, target_id: str, edge_type: str, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        active_endpoints = self.connection.execute(
            "SELECT count(*) FROM knowledge_nodes WHERE id IN (?,?) AND deleted_at IS NULL",
            (source_id, target_id),
        ).fetchone()[0]
        if active_endpoints != 2:
            raise ValueError("knowledge edges require two active node endpoints")
        now = self._now()
        edge_id = hashlib.sha256(f"{source_id}:{target_id}:{edge_type}".encode()).hexdigest()[:32]
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO knowledge_edges(id,source_id,target_id,edge_type,metadata_json,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (edge_id, source_id, target_id, edge_type,
             json.dumps(metadata, ensure_ascii=False, sort_keys=True), now),
        )
        if cursor.rowcount:
            self._event("edge", edge_id, "edge_created", {
                "source_id": source_id, "target_id": target_id, "edge_type": edge_type,
            })
        row = self.connection.execute("SELECT * FROM knowledge_edges WHERE id=?", (edge_id,)).fetchone()
        return {
            "id": row["id"], "source_id": row["source_id"], "target_id": row["target_id"],
            "edge_type": row["edge_type"], "metadata": json.loads(row["metadata_json"]),
            "created_at": row["created_at"],
        }

    def add_edge(
        self, source_id: str, target_id: str, edge_type: str, *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if edge_type not in EDGE_TYPES:
            raise ValueError("unsupported research knowledge edge type")
        source_id = self._text(source_id, "source_id", 128)
        target_id = self._text(target_id, "target_id", 128)
        if source_id == target_id:
            raise ValueError("self-referential knowledge edges are not allowed")
        clean_metadata = self._json_object(metadata, "metadata")
        with self.connection:
            return self._insert_edge(source_id, target_id, edge_type, clean_metadata)

    def list_edges(
        self, *, node_id: str | None = None, edge_type: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        """List active graph relationships without exposing deleted endpoints."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        clauses = ["s.deleted_at IS NULL", "t.deleted_at IS NULL"]
        params: list[Any] = []
        if node_id is not None:
            node_id = self._text(node_id, "node_id", 128)
            clauses.append("(e.source_id=? OR e.target_id=?)")
            params.extend((node_id, node_id))
        if edge_type is not None:
            if edge_type not in EDGE_TYPES:
                raise ValueError("unsupported research knowledge edge type")
            clauses.append("e.edge_type=?")
            params.append(edge_type)
        rows = self.connection.execute(
            "SELECT e.* FROM knowledge_edges e "
            "JOIN knowledge_nodes s ON s.id=e.source_id "
            "JOIN knowledge_nodes t ON t.id=e.target_id WHERE " + " AND ".join(clauses) +
            " ORDER BY e.created_at DESC,e.id LIMIT ?", (*params, limit),
        ).fetchall()
        return [{
            "id": row["id"], "source_id": row["source_id"], "target_id": row["target_id"],
            "edge_type": row["edge_type"], "metadata": json.loads(row["metadata_json"]),
            "created_at": row["created_at"],
        } for row in rows]

    def transition_claim(
        self, claim_id: str, status: str, *, evidence_refs: Iterable[str], reviewer_id: str
    ) -> dict[str, Any]:
        if status not in CLAIM_STATUSES:
            raise ValueError("unsupported claim status")
        reviewer_id = self._text(reviewer_id, "reviewer_id", 120)
        if isinstance(evidence_refs, (str, bytes)):
            raise ValueError("evidence_refs must be an iterable of stored references")
        refs = list(dict.fromkeys(self._text(ref, "evidence reference", 4096) for ref in evidence_refs))
        if status in _ADVANCED_CLAIM_STATUSES and not refs:
            raise ValueError("claim status requires at least one evidence reference")
        if not refs:
            raise ValueError("claim transition requires evidence references")
        claim = self.get_node(self._text(claim_id, "claim_id", 128))
        if claim is None or claim["node_type"] != "claim":
            raise ValueError("claim_id must identify an existing claim node")
        author_id = claim["metadata"].get("author_id")
        if author_id and author_id == reviewer_id:
            raise ValueError("claim reviewer must be independent from its author")
        evidence_nodes = []
        for ref in refs:
            row = self.connection.execute(
                "SELECT id,node_type FROM knowledge_nodes WHERE deleted_at IS NULL AND (id=? OR external_ref=?)",
                (ref, ref),
            ).fetchone()
            if row is None or row["id"] == claim_id or row["node_type"] not in {"source", "experiment"}:
                raise ValueError(f"evidence reference must identify a stored source or experiment: {ref[:80]}")
            evidence_nodes.append(row["id"])
        now = self._now()
        metadata = {**claim["metadata"], "evidence_refs": refs, "reviewer_id": reviewer_id}
        with self.connection:
            self.connection.execute(
                "UPDATE knowledge_nodes SET status=?,metadata_json=?,updated_at=? WHERE id=?",
                (status, json.dumps(metadata, ensure_ascii=False, sort_keys=True), now, claim_id),
            )
            for evidence_id in evidence_nodes:
                relation = {"refuted": "refutes", "contested": "contradicts",
                            "superseded": "supersedes"}.get(status, "supports")
                self._insert_edge(evidence_id, claim_id, relation, {"reviewer_id": reviewer_id})
            self._event("node", claim_id, "claim_status_changed", {
                "from": claim["status"], "to": status, "evidence_refs": refs,
                "reviewer_id": reviewer_id,
            })
        result = self.get_node(claim_id)
        assert result is not None
        return result

    def record_claim_review(
        self, claim_id: str, *, reviewer_id: str, assessment: dict[str, Any],
        evidence_refs: Iterable[str], confidence_level: str = "unassessed",
    ) -> dict[str, Any]:
        """Append a role-separated review record without promoting a claim to fact."""
        reviewer_id = self._text(reviewer_id, "reviewer_id", 120)
        claim = self.get_node(self._text(claim_id, "claim_id", 128))
        if claim is None or claim["node_type"] != "claim":
            raise ValueError("claim_id must identify an existing claim")
        author_id = claim["metadata"].get("author_id")
        if author_id and author_id == reviewer_id:
            raise ValueError("claim reviewer must be independent from its author")
        if not isinstance(assessment, dict):
            raise ValueError("assessment must be an object")
        verdict = assessment.get("verdict")
        if verdict not in {"supported", "refuted", "contested", "insufficient"}:
            raise ValueError("review verdict is invalid")
        rationale = self._text(assessment.get("rationale"), "review rationale", 1200)
        if confidence_level not in CLAIM_CONFIDENCE_LEVELS:
            raise ValueError("unsupported claim confidence level")
        if isinstance(evidence_refs, (str, bytes)):
            raise ValueError("evidence_refs must be an iterable")
        refs = list(dict.fromkeys(self._text(ref, "evidence reference", 4096) for ref in evidence_refs))
        if not refs:
            raise ValueError("claim review requires stored evidence references")
        for ref in refs:
            row = self.connection.execute(
                "SELECT id,node_type FROM knowledge_nodes WHERE deleted_at IS NULL AND (id=? OR external_ref=?)",
                (ref, ref),
            ).fetchone()
            if row is None or row["node_type"] != "source":
                raise ValueError("claim review evidence must identify stored source nodes")
        review = {"reviewer_id": reviewer_id, "verdict": verdict, "rationale": rationale,
                  "evidence_refs": refs, "confidence_level": confidence_level,
                  "confidence_basis": "reviewer_assessment_only_not_automatically_scored",
                  "review_state": "separate_review_pending_human_confirmation",
                  "reviewer_independence": "role_separated_not_independent_model",
                  "does_not_promote_claim": True}
        metadata = {**claim["metadata"], "claim_review": review}
        now = self._now()
        with self.connection:
            self.connection.execute(
                "UPDATE knowledge_nodes SET metadata_json=?,updated_at=? WHERE id=?",
                (json.dumps(metadata, ensure_ascii=False, sort_keys=True), now, claim_id),
            )
            self._event("node", claim_id, "claim_review_recorded", review)
        result = self.get_node(claim_id)
        assert result is not None
        return result

    def record_claim_evidence(
        self, claim_id: str, source_ref: str, *, direction: str,
        evidence_type: str = "unspecified", quality: str = "unassessed",
        independence: str = "unassessed", search_query: str = "", notes: str = "",
    ) -> dict[str, Any]:
        """Append a source-to-claim evidence candidate without asserting its verdict.

        Direction describes why a source was retrieved, not what its contents prove.
        Quality and independence remain unassessed until an explicit review supplies them.
        """
        claim_id = self._text(claim_id, "claim_id", 128)
        source_ref = self._text(source_ref, "source_ref", 4096)
        if direction not in CLAIM_EVIDENCE_DIRECTIONS:
            raise ValueError("unsupported claim evidence direction")
        if quality not in EVIDENCE_QUALITY_LEVELS:
            raise ValueError("unsupported evidence quality level")
        if independence not in SOURCE_INDEPENDENCE_LEVELS:
            raise ValueError("unsupported source independence level")
        evidence_type = self._text(evidence_type, "evidence_type", 80)
        search_query = self._text(search_query, "search_query", 500, optional=True)
        notes = self._text(notes, "notes", 1000, optional=True)

        claim = self.get_node(claim_id)
        if claim is None or claim["node_type"] != "claim":
            raise ValueError("claim_id must identify an active claim node")
        source = self.connection.execute(
            "SELECT id FROM knowledge_nodes WHERE deleted_at IS NULL "
            "AND node_type='source' AND (id=? OR external_ref=?)",
            (source_ref, source_ref),
        ).fetchone()
        if source is None:
            raise ValueError("claim evidence must identify a stored source node")
        source_node_id = source["id"]
        evidence_id = hashlib.sha256(
            f"{claim_id}:{source_node_id}:{direction}:{search_query}".encode("utf-8")
        ).hexdigest()[:32]
        now = self._now()
        with self.connection:
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO claim_evidence(id,claim_id,source_node_id,direction,"
                "evidence_type,quality,independence,search_query,notes,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (evidence_id, claim_id, source_node_id, direction, evidence_type,
                 quality, independence, search_query, notes, now),
            )
            if cursor.rowcount:
                self._insert_edge(source_node_id, claim_id, "relates", {
                    "evidence_id": evidence_id,
                    "direction": direction,
                    "relation_is_not_support_or_refutation": True,
                })
                self._event("node", claim_id, "claim_evidence_recorded", {
                    "evidence_id": evidence_id, "source_node_id": source_node_id,
                    "direction": direction, "evidence_type": evidence_type,
                    "quality": quality, "independence": independence,
                })
        row = self.connection.execute(
            "SELECT * FROM claim_evidence WHERE id=?", (evidence_id,)
        ).fetchone()
        return dict(row)

    def record_claim_evidence_review(
        self, evidence_id: str, *, reviewer_id: str, verdict: str,
        rationale: str, quality: str = "unassessed",
        independence: str = "unassessed",
    ) -> dict[str, Any]:
        """Append a source-content assessment; never rewrite retrieval intent or claim status."""
        evidence_id = self._text(evidence_id, "evidence_id", 128)
        reviewer_id = self._text(reviewer_id, "reviewer_id", 120)
        rationale = self._text(rationale, "rationale", 1600)
        if verdict not in EVIDENCE_REVIEW_VERDICTS:
            raise ValueError("unsupported evidence review verdict")
        if quality not in EVIDENCE_QUALITY_LEVELS:
            raise ValueError("unsupported evidence quality")
        if independence not in SOURCE_INDEPENDENCE_LEVELS:
            raise ValueError("unsupported source independence")
        evidence = self.connection.execute(
            "SELECT id,claim_id,source_node_id FROM claim_evidence WHERE id=?",
            (evidence_id,),
        ).fetchone()
        if evidence is None:
            raise ValueError("claim evidence item does not exist")
        claim = self.get_node(evidence["claim_id"])
        if claim is None:
            raise ValueError("claim evidence parent claim is unavailable")
        author_id = claim["metadata"].get("author_id")
        if author_id and author_id == reviewer_id:
            raise ValueError("evidence reviewer must be independent from claim author")
        review_id = uuid.uuid4().hex
        reviewed_at = self._now()
        review = {
            "id": review_id, "claim_evidence_id": evidence_id,
            "reviewer_id": reviewer_id, "verdict": verdict,
            "quality": quality, "independence": independence,
            "rationale": rationale, "reviewed_at": reviewed_at,
        }
        with self.connection:
            self.connection.execute(
                "INSERT INTO claim_evidence_reviews(id,claim_evidence_id,reviewer_id,"
                "verdict,quality,independence,rationale,reviewed_at) VALUES(?,?,?,?,?,?,?,?)",
                (review_id, evidence_id, reviewer_id, verdict, quality,
                 independence, rationale, reviewed_at),
            )
            self._event("node", evidence["claim_id"], "claim_evidence_review_recorded", {
                **review, "source_node_id": evidence["source_node_id"],
            })
        return review

    def record_source_relationship(
        self, source_ref: str, related_source_ref: str, *, relationship: str,
        reviewer_id: str, basis: str, confidence: str = "unassessed",
    ) -> dict[str, Any]:
        """Record provenance/ownership links as reviewed assertions, not inferred facts."""
        source_ref = self._text(source_ref, "source_ref", 4096)
        related_source_ref = self._text(related_source_ref, "related_source_ref", 4096)
        reviewer_id = self._text(reviewer_id, "reviewer_id", 120)
        basis = self._text(basis, "basis", 1600)
        if relationship not in SOURCE_RELATIONSHIPS:
            raise ValueError("unsupported source relationship")
        if confidence not in CLAIM_CONFIDENCE_LEVELS:
            raise ValueError("unsupported relationship confidence")
        rows = []
        for ref in (source_ref, related_source_ref):
            row = self.connection.execute(
                "SELECT id FROM knowledge_nodes WHERE deleted_at IS NULL "
                "AND node_type='source' AND (id=? OR external_ref=?)", (ref, ref),
            ).fetchone()
            if row is None:
                raise ValueError("source relationship requires two stored source nodes")
            rows.append(row["id"])
        if rows[0] == rows[1]:
            raise ValueError("source relationship endpoints must differ")
        relation_id = uuid.uuid4().hex
        created_at = self._now()
        with self.connection:
            self.connection.execute(
                "INSERT INTO source_relationships(id,source_node_id,related_source_node_id,"
                "relationship,confidence,basis,reviewer_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (relation_id, rows[0], rows[1], relationship, confidence,
                 basis, reviewer_id, created_at),
            )
            self._event("node", rows[0], "source_relationship_recorded", {
                "relationship_id": relation_id, "related_source_node_id": rows[1],
                "relationship": relationship, "confidence": confidence,
                "reviewer_id": reviewer_id, "basis": basis,
            })
        return {
            "id": relation_id, "source_node_id": rows[0],
            "related_source_node_id": rows[1], "relationship": relationship,
            "confidence": confidence, "basis": basis, "reviewer_id": reviewer_id,
            "created_at": created_at,
        }

    def record_source_identity_candidate(
        self, source_ref: str, related_source_ref: str, *, candidate_type: str,
        confidence: float, evidence: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist an inferred source relationship as an unreviewed candidate only."""
        source_ref = self._text(source_ref, "source_ref", 4096)
        related_source_ref = self._text(related_source_ref, "related_source_ref", 4096)
        if candidate_type not in SOURCE_IDENTITY_CANDIDATE_TYPES:
            raise ValueError("unsupported source identity candidate type")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError("candidate confidence must be between 0 and 1")
        clean_evidence = self._json_object(evidence, "evidence")
        nodes = []
        for ref in (source_ref, related_source_ref):
            row = self.connection.execute(
                "SELECT id FROM knowledge_nodes WHERE deleted_at IS NULL "
                "AND node_type='source' AND (id=? OR external_ref=?)", (ref, ref),
            ).fetchone()
            if row is None:
                raise ValueError("source identity candidate requires two stored source nodes")
            nodes.append(row["id"])
        if nodes[0] == nodes[1]:
            raise ValueError("source identity candidate endpoints must differ")
        candidate_id = hashlib.sha256(
            f"{nodes[0]}:{nodes[1]}:{candidate_type}".encode("utf-8")
        ).hexdigest()[:32]
        created_at = self._now()
        with self.connection:
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO source_identity_candidates("
                "id,source_node_id,related_source_node_id,candidate_type,confidence,evidence_json,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (candidate_id, nodes[0], nodes[1], candidate_type, float(confidence),
                 json.dumps(clean_evidence, ensure_ascii=False, sort_keys=True), created_at),
            )
            if cursor.rowcount:
                self._event("node", nodes[0], "source_identity_candidate_created", {
                    "candidate_id": candidate_id, "related_source_node_id": nodes[1],
                    "candidate_type": candidate_type, "confidence": float(confidence),
                    "candidate_is_not_verified": True,
                })
        row = self.connection.execute(
            "SELECT * FROM source_identity_candidates WHERE id=?", (candidate_id,)
        ).fetchone()
        latest_decision = self.connection.execute(
            "SELECT decision FROM source_identity_candidate_reviews WHERE candidate_id=? "
            "ORDER BY reviewed_at DESC,id DESC LIMIT 1", (candidate_id,),
        ).fetchone()
        return {
            **dict(row), "evidence": json.loads(row["evidence_json"]),
            "status": latest_decision["decision"] if latest_decision else "pending",
        }

    def list_source_identity_candidates(
        self, *, status: str = "pending", limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List pending/accepted/rejected source identity hints with their audit history."""
        if status not in {"pending", "accepted", "rejected", "all"}:
            raise ValueError("status must be pending, accepted, rejected or all")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        latest_decision = (
            "(SELECT decision FROM source_identity_candidate_reviews x "
            "WHERE x.candidate_id=c.id ORDER BY reviewed_at DESC,id DESC LIMIT 1)"
        )
        where = "s.deleted_at IS NULL AND r.deleted_at IS NULL"
        params: list[Any] = []
        if status != "all":
            where += f" AND COALESCE({latest_decision},'pending')=?"
            params.append(status)
        params.append(limit)
        rows = self.connection.execute(
            "SELECT c.*,s.title AS source_title,s.external_ref AS source_ref,"
            "r.title AS related_source_title,r.external_ref AS related_source_ref,"
            f"{latest_decision} AS latest_decision "
            "FROM source_identity_candidates c "
            "JOIN knowledge_nodes s ON s.id=c.source_node_id "
            "JOIN knowledge_nodes r ON r.id=c.related_source_node_id "
            f"WHERE {where} "
            "ORDER BY c.created_at DESC,c.id LIMIT ?", tuple(params),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["evidence"] = json.loads(item.pop("evidence_json"))
            item["status"] = item.pop("latest_decision") or "pending"
            item["reviews"] = [dict(review) for review in self.connection.execute(
                "SELECT * FROM source_identity_candidate_reviews WHERE candidate_id=? "
                "ORDER BY reviewed_at,id", (item["id"],)
            )]
            result.append(item)
        return result


    def review_source_identity_candidate(
        self, candidate_id: str, *, reviewer_id: str, decision: str, rationale: str,
    ) -> dict[str, Any]:
        """Append an explicit review; accepted supported types also enter reviewed lineage."""
        candidate_id = self._text(candidate_id, "candidate_id", 128)
        reviewer_id = self._text(reviewer_id, "reviewer_id", 120)
        rationale = self._text(rationale, "rationale", 1600)
        if decision not in {"accepted", "rejected"}:
            raise ValueError("candidate decision must be accepted or rejected")
        candidate = self.connection.execute(
            "SELECT * FROM source_identity_candidates WHERE id=?", (candidate_id,)
        ).fetchone()
        if candidate is None:
            raise ValueError("source identity candidate does not exist")
        review = {
            "id": uuid.uuid4().hex, "candidate_id": candidate_id,
            "reviewer_id": reviewer_id, "decision": decision,
            "rationale": rationale, "reviewed_at": self._now(),
        }
        with self.connection:
            self.connection.execute(
                "INSERT INTO source_identity_candidate_reviews(id,candidate_id,reviewer_id,decision,rationale,reviewed_at) "
                "VALUES(?,?,?,?,?,?)",
                (review["id"], candidate_id, reviewer_id, decision, rationale, review["reviewed_at"]),
            )
            self._event("node", candidate["source_node_id"], "source_identity_candidate_reviewed", {
                **review, "candidate_type": candidate["candidate_type"],
            })
            if decision == "accepted" and candidate["candidate_type"] in SOURCE_RELATIONSHIPS:
                confidence = float(candidate["confidence"])
                level = "high" if confidence >= 0.85 else "moderate" if confidence >= 0.65 else "low"
                self.record_source_relationship(
                    candidate["source_node_id"], candidate["related_source_node_id"],
                    relationship=candidate["candidate_type"], reviewer_id=reviewer_id,
                    basis=f"Accepted source identity candidate: {rationale}", confidence=level,
                )
        return review

    def source_lineage_snapshot(
        self, source_refs: Iterable[str], *, candidate_limit: int = 500,
    ) -> dict[str, Any]:
        """Return the reviewed lineage and unresolved identity hints for one research report.

        Only explicit reviewed dependency/independence relations contribute to counts.
        Unreviewed candidates and unlinked sources remain unknown, never independent by default.
        """
        refs = list(dict.fromkeys(
            self._text(ref, "source_ref", 4096) for ref in source_refs
            if isinstance(ref, str) and ref.strip()
        ))
        if len(refs) > 32:
            raise ValueError("source lineage snapshots support at most 32 report sources")
        if isinstance(candidate_limit, bool) or not isinstance(candidate_limit, int) or not 1 <= candidate_limit <= 500:
            raise ValueError("candidate_limit must be between 1 and 500")
        if not refs:
            return {
                "source_count": 0, "sources": [], "reviewed_relationships": [],
                "identity_candidates": [], "dependency_clusters": [],
                "dependency_relation_count": 0, "dependency_linked_source_count": 0,
                "explicit_independence_pair_count": 0, "explicitly_independent_source_count": 0,
                "unassessed_source_count": 0,
                "independence_assessment": "unassessed",
            }

        placeholders = ",".join("?" for _ in refs)
        rows = self.connection.execute(
            "SELECT * FROM knowledge_nodes WHERE deleted_at IS NULL AND node_type='source' "
            f"AND (external_ref IN ({placeholders}) OR id IN ({placeholders})) "
            "ORDER BY updated_at DESC",
            (*refs, *refs),
        ).fetchall()
        nodes = [self._decode_node(row) for row in rows]
        source_ids = {item["id"] for item in nodes}
        if not source_ids:
            return {
                "source_count": 0, "sources": [], "reviewed_relationships": [],
                "identity_candidates": [], "dependency_clusters": [],
                "dependency_relation_count": 0, "dependency_linked_source_count": 0,
                "explicit_independence_pair_count": 0, "explicitly_independent_source_count": 0,
                "unassessed_source_count": 0,
                "independence_assessment": "unassessed",
            }

        id_placeholders = ",".join("?" for _ in source_ids)
        relationship_rows = self.connection.execute(
            "SELECT x.*,a.title AS source_title,a.external_ref AS source_ref,"
            "b.title AS related_source_title,b.external_ref AS related_source_ref "
            "FROM source_relationships x "
            "JOIN knowledge_nodes a ON a.id=x.source_node_id "
            "JOIN knowledge_nodes b ON b.id=x.related_source_node_id "
            f"WHERE x.source_node_id IN ({id_placeholders}) "
            f"OR x.related_source_node_id IN ({id_placeholders}) "
            "ORDER BY x.created_at,x.id",
            (*source_ids, *source_ids),
        ).fetchall()
        relationships = [dict(row) for row in relationship_rows]
        candidates = [
            item for item in self.list_source_identity_candidates(status="all", limit=candidate_limit)
            if item["source_node_id"] in source_ids or item["related_source_node_id"] in source_ids
        ]

        parent = {source_id: source_id for source_id in source_ids}

        def find(source_id: str) -> str:
            while parent[source_id] != source_id:
                parent[source_id] = parent[parent[source_id]]
                source_id = parent[source_id]
            return source_id

        def union(left_id: str, right_id: str) -> None:
            if left_id not in parent or right_id not in parent:
                return
            left_root, right_root = find(left_id), find(right_id)
            if left_root != right_root:
                first, second = sorted((left_root, right_root))
                parent[second] = first

        dependency_edges: set[tuple[str, str, str]] = set()
        independent_pairs: set[tuple[str, str]] = set()
        for item in relationships:
            relation = item["relationship"]
            left_id, right_id = item["source_node_id"], item["related_source_node_id"]
            if relation in {"syndicated_from", "derived_from"}:
                dependency_edges.add((left_id, right_id, relation))
                union(left_id, right_id)
            elif relation == "independent_of" and left_id in source_ids and right_id in source_ids:
                independent_pairs.add(tuple(sorted((left_id, right_id))))

        for item in candidates:
            if item["status"] == "accepted" and item["candidate_type"] == "canonical_equivalent":
                left_id, right_id = item["source_node_id"], item["related_source_node_id"]
                dependency_edges.add((left_id, right_id, "canonical_equivalent"))
                union(left_id, right_id)

        clusters: dict[str, list[dict[str, Any]]] = {}
        for node in nodes:
            root_id = find(node["id"])
            clusters.setdefault(root_id, []).append({
                "source_node_id": node["id"],
                "title": node["title"],
                "url": node.get("external_ref"),
            })
        dependency_clusters = [
            {"members": sorted(members, key=lambda item: item["url"] or item["source_node_id"])}
            for members in clusters.values() if len(members) > 1
        ]
        dependency_source_ids = {
            source_id for edge in dependency_edges
            for source_id in edge[:2] if source_id in source_ids
        }
        independence_source_ids = {
            source_id for pair in independent_pairs for source_id in pair
        }
        unassessed_source_count = len(source_ids - dependency_source_ids - independence_source_ids)
        independence_assessment = (
            "partially_assessed" if dependency_edges or independent_pairs else "unassessed"
        )
        source_summaries = []
        for node in nodes:
            metadata = node.get("metadata") or {}
            identity = metadata.get("source_identity") if isinstance(metadata.get("source_identity"), dict) else {}
            source_summaries.append({
                "source_node_id": node["id"],
                "title": node["title"],
                "url": node.get("external_ref"),
                "hostname": identity.get("hostname"),
                "canonical_url": identity.get("canonical_url"),
                "publisher": identity.get("publisher"),
                "authors": identity.get("authors", []),
                "registrable_domain": identity.get("registrable_domain"),
                "domain_grouping": identity.get("domain_grouping", "unavailable"),
                "source_independence": identity.get("source_independence", "unassessed"),
                "source_credibility": identity.get("source_credibility", "unassessed"),
                "grouping_is_ownership_proof": identity.get("grouping_is_ownership_proof", False),
                "metadata_extraction": identity.get("metadata_extraction", {}),
            })

        return {
            "source_count": len(nodes),
            "sources": source_summaries,
            "reviewed_relationships": relationships,
            "identity_candidates": candidates,
            "dependency_clusters": dependency_clusters,
            "dependency_relation_count": len(dependency_edges),
            "dependency_linked_source_count": len(dependency_source_ids),
            "explicit_independence_pair_count": len(independent_pairs),
            "explicitly_independent_source_count": len(independence_source_ids),
            "unassessed_source_count": unassessed_source_count,
            "independence_assessment": independence_assessment,
            "interpretation": (
                "只把明确审查的 canonical 等价、转载/衍生依赖纳入依赖聚类；"
                "同出版方、同作者或引用关系不自动等于同一出处。"
                "没有发现依赖关系不等于已证明来源独立。"
            ),
        }

    def list_claim_evidence(self, claim_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        claim_id = self._text(claim_id, "claim_id", 128)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        rows = self.connection.execute(
            "SELECT e.*,s.title AS source_title,s.external_ref AS source_ref,"
            "s.metadata_json AS source_metadata_json "
            "FROM claim_evidence e JOIN knowledge_nodes s ON s.id=e.source_node_id "
            "WHERE e.claim_id=? AND s.deleted_at IS NULL "
            "ORDER BY e.created_at DESC,e.id LIMIT ?",
            (claim_id, limit),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["source_metadata"] = json.loads(item.pop("source_metadata_json"))
            item["reviews"] = [
                dict(review) for review in self.connection.execute(
                    "SELECT * FROM claim_evidence_reviews WHERE claim_evidence_id=? "
                    "ORDER BY reviewed_at,id", (item["id"],),
                )
            ]
            latest_review = item["reviews"][-1] if item["reviews"] else None
            item["latest_review"] = latest_review
            item["latest_verdict"] = latest_review["verdict"] if latest_review else None
            item["effective_quality"] = latest_review["quality"] if latest_review else item["quality"]
            item["effective_independence"] = latest_review["independence"] if latest_review else item["independence"]
            item["source_relationships"] = [
                dict(relation) for relation in self.connection.execute(
                    "SELECT r.*,n.external_ref AS related_source_ref,n.title AS related_source_title "
                    "FROM source_relationships r JOIN knowledge_nodes n "
                    "ON n.id=r.related_source_node_id WHERE r.source_node_id=? "
                    "UNION ALL "
                    "SELECT r.*,n.external_ref AS related_source_ref,n.title AS related_source_title "
                    "FROM source_relationships r JOIN knowledge_nodes n "
                    "ON n.id=r.source_node_id WHERE r.related_source_node_id=?",
                    (item["source_node_id"], item["source_node_id"]),
                )
            ]
            result.append(item)
        return result

    def claim_evidence_coverage(self, claim_id: str, *, source_limit: int = 32) -> dict[str, Any]:
        """Summarize evidence coverage and integrity signals without assigning credibility scores.

        Latest append-only evidence reviews supply the displayed verdict/quality/independence.
        Retrieval direction, URL transport, hashes, domain grouping and source count are
        descriptive signals only; none automatically proves or disproves a claim.
        """
        claim_id = self._text(claim_id, "claim_id", 128)
        if isinstance(source_limit, bool) or not isinstance(source_limit, int) or not 1 <= source_limit <= 32:
            raise ValueError("source_limit must be between 1 and 32")
        claim = self.get_node(claim_id)
        if claim is None or claim["node_type"] != "claim":
            raise ValueError("claim_id must identify an active claim node")
        rows = self.connection.execute(
            "SELECT e.id,e.direction,e.evidence_type,e.quality,e.independence,e.source_node_id,"
            "s.external_ref,s.metadata_json,"
            "(SELECT r.verdict FROM claim_evidence_reviews r WHERE r.claim_evidence_id=e.id "
            "ORDER BY r.reviewed_at DESC,r.id DESC LIMIT 1) AS reviewed_verdict,"
            "(SELECT r.quality FROM claim_evidence_reviews r WHERE r.claim_evidence_id=e.id "
            "ORDER BY r.reviewed_at DESC,r.id DESC LIMIT 1) AS reviewed_quality,"
            "(SELECT r.independence FROM claim_evidence_reviews r WHERE r.claim_evidence_id=e.id "
            "ORDER BY r.reviewed_at DESC,r.id DESC LIMIT 1) AS reviewed_independence,"
            "(SELECT r.reviewer_id FROM claim_evidence_reviews r WHERE r.claim_evidence_id=e.id "
            "ORDER BY r.reviewed_at DESC,r.id DESC LIMIT 1) AS reviewer_id,"
            "(SELECT count(DISTINCT r.verdict) FROM claim_evidence_reviews r WHERE r.claim_evidence_id=e.id) AS verdict_variants "
            "FROM claim_evidence e JOIN knowledge_nodes s ON s.id=e.source_node_id "
            "WHERE e.claim_id=? AND s.deleted_at IS NULL ORDER BY e.created_at,e.id",
            (claim_id,),
        ).fetchall()
        direction_counts = {item: 0 for item in CLAIM_EVIDENCE_DIRECTIONS}
        quality_counts = {item: 0 for item in EVIDENCE_QUALITY_LEVELS}
        independence_counts = {item: 0 for item in SOURCE_INDEPENDENCE_LEVELS}
        verdict_counts = {item: 0 for item in EVIDENCE_REVIEW_VERDICTS}
        reviewer_ids: set[str] = set()
        reviewed_count = 0
        review_conflict_count = 0
        source_rows: dict[str, dict[str, Any]] = {}
        for row in rows:
            direction_counts[row["direction"]] = direction_counts.get(row["direction"], 0) + 1
            quality = row["reviewed_quality"] or row["quality"] or "unassessed"
            independence = row["reviewed_independence"] or row["independence"] or "unassessed"
            quality_counts[quality] = quality_counts.get(quality, 0) + 1
            independence_counts[independence] = independence_counts.get(independence, 0) + 1
            if row["reviewed_verdict"]:
                reviewed_count += 1
                verdict_counts[row["reviewed_verdict"]] = verdict_counts.get(row["reviewed_verdict"], 0) + 1
            if row["reviewer_id"]:
                reviewer_ids.add(row["reviewer_id"])
            if int(row["verdict_variants"] or 0) > 1:
                review_conflict_count += 1
            if row["source_node_id"] not in source_rows:
                source_rows[row["source_node_id"]] = {
                    "source_ref": row["external_ref"] or row["source_node_id"],
                    "metadata": json.loads(row["metadata_json"] or "{}"),
                }

        integrity = {
            "content_hash_present_count": 0,
            "nontrivial_content_count": 0,
            "https_count": 0,
            "publication_date_parsed_count": 0,
            "publication_date_missing_count": 0,
            "publication_date_unparseable_count": 0,
            "publication_date_future_count": 0,
            "publication_date_unknown_count": 0,
        }
        hosts: set[str] = set()
        registrable_domains: set[str] = set()
        for source in source_rows.values():
            metadata = source["metadata"]
            identity = metadata.get("source_identity") if isinstance(metadata.get("source_identity"), dict) else {}
            signals = metadata.get("source_signals") if isinstance(metadata.get("source_signals"), dict) else {}
            content_hash = metadata.get("content_hash") or metadata.get("sha256")
            if isinstance(content_hash, str) and re.fullmatch(r"[0-9a-fA-F]{64}", content_hash):
                integrity["content_hash_present_count"] += 1
            chars = metadata.get("chars")
            if isinstance(chars, int) and not isinstance(chars, bool) and chars >= 200:
                integrity["nontrivial_content_count"] += 1
            elif signals.get("content_nontrivial") is True:
                integrity["nontrivial_content_count"] += 1
            if signals.get("https") is True or str(source["source_ref"]).casefold().startswith("https://"):
                integrity["https_count"] += 1
            publication = signals.get("publication_date") if isinstance(signals.get("publication_date"), dict) else {}
            publication_status = publication.get("status")
            if publication_status not in {"parsed", "missing", "unparseable", "future"}:
                publication_status = "unknown"
            integrity[f"publication_date_{publication_status}_count"] += 1
            hostname = identity.get("hostname")
            if isinstance(hostname, str) and hostname.strip():
                hosts.add(hostname.casefold().rstrip("."))
            registrable = identity.get("registrable_domain")
            if isinstance(registrable, str) and registrable.strip():
                registrable_domains.add(registrable.casefold().rstrip("."))

        source_refs = [item["source_ref"] for item in list(source_rows.values())[:source_limit]]
        lineage = self.source_lineage_snapshot(source_refs) if source_refs else self.source_lineage_snapshot(())
        evidence_count = len(rows)
        support_count = direction_counts.get("support_search_candidate", 0)
        counter_count = direction_counts.get("counter_search_candidate", 0)
        if evidence_count == 0:
            coverage_state = "no_evidence"
        elif support_count and counter_count:
            coverage_state = "both_search_directions_present"
        elif support_count:
            coverage_state = "counter_search_missing"
        elif counter_count:
            coverage_state = "support_search_missing"
        else:
            coverage_state = "both_search_directions_missing"
        unassessed_quality = quality_counts.get("unassessed", 0)
        quality_state = (
            "unassessed" if evidence_count == 0 or unassessed_quality == evidence_count
            else "partially_assessed" if unassessed_quality else "assessed"
        )
        review_state = (
            "unassessed" if evidence_count == 0 or reviewed_count == 0
            else "reviewed" if reviewed_count == evidence_count else "partially_reviewed"
        )
        return {
            "evidence_count": evidence_count,
            "source_count": len(source_rows),
            "direction_counts": direction_counts,
            "both_support_and_counter_present": bool(support_count and counter_count),
            "coverage_state": coverage_state,
            "reviewed_evidence_count": reviewed_count,
            "unreviewed_evidence_count": evidence_count - reviewed_count,
            "review_verdict_counts": verdict_counts,
            "reviewer_count": len(reviewer_ids),
            "review_conflict_count": review_conflict_count,
            "review_coverage_state": review_state,
            "quality_counts": quality_counts,
            "quality_assessment_state": quality_state,
            "independence_counts": independence_counts,
            "independence_assessment": lineage.get("independence_assessment", "unassessed"),
            "source_integrity_signals": integrity,
            "distinct_host_count": len(hosts),
            "heuristic_registrable_domain_count": len(registrable_domains),
            "domain_grouping_is_ownership_proof": False,
            "source_lineage": lineage,
            "source_lineage_truncated": len(source_rows) > source_limit,
            "overall_quality": "not_automatically_scored",
            "retrieval_direction_is_not_evidence_verdict": True,
            "interpretation": (
                "这些计数用于发现证据覆盖与资料完整性缺口，不构成来源信誉或主张置信度评分；"
                "域名分组不是所有权证明，未发现依赖关系不等于已证明来源独立。"
            ),
        }

    def claim_dossier(self, claim_id: str, *, evidence_limit: int = 12) -> dict[str, Any]:
        """Return the original claim, traceable evidence candidates and review history."""
        claim_id = self._text(claim_id, "claim_id", 128)
        if isinstance(evidence_limit, bool) or not isinstance(evidence_limit, int) or not 1 <= evidence_limit <= 100:
            raise ValueError("evidence_limit must be between 1 and 100")
        claim = self.get_node(claim_id)
        if claim is None or claim["node_type"] != "claim":
            raise ValueError("claim_id must identify an active claim node")
        metadata = claim["metadata"]
        evidence = self.list_claim_evidence(claim_id, limit=evidence_limit)
        counts = {direction: 0 for direction in CLAIM_EVIDENCE_DIRECTIONS}
        for row in self.connection.execute(
            "SELECT direction,count(*) AS total FROM claim_evidence "
            "WHERE claim_id=? GROUP BY direction", (claim_id,),
        ):
            counts[row["direction"]] = row["total"]

        history = []
        for event in reversed(self.list_events(entity_id=claim_id, limit=500)):
            if event["event_type"] != "claim_review_recorded":
                continue
            payload = event["payload"]
            history.append({
                "sequence": event["sequence"],
                "created_at": event["created_at"],
                "reviewer_id": payload.get("reviewer_id", ""),
                "verdict": payload.get("verdict", "insufficient"),
                "rationale": payload.get("rationale", ""),
                "confidence_level": payload.get("confidence_level", "unassessed"),
                "confidence_basis": payload.get(
                    "confidence_basis", "legacy_or_unspecified_reviewer_assessment"
                ),
                "evidence_refs": payload.get("evidence_refs", []),
            })
        latest = history[-1] if history else {}
        original_source = metadata.get("original_claim_source")
        if not isinstance(original_source, str) or not original_source.strip():
            original_source = None
        return {
            "claim_id": claim_id,
            "title": claim["title"],
            "original_claim": metadata.get("original_claim") or claim["summary"],
            "original_source": original_source,
            "related_source_refs": claim["provenance"],
            "claim_status": claim["status"],
            "epistemic_status": metadata.get("epistemic_status", "unspecified"),
            "evidence": evidence,
            "evidence_counts": counts,
            "evidence_coverage": self.claim_evidence_coverage(claim_id),
            "confidence": {
                "level": latest.get("confidence_level", "unassessed"),
                "reviewer_id": latest.get("reviewer_id"),
                "basis": latest.get("confidence_basis", "not_assessed"),
                "automatic_scoring": False,
            },
            "confidence_history": history,
            "retrieval_direction_is_not_evidence_verdict": True,
        }

    def search_context(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        """Retrieve lexical matches plus a bounded one-hop graph neighborhood.

        Similarity and graph adjacency are retrieval hints only; neither relationship
        nor proximity upgrades a claim's epistemic status.
        """
        query = self._text(query, "query", 2000)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        tokens = set(re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]", query.casefold()))
        if not tokens:
            return []
        ranked = []
        for node in self.list_nodes(limit=500):
            metadata = node["metadata"]
            searchable = " ".join((node["title"], node["summary"], metadata.get("original_claim", ""), metadata.get("signature", ""), metadata.get("reason_to_avoid", ""))).casefold()
            candidate = set(re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]", searchable))
            score = len(tokens & candidate) / len(tokens)
            if score > 0:
                ranked.append((score, node["updated_at"], node))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        if len(ranked) <= limit:
            lexical_budget = max(1, limit // 2) if ranked else 0
        else:
            lexical_budget = max(1, limit // 2)
        selected = [
            {**node, "match_score": round(score, 3), "context_origin": "lexical_match"}
            for score, _, node in ranked[:lexical_budget]
        ]
        selected_ids = {item["id"] for item in selected}
        for anchor in list(selected):
            if len(selected) >= limit:
                break
            for edge in self.list_edges(node_id=anchor["id"], limit=24):
                neighbor_id = edge["target_id"] if edge["source_id"] == anchor["id"] else edge["source_id"]
                if neighbor_id in selected_ids:
                    continue
                neighbor = self.get_node(neighbor_id)
                if neighbor is None:
                    continue
                selected.append({
                    **neighbor, "match_score": 0.0, "context_origin": "graph_neighbor",
                    "related_via": {"node_id": anchor["id"], "edge_id": edge["id"],
                                    "edge_type": edge["edge_type"],
                                    "direction": "outgoing" if edge["source_id"] == anchor["id"] else "incoming"},
                })
                selected_ids.add(neighbor_id)
                if len(selected) >= limit:
                    break
        if len(selected) < limit:
            for score, _, node in ranked[lexical_budget:]:
                if node["id"] in selected_ids:
                    continue
                selected.append({**node, "match_score": round(score, 3), "context_origin": "lexical_match"})
                selected_ids.add(node["id"])
                if len(selected) >= limit:
                    break
        return selected


    def record_research_run(
        self, query: str, *, run_id: str, source_refs: Iterable[str],
        status: str, failures: Iterable[dict[str, str]] = (), task_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist a research attempt and source/failure links without asserting claims."""
        query = self._text(query, "query", 2000)
        run_id = self._text(run_id, "run_id", 160)
        if status not in {"succeeded", "partial", "failed"}:
            raise ValueError("research status must be succeeded, partial or failed")
        if isinstance(source_refs, (str, bytes)):
            raise ValueError("source_refs must be an iterable")
        refs = list(dict.fromkeys(self._text(item, "source reference", 4096) for item in source_refs))
        if len(refs) > 32:
            raise ValueError("a research run can link at most 32 sources")
        task_ref = self._text(task_id, "task_id", 160) if task_id else None
        idea = self.upsert_node(
            "idea", query[:500], summary="Research question captured for future retrieval.",
            external_ref="research-query:" + hashlib.sha256(query.casefold().encode()).hexdigest(),
            metadata={"task_ids": [task_ref] if task_ref else []},
            provenance=(f"task:{task_ref}",) if task_ref else (),
        )
        experiment = self.upsert_node(
            "experiment", "Research run " + run_id[:80],
            summary=f"Research pipeline outcome: {status}; sources={len(refs)}.",
            external_ref="research-run:" + run_id,
            metadata={"task_id": task_ref, "status": status, "source_refs": refs, "source_count": len(refs),
                      "execution_class": "research_source_retrieval",
                      "epistemic_status": "research_observation_not_claim_verdict",
                      "verdict_assigned": False, "not_claim_evidence": True},
            provenance=tuple(refs[:32]),
        )
        self.add_edge(idea["id"], experiment["id"], "tested_by")
        linked_sources = 0
        for ref in refs:
            source = self.connection.execute(
                "SELECT id FROM knowledge_nodes WHERE node_type='source' AND external_ref=? AND deleted_at IS NULL", (ref,)
            ).fetchone()
            if source is not None:
                self.add_edge(source["id"], experiment["id"], "relates")
                linked_sources += 1
        failure_ids = []
        failure_items = list(failures)[:16]
        if status == "failed" and not failure_items:
            failure_items = [{"stage": "search", "error": "NoSourcesFound"}]
        for item in failure_items:
            if not isinstance(item, dict):
                continue
            stage = self._text(str(item.get("stage", "unknown")), "failure stage", 80)
            error = self._text(str(item.get("error", "unknown")), "failure error", 120)
            failure = self.record_failure(
                f"Research {stage} failure", summary=f"{stage}: {error}",
                signature=f"research:{stage}:{error}",
                reason_to_avoid="Do not blindly repeat the same attempt; gather new evidence and use bounded recovery.",
                task_id=task_ref or run_id, provenance=(f"research-run:{run_id}",),
            )
            failure_ids.append(failure["id"])
            self.add_edge(experiment["id"], failure["id"], "relates", metadata={
                "relationship": "failure_observed_in_run", "stage": stage, "task_id": task_ref,
            })
            self.add_edge(failure["id"], idea["id"], "addresses_gap", metadata={
                "relationship": "failure_in_research_question", "run_id": run_id,
            })
        return {"idea_id": idea["id"], "experiment_id": experiment["id"],
                "source_count": len(refs), "linked_source_count": linked_sources, "failure_ids": failure_ids}

    def record_commander_outcome(
        self, *, task_id: str, goal: str, run_id: str, plan_id: str,
        status: str, review_approved: bool, reviewer_id: str | None,
        researcher_outputs: Iterable[dict[str, str]] = (),
    ) -> dict[str, Any]:
        """Persist Commander notes as proposed claims, never self-approved facts."""
        if status not in {"succeeded", "failed"}:
            raise ValueError("Commander outcome status must be succeeded or failed")
        task_ref = self._text(task_id, "task_id", 160)
        goal = self._text(goal, "goal", 2000)
        run_id = self._text(run_id, "run_id", 160)
        plan_id = self._text(plan_id, "plan_id", 160)
        idea = self.upsert_node(
            "idea", goal[:500], summary="Commander goal captured for research continuity.",
            external_ref=f"commander-goal:{task_ref}", metadata={"task_ids": [task_ref]},
            provenance=(f"task:{task_ref}",),
        )
        experiment = self.upsert_node(
            "experiment", "Commander run " + run_id[:80],
            summary=f"Commander outcome: {status}; independent_review={bool(review_approved)}.",
            external_ref="commander-run:" + run_id,
            metadata={"task_id": task_ref, "plan_id": plan_id, "status": status,
                      "review_approved": bool(review_approved), "reviewer_id": reviewer_id},
            provenance=(f"task:{task_ref}", f"commander-plan:{plan_id}"),
        )
        self.add_edge(idea["id"], experiment["id"], "tested_by")
        proposed_claim_ids = []
        for item in list(researcher_outputs)[:8]:
            if not isinstance(item, dict):
                continue
            step_id = self._text(str(item.get("step_id", "researcher")), "step_id", 120)
            summary = self._text(str(item.get("output", "")), "research output", 2000, optional=True)
            if len(summary) < 40:
                continue
            claim = self.upsert_node(
                "claim", "Proposed research note from " + step_id, summary=summary,
                external_ref=f"commander-claim:{task_ref}:{step_id}",
                metadata={"author_id": step_id, "task_ids": [task_ref], "review_state": "proposed_not_fact_checked"},
                provenance=(f"task:{task_ref}", f"commander-run:{run_id}"),
            )
            self.add_edge(experiment["id"], claim["id"], "relates")
            proposed_claim_ids.append(claim["id"])
        if status == "failed":
            failure = self.record_failure(
                "Commander run failed", summary=f"Commander task {task_ref} failed.",
                signature="commander:execution_failed",
                reason_to_avoid="Inspect Ticket events and independent review findings before retrying.",
                task_id=task_ref, provenance=(f"commander-run:{run_id}",),
            )
            self.add_edge(experiment["id"], failure["id"], "relates", metadata={
                "relationship": "failure_observed_in_commander_run", "task_id": task_ref,
            })
            self.add_edge(failure["id"], idea["id"], "addresses_gap", metadata={
                "relationship": "failure_in_commander_goal", "run_id": run_id,
            })
        return {"idea_id": idea["id"], "experiment_id": experiment["id"], "proposed_claim_ids": proposed_claim_ids}

    def aggregate_improvement_signals(self, *, since: datetime) -> dict[str, int]:
        """Return privacy-safe research improvement counts for an explicit time window."""
        if not isinstance(since, datetime):
            raise ValueError("since must be a datetime")
        if since.tzinfo is None or since.utcoffset() is None:
            since = since.replace(tzinfo=timezone.utc)
        cutoff = since.astimezone(timezone.utc).isoformat()
        recent = [node for node in self.list_nodes(limit=500) if node["updated_at"] >= cutoff]
        failures = [node for node in recent if node["node_type"] == "failure"]
        repeated_groups = 0
        repeat_count = 0
        for node in failures:
            task_ids = node["metadata"].get("task_ids", [])
            occurrences = len(task_ids) if isinstance(task_ids, list) else 1
            if occurrences >= 2:
                repeated_groups += 1
                repeat_count += occurrences - 1
        proposed_claims = sum(
            node["node_type"] == "claim"
            and node["status"] in {"proposed", "unverified"}
            and node["metadata"].get("epistemic_status") != "symbolic_hypothesis_only"
            for node in recent
        )
        symbolic_hypotheses = sum(
            node["node_type"] == "claim"
            and node["status"] in {"proposed", "unverified"}
            and node["metadata"].get("epistemic_status") == "symbolic_hypothesis_only"
            and node["metadata"].get("review_state") == "not_researched"
            for node in recent
        )
        failed_experiments = sum(
            node["node_type"] == "experiment" and node["metadata"].get("status") == "failed"
            for node in recent
        )
        return {
            "failure_signature_groups": len(failures),
            "repeated_failure_groups": repeated_groups,
            "repeated_failure_occurrences": repeat_count,
            "proposed_claim_backlog": proposed_claims,
            "unreviewed_symbolic_hypotheses": symbolic_hypotheses,
            "failed_experiments": failed_experiments,
        }

    def sync_improvement_proposal_state(
        self, proposal_id: str, *, proposal_status: str,
        events: Iterable[dict[str, Any]],
    ) -> dict[str, int]:
        """Mirror allowlisted ImprovementStore lifecycle events without copying free-text audit data."""
        proposal_id = self._text(proposal_id, "proposal_id", 128)
        status_to_review = {
            "proposed": "awaiting_research",
            "researching": "research_in_progress",
            "ready_for_review": "pending_human_review",
            "approved": "human_approved",
            "rejected": "human_rejected",
            "implemented": "implemented_after_validation",
            "rolled_back": "rolled_back_after_validation",
        }
        if not isinstance(proposal_status, str) or proposal_status not in status_to_review:
            raise ValueError("proposal_status is not a supported ImprovementStore state")
        if isinstance(events, (str, bytes)):
            raise ValueError("events must be an iterable of audit event objects")

        history_by_id: dict[str, dict[str, str]] = {}
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("each improvement event must be an object")
            event_id = self._text(event.get("id"), "event id", 128)
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", event_id):
                raise ValueError("event id must be a safe stable reference")
            from_status = event.get("from_status")
            to_status = event.get("to_status")
            if not isinstance(to_status, str) or to_status not in status_to_review or (
                from_status is not None and (not isinstance(from_status, str) or from_status not in status_to_review)
            ):
                raise ValueError("improvement event status is not supported")
            created_at = self._text(event.get("created_at"), "event created_at", 80)
            try:
                stamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("event created_at must be valid ISO-8601") from exc
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                raise ValueError("event created_at must include a timezone")
            if from_status == to_status:
                continue
            history_by_id[event_id] = {
                "event_id": event_id,
                "from_status": from_status or "unknown",
                "to_status": to_status,
                "created_at": stamp.astimezone(timezone.utc).isoformat(),
            }

        incoming_history = sorted(
            history_by_id.values(), key=lambda item: (item["created_at"], item["event_id"])
        )[-100:]
        nodes = [
            node for node in self.list_nodes(node_type="idea", limit=500)
            if node["metadata"].get("improvement_proposal_id") == proposal_id
            and node["metadata"].get("epistemic_status") == "hypothesis"
        ]
        updated_nodes = unchanged_nodes = 0
        for node in nodes:
            old_metadata = node["metadata"]
            combined = {
                item["event_id"]: item
                for item in old_metadata.get("review_history", [])
                if isinstance(item, dict) and isinstance(item.get("event_id"), str)
            }
            combined.update({item["event_id"]: item for item in incoming_history})
            review_history = sorted(
                combined.values(), key=lambda item: (item.get("created_at", ""), item["event_id"])
            )[-100:]
            approval_required = proposal_status in {"proposed", "researching", "ready_for_review"}
            human_decision = (
                "approved" if proposal_status in {"approved", "implemented", "rolled_back"}
                else "rejected" if proposal_status == "rejected" else None
            )
            patch = {
                "improvement_status": proposal_status,
                "review_state": status_to_review[proposal_status],
                "approval_required": approval_required,
                "human_decision": human_decision,
                "review_history": review_history,
            }
            if all(old_metadata.get(key) == value for key, value in patch.items()):
                unchanged_nodes += 1
                continue
            self.upsert_node(
                "idea", node["title"], summary=node["summary"],
                external_ref=node["external_ref"], metadata=patch,
                provenance=node["provenance"],
            )
            updated_nodes += 1
        return {"updated_nodes": updated_nodes, "unchanged_nodes": unchanged_nodes}

    def record_classified_failure_research(
        self, *, error_code: str, category: str, occurrences: int,
        retryable: int, non_retryable: int, lookback_hours: int,
        observed_at: str, proposal_id: str,
    ) -> dict[str, str]:
        """Persist only redacted aggregate observations and an explicitly unverified hypothesis."""
        error_code = self._text(error_code, "error_code", 80)
        category = self._text(category, "category", 40)
        proposal_id = self._text(proposal_id, "proposal_id", 128)
        observed_at = self._text(observed_at, "observed_at", 80)
        try:
            observed_stamp = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("observed_at must be a valid ISO-8601 timestamp") from exc
        if observed_stamp.tzinfo is None or observed_stamp.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        observed_at = observed_stamp.astimezone(timezone.utc).isoformat()
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,79}", error_code):
            raise ValueError("error_code must be a stable lowercase classification code")
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,39}", category):
            raise ValueError("category must be a stable lowercase classification")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", proposal_id):
            raise ValueError("proposal_id must be a safe proposal reference")
        for value in (occurrences, retryable, non_retryable, lookback_hours):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("failure aggregate counts and lookback must be integers")
        if occurrences < 2 or retryable < 0 or non_retryable < 0 or retryable + non_retryable != occurrences:
            raise ValueError("failure aggregate counts are inconsistent")
        if not 1 <= lookback_hours <= 720:
            raise ValueError("lookback_hours must be between 1 and 720")

        snapshot = {
            "error_code": error_code, "category": category,
            "occurrences": occurrences, "retryable": retryable,
            "non_retryable": non_retryable, "lookback_hours": lookback_hours,
            "observed_at": observed_at,
        }
        failure = self.upsert_node(
            "failure", f"Classified failure observation: {error_code}",
            summary=f"Observed {occurrences} classified occurrences in a {lookback_hours}h window; no task content was retained.",
            external_ref=f"classified-failure:{error_code}",
            metadata={"observation_kind": "classified_failure_aggregate", **snapshot},
        )
        existing_row = self.connection.execute(
            "SELECT metadata_json FROM knowledge_nodes WHERE natural_key=? AND deleted_at IS NULL",
            (f"idea:classified-failure-hypothesis:{error_code}",),
        ).fetchone()
        existing_metadata = json.loads(existing_row["metadata_json"]) if existing_row else {}
        lifecycle_metadata = {
            "review_state": existing_metadata.get("review_state", "pending_human_review"),
            "approval_required": existing_metadata.get("approval_required", True),
            "improvement_status": existing_metadata.get("improvement_status"),
            "human_decision": existing_metadata.get("human_decision"),
            "review_history": existing_metadata.get("review_history", []),
            "counter_evidence": existing_metadata.get(
                "counter_evidence", {"status": "not_collected", "experiment_ids": []}
            ),
        }
        hypothesis = self.upsert_node(
            "idea", f"Root-cause hypothesis: {error_code}",
            summary="The repeated failure code identifies a recurring failure class; its owning stage and root cause remain unverified.",
            external_ref=f"classified-failure-hypothesis:{error_code}",
            metadata={
                "epistemic_status": "hypothesis",
                "failure_observation_id": failure["id"],
                "improvement_proposal_id": proposal_id,
                **lifecycle_metadata,
                "evidence_snapshot": snapshot,
                "alternative_explanations": [
                    "provider-side transient unavailability",
                    "local configuration or endpoint mismatch",
                    "timeout or network-path instability",
                ],
            },
        )
        self.add_edge(failure["id"], hypothesis["id"], "addresses_gap", metadata={
            "kind": "root_cause_investigation", "improvement_proposal_id": proposal_id,
        })
        self.add_edge(hypothesis["id"], failure["id"], "relates", metadata={
            "improvement_proposal_id": proposal_id,
        })
        return {"failure_id": failure["id"], "hypothesis_id": hypothesis["id"]}

    def record_classified_failure_experiment(
        self, hypothesis_id: str, *, run_id: str, outcome: str,
        test_count: int, passed_count: int, failed_count: int,
        evidence_refs: Iterable[str] = (),
    ) -> dict[str, Any]:
        """Append a redacted deterministic test record and link its support/refutation direction."""
        hypothesis_id = self._text(hypothesis_id, "hypothesis_id", 128)
        run_id = self._text(run_id, "run_id", 120)
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", run_id):
            raise ValueError("run_id must be a safe stable experiment reference")
        if not isinstance(outcome, str) or outcome not in {"supports", "refutes", "inconclusive"}:
            raise ValueError("outcome must be supports, refutes, or inconclusive")
        for value in (test_count, passed_count, failed_count):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("experiment counts must be non-negative integers")
        if test_count < 1 or passed_count + failed_count != test_count:
            raise ValueError("experiment counts must sum to a positive test count")
        if isinstance(evidence_refs, (str, bytes)):
            raise ValueError("evidence_refs must be an iterable of stored references")
        refs = list(dict.fromkeys(self._text(ref, "evidence reference", 4096) for ref in evidence_refs))
        if len(refs) > 16:
            raise ValueError("at most 16 experiment evidence references are supported")
        hypothesis = self.get_node(hypothesis_id)
        if hypothesis is None or hypothesis["node_type"] != "idea" or hypothesis["metadata"].get("epistemic_status") != "hypothesis":
            raise ValueError("hypothesis_id must identify a classified-failure hypothesis")
        evidence_nodes = []
        for ref in refs:
            row = self.connection.execute(
                "SELECT id,node_type FROM knowledge_nodes WHERE deleted_at IS NULL AND (id=? OR external_ref=?)",
                (ref, ref),
            ).fetchone()
            if row is None or row["node_type"] not in {"source", "experiment"}:
                raise ValueError("experiment evidence reference must identify a stored source or experiment")
            evidence_nodes.append(row["id"])
        error_code = hypothesis["metadata"].get("evidence_snapshot", {}).get("error_code", "unknown")
        experiment_ref = f"classified-failure-experiment:{hypothesis_id}:{run_id}"
        experiment_metadata = {
            "experiment_kind": "classified_failure_reproduction",
            "hypothesis_id": hypothesis_id, "run_id": run_id,
            "outcome": outcome, "test_count": test_count,
            "passed_count": passed_count, "failed_count": failed_count,
            "evidence_refs": evidence_nodes,
            "raw_output_retained": False,
        }
        existing_row = self.connection.execute(
            "SELECT id FROM knowledge_nodes WHERE natural_key=? AND deleted_at IS NULL",
            (f"experiment:{experiment_ref}",),
        ).fetchone()
        if existing_row is not None:
            experiment = self.get_node(existing_row["id"])
            assert experiment is not None
            if any(experiment["metadata"].get(key) != value for key, value in experiment_metadata.items()):
                raise ValueError("experiment run_id already recorded with a different result")
        else:
            experiment = self.upsert_node(
                "experiment", f"Failure hypothesis experiment: {error_code}",
                summary=f"Deterministic test record outcome: {outcome}; {passed_count}/{test_count} passed.",
                external_ref=experiment_ref, metadata=experiment_metadata,
            )
        edge_type = {"supports": "supports", "refutes": "refutes", "inconclusive": "relates"}[outcome]
        self.add_edge(hypothesis_id, experiment["id"], edge_type, metadata={"run_id": run_id})
        for source_id in evidence_nodes:
            self.add_edge(experiment["id"], source_id, "relates", metadata={"evidence_role": "experiment_reference"})
        if outcome == "refutes":
            counter = hypothesis["metadata"].get("counter_evidence", {"experiment_ids": []})
            experiment_ids = list(dict.fromkeys([*counter.get("experiment_ids", []), experiment["id"]]))
            self.upsert_node(
                "idea", hypothesis["title"], summary=hypothesis["summary"],
                external_ref=hypothesis["external_ref"],
                metadata={**hypothesis["metadata"],
                          "counter_evidence": {"status": "recorded", "experiment_ids": experiment_ids}},
            )
        return {
            "experiment_id": experiment["id"], "outcome": outcome,
            "test_count": test_count, "passed_count": passed_count,
            "failed_count": failed_count, "evidence_count": len(evidence_nodes),
        }

    def record_failure(
        self, title: str, *, summary: str, signature: str, reason_to_avoid: str,
        task_id: str | None = None, provenance: Iterable[str] = (),
    ) -> dict[str, Any]:
        signature = self._text(signature, "signature", 240)
        task_ref = self._text(task_id, "task_id", 160) if task_id is not None else None
        return self.upsert_node(
            "failure", title, summary=summary, external_ref=f"failure:{signature}",
            status="active",
            metadata={"signature": signature, "reason_to_avoid": self._text(reason_to_avoid, "reason_to_avoid", 1000),
                      "task_ids": [task_ref] if task_ref else []},
            provenance=provenance,
        )

    def search_failures(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        query = self._text(query, "query", 500)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        tokens = set(re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]", query.casefold()))
        if not tokens:
            return []
        matches = []
        for node in self.list_nodes(node_type="failure", limit=500):
            haystack = " ".join([
                node["title"], node["summary"], node["metadata"].get("signature", ""),
                node["metadata"].get("reason_to_avoid", ""),
            ]).casefold()
            candidate = set(re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]", haystack))
            score = len(tokens & candidate) / len(tokens)
            if score > 0:
                matches.append((score, node["updated_at"], node))
        matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [{**node, "match_score": round(score, 3)} for score, _, node in matches[:limit]]

    def link_node_to_initiative(
        self, initiative_store: Any, initiative_id: str, node_id: str, *,
        relationship: str = "related", evolution_cycle_id: str | None = None,
    ) -> dict[str, Any]:
        """Link one active graph node to the creator ledger; both writes are idempotent.

        The initiative ledger is written first. If the graph metadata update is interrupted,
        repeating this call safely repairs the graph-side association.
        """
        initiative_id = self._text(initiative_id, "initiative_id", 128)
        node_id = self._text(node_id, "node_id", 128)
        if not re.fullmatch(r"[0-9a-f]{32}", initiative_id):
            raise ValueError("initiative_id must be a 32-character lowercase UUID hex string")
        if evolution_cycle_id is not None and not re.fullmatch(r"cycle_[0-9a-f]{32}", evolution_cycle_id):
            raise ValueError("evolution_cycle_id must be a valid Muse Evolution Cycle ID")
        node = self.get_node(node_id)
        if node is None:
            raise ValueError("knowledge node does not exist or is deleted")
        link = initiative_store.record_knowledge_link(
            initiative_id, node_id, node_type=node["node_type"],
            relationship=relationship, evolution_cycle_id=evolution_cycle_id,
        )
        metadata = dict(node["metadata"])
        linked_initiatives = metadata.get("creator_initiative_ids", [])
        if not isinstance(linked_initiatives, list):
            linked_initiatives = []
        changed = False
        if initiative_id not in linked_initiatives:
            linked_initiatives.append(initiative_id)
            changed = True
        metadata["creator_initiative_ids"] = linked_initiatives[-32:]
        if evolution_cycle_id:
            cycles = metadata.get("evolution_cycle_ids", [])
            if not isinstance(cycles, list):
                cycles = []
            if evolution_cycle_id not in cycles:
                cycles.append(evolution_cycle_id)
                changed = True
            metadata["evolution_cycle_ids"] = cycles[-32:]
        if changed:
            with self.connection:
                self.connection.execute(
                    "UPDATE knowledge_nodes SET metadata_json=?,updated_at=? WHERE id=?",
                    (json.dumps(metadata, ensure_ascii=False, sort_keys=True), self._now(), node_id),
                )
                self._event("node", node_id, "initiative_linked", {
                    "initiative_id": initiative_id, "relationship": relationship,
                    "evolution_cycle_id": evolution_cycle_id,
                })
        updated = self.get_node(node_id)
        assert updated is not None
        return {"node": updated, "initiative_link": link}

    def record_task_event(self, task_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
        task_id = self._text(task_id, "task_id", 160)
        event_type = self._text(event_type, "event_type", 80)
        clean_payload = self._json_object(payload, "payload")
        with self.connection:
            self._event("task", task_id, event_type, clean_payload)

    def list_events(self, *, entity_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if entity_id is None:
            rows = self.connection.execute(
                "SELECT * FROM knowledge_events ORDER BY sequence DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM knowledge_events WHERE entity_id=? ORDER BY sequence DESC LIMIT ?",
                (self._text(entity_id, "entity_id", 160), limit),
            ).fetchall()
        return [{
            "sequence": row["sequence"], "event_id": row["event_id"],
            "entity_type": row["entity_type"], "entity_id": row["entity_id"],
            "event_type": row["event_type"], "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
        } for row in rows]

    def stats(self) -> dict[str, int]:
        return {
            "nodes": self.connection.execute(
                "SELECT count(*) FROM knowledge_nodes WHERE deleted_at IS NULL"
            ).fetchone()[0],
            "edges": self.connection.execute("SELECT count(*) FROM knowledge_edges").fetchone()[0],
            "events": self.connection.execute("SELECT count(*) FROM knowledge_events").fetchone()[0],
        }

    def lint(self) -> list[dict[str, str]]:
        findings: list[dict[str, str]] = []
        bad_claims = self.connection.execute(
            "SELECT n.id FROM knowledge_nodes n WHERE n.node_type='claim' "
            "AND n.deleted_at IS NULL AND n.status IN ('supported','refuted','contested','superseded') "
            "AND (json_extract(n.metadata_json,'$.reviewer_id') IS NULL "
            "OR COALESCE(json_array_length(json_extract(n.metadata_json,'$.evidence_refs')),0)=0)"
        ).fetchall()
        findings.extend({"code": "claim_missing_evidence_or_reviewer", "node_id": row["id"]} for row in bad_claims)
        contradictions = self.connection.execute(
            "SELECT e.source_id,e.target_id FROM knowledge_edges e "
            "JOIN knowledge_nodes s ON s.id=e.source_id JOIN knowledge_nodes t ON t.id=e.target_id "
            "WHERE e.edge_type='contradicts' AND s.node_type='claim' AND t.node_type='claim' "
            "AND s.deleted_at IS NULL AND t.deleted_at IS NULL"
        ).fetchall()
        findings.extend({"code": "contradictory_claims", "source_id": row["source_id"],
                         "target_id": row["target_id"]} for row in contradictions)
        isolated = self.connection.execute(
            "SELECT n.id FROM knowledge_nodes n WHERE n.deleted_at IS NULL "
            "AND n.node_type IN ('idea','experiment','claim') "
            "AND NOT EXISTS (SELECT 1 FROM knowledge_edges e WHERE e.source_id=n.id OR e.target_id=n.id)"
        ).fetchall()
        findings.extend({"code": "unlinked_knowledge_node", "node_id": row["id"]} for row in isolated)
        return findings
