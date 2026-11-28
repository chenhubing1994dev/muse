"""Muse global epistemic taxonomy (认知性质标签) — the single source of truth.

Every inspiration, memory, research object and creative brief carries exactly one of the
eight tags below. Only ``fact`` and ``observation`` may ever count as evidence-grade; the
symbolic / rumor / fiction tags are hard-locked away from evidence and from the User Track.

This module is dependency-free so stores, APIs and the diagnostic engine can import it
without side effects.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

EPISTEMIC_TAGS: tuple[str, ...] = (
    "fact", "observation", "interpretation", "experience",
    "hypothesis", "symbolic_inspiration", "rumor", "fiction",
)
EPISTEMIC_TAG_LABELS_ZH: dict[str, str] = {
    "fact": "事实", "observation": "观察", "interpretation": "解释", "experience": "经验",
    "hypothesis": "假设", "symbolic_inspiration": "象征灵感", "rumor": "传闻", "fiction": "虚构设定",
}
_ALIASES: dict[str, str] = {
    **{zh: tag for tag, zh in EPISTEMIC_TAG_LABELS_ZH.items()},
    "symbolic": "symbolic_inspiration", "metaphysics": "symbolic_inspiration", "玄学": "symbolic_inspiration",
    "玄学推演": "symbolic_inspiration", "象征": "symbolic_inspiration", "unverified_hypothesis": "hypothesis",
    "待验证假设": "hypothesis", "fictional": "fiction", "gossip": "rumor",
}
EVIDENCE_GRADE_TAGS = frozenset({"fact", "observation"})
NEVER_EVIDENCE_TAGS = frozenset({"symbolic_inspiration", "rumor", "fiction", "hypothesis"})
# Tags a *user-stated or user-confirmed* memory may carry on the User Track.
USER_TRACK_TAGS = frozenset({"fact", "observation", "experience", "interpretation", "hypothesis"})
SYMBOLIC_LOCK_LABEL = "玄学推演／象征灵感／待验证假设"
INSUFFICIENT_EVIDENCE = "insufficient_evidence"
UNVERIFIED = "unverified"

# Origins: who produced the content. Agent origins can never self-promote to User Track.
USER_ORIGINS = frozenset({"user_stated", "user_confirmed"})
AGENT_ORIGINS = frozenset({"agent_inference", "agent_experience", "model_output", "web_research", "symbolic_lab"})


class EpistemicTagError(ValueError):
    """Content is missing a valid tag or attempts an illegal epistemic upgrade."""


class UserTrackContamination(PermissionError):
    """An unconfirmed agent inference attempted to enter the User Track."""


def normalize_tag(tag: Any) -> str:
    if not isinstance(tag, str) or not tag.strip():
        raise EpistemicTagError("epistemic tag must be non-empty text")
    key = tag.strip()
    resolved = key if key in EPISTEMIC_TAGS else _ALIASES.get(key) or _ALIASES.get(key.casefold())
    if resolved not in EPISTEMIC_TAGS:
        raise EpistemicTagError(f"unknown epistemic tag {tag!r}; expected one of {', '.join(EPISTEMIC_TAGS)}")
    return resolved


def is_evidence_grade(tag: str) -> bool:
    return normalize_tag(tag) in EVIDENCE_GRADE_TAGS


@dataclass(frozen=True)
class EpistemicStamp:
    tag: str
    source: str
    confidence: float
    origin: str

    def as_dict(self) -> dict[str, Any]:
        return {"epistemic_tag": self.tag, "epistemic_label_zh": EPISTEMIC_TAG_LABELS_ZH[self.tag],
                "source": self.source, "confidence": self.confidence, "origin": self.origin,
                "is_evidence": self.tag in EVIDENCE_GRADE_TAGS,
                **({"lock_label": SYMBOLIC_LOCK_LABEL} if self.tag == "symbolic_inspiration" else {})}


def stamp(tag: Any, *, source: str, confidence: float = 0.5, origin: str = "agent_inference") -> EpistemicStamp:
    resolved = normalize_tag(tag)
    if not isinstance(source, str) or not source.strip():
        raise EpistemicTagError("every tagged item needs a source")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
        raise EpistemicTagError("confidence must be between 0 and 1")
    if origin not in USER_ORIGINS | AGENT_ORIGINS:
        raise EpistemicTagError(f"unknown origin {origin!r}")
    if resolved in NEVER_EVIDENCE_TAGS and float(confidence) > 0.6 and resolved != "hypothesis":
        # Symbolic, rumor and fiction content may never present itself as highly certain.
        confidence = 0.6
    return EpistemicStamp(resolved, source.strip()[:500], float(confidence), origin)


def tag_payload(payload: Mapping[str, Any], tag: Any, *, source: str, confidence: float = 0.5,
                origin: str = "agent_inference") -> dict[str, Any]:
    stamped = dict(payload)
    stamped.update(stamp(tag, source=source, confidence=confidence, origin=origin).as_dict())
    return stamped


def require_tag(payload: Any, *, path: str = "payload") -> str:
    """Fail closed when a payload lacks a tag or claims evidence status it cannot hold."""
    if not isinstance(payload, Mapping):
        raise EpistemicTagError(f"{path} must be an object")
    tag = normalize_tag(payload.get("epistemic_tag"))
    if not str(payload.get("source") or "").strip():
        raise EpistemicTagError(f"{path} has no source")
    if tag not in EVIDENCE_GRADE_TAGS and payload.get("is_evidence") is True:
        raise EpistemicTagError(f"{path} tagged {tag} may never be marked as evidence")
    if tag == "symbolic_inspiration" and payload.get("lock_label") != SYMBOLIC_LOCK_LABEL:
        raise EpistemicTagError(f"{path} lost the symbolic isolation label")
    return tag


def assert_user_track_admissible(*, tag: Any, origin: str, user_confirmed: bool = False) -> str:
    """User Track only stores what the user said or explicitly confirmed."""
    resolved = normalize_tag(tag)
    if resolved not in USER_TRACK_TAGS:
        raise UserTrackContamination(f"tag {resolved} can never live on the User Track")
    if origin in USER_ORIGINS:
        return resolved
    if origin in AGENT_ORIGINS and user_confirmed is True:
        return resolved
    raise UserTrackContamination(
        f"origin {origin!r} requires explicit user confirmation before entering the User Track")


def research_verdict(supporting_independent: int, refuting_independent: int) -> str:
    """Honest verdict from *independent* source counts (syndicated copies must be pre-collapsed)."""
    for value in (supporting_independent, refuting_independent):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("counts must be non-negative integers")
    if supporting_independent == 0 and refuting_independent == 0:
        return INSUFFICIENT_EVIDENCE
    if supporting_independent >= 2 and refuting_independent == 0:
        return "supported"
    if refuting_independent >= 2 and supporting_independent == 0:
        return "refuted"
    if supporting_independent and refuting_independent:
        return "contested"
    return UNVERIFIED


# ---- legacy private labels -> canonical tags -------------------------------------------------
# Every module-private ``epistemic_status``-style literal found in src/muse must appear here
# (scripts/muse_diag.py L1.5 fails on any unmapped literal). Mapping may never upgrade: a legacy
# label that says "not_", "unverified" or "only" can never become an evidence-grade tag.
LEGACY_TAG_MAP: dict[str, str] = {
    "observed": "observation",
    "legacy_unverified": "hypothesis",  # T7: pre-taxonomy User Track memory awaiting human review
    "evidence": "interpretation",  # philosophy.py: a self-declared "evidence" label is not verified evidence
    "opinion": "interpretation",
    "discovery_only": "hypothesis",  # website_knowledge.py roles: unread candidates are never evidence
    "support_candidate": "hypothesis",
    "counter_candidate": "hypothesis",
    "context_candidate": "hypothesis",
    "reported": "rumor",
    "assumption": "hypothesis",
    "research_observation_not_claim_verdict": "interpretation",
    "creator_recorded_observation_unverified": "experience",
    "creator_feedback_observation_not_independent_evidence": "experience",
    "observed_execution_failure_not_claim_evidence": "experience",
    "unverified_user_feedback_signal": "experience",
    "active_principle_experience_grounded": "experience",
    "engineering_failure_not_claim_refutation": "experience",
    "engineering_review_rejected_not_external_evidence": "experience",
    "engineering_validation_not_external_evidence": "experience",
    "review_pipeline_failure_not_claim_refutation": "experience",
    "user_memory_context_not_external_proof": "interpretation",
    "model_synthesized_factual_statement_needs_human_verification": "hypothesis",
    "experience_and_hypothesis_only": "hypothesis",
    "hypothesis_and_experience": "hypothesis",
    "alternative_explanation_only": "hypothesis",
    "creative_hypothesis_only": "hypothesis",
    "creator_dialogue_inspiration_only": "hypothesis",
    "creator_inspired_hypothesis_only": "hypothesis",
    "cross_domain_hypothesis_not_tradition_consensus": "hypothesis",
    "frontier_science_research": "hypothesis",
    "hypothesis": "hypothesis",
    "hypothesis_only": "hypothesis",
    "hypothesis_only_not_fact": "hypothesis",
    "local_inspiration_hypothesis_only": "hypothesis",
    "research_plan_not_result": "hypothesis",
    "third_party_research_unverified_by_muse": "hypothesis",
    "unreviewed_counter_evidence_candidate": "hypothesis",
    "unverified_candidate_not_a_finding": "hypothesis",
    "unverified_claim_investigation": "hypothesis",
    "symbolic_hypothesis_and_experience_only": "symbolic_inspiration",
    "symbolic_hypothesis_only": "symbolic_inspiration",
    "symbolic_inspiration_only": "symbolic_inspiration",
    "symbolic_only": "symbolic_inspiration",
    "symbolic_structure_only": "symbolic_inspiration",
    "symbolic_structure_only_not_evidence": "symbolic_inspiration",
    "traditional_calculation": "symbolic_inspiration",
    "traditional_calculation_not_evidence": "symbolic_inspiration",
    "traditional_concept_record": "symbolic_inspiration",
    "traditional_framework_interpretation_unverified": "symbolic_inspiration",
    "traditional_framework_statement_not_empirical": "symbolic_inspiration",
    "traditional_rule_variant_not_consensus": "symbolic_inspiration",
}
NON_LABEL_LITERALS = frozenset({"", "id", "epistemic_status", "not_recorded", "unspecified"})
_NON_UPGRADE_MARKERS = ("not_", "unverified", "only", "needs_")


def canonical_tag(value: Any) -> str:
    """Map a canonical tag, a Chinese/alias label or a legacy private label to one of the 8 tags."""
    if isinstance(value, str) and value in LEGACY_TAG_MAP:
        return LEGACY_TAG_MAP[value]
    return normalize_tag(value)


def legacy_map_violations() -> list[str]:
    bad = []
    for legacy, tag in LEGACY_TAG_MAP.items():
        if tag not in EPISTEMIC_TAGS:
            bad.append(f"{legacy}->{tag}: not a canonical tag")
        elif tag in EVIDENCE_GRADE_TAGS and any(m in legacy for m in _NON_UPGRADE_MARKERS):
            bad.append(f"{legacy}->{tag}: illegal upgrade to evidence grade")
    return bad
