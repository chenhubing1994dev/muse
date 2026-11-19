"""Human-readable, read-only evidence review queue for Research Wiki."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
from typing import Any


def build_review_queue(
    database: str | Path, *, limit: int = 100, claim_id: str | None = None,
    include_reviewed: bool = False,
) -> dict[str, Any]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    path = Path(database).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        required = {"knowledge_nodes", "claim_evidence", "claim_evidence_reviews"}
        present = {row["name"] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        missing = sorted(required - present)
        if missing:
            raise RuntimeError("database schema is incomplete: " + ", ".join(missing))

        where = ["c.deleted_at IS NULL", "s.deleted_at IS NULL"]
        params: list[Any] = []
        if not include_reviewed:
            where.append(
                "NOT EXISTS (SELECT 1 FROM claim_evidence_reviews r "
                "WHERE r.claim_evidence_id=e.id)"
            )
        if claim_id:
            where.append("e.claim_id=?")
            params.append(claim_id)
        params.append(limit)
        rows = connection.execute(
            "SELECT e.id AS evidence_id,e.claim_id,e.source_node_id,e.direction,"
            "e.evidence_type,e.quality,e.independence,e.search_query,e.notes,e.created_at,"
            "c.title AS claim_title,c.summary AS claim_summary,c.status AS claim_status,"
            "c.metadata_json AS claim_metadata_json,"
            "s.title AS source_title,s.summary AS source_summary,s.external_ref AS source_ref,"
            "(SELECT count(*) FROM claim_evidence_reviews r "
            " WHERE r.claim_evidence_id=e.id) AS review_count,"
            "(SELECT r.verdict FROM claim_evidence_reviews r "
            " WHERE r.claim_evidence_id=e.id ORDER BY r.reviewed_at DESC,r.id DESC LIMIT 1) "
            "AS latest_verdict "
            "FROM claim_evidence e "
            "JOIN knowledge_nodes c ON c.id=e.claim_id AND c.node_type='claim' "
            "JOIN knowledge_nodes s ON s.id=e.source_node_id AND s.node_type='source' "
            "WHERE " + " AND ".join(where) +
            " ORDER BY e.created_at,e.id LIMIT ?",
            tuple(params),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                metadata = json.loads(item.pop("claim_metadata_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            item["original_claim"] = metadata.get("original_claim") or item["claim_summary"] or item["claim_title"]
            item["original_claim_source"] = metadata.get("original_claim_source")
            item["review_status"] = "pending" if item["review_count"] == 0 else "reviewed"
            item["retrieval_direction_is_not_verdict"] = True
            items.append(item)
        return {
            "schema": "muse.research_wiki.review_queue.v1",
            "database": str(path),
            "read_only": True,
            "filter": {"claim_id": claim_id, "include_reviewed": include_reviewed},
            "count": len(items),
            "limit": limit,
            "items": items,
        }
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Research Wiki evidence review workbench"
    )
    parser.add_argument("--db", default="data/research/knowledge.sqlite3")
    parser.add_argument("--limit", type=int, default=100, help="maximum items (1..1000)")
    parser.add_argument("--claim-id", help="restrict the queue to one claim ID")
    parser.add_argument("--include-reviewed", action="store_true")
    parser.add_argument("--output", help="optional JSON output path; database remains read-only")
    args = parser.parse_args(argv)
    result = build_review_queue(
        args.db, limit=args.limit, claim_id=args.claim_id,
        include_reviewed=args.include_reviewed,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
