"""URL/content fingerprinting and cross-source corroboration for Muse Radar.

The merge-key, title-clustering and normalization concepts are independently reimplemented
from ZiChuanLan/freebie-radar (MIT, copyright 2026 ZiChuanLan); no TypeScript runtime,
third-party package or external script is imported.

Muse source-identity contract (verified): normalize_source_url(url, *, base_url=None),
build_source_identity_candidates(sources, *, existing_sources=None), and
find_exact_content_matches(sources, *, existing_sources=None). Identity candidates are
review hints only; host diversity is never treated as proof of independent ownership.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from muse.source_identity import (
    build_source_identity_candidates,
    find_exact_content_matches,
    normalize_source_url,
)

from .source_hints import source_group_diversity_hint
from .models import EvidenceRecord, IndependenceStatus, OpportunityCandidate, utc_now


_TRACKING_EXACT = frozenset({
    "ref", "referrer", "source", "sourceid", "campaign", "campaignid",
    "fbclid", "gclid", "dclid", "msclkid", "igshid", "mc_cid", "mc_eid",
    "vero_id", "oly_anon_id", "oly_enc_id", "mkt_tok", "spm", "scm",
    "share_source", "share_medium", "share_session_id", "share_tag",
})
_TRACKING_PREFIXES = ("utm_", "pk_", "ga_", "vero_", "oly_", "share_")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_LATIN_RE = re.compile(r"[a-z0-9]+(?:[._+-][a-z0-9]+)*", re.IGNORECASE)
_STOP_WORDS = frozenset({
    "free", "freebie", "giveaway", "offer", "offers", "deal", "deals", "promo",
    "promotion", "limited", "today", "new", "latest", "official", "project",
    "github", "reddit", "rss", "release", "update", "get", "claim", "now",
    "免费", "福利", "领取", "限时", "开源", "项目", "发布", "更新", "官方",
})


def normalize_url_identity(url: str) -> str:
    """Normalize protocol, tracking parameters, default ports and host casing."""
    normalized = normalize_source_url(url)
    parsed = urlsplit(normalized)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("source URL contains an invalid port") from exc
    if port in {80, 443}:
        port = None
    netloc = f"{host}:{port}" if port else host
    query = [
        (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in _TRACKING_EXACT
        and not any(key.casefold().startswith(prefix) for prefix in _TRACKING_PREFIXES)
    ]
    query.sort(key=lambda item: (item[0].casefold(), item[1]))
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit(("", netloc, path, urlencode(query, doseq=True), ""))


def _tokens(text: str) -> tuple[str, ...]:
    normalized = text.casefold()
    words: list[str] = []
    for match in _LATIN_RE.finditer(normalized):
        token = match.group(0).strip("._+-")
        if len(token) >= 2 and token not in _STOP_WORDS:
            words.append(token)
    for run in _CJK_RE.findall(normalized):
        if len(run) == 1:
            words.append(run)
        else:
            words.extend(run[index:index + 2] for index in range(len(run) - 1))
    return tuple(words)


def _shingles(text: str, width: int = 3) -> frozenset[str]:
    tokens = _tokens(text)
    if not tokens:
        return frozenset()
    if len(tokens) <= width:
        return frozenset({" ".join(tokens)})
    return frozenset(" ".join(tokens[index:index + width]) for index in range(len(tokens) - width + 1))


def _simhash(text: str, bits: int = 64) -> int:
    tokens = _tokens(text)
    if not tokens:
        return 0
    weights = [0] * bits
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        for bit in range(bits):
            weights[bit] += 1 if (value >> bit) & 1 else -1
    result = 0
    for bit, weight in enumerate(weights):
        if weight >= 0:
            result |= 1 << bit
    return result


def _hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)



@dataclass(frozen=True, slots=True)
class ContentFingerprint:
    url_key: str
    title_simhash: str
    title_shingles: tuple[str, ...]
    body_simhash: str
    body_shingles: tuple[str, ...]

    @classmethod
    def build(cls, candidate: OpportunityCandidate) -> "ContentFingerprint":
        title = candidate.title
        body = f"{candidate.title}\n{candidate.summary}"
        return cls(
            url_key=normalize_url_identity(candidate.url),
            title_simhash=f"{_simhash(title):016x}",
            title_shingles=tuple(sorted(_shingles(title))),
            body_simhash=f"{_simhash(body):016x}",
            body_shingles=tuple(sorted(_shingles(body))),
        )


@dataclass(frozen=True, slots=True)
class MergeResult:
    candidate: OpportunityCandidate
    created: bool
    evidence_added: int
    distinct_source_count: int
    independent_source_count: int
    identity_candidates: tuple[Mapping[str, Any], ...]
    exact_content_matches: tuple[Mapping[str, Any], ...]


class UnsupportedRadarSchemaVersion(RuntimeError):
    """The Radar database was created by a newer application version."""


class RadarDatabaseCorruptionError(RuntimeError):
    """The Radar database cannot be opened or migrated safely."""


class CorroborationStore:
    """SQLite-backed atomic opportunity/evidence store; no Commander or TaskQueue access."""

    SCHEMA_VERSION = 2

    def __init__(self, database: str | Path, *, busy_timeout_ms: int = 10_000) -> None:
        if isinstance(busy_timeout_ms, bool) or not isinstance(busy_timeout_ms, int) or not 100 <= busy_timeout_ms <= 60_000:
            raise ValueError("busy_timeout_ms must be an integer from 100 to 60000")
        from .models import validate_radar_database_path
        self.path = validate_radar_database_path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(self.path, timeout=busy_timeout_ms / 1000, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute("CREATE TABLE IF NOT EXISTS radar_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            current = self.connection.execute(
                "SELECT value FROM radar_meta WHERE key='schema_version'"
            ).fetchone()
            try:
                version = int(current["value"]) if current is not None else 0
            except (TypeError, ValueError) as exc:
                raise RadarDatabaseCorruptionError("Radar schema version metadata is invalid") from exc
            if version > self.SCHEMA_VERSION:
                raise UnsupportedRadarSchemaVersion(
                    f"Radar schema {version} is newer than supported schema {self.SCHEMA_VERSION}"
                )
            migrations = {0: self._migrate_0_to_1, 1: self._migrate_1_to_2}
            while version < self.SCHEMA_VERSION:
                migration = migrations.get(version)
                if migration is None:
                    raise RadarDatabaseCorruptionError(f"missing Radar migration from schema {version}")
                migration(self.connection)
                version += 1
                self.connection.execute(
                    "INSERT INTO radar_meta(key,value) VALUES('schema_version',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(version),)
                )
            self.connection.commit()
        except UnsupportedRadarSchemaVersion:
            self.connection.rollback()
            self.connection.close()
            raise
        except RadarDatabaseCorruptionError:
            self.connection.rollback()
            self.connection.close()
            raise
        except (sqlite3.DatabaseError, OSError) as exc:
            self.connection.rollback()
            self.connection.close()
            raise RadarDatabaseCorruptionError("Radar database could not be opened or migrated") from exc

    @staticmethod
    def _migrate_0_to_1(connection: sqlite3.Connection) -> None:
        connection.execute("""CREATE TABLE IF NOT EXISTS radar_opportunities (
            candidate_id TEXT PRIMARY KEY, url_key TEXT NOT NULL, title_simhash TEXT NOT NULL,
            title_shingles_json TEXT NOT NULL, body_simhash TEXT NOT NULL,
            body_shingles_json TEXT NOT NULL, candidate_json TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_radar_opportunities_url ON radar_opportunities(url_key)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_radar_opportunities_updated ON radar_opportunities(updated_at)")

    @staticmethod
    def _migrate_1_to_2(connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_radar_opportunities_created ON radar_opportunities(created_at)"
        )

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def __enter__(self) -> "CorroborationStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> tuple[OpportunityCandidate, ContentFingerprint]:
        """Rebuild a stored candidate and its persisted fingerprint (fingerprints are not recomputed)."""
        candidate = OpportunityCandidate.from_mapping(json.loads(row["candidate_json"]))
        fingerprint = ContentFingerprint(
            url_key=row["url_key"],
            title_simhash=row["title_simhash"],
            title_shingles=tuple(json.loads(row["title_shingles_json"])),
            body_simhash=row["body_simhash"],
            body_shingles=tuple(json.loads(row["body_shingles_json"])),
        )
        return candidate, fingerprint

    @staticmethod
    def _matches(left: ContentFingerprint, right: ContentFingerprint) -> bool:
        if left.url_key and left.url_key == right.url_key:
            return True
        left_title_tokens = _tokens(" ".join(left.title_shingles))
        right_title_tokens = _tokens(" ".join(right.title_shingles))
        if min(len(left_title_tokens), len(right_title_tokens)) < 3:
            return False
        title_overlap = _jaccard(frozenset(left.title_shingles), frozenset(right.title_shingles))
        title_distance = _hamming(int(left.title_simhash, 16), int(right.title_simhash, 16))
        if title_overlap >= 0.82 and title_distance <= 9:
            return True
        body_overlap = _jaccard(frozenset(left.body_shingles), frozenset(right.body_shingles))
        body_distance = _hamming(int(left.body_simhash, 16), int(right.body_simhash, 16))
        return title_overlap >= 0.48 and body_overlap >= 0.88 and body_distance <= 8
    @staticmethod
    def _merge(existing: OpportunityCandidate, incoming: OpportunityCandidate) -> tuple[OpportunityCandidate, int]:
        evidence: dict[tuple[str, str], EvidenceRecord] = {}
        for item in (*existing.evidence_chain, *incoming.evidence_chain):
            key = (item.source_id, normalize_url_identity(item.url))
            prior = evidence.get(key)
            if prior is None or item.observed_at > prior.observed_at:
                evidence[key] = item
        chain = tuple(sorted(evidence.values(), key=lambda item: (item.observed_at, item.source_id)))
        domains = {source_group_diversity_hint(item.url) for item in chain}
        domains.discard("")
        # Cross-source discovery earns only a small provisional uplift. It never changes
        # independence labels or verification status; Muse's review process owns those.
        corroboration_uplift = min(0.12, max(0, len(domains) - 1) * 0.04)
        credibility = min(1.0, max(existing.credibility, incoming.credibility) + corroboration_uplift)
        metadata = {
            **dict(existing.metadata),
            **dict(incoming.metadata),
            "corroboration_assessment": "distinct_origin_hints_not_proof_of_independence",
            "corroboration_domains": sorted(domains),
            "corroboration_uplift": round(corroboration_uplift, 4),
        }
        merged = replace(
            existing,
            title=incoming.title if len(incoming.title) > len(existing.title) else existing.title,
            summary=incoming.summary if len(incoming.summary) > len(existing.summary) else existing.summary,
            lane=incoming.lane if incoming.muse_fit > existing.muse_fit else existing.lane,
            access_tier=incoming.access_tier if incoming.value > existing.value else existing.access_tier,
            expires_at=incoming.expires_at or existing.expires_at,
            value=max(existing.value, incoming.value),
            credibility=credibility,
            muse_fit=max(existing.muse_fit, incoming.muse_fit),
            friction_penalty=min(existing.friction_penalty, incoming.friction_penalty),
            evidence_chain=chain,
            traps=tuple(sorted(set((*existing.traps, *incoming.traps)), key=lambda item: item.value)),
            license_spdx=incoming.license_spdx or existing.license_spdx,
            license_risk=incoming.license_risk if incoming.license_spdx else existing.license_risk,
            distinct_source_count=len({item.source_id for item in chain}),
            score_reasons=tuple(dict.fromkeys((*existing.score_reasons, *incoming.score_reasons))),
            metadata=metadata,
            updated_at=utc_now().isoformat(),
        )
        return merged, max(0, len(chain) - len(existing.evidence_chain))

    def _identity_hints(self, candidate: OpportunityCandidate) -> tuple[tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...]]:
        sources: list[dict[str, Any]] = []
        for index, item in enumerate(candidate.evidence_chain):
            source_id = item.source_id.strip()
            if not source_id:
                continue
            sources.append({
                "source_node_id": source_id,
                "id": source_id,
                "url": item.url,
                "external_ref": item.url,
                "title": item.title,
                "metadata": {
                    "content_hash": item.content_sha256,
                    "sha256": item.content_sha256,
                    "truncated": False,
                    "source_identity": {
                        "canonical_url": item.canonical_url or item.url,
                        "authors": [item.publisher] if item.publisher else [],
                        "publisher": item.publisher,
                    },
                },
            })
        if len(sources) < 2:
            return (), ()
        try:
            identity = build_source_identity_candidates(sources, existing_sources=None)
        except (TypeError, ValueError, KeyError):
            identity = []
        try:
            exact = find_exact_content_matches(sources, existing_sources=None)
        except (TypeError, ValueError, KeyError):
            exact = []
        return tuple(identity), tuple(exact)

    def merge(self, incoming: OpportunityCandidate) -> MergeResult:
        if not isinstance(incoming, OpportunityCandidate):
            raise TypeError("incoming must be an OpportunityCandidate")
        fingerprint = ContentFingerprint.build(incoming)
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                exact_rows = self.connection.execute(
                    "SELECT * FROM radar_opportunities WHERE url_key=? ORDER BY updated_at DESC LIMIT 5",
                    (fingerprint.url_key,),
                ).fetchall()
                rows = exact_rows or self.connection.execute(
                    "SELECT * FROM radar_opportunities ORDER BY updated_at DESC LIMIT 5000"
                ).fetchall()
                matches: list[tuple[OpportunityCandidate, ContentFingerprint]] = []
                for row in rows:
                    current, current_fp = self._from_row(row)
                    if self._matches(current_fp, fingerprint):
                        matches.append((current, current_fp))
                if matches:
                    matches.sort(key=lambda pair: (pair[0].created_at, pair[0].candidate_id))
                    existing, _ = matches[0]
                    merged, added = self._merge(existing, incoming)
                    merged_fp = ContentFingerprint.build(merged)
                    payload = json.dumps(merged.to_dict(), ensure_ascii=False, sort_keys=True, allow_nan=False)
                    self.connection.execute(
                        """UPDATE radar_opportunities
                           SET url_key=?,title_simhash=?,title_shingles_json=?,body_simhash=?,
                               body_shingles_json=?,candidate_json=?,updated_at=?
                           WHERE candidate_id=?""",
                        (
                            merged_fp.url_key, merged_fp.title_simhash,
                            json.dumps(merged_fp.title_shingles, ensure_ascii=False),
                            merged_fp.body_simhash,
                            json.dumps(merged_fp.body_shingles, ensure_ascii=False),
                            payload, merged.updated_at, existing.candidate_id,
                        ),
                    )
                    self.connection.commit()
                    identity, exact = self._identity_hints(merged)
                    return MergeResult(
                        merged, False, added, merged.distinct_source_count,
                        merged.independent_source_count, identity, exact,
                    )
                payload = json.dumps(incoming.to_dict(), ensure_ascii=False, sort_keys=True, allow_nan=False)
                self.connection.execute(
                    """INSERT INTO radar_opportunities
                       (candidate_id,url_key,title_simhash,title_shingles_json,body_simhash,
                        body_shingles_json,candidate_json,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        incoming.candidate_id, fingerprint.url_key, fingerprint.title_simhash,
                        json.dumps(fingerprint.title_shingles, ensure_ascii=False),
                        fingerprint.body_simhash, json.dumps(fingerprint.body_shingles, ensure_ascii=False),
                        payload, incoming.created_at, incoming.updated_at,
                    ),
                )
                self.connection.commit()
                identity, exact = self._identity_hints(incoming)
                return MergeResult(
                    incoming, True, len(incoming.evidence_chain),
                    incoming.distinct_source_count, incoming.independent_source_count,
                    identity, exact,
                )
            except Exception:
                self.connection.rollback()
                raise

    def get(self, candidate_id: str) -> OpportunityCandidate | None:
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise ValueError("candidate_id must be non-empty text")
        with self._lock:
            row = self.connection.execute(
                "SELECT candidate_json FROM radar_opportunities WHERE candidate_id=?",
                (candidate_id.strip(),),
            ).fetchone()
        return OpportunityCandidate.from_mapping(json.loads(row["candidate_json"])) if row else None

    def list_recent(self, *, limit: int = 100) -> list[OpportunityCandidate]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer from 1 to 500")
        with self._lock:
            rows = self.connection.execute(
                "SELECT candidate_json FROM radar_opportunities ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [OpportunityCandidate.from_mapping(json.loads(row["candidate_json"])) for row in rows]
