"""Auditable research lifecycle and conservative evidence assessment for Muse Claims."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from muse.lease_recovery import MigrationError, _backup_database, _integrity
from muse.research_knowledge import ResearchKnowledgeStore


class ResearchStateError(RuntimeError):
    """A research lifecycle transition or assessment was refused."""


RESEARCH_STATES = frozenset({
    "not_researched", "queued", "researching", "evidence_collected", "evaluated", "failed",
})
ALLOWED_TRANSITIONS = {
    "not_researched": {"queued", "failed"},
    "queued": {"researching", "failed"},
    "researching": {"evidence_collected", "failed"},
    "evidence_collected": {"evaluated", "failed"},
    "evaluated": set(),
    "failed": {"queued"},
}
_ALLOWED_METADATA_KEYS = frozenset({
    "research_status", "research_round", "research_task_ids", "research_task_keys",
    "research_status_updated_at", "research_failure_history", "research_failure",
    "research_assessment", "evidence_collected_at",
})


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


class ResearchStateMachine:
    """Change only research metadata on proposed Claim nodes."""

    def __init__(self, knowledge_db: str | Path, *, handoff_dir: str | Path = "data/handoff") -> None:
        self.path = Path(knowledge_db)
        self.handoff_dir = Path(handoff_dir)

    def _snapshot(self) -> Path:
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        if _integrity(self.path) != "ok":
            raise ResearchStateError("knowledge database integrity check failed")
        self.handoff_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = self.handoff_dir / f"research-state-{stamp}-{uuid.uuid4().hex[:10]}.sqlite3"
        if target.exists():
            raise MigrationError(f"backup target exists; refusing overwrite: {target}")
        _backup_database(self.path, target)
        return target

    def _read_claim(self, claim_id: str) -> tuple[sqlite3.Row, dict[str, Any]]:
        db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            row = db.execute(
                "SELECT id,node_type,status,metadata_json FROM knowledge_nodes WHERE id=? AND deleted_at IS NULL",
                (claim_id,),
            ).fetchone()
            if row is None or row["node_type"] != "claim":
                raise KeyError(claim_id)
            metadata = json.loads(row["metadata_json"] or "{}")
            return row, metadata
        finally:
            db.close()

    def get_status(self, claim_id: str) -> str:
        _, metadata = self._read_claim(claim_id)
        return str(metadata.get("research_status", "not_researched"))

    def transition(
        self, claim_id: str, new_state: str, *,
        metadata_updates: dict[str, Any] | None = None,
        allow_retry: bool = False,
    ) -> dict[str, Any]:
        if new_state not in RESEARCH_STATES:
            raise ValueError(f"unsupported research state: {new_state}")
        updates = metadata_updates or {}
        if not isinstance(updates, dict) or not set(updates).issubset(_ALLOWED_METADATA_KEYS):
            raise ValueError("metadata_updates contains fields outside the research allowlist")
        if updates.get("research_status", new_state) != new_state:
            raise ValueError("metadata_updates cannot override research_status")

        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=10000")
            row = db.execute(
                "SELECT node_type,status,metadata_json FROM knowledge_nodes WHERE id=? AND deleted_at IS NULL",
                (claim_id,),
            ).fetchone()
            if row is None or row["node_type"] != "claim":
                raise KeyError(claim_id)
            if row["status"] != "proposed":
                raise ResearchStateError("only proposed Claim nodes may enter this research lifecycle")
            metadata = json.loads(row["metadata_json"] or "{}")
            current = metadata.get("research_status", "not_researched")
            if current not in RESEARCH_STATES:
                raise ResearchStateError(f"unknown current research state: {current}")
            if current == new_state and not updates:
                return {"claim_id": claim_id, "research_status": current, "changed": False}
            if current == "failed" and new_state == "queued" and not allow_retry:
                raise ResearchStateError("failed research requires explicit retry")
            if new_state not in ALLOWED_TRANSITIONS[current]:
                raise ResearchStateError(f"illegal research transition: {current} -> {new_state}")
            metadata.update(updates)
            metadata["research_status"] = new_state
            metadata["research_status_updated_at"] = _stamp()
            snapshot = self._snapshot()
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "UPDATE knowledge_nodes SET metadata_json=?,updated_at=? "
                "WHERE id=? AND node_type='claim' AND status='proposed' AND metadata_json=?",
                (json.dumps(metadata, ensure_ascii=False, sort_keys=True), _stamp(), claim_id, row["metadata_json"]),
            )
            if cursor.rowcount != 1:
                raise ResearchStateError("research metadata compare-and-swap failed")
            db.commit()
            return {
                "claim_id": claim_id, "previous_status": current,
                "research_status": new_state, "changed": True, "backup": str(snapshot),
            }
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def record_failure(self, claim_id: str, *, reason: str, error_code: str) -> dict[str, Any]:
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise ValueError("reason must contain 1-1000 characters")
        if not isinstance(error_code, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{1,79}", error_code):
            raise ValueError("error_code must be a bounded lowercase identifier")
        _, metadata = self._read_claim(claim_id)
        current = metadata.get("research_status", "not_researched")
        if current == "failed":
            return {"claim_id": claim_id, "research_status": "failed", "changed": False}
        history = metadata.get("research_failure_history", [])
        if not isinstance(history, list):
            history = []
        history.append({"reason": reason.strip(), "error_code": error_code, "failed_at": _stamp()})
        return self.transition(
            claim_id, "failed",
            metadata_updates={
                "research_failure": {"reason": reason.strip(), "error_code": error_code},
                "research_failure_history": history[-20:],
            },
        )

    @staticmethod
    def _verified_citation(item: dict[str, Any], review: dict[str, Any]) -> bool:
        source_metadata = item.get("source_metadata")
        if not isinstance(source_metadata, dict):
            return False
        source_hash = source_metadata.get("content_hash") or source_metadata.get("sha256")
        if not _valid_sha(source_hash) or int(source_metadata.get("chars") or 0) < 200:
            return False
        for citation in review.get("citations", []):
            excerpt = citation.get("excerpt")
            if not isinstance(excerpt, str) or not excerpt:
                continue
            if citation.get("source_url") != item.get("source_ref"):
                continue
            if citation.get("source_sha256") != source_hash:
                continue
            start_char = citation.get("start_char")
            end_char = citation.get("end_char")
            if (isinstance(start_char, bool) or not isinstance(start_char, int)
                    or isinstance(end_char, bool) or not isinstance(end_char, int)
                    or start_char < 0 or end_char - start_char != len(excerpt)):
                continue
            if not _valid_sha(citation.get("excerpt_sha256")):
                continue
            if hashlib.sha256(excerpt.encode("utf-8")).hexdigest() != citation["excerpt_sha256"]:
                continue
            freshness = citation.get("freshness")
            if isinstance(freshness, dict) and freshness.get("source_current") is False:
                continue
            return True
        return False

    def evaluate_claim(self, claim_id: str) -> dict[str, Any]:
        if self.get_status(claim_id) != "evidence_collected":
            raise ResearchStateError("claim must be evidence_collected before evaluation")
        snapshot = self._snapshot()
        with ResearchKnowledgeStore(self.path) as knowledge:
            claim = knowledge.get_node(claim_id)
            if claim is None or claim.get("node_type") != "claim" or claim.get("status") != "proposed":
                raise ResearchStateError("claim is missing or no longer proposed")
            evidence = knowledge.list_claim_evidence(claim_id, limit=100)
            coverage = knowledge.claim_evidence_coverage(claim_id, source_limit=32)
            directions = {"support_search_candidate": [], "counter_search_candidate": []}
            support_source_refs: set[str] = set()
            refute_source_refs: set[str] = set()
            support_domains: set[str] = set()
            refute_domains: set[str] = set()
            support_reviews: list[dict[str, Any]] = []
            refute_reviews: list[dict[str, Any]] = []
            source_domains: set[str] = set()
            reviewer_ids: set[str] = set()
            verified_citations_by_direction = {key: False for key in directions}

            for item in evidence:
                direction = item.get("direction")
                if direction not in directions:
                    continue
                directions[direction].append(item)
                domain = None
                source_metadata = item.get("source_metadata") or {}
                identity = source_metadata.get("source_identity")
                if isinstance(identity, dict):
                    domain = identity.get("registrable_domain") or identity.get("hostname")
                    if isinstance(domain, str) and domain:
                        source_domains.add(domain.casefold())
                for review in item.get("reviews", []):
                    reviewer_id = review.get("reviewer_id")
                    if isinstance(reviewer_id, str) and reviewer_id:
                        reviewer_ids.add(reviewer_id)
                    citation_ok = self._verified_citation(item, review)
                    if citation_ok:
                        verified_citations_by_direction[direction] = True
                    verdict = review.get("verdict")
                    if verdict not in {"supports", "refutes"}:
                        continue
                    if review.get("quality") not in {"moderate", "high"}:
                        continue
                    if review.get("independence") != "independent" or not citation_ok:
                        continue
                    summary = {
                        "evidence_id": item.get("id"),
                        "source_ref": item.get("source_ref"),
                        "source_sha256": source_metadata.get("content_hash") or source_metadata.get("sha256"),
                        "direction": direction,
                        "verdict": verdict,
                        "reviewer_id": reviewer_id,
                        "quality": review.get("quality"),
                        "independence": review.get("independence"),
                    }
                    summary["publisher_domain"] = domain
                    if verdict == "supports":
                        support_source_refs.add(str(item.get("source_ref")))
                        if domain:
                            support_domains.add(domain.casefold())
                    else:
                        refute_source_refs.add(str(item.get("source_ref")))
                        if domain:
                            refute_domains.add(domain.casefold())
                    (support_reviews if verdict == "supports" else refute_reviews).append(summary)

            both_directions = all(directions.values())
            both_direction_citations = all(verified_citations_by_direction.values())
            conflicts = int(coverage.get("review_conflict_count", 0))
            reasons: list[str] = []
            conclusion = "inconclusive"
            if not both_directions:
                reasons.append("support_or_counter_search_sources_missing")
            if not both_direction_citations:
                reasons.append("verified_citation_missing_from_one_or_both_directions")
            if not reviewer_ids:
                reasons.append("no_evidence_reviews")
            if conflicts:
                reasons.append("conflicting_reviews_require_human_resolution")
            if len(source_domains) < 2:
                reasons.append("fewer_than_two_distinct_publisher_domains")
            enough_basis = (
                both_directions and both_direction_citations and reviewer_ids
                and conflicts == 0 and len(source_domains) >= 2
            )
            if enough_basis and len(support_source_refs) >= 2 and len(support_domains) >= 2 and not refute_source_refs:
                conclusion = "supported"
            elif enough_basis and len(refute_source_refs) >= 2 and len(refute_domains) >= 2 and not support_source_refs:
                conclusion = "refuted"
            elif enough_basis:
                reasons.append("insufficient_or_conflicting_reviewed_verdicts")

            assessment = {
                "conclusion": conclusion,
                "assessed_at": _stamp(),
                "evidence_count": len(evidence),
                "support_search_source_count": len(directions["support_search_candidate"]),
                "counter_search_source_count": len(directions["counter_search_candidate"]),
                "verified_support_source_count": len(support_source_refs),
                "verified_refute_source_count": len(refute_source_refs),
                "verified_support_review_count": len(support_reviews),
                "verified_refute_review_count": len(refute_reviews),
                "distinct_publisher_domain_count": len(source_domains),
                "reviewer_count": len(reviewer_ids),
                "review_conflict_count": conflicts,
                "supporting_sources": support_reviews,
                "counter_evidence_sources": [
                    {
                        "evidence_id": item.get("id"),
                        "source_ref": item.get("source_ref"),
                        "source_sha256": (item.get("source_metadata") or {}).get("content_hash"),
                        "direction": item.get("direction"),
                    }
                    for item in directions["counter_search_candidate"]
                ],
                "verified_refuting_reviews": refute_reviews,
                "reasons": reasons,
                "claim_status_unchanged": True,
                "automatic_fact_promotion": False,
                "requires_human_confirmation_before_fact_promotion": True,
            }

            db = knowledge.connection
            row = db.execute(
                "SELECT metadata_json,status FROM knowledge_nodes WHERE id=?", (claim_id,)
            ).fetchone()
            if row is None or row["status"] != "proposed":
                raise ResearchStateError("claim changed before evaluation write")
            metadata = json.loads(row["metadata_json"] or "{}")
            if metadata.get("research_status") != "evidence_collected":
                raise ResearchStateError("research state changed during evaluation")
            metadata["research_status"] = "evaluated"
            metadata["research_assessment"] = assessment
            metadata["research_status_updated_at"] = _stamp()
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "UPDATE knowledge_nodes SET metadata_json=?,updated_at=? "
                "WHERE id=? AND status='proposed' AND metadata_json=?",
                (json.dumps(metadata, ensure_ascii=False, sort_keys=True), _stamp(), claim_id, row["metadata_json"]),
            )
            if cursor.rowcount != 1:
                raise ResearchStateError("evaluation metadata compare-and-swap failed")
            db.commit()
        return {"claim_id": claim_id, **assessment, "backup": str(snapshot)}
