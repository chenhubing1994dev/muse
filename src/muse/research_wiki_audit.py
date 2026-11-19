"""Read-only Research Wiki governance audit; never initializes or mutates the database."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
from typing import Any


def audit_database(database: str | Path, *, limit: int = 500) -> dict[str, Any]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000:
        raise ValueError("limit must be between 1 and 5000")
    path = Path(database).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        required = {
            "knowledge_nodes", "knowledge_edges", "claim_evidence",
            "claim_evidence_reviews", "source_relationships",
            "source_identity_candidates", "source_identity_candidate_reviews",
        }
        present = {row["name"] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        missing = sorted(required - present)
        if missing:
            raise RuntimeError("database schema is incomplete: " + ", ".join(missing))

        def rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
            return [dict(row) for row in connection.execute(sql, params).fetchall()]

        summary = {
            "active_nodes": connection.execute(
                "SELECT count(*) FROM knowledge_nodes WHERE deleted_at IS NULL"
            ).fetchone()[0],
            "active_edges": connection.execute(
                "SELECT count(*) FROM knowledge_edges e "
                "JOIN knowledge_nodes s ON s.id=e.source_id "
                "JOIN knowledge_nodes t ON t.id=e.target_id "
                "WHERE s.deleted_at IS NULL AND t.deleted_at IS NULL"
            ).fetchone()[0],
            "lint_scope_isolated_nodes": connection.execute(
                "SELECT count(*) FROM knowledge_nodes n WHERE n.deleted_at IS NULL "
                "AND n.node_type IN ('idea','experiment','claim') "
                "AND NOT EXISTS (SELECT 1 FROM knowledge_edges e "
                "WHERE e.source_id=n.id OR e.target_id=n.id)"
            ).fetchone()[0],
            "claims_without_evidence": connection.execute(
                "SELECT count(*) FROM knowledge_nodes n WHERE n.deleted_at IS NULL "
                "AND n.node_type='claim' AND NOT EXISTS "
                "(SELECT 1 FROM claim_evidence e WHERE e.claim_id=n.id)"
            ).fetchone()[0],
            "evidence_items_unreviewed": connection.execute(
                "SELECT count(*) FROM claim_evidence e WHERE NOT EXISTS "
                "(SELECT 1 FROM claim_evidence_reviews r WHERE r.claim_evidence_id=e.id)"
            ).fetchone()[0],
            "source_relationships": connection.execute(
                "SELECT count(*) FROM source_relationships"
            ).fetchone()[0],
            "pending_source_identity_candidates": connection.execute(
                "SELECT count(*) FROM source_identity_candidates c WHERE NOT EXISTS "
                "(SELECT 1 FROM source_identity_candidate_reviews r WHERE r.candidate_id=c.id)"
            ).fetchone()[0],
            "advanced_claims": connection.execute(
                "SELECT count(*) FROM knowledge_nodes WHERE deleted_at IS NULL "
                "AND node_type='claim' AND status IN ('supported','refuted','contested','superseded')"
            ).fetchone()[0],
        }

        queue: list[dict[str, Any]] = []
        for row in rows(
            "SELECT n.id,n.node_type,n.status FROM knowledge_nodes n "
            "WHERE n.deleted_at IS NULL AND n.node_type IN ('idea','experiment','claim') "
            "AND NOT EXISTS (SELECT 1 FROM knowledge_edges e "
            "WHERE e.source_id=n.id OR e.target_id=n.id) ORDER BY n.node_type,n.id LIMIT ?",
            (limit,),
        ):
            queue.append({"kind": "isolated_knowledge_node", **row, "recommended_action": "classify_before_linking"})

        for row in rows(
            "SELECT n.id,n.node_type,n.status FROM knowledge_nodes n "
            "WHERE n.deleted_at IS NULL AND n.node_type='claim' AND NOT EXISTS "
            "(SELECT 1 FROM claim_evidence e WHERE e.claim_id=n.id) "
            "ORDER BY n.status,n.id LIMIT ?", (limit,),
        ):
            queue.append({"kind": "claim_without_evidence", **row, "recommended_action": "keep_unverified_and_research"})

        for row in rows(
            "SELECT e.id AS evidence_id,e.claim_id,e.source_node_id FROM claim_evidence e "
            "WHERE NOT EXISTS (SELECT 1 FROM claim_evidence_reviews r WHERE r.claim_evidence_id=e.id) "
            "ORDER BY e.claim_id,e.id LIMIT ?", (limit,),
        ):
            queue.append({"kind": "unreviewed_evidence_candidate", **row, "recommended_action": "review_source_content"})

        for row in rows(
            "SELECT c.id AS candidate_id,c.candidate_type,c.source_node_id,c.related_source_node_id "
            "FROM source_identity_candidates c WHERE NOT EXISTS "
            "(SELECT 1 FROM source_identity_candidate_reviews r WHERE r.candidate_id=c.id) "
            "ORDER BY c.candidate_type,c.id LIMIT ?", (limit,),
        ):
            queue.append({"kind": "pending_source_identity_candidate", **row, "recommended_action": "verify_lineage_manually"})

        return {
            "schema": "muse.research_wiki.audit.v1",
            "database": str(path),
            "read_only": True,
            "summary": summary,
            "queue_count": len(queue),
            "queue_truncated_per_category_at": limit,
            "queue": queue,
        }
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Research Wiki governance audit")
    parser.add_argument("--db", default="data/research/knowledge.sqlite3")
    parser.add_argument("--limit", type=int, default=500, help="maximum rows per queue category (1..5000)")
    parser.add_argument("--output", help="optional JSON output path; database remains read-only")
    args = parser.parse_args(argv)
    result = audit_database(args.db, limit=args.limit)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
