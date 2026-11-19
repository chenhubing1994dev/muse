"""Explicit, append-only evidence review writer with verified citation anchors."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from muse.research_knowledge import (
    EVIDENCE_QUALITY_LEVELS, EVIDENCE_REVIEW_VERDICTS,
    SOURCE_INDEPENDENCE_LEVELS, ResearchKnowledgeStore,
)
from muse.research_wiki_citation import verify_citation


def record_review(
    database: str | Path, *, evidence_id: str, reviewer_id: str, verdict: str,
    quality: str, independence: str, rationale: str, confirm_write: bool = False,
    evidence_database: str | Path = "data/research/evidence.sqlite3",
    source_sha256: str | None = None, excerpt: str | None = None,
) -> dict[str, Any]:
    if confirm_write is not True:
        raise PermissionError("explicit confirm_write=True is required")
    if verdict not in EVIDENCE_REVIEW_VERDICTS:
        raise ValueError("unsupported verdict")
    if quality not in EVIDENCE_QUALITY_LEVELS:
        raise ValueError("unsupported quality")
    if independence not in SOURCE_INDEPENDENCE_LEVELS:
        raise ValueError("unsupported independence")
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError("rationale must explain the source-content assessment")
    path = Path(database).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    citation_anchor = None
    with ResearchKnowledgeStore(path) as store:
        evidence = store.connection.execute(
            "SELECT n.external_ref,c.metadata_json FROM claim_evidence e "
            "JOIN knowledge_nodes n ON n.id=e.source_node_id "
            "JOIN knowledge_nodes c ON c.id=e.claim_id WHERE e.id=?",
            (evidence_id,),
        ).fetchone()
    if evidence is None:
        raise ValueError("claim evidence item does not exist")
    author_id = json.loads(evidence["metadata_json"]).get("author_id")
    if author_id and author_id == reviewer_id:
        raise ValueError("evidence reviewer must be independent from claim author")
    if verdict in {"supports", "refutes"} and (not source_sha256 or not excerpt):
        raise ValueError("supports/refutes require --source-sha256 and --excerpt")
    if source_sha256 or excerpt:
        if not source_sha256 or not excerpt:
            raise ValueError("source SHA-256 and excerpt must be provided together")
        if not evidence["external_ref"]:
            raise ValueError("evidence source node has no exact source URL")
        citation_anchor = verify_citation(
            evidence_database, source_url=evidence["external_ref"],
            expected_sha256=source_sha256, excerpt=excerpt,
        )
    with ResearchKnowledgeStore(path) as store:
        review = store.record_claim_evidence_review(
            evidence_id, reviewer_id=reviewer_id, verdict=verdict,
            quality=quality, independence=independence, rationale=rationale,
            citation_anchor=citation_anchor,
        )
    return {"schema": "muse.research_wiki.evidence_review.v2",
            "database": str(path), "append_only": True, "review": review}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Append one explicit human evidence review; supports/refutes require a verified source excerpt"
    )
    parser.add_argument("--db", default="data/research/knowledge.sqlite3")
    parser.add_argument("--evidence-db", default="data/research/evidence.sqlite3")
    parser.add_argument("--evidence-id", required=True)
    parser.add_argument("--reviewer-id", required=True)
    parser.add_argument("--verdict", required=True, choices=sorted(EVIDENCE_REVIEW_VERDICTS))
    parser.add_argument("--quality", required=True, choices=sorted(EVIDENCE_QUALITY_LEVELS))
    parser.add_argument("--independence", required=True, choices=sorted(SOURCE_INDEPENDENCE_LEVELS))
    parser.add_argument("--rationale", required=True,
                        help="record what source content was checked and why it supports this verdict")
    parser.add_argument("--source-sha256", help="required for supports/refutes; hash of stored source snapshot")
    parser.add_argument("--excerpt", help="exact unique excerpt from the stored source snapshot (max 2000 chars)")
    parser.add_argument("--confirm-write", action="store_true",
                        help="required explicit opt-in to append a review record")
    args = parser.parse_args(argv)
    result = record_review(
        args.db, evidence_id=args.evidence_id, reviewer_id=args.reviewer_id,
        verdict=args.verdict, quality=args.quality, independence=args.independence,
        rationale=args.rationale, confirm_write=args.confirm_write,
        evidence_database=args.evidence_db, source_sha256=args.source_sha256,
        excerpt=args.excerpt,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
