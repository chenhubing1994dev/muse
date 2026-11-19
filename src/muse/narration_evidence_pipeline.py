"""Orchestrate claim dossier lookup, narration evidence gating and run persistence."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re
from typing import Any

from muse.narration_evidence_contract import (
    build_evidence_by_claim_from_dossier,
    build_narration_evidence_contract,
)
from muse.narration_sync import NarrationSyncStore
from muse.source_freshness import refresh_dossier_citations

_CLAIM_ID = re.compile(r"^[0-9a-f]{32}$")


def persist_claim_grounded_narration_run(
    narration_result: Mapping[str, Any],
    research_store: Any,
    sync_store: NarrationSyncStore,
    *,
    timeline: list[dict[str, Any]] | None = None,
    audio_path: str | Path | None = None,
    subtitle_path: str | Path | None = None,
    source_reader: Any = None,
) -> dict[str, Any]:
    """Fetch live claim dossiers, build a fail-closed contract and persist one run.

    Missing dossiers are left without evidence and therefore block factual
    segments. Live freshness is appended separately from human review; it never
    changes a verdict and failures remain unknown/stale.
    """
    if not isinstance(narration_result, Mapping):
        raise ValueError("narration_result must be a mapping")
    topic, style = narration_result.get("topic"), narration_result.get("style")
    segments = narration_result.get("segments")
    if not isinstance(topic, str) or not topic.strip():
        raise ValueError("narration_result topic is required")
    if not isinstance(style, str) or not style.strip():
        raise ValueError("narration_result style is required")
    if not isinstance(segments, list) or not segments:
        raise ValueError("narration_result must contain non-empty segments")

    claim_ids: list[str] = []
    for segment in segments:
        if not isinstance(segment, Mapping):
            raise ValueError("narration segments must be mappings")
        values = segment.get("claim_ids", [])
        if not isinstance(values, list):
            raise ValueError("segment claim_ids must be a list")
        for claim_id in values:
            if not isinstance(claim_id, str) or not _CLAIM_ID.fullmatch(claim_id):
                raise ValueError("segment claim_ids must contain valid 32-character Claim IDs")
            if claim_id not in claim_ids:
                claim_ids.append(claim_id)

    evidence_by_claim: dict[str, list[dict[str, Any]]] = {}
    freshness_totals = {"checked": 0, "current": 0, "stale": 0, "unknown": 0, "skipped": 0}
    for claim_id in claim_ids:
        try:
            dossier = research_store.claim_dossier(claim_id, evidence_limit=50)
        except (KeyError, ValueError):
            continue
        remaining_checks = 24 - freshness_totals["checked"]
        freshness = (
            refresh_dossier_citations(
                research_store, dossier, reader=source_reader, max_checks=remaining_checks,
            )
            if remaining_checks > 0 else
            {"checked": 0, "current": 0, "stale": 0, "unknown": 0, "skipped": 0}
        )
        for key, value in freshness.items():
            freshness_totals[key] += value
        if freshness["checked"]:
            dossier = research_store.claim_dossier(claim_id, evidence_limit=50)
        adapted = build_evidence_by_claim_from_dossier(dossier)
        for adapted_claim_id, records in adapted.items():
            evidence_by_claim.setdefault(adapted_claim_id, [])
            seen = {item["evidence_id"] for item in evidence_by_claim[adapted_claim_id]}
            evidence_by_claim[adapted_claim_id].extend(
                item for item in records if item["evidence_id"] not in seen
            )

    contract = build_narration_evidence_contract(segments, evidence_by_claim)
    run = sync_store.save_run(
        topic=topic,
        style=style,
        segments=segments,
        timeline=timeline,
        audio_path=audio_path,
        subtitle_path=subtitle_path,
        evidence_contract=contract,
    )
    return {
        "run": run,
        "evidence_contract": contract,
        "claim_count": len(claim_ids),
        "evidence_count": sum(len(records) for records in evidence_by_claim.values()),
        "freshness_checks": freshness_totals,
    }
