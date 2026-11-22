"""Bridge research reports into evidence-aware business pilot candidates.

This adapter is deliberately explicit and side-effect bounded:
- research sources remain traceability references, not proof of customer demand;
- commercial facts and ratings must be supplied by the caller;
- a research-derived note is recorded only as estimate/assumption;
- no Initiative promotion, Commander ticket, worker dispatch, publishing or GPU work.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from muse.business_opportunities import (
    DIMENSIONS as BUSINESS_RATING_DIMENSIONS,
    BusinessOpportunityStore,
    score_opportunity,
)
from muse.value_forge import ValueForge

_BRIEF_LIMITS = {
    "title": 200,
    "customer_segment": 500,
    "customer_problem": 2000,
    "proposed_offer": 2000,
    "validation_test": 2000,
    "success_signal": 1000,
}
_RESEARCH_NOTE_TYPE = "estimate"
_RESEARCH_NOTE_STATUS = "assumption"


def _clean_text(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be non-empty text of at most {limit} characters")
    cleaned = value.strip()
    if any(ord(char) < 32 and char not in (chr(13), chr(10), chr(9)) for char in cleaned):
        raise ValueError(f"{name} contains an unsupported control character")
    return cleaned


def _safe_source_records(report: Mapping[str, Any]) -> list[dict[str, str]]:
    raw_sources = report.get("sources")
    if not isinstance(raw_sources, list):
        return []
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_sources[:50]:
        if not isinstance(item, Mapping):
            continue
        url = item.get("url")
        if not isinstance(url, str) or len(url) > 2048:
            continue
        try:
            parsed = urlsplit(url)
            valid = (
                parsed.scheme in {"http", "https"}
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
            )
        except ValueError:
            valid = False
        if not valid or url in seen:
            continue
        seen.add(url)
        records.append({
            "url": url,
            "title": str(item.get("title") or "")[:200],
            "source_kind": str(item.get("source_kind") or "unclassified")[:80],
            "sha256": str(item.get("sha256") or "")[:64],
        })
        if len(records) >= 12:
            break
    return records


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def create_candidate_from_research_report(
    report: Mapping[str, Any],
    business_brief: Mapping[str, Any],
    ratings: Mapping[str, int],
    *,
    business_database: str | Path,
    report_path: str = "",
    currency: str = "USD",
    estimated_price_minor: int | None = None,
    estimated_cost_minor: int | None = None,
) -> dict[str, Any]:
    """Persist one human-framed business hypothesis using Muse's existing ledgers.

    business_brief must explicitly describe the customer segment, problem,
    offer and validation experiment. The research report cannot supply or infer
    willingness-to-pay, demand, pricing or customer testimony.

    An estimate/assumption evidence row preserves report provenance without
    increasing the business ledger's validated-evidence count. Replays are
    deterministic and idempotent.
    """
    if not isinstance(report, Mapping):
        raise TypeError("report must be a mapping")
    if not isinstance(business_brief, Mapping):
        raise TypeError("business_brief must be a mapping")
    if set(business_brief) != set(_BRIEF_LIMITS):
        raise ValueError(
            "business_brief must contain exactly: " + ", ".join(_BRIEF_LIMITS)
        )
    brief = {
        name: _clean_text(business_brief[name], name, limit)
        for name, limit in _BRIEF_LIMITS.items()
    }

    if not isinstance(ratings, Mapping):
        raise TypeError("ratings must be a mapping")
    normalized_ratings = dict(ratings)
    if set(normalized_ratings) != set(BUSINESS_RATING_DIMENSIONS):
        raise ValueError(
            "ratings must contain exactly: " + ", ".join(BUSINESS_RATING_DIMENSIONS)
        )
    # Run the existing scorer before opening the database, so invalid input
    # cannot leave a partially created candidate.
    score_opportunity(normalized_ratings)

    if not isinstance(report_path, str) or len(report_path) > 2048:
        raise ValueError("report_path must be text of at most 2048 characters")
    report_path = report_path.strip()
    if any(ord(char) < 32 for char in report_path):
        raise ValueError("report_path contains an unsupported control character")

    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency must be a three-letter uppercase code")
    for name, value in (
        ("estimated_price_minor", estimated_price_minor),
        ("estimated_cost_minor", estimated_cost_minor),
    ):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise ValueError(f"{name} must be a non-negative integer or None")

    source_records = _safe_source_records(report)
    synthesis = report.get("synthesis")
    synthesis = synthesis if isinstance(synthesis, Mapping) else {}
    report_material = {
        "report_path": report_path,
        "topic_key": str(report.get("topic_key") or "")[:256],
        "evolution_cycle_id": str(report.get("evolution_cycle_id") or "")[:128],
        "evidence_coverage_score": report.get("evidence_coverage_score"),
        "sources": source_records,
        "executive_summary": str(synthesis.get("executive_summary") or "")[:4000],
        "counter_evidence": synthesis.get("counter_evidence")
        if isinstance(synthesis.get("counter_evidence"), list) else [],
        "testable_experiments": synthesis.get("testable_experiments")
        if isinstance(synthesis.get("testable_experiments"), list) else [],
    }
    report_digest = _canonical_digest(report_material)

    price_value = 0 if estimated_price_minor is None else estimated_price_minor
    cost_value = 0 if estimated_cost_minor is None else estimated_cost_minor
    opportunity_material = {
        "business_brief": brief,
        "ratings": normalized_ratings,
        "currency": currency,
        "estimated_price_minor": estimated_price_minor,
        "estimated_cost_minor": estimated_cost_minor,
    }
    opportunity_digest = _canonical_digest(opportunity_material)
    opportunity_key = f"research-pilot:{opportunity_digest}"
    evidence_digest = _canonical_digest({
        "opportunity_key": opportunity_key,
        "report_digest": report_digest,
    })
    evidence_key = f"research-assumption:{evidence_digest}"

    forge = ValueForge()
    safe_report = dict(report)
    safe_report["sources"] = source_records
    research_evaluation = forge.evaluate_research_report(
        safe_report, report_path=report_path
    )
    pattern = forge.get_pattern("demand_to_business_pilot")
    refs = [report_path] if report_path else []
    refs.extend(record["url"] for record in source_records)
    refs = list(dict.fromkeys(refs))[:12]

    trace_refs = ", ".join(ref[:180] for ref in refs[:6]) or "no external source URL"
    research_note = (
        "Research-derived hypothesis only. "
        f"report_sha256={report_digest}; report_ref={report_path or 'not supplied'}; "
        f"source_refs={trace_refs}. "
        "These references do not establish customer demand, customer interviews, "
        "willingness to pay, market size or validated pricing. Obtain independent "
        "customer and market evidence before treating this candidate as validated."
    )[:2000]

    with BusinessOpportunityStore(business_database) as store:
        opportunity = store.create(
            idempotency_key=opportunity_key,
            title=brief["title"],
            customer_segment=brief["customer_segment"],
            customer_problem=brief["customer_problem"],
            proposed_offer=brief["proposed_offer"],
            validation_test=brief["validation_test"],
            success_signal=brief["success_signal"],
            ratings=normalized_ratings,
            currency=currency,
            estimated_price_minor=price_value,
            estimated_cost_minor=cost_value,
        )
        if opportunity["status"] in {"rejected", "parked"}:
            raise ValueError("rejected or parked business opportunities cannot accept new research")
        assumption = store.add_evidence(
            # This is an explicitly labelled assumption/provenance note,
            # not an imported market or customer evidence record.
            opportunity_id=opportunity["id"],
            idempotency_key=evidence_key,
            evidence_type=_RESEARCH_NOTE_TYPE,
            epistemic_status=_RESEARCH_NOTE_STATUS,
            summary=research_note,
        )
        opportunity = store.get(opportunity["id"])

    pricing_state = (
        "not_provided"
        if estimated_price_minor is None and estimated_cost_minor is None
        else "caller_supplied_estimates_unverified"
    )
    return {
        "schema_version": 1,
        "status": "opportunity_resolved",
        "opportunity_status": opportunity["status"],
        "opportunity": opportunity,
        "research_evaluation": research_evaluation,
        "research_refs": refs,
        "research_report_sha256": report_digest,
        "research_assumption_evidence_id": assumption["id"],
        "commercial_pattern": {
            "id": "demand_to_business_pilot",
            "hypothesis": pattern["hypothesis"],
            "validation_first_step": pattern["first_step"],
            "guardrails": pattern["guardrails"],
        },
        "pricing_state": pricing_state,
        "market_demand_inferred": False,
        "willingness_to_pay_inferred": False,
        "customer_validation_required": True,
        "requires_human_review": True,
        "initiative_created": False,
        "dispatch_started": False,
        "publishing_started": False,
        "gpu_work_started": False,
    }
