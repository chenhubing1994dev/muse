"""Fail-closed, claim-level evidence contract for narration segments.

This module is deliberately pure: it does not change narration generation,
research verdicts, or publication state. Callers must supply independently
reviewed evidence records and verified citation-anchor/freshness signals.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

_SCHEMA_VERSION = 1
_CLAIM_ID = re.compile(r"^[0-9a-f]{32}$")
_EVIDENCE_ID = re.compile(r"^[0-9a-f]{32}$")
_VERDICTS = {"supports", "refutes", "mentions", "insufficient", "unreviewed"}


def _identifier(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{label} must be a 32-character lowercase hexadecimal id")
    return value


def _timecode(segment: Mapping[str, Any]) -> dict[str, float] | None:
    has_start = "start_seconds" in segment
    has_end = "end_seconds" in segment
    if not has_start and not has_end:
        return None
    if has_start != has_end:
        raise ValueError("segment timecode requires both start_seconds and end_seconds")
    start, end = segment["start_seconds"], segment["end_seconds"]
    if (isinstance(start, bool) or not isinstance(start, (int, float))
            or isinstance(end, bool) or not isinstance(end, (int, float))
            or not math.isfinite(start) or not math.isfinite(end)
            or start < 0 or end <= start):
        raise ValueError("segment timecode must be finite, non-negative and increasing")
    return {"start_seconds": float(start), "end_seconds": float(end)}


def build_evidence_by_claim_from_dossier(dossier: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Adapt a ResearchKnowledgeStore dossier without upgrading evidence.

    Verdicts come only from stored append-only reviews. A citation anchor is
    verified only when that review stores a citation for the exact source URL.
    Freshness remains unknown because the dossier has no verified lifecycle field.
    """
    if not isinstance(dossier, Mapping):
        raise ValueError("claim dossier must be a mapping")
    claim_id = _identifier(dossier.get("claim_id"), _CLAIM_ID, "claim_id")
    raw_evidence = dossier.get("evidence", [])
    if not isinstance(raw_evidence, Sequence) or isinstance(raw_evidence, (str, bytes)):
        raise ValueError("claim dossier evidence must be a sequence")

    adapted: list[dict[str, Any]] = []
    for item in raw_evidence:
        if not isinstance(item, Mapping):
            raise ValueError("each dossier evidence item must be a mapping")
        evidence_id = _identifier(item.get("id"), _EVIDENCE_ID, "evidence_id")
        source_ref = item.get("source_ref", "")
        if not isinstance(source_ref, str):
            source_ref = ""
        latest_review = item.get("latest_review")
        if latest_review is None:
            reviews = item.get("reviews", [])
            if isinstance(reviews, Sequence) and not isinstance(reviews, (str, bytes)) and reviews:
                latest_review = reviews[-1]
        if not isinstance(latest_review, Mapping):
            latest_review = {}
        verdict = latest_review.get("verdict", "unreviewed")
        if verdict not in _VERDICTS:
            verdict = "unreviewed"
        citations = latest_review.get("citations", [])
        matching_citations = [
            citation for citation in citations
            if isinstance(citation, Mapping)
            and citation.get("source_url") == source_ref
            and isinstance(citation.get("source_sha256"), str)
            and re.fullmatch(r"[a-f0-9]{64}", citation["source_sha256"]) is not None
            and isinstance(citation.get("excerpt_sha256"), str)
            and re.fullmatch(r"[a-f0-9]{64}", citation["excerpt_sha256"]) is not None
        ] if isinstance(citations, Sequence) and not isinstance(citations, (str, bytes)) else []
        anchor_verified = bool(verdict in {"supports", "refutes"} and matching_citations)
        freshness_values = [
            citation.get("freshness", {}).get("source_current")
            for citation in matching_citations if isinstance(citation.get("freshness"), Mapping)
        ]
        source_current = True if True in freshness_values else False if False in freshness_values else None
        adapted.append({
            "evidence_id": evidence_id,
            "verdict": verdict,
            "citation_anchor_verified": anchor_verified,
            "source_current": source_current,
            "source_ref": source_ref,
            "direction": item.get("direction"),
            "review_state": "reviewed" if latest_review else "unreviewed",
        })
    return {claim_id: adapted}


def build_narration_evidence_contract(
    segments: Sequence[Mapping[str, Any]],
    evidence_by_claim: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Build a deterministic publishability report without inventing verdicts.

    Each segment supplies ``text``, optional integer ``index``, optional
    ``factual`` (defaults to True), and ``claim_ids``. Each evidence record
    supplies ``evidence_id``, ``verdict``, ``citation_anchor_verified`` and
    ``source_current`` (True/False/None). Only a reviewed ``supports`` record
    with a verified citation anchor and explicitly current source can pass.
    Any verified refutation blocks the segment; missing or unknown checks fail
    closed. Candidate retrieval direction is never interpreted as a verdict.
    """
    if not isinstance(segments, Sequence) or isinstance(segments, (str, bytes)) or not segments:
        raise ValueError("segments must be a non-empty sequence")
    if not isinstance(evidence_by_claim, Mapping):
        raise ValueError("evidence_by_claim must be a mapping")

    normalized_evidence: dict[str, list[dict[str, Any]]] = {}
    for raw_claim_id, records in evidence_by_claim.items():
        claim_id = _identifier(raw_claim_id, _CLAIM_ID, "claim_id")
        if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
            raise ValueError("claim evidence records must be a sequence")
        normalized: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, Mapping):
                raise ValueError("each evidence record must be a mapping")
            evidence_id = _identifier(record.get("evidence_id"), _EVIDENCE_ID, "evidence_id")
            verdict = record.get("verdict", "unreviewed")
            if verdict not in _VERDICTS:
                raise ValueError("unsupported evidence verdict")
            anchor = record.get("citation_anchor_verified", False)
            current = record.get("source_current")
            if not isinstance(anchor, bool):
                raise ValueError("citation_anchor_verified must be boolean")
            if current is not True and current is not False and current is not None:
                raise ValueError("source_current must be True, False or None")
            normalized.append({
                "evidence_id": evidence_id,
                "verdict": verdict,
                "citation_anchor_verified": anchor,
                "source_current": current,
                "source_ref": str(record.get("source_ref", "")),
            })
        normalized_evidence[claim_id] = normalized

    output_segments: list[dict[str, Any]] = []
    for position, segment in enumerate(segments, 1):
        if not isinstance(segment, Mapping):
            raise ValueError("each narration segment must be a mapping")
        text = segment.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("each narration segment must have non-empty text")
        factual = segment.get("factual", True)
        if not isinstance(factual, bool):
            raise ValueError("factual must be boolean")
        raw_claim_ids = segment.get("claim_ids", [])
        if not isinstance(raw_claim_ids, Sequence) or isinstance(raw_claim_ids, (str, bytes)):
            raise ValueError("claim_ids must be a sequence")
        claim_ids = list(dict.fromkeys(
            _identifier(value, _CLAIM_ID, "claim_id") for value in raw_claim_ids
        ))
        timecode = _timecode(segment)
        if not factual:
            gate_status = "not_applicable"
            reasons: list[str] = []
            attached: list[dict[str, Any]] = []
        else:
            reasons = []
            attached = []
            if not claim_ids:
                reasons.append("missing_claim_link")
            for claim_id in claim_ids:
                records = normalized_evidence.get(claim_id, [])
                attached.extend({"claim_id": claim_id, **record} for record in records)
                verified_refutations = [
                    item for item in records
                    if item["verdict"] == "refutes" and item["citation_anchor_verified"]
                ]
                if verified_refutations:
                    reasons.append("verified_counter_evidence_unresolved")
                valid_support = [
                    item for item in records
                    if item["verdict"] == "supports"
                    and item["citation_anchor_verified"]
                    and item["source_current"] is True
                ]
                if not valid_support:
                    if any(item["verdict"] == "supports" and item["source_current"] is False for item in records):
                        reasons.append("supporting_source_stale")
                    elif any(item["verdict"] == "supports" and item["source_current"] is None for item in records):
                        reasons.append("supporting_source_freshness_unknown")
                    elif any(item["verdict"] == "supports" and not item["citation_anchor_verified"] for item in records):
                        reasons.append("citation_anchor_unverified")
                    else:
                        reasons.append("no_verified_support")
            reasons = list(dict.fromkeys(reasons))
            gate_status = "ready" if not reasons else "blocked"

        entry: dict[str, Any] = {
            "index": segment.get("index", position),
            "text": text,
            "sentence_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "factual": factual,
            "claim_ids": claim_ids,
            "evidence": attached,
            "gate_status": gate_status,
            "blocking_reasons": reasons,
        }
        if timecode is not None:
            entry["timecode"] = timecode
        output_segments.append(entry)

    publishable = all(item["gate_status"] != "blocked" for item in output_segments)
    return {
        "schema_version": _SCHEMA_VERSION,
        "contract": "claim_grounded_narration",
        "publishable": publishable,
        "status": "ready" if publishable else "blocked",
        "segments": output_segments,
        "policy": {
            "retrieval_direction_is_not_verdict": True,
            "requires_reviewed_support": True,
            "requires_verified_citation_anchor": True,
            "requires_explicitly_current_source": True,
            "verified_counter_evidence_blocks": True,
        },
    }
