"""Typed domain contracts for Muse Global Opportunity Radar.

Adapted concepts from ZiChuanLan/freebie-radar (MIT, copyright 2026 ZiChuanLan):
source taxonomy, acquisition-mode separation, expiry signals and explainable scoring.
This module contains no scheduler, network client, provider initialization or side effects.
"""
from __future__ import annotations

import json
import math
import os
import re

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping


class OpportunityLane(StrEnum):
    COST_SAVING = "COST_SAVING"
    TECH_FUSION = "TECH_FUSION"
    CREATIVE_ASSET = "CREATIVE_ASSET"
    MONETIZATION = "MONETIZATION"


class AccessTier(StrEnum):
    TRULY_FREE = "TRULY_FREE"
    CONDITIONAL_FREE = "CONDITIONAL_FREE"
    FREE_TRIAL = "FREE_TRIAL"
    PROMO_CREDIT = "PROMO_CREDIT"
    PAID_HIGH_ROI = "PAID_HIGH_ROI"
    UNKNOWN = "UNKNOWN"


class VerificationStatus(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    SUSPICIOUS = "SUSPICIOUS"
    EXPIRED = "EXPIRED"


class TrapFlag(StrEnum):
    REQUIRES_CREDIT_CARD = "requires_credit_card"
    GEO_RESTRICTED = "geo_restricted"
    WAITLIST_ONLY = "waitlist_only"
    REFERRAL_BAIT = "referral_bait"
    AUTO_RENEWAL = "auto_renewal"
    USAGE_CAP = "usage_cap"
    STUDENT_ONLY = "student_only"
    INVITE_ONLY = "invite_only"
    PAYMENT_METHOD_REQUIRED = "payment_method_required"


class LicenseRisk(StrEnum):
    PERMISSIVE = "PERMISSIVE"
    WEAK_COPYLEFT = "WEAK_COPYLEFT"
    STRONG_COPYLEFT = "STRONG_COPYLEFT"
    NETWORK_COPYLEFT = "NETWORK_COPYLEFT"
    PROPRIETARY = "PROPRIETARY"
    UNKNOWN = "UNKNOWN"


class IndependenceStatus(StrEnum):
    UNASSESSED = "unassessed"
    INDEPENDENT = "independent"
    DEPENDENT = "dependent"
    UNCLEAR = "unclear"


DEFAULT_RADAR_DB_PATH = Path(__file__).resolve().parents[3] / "data" / "opportunity_radar" / "radar.sqlite3"
_FORBIDDEN_DATABASE_NAMES = frozenset({
    "muse.db", "muse.sqlite", "muse.sqlite3", "knowledge.db", "knowledge.sqlite3",
    "research_knowledge.db", "research_knowledge.sqlite3", "task_queue.db", "task_queue.sqlite3",
    "tasks.db", "tasks.sqlite3", "research_evidence.db", "research_evidence.sqlite3",
})


def validate_radar_database_path(value: str | Path) -> Path:
    """Resolve a Radar-owned DB path and reject known Muse business databases."""
    path = Path(value).expanduser().resolve(strict=False)
    if path.name.casefold() in _FORBIDDEN_DATABASE_NAMES:
        raise ValueError("Radar database_path must not target an existing Muse business database")
    return path


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_datetime(value: str | datetime | None, *, field_name: str) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    else:
        raise TypeError(f"{field_name} must be an ISO-8601 string or datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _bounded_text(value: Any, name: str, maximum: int, *, optional: bool = False) -> str:
    if value is None and optional:
        return ""
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    text = value.strip()
    if (not text and not optional) or len(text) > maximum:
        raise ValueError(f"{name} must be non-empty text of at most {maximum} characters")
    return text


def _unit_interval(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number in [0, 1]")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError(f"{name} must be finite and in [0, 1]")
    return result


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    source_id: str
    source_type: str
    url: str
    observed_at: str
    title: str
    snippet: str = ""
    canonical_url: str = ""
    publisher: str = ""
    registrable_domain: str = ""
    content_sha256: str = ""
    independence: IndependenceStatus = IndependenceStatus.UNASSESSED
    source_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _bounded_text(self.source_id, "source_id", 160)
        _bounded_text(self.source_type, "source_type", 80)
        _bounded_text(self.url, "url", 4096)
        _bounded_text(self.title, "title", 500)
        _bounded_text(self.snippet, "snippet", 4000, optional=True)
        _bounded_text(self.canonical_url, "canonical_url", 4096, optional=True)
        _bounded_text(self.publisher, "publisher", 200, optional=True)
        _bounded_text(self.registrable_domain, "registrable_domain", 253, optional=True)
        parsed = parse_datetime(self.observed_at, field_name="observed_at")
        if parsed is None:
            raise ValueError("observed_at is required")
        object.__setattr__(self, "observed_at", parsed.isoformat())
        if self.content_sha256 and not re.fullmatch(r"[a-f0-9]{64}", self.content_sha256.casefold()):
            raise ValueError("content_sha256 must be a 64-character SHA-256 hex digest")
        # Evidence objects and serialized fields are observations, never review credentials.
        object.__setattr__(self, "independence", IndependenceStatus.UNASSESSED)
        if not isinstance(self.source_metadata, Mapping):
            raise TypeError("source_metadata must be a mapping")
        try:
            json.dumps(dict(self.source_metadata), ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("source_metadata must be JSON serializable") from exc

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "EvidenceRecord":
        if not isinstance(raw, Mapping):
            raise TypeError("evidence record must be a mapping")
        return cls(
            source_id=raw.get("source_id", ""),
            source_type=raw.get("source_type", "unknown"),
            url=raw.get("url", ""),
            observed_at=raw.get("observed_at") or utc_now().isoformat(),
            title=raw.get("title", ""),
            snippet=raw.get("snippet", ""),
            canonical_url=raw.get("canonical_url", ""),
            publisher=raw.get("publisher", ""),
            registrable_domain=raw.get("registrable_domain", ""),
            content_sha256=raw.get("content_sha256", ""),
            independence=IndependenceStatus.UNASSESSED,
            source_metadata=raw.get("source_metadata", {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "url": self.url,
            "observed_at": self.observed_at,
            "title": self.title,
            "snippet": self.snippet,
            "canonical_url": self.canonical_url,
            "publisher": self.publisher,
            "registrable_domain": self.registrable_domain,
            "content_sha256": self.content_sha256,
            "independence": self.independence.value,
            "source_metadata": dict(self.source_metadata),
        }


@dataclass(frozen=True, slots=True)
class OpportunityCandidate:
    candidate_id: str
    title: str
    summary: str
    url: str
    lane: OpportunityLane
    access_tier: AccessTier
    verification_status: VerificationStatus
    expires_at: str | None
    value: float
    credibility: float
    muse_fit: float
    friction_penalty: float
    evidence_chain: tuple[EvidenceRecord, ...] = ()
    traps: tuple[TrapFlag, ...] = ()
    license_spdx: str = ""
    license_risk: LicenseRisk = LicenseRisk.UNKNOWN
    distinct_source_count: int = 0
    independent_source_count: int = field(init=False, default=0)
    score_reasons: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = field(default_factory=lambda: utc_now().isoformat())

    def __post_init__(self) -> None:
        _bounded_text(self.candidate_id, "candidate_id", 160)
        _bounded_text(self.title, "title", 500)
        _bounded_text(self.summary, "summary", 4000, optional=True)
        _bounded_text(self.url, "url", 4096)
        for name in ("value", "credibility", "muse_fit", "friction_penalty"):
            object.__setattr__(self, name, _unit_interval(getattr(self, name), name))
        for name, enum_type in (
            ("lane", OpportunityLane),
            ("access_tier", AccessTier),
            ("verification_status", VerificationStatus),
            ("license_risk", LicenseRisk),
        ):
            value = getattr(self, name)
            if not isinstance(value, enum_type):
                object.__setattr__(self, name, enum_type(value))
        normalized_expiry = parse_datetime(self.expires_at, field_name="expires_at")
        object.__setattr__(self, "expires_at", normalized_expiry.isoformat() if normalized_expiry else None)
        for name in ("created_at", "updated_at"):
            parsed = parse_datetime(getattr(self, name), field_name=name)
            if parsed is None:
                raise ValueError(f"{name} is required")
            object.__setattr__(self, name, parsed.isoformat())
        if not isinstance(self.evidence_chain, tuple) or any(
            not isinstance(item, EvidenceRecord) for item in self.evidence_chain
        ):
            raise TypeError("evidence_chain must be a tuple of EvidenceRecord")
        if not isinstance(self.traps, tuple):
            raise TypeError("traps must be a tuple")
        object.__setattr__(self, "traps", tuple(
            item if isinstance(item, TrapFlag) else TrapFlag(item) for item in self.traps
        ))
        if len(set(self.traps)) != len(self.traps):
            raise ValueError("traps must not contain duplicates")
        if not isinstance(self.distinct_source_count, int) or isinstance(self.distinct_source_count, bool) or self.distinct_source_count < 0:
            raise ValueError("distinct_source_count must be a non-negative integer")
        # No Radar-side constructor or deserializer can assert source independence.
        # A future reviewed-store adapter may expose a separately audited aggregate.
        object.__setattr__(self, "independent_source_count", 0)
        if not isinstance(self.score_reasons, tuple) or any(not isinstance(x, str) for x in self.score_reasons):
            raise TypeError("score_reasons must be a tuple of strings")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        try:
            json.dumps(dict(self.metadata), ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata must be JSON serializable") from exc

    @property
    def composite_score(self) -> float:
        """Normalized 0..1 score: value × credibility × Muse fit − friction."""
        return round(max(0.0, self.value * self.credibility * self.muse_fit - self.friction_penalty), 6)

    @property
    def is_expired(self) -> bool:
        expiry = parse_datetime(self.expires_at, field_name="expires_at")
        return expiry is not None and expiry <= utc_now()

    def transition_expired(self, *, now: datetime | None = None) -> "OpportunityCandidate":
        """Return an EXPIRED copy when due; expiry never upgrades verification."""
        moment = now or utc_now()
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("now must include an explicit timezone")
        expiry = parse_datetime(self.expires_at, field_name="expires_at")
        if expiry is None or expiry > moment.astimezone(timezone.utc):
            return self
        if self.verification_status is VerificationStatus.EXPIRED:
            return self
        return replace(
            self,
            verification_status=VerificationStatus.EXPIRED,
            updated_at=moment.astimezone(timezone.utc).isoformat(),
        )


    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "title": self.title,
            "summary": self.summary,
            "url": self.url,
            "lane": self.lane.value,
            "access_tier": self.access_tier.value,
            "verification_status": self.verification_status.value,
            "expires_at": self.expires_at,
            "value": self.value,
            "credibility": self.credibility,
            "muse_fit": self.muse_fit,
            "friction_penalty": self.friction_penalty,
            "composite_score": self.composite_score,
            "evidence_chain": [item.to_dict() for item in self.evidence_chain],
            "traps": [item.value for item in self.traps],
            "license_spdx": self.license_spdx,
            "license_risk": self.license_risk.value,
            "distinct_source_count": self.distinct_source_count,
            "independent_source_count": self.independent_source_count,
            "score_reasons": list(self.score_reasons),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "OpportunityCandidate":
        if not isinstance(raw, Mapping):
            raise TypeError("candidate must be a mapping")
        evidence = raw.get("evidence_chain", ())
        if not isinstance(evidence, (list, tuple)):
            raise TypeError("evidence_chain must be a list or tuple")
        traps = raw.get("traps", ())
        reasons = raw.get("score_reasons", ())
        if not isinstance(traps, (list, tuple)) or not isinstance(reasons, (list, tuple)):
            raise TypeError("traps and score_reasons must be sequences")
        return cls(
            candidate_id=raw.get("candidate_id", ""),
            title=raw.get("title", ""),
            summary=raw.get("summary", ""),
            url=raw.get("url", ""),
            lane=OpportunityLane(raw.get("lane", OpportunityLane.COST_SAVING.value)),
            access_tier=AccessTier(raw.get("access_tier", AccessTier.UNKNOWN.value)),
            verification_status=VerificationStatus(raw.get("verification_status", VerificationStatus.UNVERIFIED.value)),
            expires_at=raw.get("expires_at"),
            value=raw.get("value", 0.0),
            credibility=raw.get("credibility", 0.0),
            muse_fit=raw.get("muse_fit", 0.0),
            friction_penalty=raw.get("friction_penalty", 0.0),
            evidence_chain=tuple(
                item if isinstance(item, EvidenceRecord) else EvidenceRecord.from_mapping(item)
                for item in evidence
            ),
            traps=tuple(TrapFlag(item) for item in traps),
            license_spdx=raw.get("license_spdx", ""),
            license_risk=LicenseRisk(raw.get("license_risk", LicenseRisk.UNKNOWN.value)),
            distinct_source_count=raw.get("distinct_source_count", 0),
            score_reasons=tuple(reasons),
            metadata=raw.get("metadata", {}),
            created_at=raw.get("created_at") or utc_now().isoformat(),
            updated_at=raw.get("updated_at") or utc_now().isoformat(),
        )


@dataclass(frozen=True, slots=True)
class RadarConfig:
    enabled: bool = False
    semantic_enabled: bool = False
    database_path: Path = DEFAULT_RADAR_DB_PATH
    request_timeout_seconds: float = 12.0
    max_response_bytes: int = 1_000_000
    max_text_chars: int = 20_000
    max_retries: int = 3
    retry_base_seconds: float = 0.75
    min_host_gap_seconds: float = 1.0
    host_concurrency: int = 2
    cache_ttl_seconds: int = 900
    semantic_timeout_seconds: float = 8.0
    max_candidates_per_source: int = 100

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool) or not isinstance(self.semantic_enabled, bool):
            raise TypeError("enabled flags must be booleans")
        if not isinstance(self.database_path, (str, Path)):
            raise TypeError("database_path must be a path")
        object.__setattr__(self, "database_path", validate_radar_database_path(self.database_path))
        for name in ("request_timeout_seconds", "retry_base_seconds", "min_host_gap_seconds", "semantic_timeout_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")
        for name, minimum, maximum in (
            ("max_response_bytes", 1024, 2_000_000),
            ("max_text_chars", 100, 100_000),
            ("max_retries", 0, 8),
            ("host_concurrency", 1, 8),
            ("cache_ttl_seconds", 0, 604_800),
            ("max_candidates_per_source", 1, 500),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "RadarConfig":
        env = os.environ if environ is None else environ

        def flag(name: str, default: bool) -> bool:
            raw = env.get(name)
            if raw is None:
                return default
            normalized = raw.strip().casefold()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
            raise ValueError(f"{name} must be an explicit boolean value")

        def number(name: str, default: int | float, cast: type[int] | type[float]) -> int | float:
            raw = env.get(name)
            if raw is None or not raw.strip():
                return default
            try:
                return cast(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} has an invalid numeric value") from exc

        return cls(
            enabled=flag("MUSE_RADAR_ENABLED", False),
            semantic_enabled=flag("MUSE_RADAR_SEMANTIC_ENABLED", False),
            database_path=Path(env.get("MUSE_RADAR_DB", str(DEFAULT_RADAR_DB_PATH))),
            request_timeout_seconds=float(number("MUSE_RADAR_TIMEOUT", 12.0, float)),
            max_response_bytes=int(number("MUSE_RADAR_MAX_BYTES", 1_000_000, int)),
            max_text_chars=int(number("MUSE_RADAR_MAX_CHARS", 20_000, int)),
            max_retries=int(number("MUSE_RADAR_RETRIES", 3, int)),
            retry_base_seconds=float(number("MUSE_RADAR_RETRY_BASE", 0.75, float)),
            min_host_gap_seconds=float(number("MUSE_RADAR_HOST_GAP", 1.0, float)),
            host_concurrency=int(number("MUSE_RADAR_HOST_CONCURRENCY", 2, int)),
            cache_ttl_seconds=int(number("MUSE_RADAR_CACHE_TTL", 900, int)),
            semantic_timeout_seconds=float(number("MUSE_RADAR_SEMANTIC_TIMEOUT", 8.0, float)),
            max_candidates_per_source=int(number("MUSE_RADAR_SOURCE_LIMIT", 100, int)),
        )
